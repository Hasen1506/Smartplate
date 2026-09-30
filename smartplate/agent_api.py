"""Delegated, scoped MCP tools and owner-only browser checkout handoffs.

Streamable HTTP JSON responses, explicitly supporting the 2025 protocol versions.
Bearer tokens are separate from recovery codes and Swiggy tokens. They cannot
approve a purchase or reach owner APIs. A stdio bridge covers desktop clients.
"""
import datetime as dt
import json
import secrets
from urllib.parse import urlsplit

from flask import jsonify, request

from . import access, clock, config, db, food_memory, live_basket, live_core, ratelimit
from .integrations import swiggy_connect, swiggy_live as live
from .runtime import state_lock

VERSIONS = ('2025-03-26', '2025-06-18', '2025-11-25')
SCOPES = ('read', 'plan', 'memory', 'cart')
SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_grants (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 token_hash TEXT NOT NULL UNIQUE, label TEXT NOT NULL, scopes TEXT NOT NULL,
 created_ts TEXT NOT NULL, expires_ts TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
 last_used_ts TEXT
);
CREATE TABLE IF NOT EXISTS agent_handoffs (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 grant_id TEXT NOT NULL REFERENCES agent_grants(id), quote_token TEXT NOT NULL,
 payload TEXT NOT NULL, state TEXT NOT NULL, expires_ts TEXT NOT NULL, result TEXT
);
"""


def init_schema():
    with db.cursor() as cur:
        cur.executescript(SCHEMA)


def base_url():
    url = config.PUBLIC_URL.rstrip('/') or request.host_url.rstrip('/')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1')):
        raise ValueError('Configure the HTTPS public URL before enabling agent connections')
    return url


def private_owner(user_id):
    with db.cursor() as cur:
        row = cur.execute('SELECT access_hash FROM users WHERE id=?', (user_id,)).fetchone()
    if not row or not row['access_hash']:
        raise ValueError('Create a private profile to connect your personal agent')


def grants(user_id):
    private_owner(user_id)
    with db.cursor() as cur:
        rows = cur.execute('SELECT id,label,scopes,created_ts,expires_ts,revoked,last_used_ts FROM agent_grants WHERE user_id=? ORDER BY created_ts DESC', (user_id,)).fetchall()
    return {'endpoint': base_url() + '/mcp', 'connections': [{**dict(r), 'scopes': db.jl(r['scopes'])} for r in rows]}


def issue(user_id, body):
    private_owner(user_id)
    label, scopes, days = body.get('label'), body.get('scopes'), body.get('days', 7)
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
        raise ValueError('Name the agent connection using 1–80 characters')
    if not isinstance(scopes, list) or not scopes or len(scopes) > 4 or any(s not in SCOPES for s in scopes):
        raise ValueError('Choose explicit read, plan, memory and/or cart permissions')
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 30:
        raise ValueError('Connection lifetime must be 1–30 days')
    now = clock.now()
    token, identifier = secrets.token_urlsafe(32), secrets.token_urlsafe(16)
    with db.cursor() as cur:
        cur.execute('DELETE FROM agent_handoffs WHERE user_id=? AND expires_ts<?', (user_id, now.isoformat()))
        count = cur.execute('SELECT COUNT(*) FROM agent_grants WHERE user_id=? AND revoked=0 AND expires_ts>?', (user_id, now.isoformat())).fetchone()[0]
        if count >= 10:
            raise ValueError('Revoke an existing connection before adding another')
        cur.execute('INSERT INTO agent_grants(id,user_id,token_hash,label,scopes,created_ts,expires_ts) VALUES (?,?,?,?,?,?,?)',
                    (identifier, user_id, access.digest(token), label.strip(), db.jd(sorted(set(scopes))),
                     now.isoformat(), (now + dt.timedelta(days=days)).isoformat()))
    return {'id': identifier, 'token': token, 'endpoint': base_url() + '/mcp',
            'expires_ts': (now + dt.timedelta(days=days)).isoformat(), 'scopes': sorted(set(scopes)),
            'note': 'Shown once. Store as a connector secret. It cannot approve purchases.'}


def revoke(user_id, grant_id):
    with db.cursor() as cur:
        cur.execute('UPDATE agent_grants SET revoked=1 WHERE id=? AND user_id=?', (grant_id, user_id))
        cur.execute("UPDATE agent_handoffs SET state='revoked' WHERE grant_id=? AND user_id=? AND state='pending'", (grant_id, user_id))
    return {'revoked': True}


def authenticate():
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer ') or not 20 <= len(header[7:]) <= 200:
        return None
    with db.cursor() as cur:
        row = cur.execute('SELECT * FROM agent_grants WHERE token_hash=? AND revoked=0 AND expires_ts>?',
                          (access.digest(header[7:]), clock.now().isoformat())).fetchone()
        if row:
            cur.execute('UPDATE agent_grants SET last_used_ts=? WHERE id=?', (clock.now().isoformat(), row['id']))
    return {**dict(row), 'scopes': db.jl(row['scopes'])} if row else None


def string(maximum=120, **extra):
    return {'type': 'string', 'minLength': 1, 'maxLength': maximum, **extra}


def obj(properties=None, required=()):
    return {'type': 'object', 'properties': properties or {}, 'required': list(required), 'additionalProperties': False}


def validate(value, schema, path='arguments'):
    kind = schema.get('type')
    good = {'object': lambda: isinstance(value, dict), 'array': lambda: isinstance(value, list),
            'string': lambda: isinstance(value, str), 'integer': lambda: isinstance(value, int) and not isinstance(value, bool),
            'number': lambda: isinstance(value, (int, float)) and not isinstance(value, bool),
            'boolean': lambda: isinstance(value, bool)}
    if kind not in good or not good[kind]():
        raise ValueError(path + ' has an invalid type')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(path + ' has an unsupported value')
    if kind == 'object':
        properties = schema.get('properties', {})
        if any(k not in value for k in schema.get('required', [])):
            raise ValueError(path + ' is missing required fields')
        for k, v in value.items():
            if k in properties:
                validate(v, properties[k], path + '.' + k)
            elif schema.get('additionalProperties') is False:
                raise ValueError(path + ' contains an unknown field')
            elif isinstance(schema.get('additionalProperties'), dict):
                validate(v, schema['additionalProperties'], path + '.' + k)
    if kind == 'array':
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 100):
            raise ValueError(path + ' has too many or too few entries')
        for v in value:
            validate(v, schema['items'], path + '[]')
    if kind == 'string' and not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 1000):
        raise ValueError(path + ' has an invalid length')
    if kind in ('number', 'integer'):
        import math
        if not math.isfinite(value) or not schema.get('minimum', -1e10) <= value <= schema.get('maximum', 1e10):
            raise ValueError(path + ' has an invalid number')


MEAL = string(enum=['breakfast', 'lunch', 'dinner'])
PLACE = {'restaurant_id': string(), 'restaurant_name': string(200)}
ITEM = {**PLACE, 'item_id': string(), 'item_name': string(200)}
MAPPING = {'type': 'object', 'additionalProperties': string()}
WEEK = obj({'budget': {'type': 'number', 'minimum': 1, 'maximum': 100000},
            'fee_reserve': {'type': 'number', 'minimum': 0, 'maximum': 500}, 'start_date': string(10),
            'meals': {'type': 'array', 'items': MEAL, 'minItems': 1, 'maxItems': 3},
            'times': obj({m: string(5) for m in ('breakfast', 'lunch', 'dinner')})}, ('budget', 'meals'))
LINE = obj({'item_id': string(), 'item_name': string(200), 'quantity': {'type': 'integer', 'minimum': 1, 'maximum': 10},
            'variants': MAPPING, 'same_options': {'type': 'boolean'}}, ('item_id', 'item_name', 'quantity', 'variants'))
BASKET = obj({**PLACE, 'items': {'type': 'array', 'items': LINE, 'minItems': 1, 'maxItems': 10}}, ('restaurant_id', 'restaurant_name', 'items'))
TOOLS = {}


def tool(name, scope, description, schema, handler, *, readonly=False):
    TOOLS[name] = {'scope': scope, 'handler': handler, 'definition': {'name': name, 'description': description,
        'inputSchema': schema, 'annotations': {'readOnlyHint': readonly, 'destructiveHint': not readonly,
                                             'openWorldHint': True, 'idempotentHint': readonly}}}


def request_checkout(uid, body, grant):
    preview = live.live_checkout_preview(uid)
    token = preview.pop('fingerprint')
    identifier = secrets.token_urlsafe(24)
    with db.cursor() as cur:
        cur.execute("UPDATE agent_handoffs SET state='superseded' WHERE user_id=? AND state='pending'", (uid,))
        cur.execute('INSERT INTO agent_handoffs VALUES (?,?,?,?,?,?,?,NULL)',
                    (identifier, uid, grant['id'], token, db.jd(preview), 'pending',
                     (clock.now() + dt.timedelta(minutes=5)).isoformat()))
    return {'status': 'human_approval_required', 'review_id': identifier, 'quote': preview,
            'approval_url': base_url() + '/?tab=places&agent_review=' + identifier,
            'note': 'The profile owner must open this link on their signed-in device and review and confirm. Planning permission is not permission to spend.'}


def handoff(uid, identifier, grant_id=None):
    with db.cursor() as cur:
        row = cur.execute('SELECT h.* FROM agent_handoffs h JOIN agent_grants g ON g.id=h.grant_id WHERE h.id=? AND h.user_id=? AND g.revoked=0 AND g.expires_ts>?',
                          (identifier, uid, clock.now().isoformat())).fetchone()
    if not row or (grant_id and row['grant_id'] != grant_id):
        raise ValueError('This review is not available for this profile or agent')
    state = row['state']
    if state == 'pending' and row['expires_ts'] < clock.now().isoformat():
        state = 'expired'
    return {**dict(row), 'state': state, 'payload': db.jl(row['payload'], {}), 'result': db.jl(row['result'], None)}


def handoff_view(uid, identifier):
    row = handoff(uid, identifier)
    return {'review_id': identifier, 'state': row['state'], 'quote': row['payload'], 'result': row['result'], 'expires_ts': row['expires_ts']}


def approve(uid, identifier):
    # Only an owner API calls this function. MCP never sees the quote token.
    row = handoff(uid, identifier)
    if row['state'] != 'pending':
        raise live.CartChanged('This review expired or was already used; ask the agent for a fresh review')
    with db.cursor() as cur:
        cur.execute("UPDATE agent_handoffs SET state='started' WHERE id=? AND user_id=? AND state='pending'", (identifier, uid))
        if cur.rowcount != 1:
            raise live.CartChanged('This review was already used')
    try:
        result = live.place_live_order(uid, row['quote_token'])
    except Exception:
        with db.cursor() as cur:
            cur.execute("UPDATE agent_handoffs SET state='review_failed' WHERE id=?", (identifier,))
        raise
    with db.cursor() as cur:
        cur.execute("UPDATE agent_handoffs SET state='confirmed',result=? WHERE id=?", (db.jd(result), identifier))
    return result


def confirm(uid, body, grant):
    row = handoff(uid, body['review_id'], grant['id'])
    return {'status': row['state'], 'result': row['result'],
            'note': 'This tool only reports the owner approval outcome. It cannot place an order or approve for the owner.'}


def setup_tools():
    def add(name, scope, desc, schema, fn, readonly=False):
        tool(name, scope, desc, schema, lambda u, b, g: fn(u, b), readonly=readonly)
    add('get_connection_status', 'read', 'Read connection state; owner connects Swiggy in SmartPlate. No credentials returned.', obj(), lambda u,b: swiggy_connect.status(u), True)
    add('get_addresses', 'read', 'Read real Swiggy saved addresses. Never invent an address ID.', obj(), lambda u,b: {'addresses': live.addresses(u)}, True)
    add('choose_address', 'cart', 'Select a real saved address; clears prepared cart intent. Existing plan for a different address is hidden.', obj({'address_id': string()}, ('address_id',)), lambda u,b: live.choose_address(u,b['address_id']))
    add('search_restaurants', 'read', 'Search real restaurants serviceable at the selected address.', obj({'query': string(80)}, ('query',)), lambda u,b: live.search_live_restaurants(u,b['query']), True)
    add('get_favourites', 'read', 'Read real address-scoped favourite restaurants.', obj(), lambda u,b: {'restaurants': live.live_favourites(u)}, True)
    add('set_favourite', 'plan', 'Set favourite explicitly, using a verified restaurant ID. Does not toggle on retries.', obj({**PLACE,'favourite': {'type':'boolean'}}, ('restaurant_id','restaurant_name','favourite')), set_favourite)
    add('get_menu', 'read', 'Fresh real compact menu, maximum 150 items. Prices/options may be unknown.', obj(PLACE, PLACE.keys()), lambda u,b: live.live_menu(u,b['restaurant_id'],b['restaurant_name']), True)
    add('search_dishes', 'read', 'Search a real restaurant menu, including beyond its compact browse cap.', obj({**PLACE,'query':string(80),'offset':{'type':'integer','minimum':0,'maximum':10000}}, (*PLACE.keys(),'query')), lambda u,b: live.search_live_dishes(u,b['restaurant_id'],b['restaurant_name'],b['query'],b.get('offset',0)), True)
    add('plan_meals', 'plan', 'Plan a 7-day estimate from live favourite menus. Require explicit covered meals and budget. Coverage precedes variety. Never purchases or guarantees nutrition/portions.', WEEK, live_core.build_week)
    add('revise_plan', 'plan', 'Replan remaining week after actual spending; keep recorded/past meals. Requires latest version.', obj({**WEEK['properties'],'expected_version':{'type':'integer','minimum':1}}, ('expected_version',)), live_core.revise_week)
    add('get_week_status', 'read', 'Read week, actual ledger, remaining budget and whether revision is needed.', obj(), lambda u,b: live_core.week_status(u), True)
    add('get_food_memory', 'read', 'Read explicit food preferences. No assumed or generated personal history.', obj(), lambda u,b: {'items':food_memory.memory(u)}, True)
    add('remember_food', 'memory', 'Store user-stated like/avoid and meal suitability for a real dish. Avoid is a hard planner exclusion; unknown ingredient safety is unchanged.', obj({'restaurant_id':string(),'item_id':string(),'item_name':string(200),'preference':string(enum=['like','neutral','avoid']), 'suitable_meals':{'type':'array','items':MEAL,'maxItems':3},'note':string(500,minLength=0)}, ('restaurant_id','item_id','preference')), remember_food)
    add('forget_food', 'memory', 'Remove a saved dish preference.', obj({'restaurant_id':string(),'item_id':string()}, ('restaurant_id','item_id')), lambda u,b:food_memory.forget(u,b['restaurant_id'],b['item_id']))
    add('record_meal', 'memory', 'Record what the user actually ate and paid, including home meals. Requires a stable unique event key; retries never double-count. User-reported, not provider-verified.', obj({'date':string(10),'meal':MEAL,'amount':{'type':'number','minimum':0,'maximum':100000},'event_key':string(120,minLength=8),'note':string(500,minLength=0)}, ('date','meal','amount','event_key')), food_memory.record)
    add('assign_order_meal', 'memory', 'Allocate a confirmed SmartPlate order to the meal it covered. Its verified payable amount is already in the ledger and cannot be changed.', obj({'order_id':string(),'meal':MEAL},('order_id','meal')),food_memory.assign_order)
    add('remove_reported_meal', 'memory', 'Remove an incorrect user-reported meal, then record the correction with a fresh event key.', obj({'event_id':{'type':'integer','minimum':1}}, ('event_id',)),lambda u,b:food_memory.remove_event(u,b['event_id']))
    add('quote_meal', 'read', 'Fetch fresh dish details and valid variant groups before preparing a cart.', obj(ITEM, ITEM.keys()), live_basket.details, True)
    add('review_basket', 'cart', 'Preview real basket selections; returns a fingerprint to supply when preparing it. Does not change the cart.', BASKET, live_basket.preview, True)
    add('prepare_order', 'cart', 'Prepare an empty Swiggy cart from the reviewed exact basket fingerprint. Refuses stale selections or an occupied cart. Does not purchase.', obj({**BASKET['properties'],'expected_fingerprint':string(200)}, (*BASKET['required'],'expected_fingerprint')), live_basket.prepare)
    add('get_cart', 'read', 'Read current real cart and whether its exact intent is verified.', obj(),lambda u,b:live.current_live_cart(u), True)
    add('get_addons', 'read', 'Read variant-specific add-on constraints for the prepared basket.', obj(), lambda u,b:live_basket.addon_review(u), True)
    selections={'type':'object','additionalProperties':{'type':'object','additionalProperties':{'type':'array','items':string(),'maxItems':20}}}
    add('set_addons','cart','Apply provider-offered add-ons to the reviewed basket, then verify readback.',obj({'selections':selections,'expected_fingerprint':string(200),'same_options':{'type':'boolean'}},('selections','expected_fingerprint')),live_basket.add_addons)
    tool('request_order_review','cart','Request a five-minute browser handoff for human approval of the exact cart total/address/COD. Agent cannot approve or buy.',obj(),request_checkout)
    tool('confirm_order','read','Report the outcome of human checkout approval. Never executes purchases; approved:true is not accepted.',obj({'review_id':string()},('review_id',)),confirm,readonly=True)
    add('track_order','read','Track an order confirmed for this profile.',obj({'order_id':string()},('order_id',)),lambda u,b:live.live_order_status(u,b['order_id']),True)


def set_favourite(uid, body):
    existing = any(r['id'] == body['restaurant_id'] for r in live.live_favourites(uid))
    if existing != body['favourite']:
        return live.toggle_live_favourite(uid,body['restaurant_id'],body['restaurant_name'])
    return {'favourite':existing,'restaurants':live.live_favourites(uid)}


def remember_food(uid, body):
    # Verify IDs without inferring preference from a click or price.
    restaurants = live.live_favourites(uid)
    place = next((r for r in restaurants if r['id'] == body['restaurant_id']), None)
    if not place:
        raise ValueError('Favourite the real restaurant before storing dish memory')
    menu = live.live_menu(uid,place['id'],place['name'])
    if not any(i['id'] == body['item_id'] for i in menu['items']):
        if not body.get('item_name'):
            raise ValueError('Supply the exact dish name from scoped search for a dish beyond the compact menu')
        live.live_item_details(uid, place['id'], place['name'], body['item_id'], body['item_name'])
    return food_memory.remember(uid,body)


setup_tools()


def register(app):
    @app.route('/mcp', methods=['POST','GET','DELETE'])
    def mcp():
        if request.headers.get('Origin') and request.headers['Origin'] != base_url():
            return jsonify(error='Invalid origin'),403
        grant = authenticate()
        if not grant:
            response = jsonify(error='Connect using a scoped agent token from your private profile')
            response.status_code=401
            response.headers['WWW-Authenticate']='Bearer realm="SmartPlate"'
            return response
        if request.method != 'POST':
            return jsonify(error='No SSE stream or server session; use POST'),405
        version=request.headers.get('MCP-Protocol-Version','2025-03-26')
        if version not in VERSIONS:
            return jsonify(error='Unsupported MCP protocol version',supported=list(VERSIONS)),400
        accept=request.headers.get('Accept','')
        if 'application/json' not in accept or 'text/event-stream' not in accept:
            return jsonify(error='Accept must include application/json and text/event-stream'),406
        body=request.get_json(silent=True)
        identifier=body.get('id') if isinstance(body,dict) else None
        def error(code,message,status=200):
            return jsonify(jsonrpc='2.0',id=identifier,error={'code':code,'message':message}),status
        if not isinstance(body,dict) or body.get('jsonrpc')!='2.0' or not isinstance(body.get('method'),str) or (identifier is not None and (isinstance(identifier,bool) or not isinstance(identifier,(str,int)))):
            return error(-32600,'Invalid JSON-RPC request',400)
        method,params=body['method'],body.get('params',{})
        if not isinstance(params,dict):
            return error(-32602,'params must be an object')
        ratelimit.check('agent:'+grant['id'],60,60)
        if 'id' not in body:
            if method not in ('notifications/initialized','notifications/cancelled'):
                return error(-32600,'Unsupported notification',400)
            return '',202
        if method=='initialize':
            requested=params.get('protocolVersion')
            result={'protocolVersion':requested if requested in VERSIONS else VERSIONS[-1],
                    'serverInfo':{'name':'SmartPlate','version':'1.2.0'},'capabilities':{'tools':{'listChanged':False}},
                    'instructions':'Tools use only the connected private profile. Menus and totals are fresh state; meal suitability and nutrition may be unverified. Obtain exact user meal scope and budget. Request browser checkout approval; never interpret plan or cart permission as permission to spend.'}
        elif method=='ping':
            result={}
        elif method=='tools/list':
            result={'tools':[t['definition'] for t in TOOLS.values() if t['scope'] in grant['scopes']]}
        elif method=='tools/call':
            name=params.get('name')
            if not isinstance(name,str) or name not in TOOLS:
                return error(-32602,'Unknown tool')
            t=TOOLS[name]
            if t['scope'] not in grant['scopes']:
                return error(-32602,'This connection has no permission for this tool')
            try:
                arguments=params.get('arguments',{})
                validate(arguments,t['definition']['inputSchema'])
                with state_lock:
                    refreshed = authenticate()
                    if not refreshed or refreshed['id'] != grant['id']:
                        raise ValueError('This agent connection expired or was revoked')
                    output=t['handler'](grant['user_id'],arguments,grant)
                if not isinstance(output,dict):
                    output={'items':output}
                result={'content':[{'type':'text','text':json.dumps(output,ensure_ascii=False,allow_nan=False)}]}
                if version!='2025-03-26':
                    result['structuredContent']=output
            except (ValueError,KeyError,TypeError,live.CartChanged,swiggy_connect.SwiggyError) as exc:
                payload={'status':'price_changed' if isinstance(exc,live.CartChanged) else 'action_required',
                         'code':getattr(exc,'code','invalid_input'),'message':str(exc)}
                result={'isError':True,'content':[{'type':'text','text':json.dumps(payload)}]}
        else:
            return error(-32601,'Method not found')
        response=jsonify(jsonrpc='2.0',id=identifier,result=result)
        response.headers['Cache-Control']='no-store'
        return response

    @app.get('/api/user/<int:user_id>/agents')
    def list_agents(user_id):
        return jsonify(grants(user_id))

    @app.post('/api/user/<int:user_id>/agents')
    def connect_agent(user_id):
        return jsonify(issue(user_id,request.get_json()))

    @app.post('/api/user/<int:user_id>/agents/<grant_id>/revoke')
    def revoke_agent(user_id,grant_id):
        return jsonify(revoke(user_id,grant_id))

    @app.get('/api/user/<int:user_id>/agent-review/<identifier>')
    def review(user_id,identifier):
        return jsonify(handoff_view(user_id,identifier))

    @app.post('/api/user/<int:user_id>/agent-review/<identifier>')
    def checkout(user_id,identifier):
        if request.get_json().get('confirmation')!='PLACE ORDER':
            raise ValueError('Review and explicitly confirm this order')
        return jsonify(approve(user_id,identifier))
