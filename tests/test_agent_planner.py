"""Coverage, ledger, delegated access and human approval contract regressions."""
import datetime as dt
import json

import pytest

from smartplate import clock, config, db, food_memory, live_planner
from smartplate.app import create_app
from smartplate.integrations import swiggy_connect
from test_followups import _connect
from live_stub import FakeBasket


def slots(n=7):
    return [{'date':(dt.date(2026,10,1)+dt.timedelta(days=i)).isoformat(),'meal':'dinner'} for i in range(n)]


def candidates():
    return [{'id':'cheap','restaurant_id':'real-r','price':50},
            {'id':'expensive','restaurant_id':'real-r','price':200}]


def test_coverage_precedes_variety_in_700_rupee_week():
    result=live_planner.solve(slots(),candidates(),700,50)
    assert result['covered']==7 and result['estimated_total']==700
    assert all(c['id']=='cheap' for c in result['items'])
    assert result['shortfall']==0 and result['solver']['coverage_optimal']


def test_preferences_never_override_budget_and_shortfall_is_explicit():
    result=live_planner.solve(slots(),candidates(),650,50,
        preferences={'real-r:expensive':{'preference':'like'}})
    assert result['covered']==6 and result['estimated_total']<=650
    assert result['minimum_full_coverage']==700 and result['shortfall']==50
    avoided=live_planner.solve(slots(),candidates(),700,50,
        preferences={'real-r:cheap':{'preference':'avoid'}})
    assert avoided['covered']==2 and avoided['shortfall']==1050


def test_suitability_and_no_solver_fallback_are_honest(monkeypatch):
    def failed(*args,**kwargs):
        import pulp
        raise pulp.PulpSolverError('No solver')
    monkeypatch.setattr('pulp.LpProblem.solve',failed)
    result=live_planner.solve(slots(),candidates(),700,50,
        preferences={'real-r:cheap':{'suitable_meals':['breakfast']}})
    assert result['covered']==2 and result['estimated_total']<=700
    assert result['solver']['fallback'] and not result['solver']['coverage_optimal']
    result=live_planner.solve(slots(),[],700,50)
    assert result['covered']==0 and result['shortfall'] is None


@pytest.fixture
def core(seeded,monkeypatch):
    monkeypatch.setattr(config,'LIVE_ORDERS',True)
    fake=FakeBasket()
    monkeypatch.setattr(swiggy_connect,'_http',fake)
    client=create_app().test_client()
    _connect(client,fake,uid=3)
    monkeypatch.setattr(config,'PUBLIC_URL','http://localhost')
    assert client.post('/api/user/3/swiggy/address',json={'address_id':'addr-home'}).status_code==200
    assert client.post('/api/user/3/swiggy/favourites',json={'restaurant_id':'r-1','restaurant_name':'Hotel Saravana Bhavan'}).status_code==200
    return client,fake


def grant(client,scopes=None):
    response=client.post('/api/user/3/agents',json={'label':'Test agent','scopes':scopes or ['read','plan','memory','cart'],'days':1})
    assert response.status_code==200,response.get_json()
    return response.get_json()


def rpc(client,token,name,arguments=None,**headers):
    return client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/call',
        'params':{'name':name,'arguments':arguments or {}}},headers={
        'Authorization':'Bearer '+token,'Accept':'application/json, text/event-stream',
        'MCP-Protocol-Version':'2025-11-25',**headers})


def output(response):
    assert response.status_code==200,response.get_json()
    result=response.get_json()['result']
    assert not result.get('isError'),result
    return json.loads(result['content'][0]['text'])


def test_mcp_protocol_scope_revoke_expiry_and_owner_isolation(core):
    client,fake=core
    g=grant(client,['read'])
    headers={'Authorization':'Bearer '+g['token'],'Accept':'application/json, text/event-stream'}
    initial=client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'initialize',
        'params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'Test','version':'1'}}},headers=headers)
    assert initial.get_json()['result']['protocolVersion']=='2025-06-18'
    tools=client.post('/mcp',json={'jsonrpc':'2.0','id':2,'method':'tools/list'},headers=headers).get_json()['result']['tools']
    assert 'plan_meals' not in {t['name'] for t in tools}
    assert rpc(client,g['token'],'plan_meals',{'budget':700,'meals':['dinner']}).get_json()['error']['code']==-32602
    assert rpc(client,g['token'],'get_food_memory',{'user_id':1}).get_json()['result']['isError']
    assert rpc(client,g['token'],'get_menu',{'restaurant_id':'r-1','restaurant_name':'Hotel Saravana Bhavan'},Origin='https://evil.example').status_code==403
    stranger=create_app().test_client()
    assert stranger.get('/api/user/3/food-memory',headers={'Authorization':'Bearer '+g['token']}).status_code==401
    assert stranger.post('/api/user/3/agents',json={'label':'Steal','scopes':['read']},headers={'Authorization':'Bearer '+g['token']}).status_code==401
    assert stranger.get('/mcp',headers=headers).status_code==405
    assert client.post('/api/user/3/agents/'+g['id']+'/revoke',json={}).status_code==200
    assert rpc(client,g['token'],'get_food_memory').status_code==401
    g=grant(client,['read'])
    with db.cursor() as cur:
        cur.execute("UPDATE agent_grants SET expires_ts='2000-01-01' WHERE id=?",(g['id'],))
    assert rpc(client,g['token'],'get_food_memory').status_code==401


def test_memory_ledger_and_revision_preserve_actual_spend(core):
    client,fake=core
    now=clock.now().date().isoformat()
    first=client.post('/api/user/3/swiggy/week',json={'budget':1500,'meals':['dinner']}).get_json()['plan']
    assert first['coverage']['covered'] > 0
    event={'date':now,'meal':'dinner','amount':300,'note':'Actually ate out','event_key':'reported-meal-01'}
    assert client.post('/api/user/3/food-events',json=event).get_json()['duplicate'] is False
    assert client.post('/api/user/3/food-events',json=event).get_json()['duplicate'] is True
    assert client.post('/api/user/3/food-events',json={**event,'amount':301}).status_code==400
    assert client.get('/api/user/3/swiggy/week/status').get_json()['status']=='revision_required'
    assert client.post('/api/user/3/food-memory',json={'restaurant_id':'r-1','item_id':'m0','preference':'avoid'}).status_code==200
    revised=client.post('/api/user/3/swiggy/week/revise',json={'expected_version':first['version']}).get_json()['plan']
    assert revised['id']==first['id'] and revised['version']==2
    assert revised['actual_spend']==300 and revised['remaining_budget']==1200 and revised['estimated_total']<=1500
    assert revised['slots'][0]['state']=='recorded'
    assert all(not s['item'] or s['item']['id']!='m0' for s in revised['slots'])
    assert client.post('/api/user/3/swiggy/week/revise',json={'expected_version':1}).status_code==409
    assert 'place_food_order' not in fake.tool_calls()


def test_overspending_is_preserved_instead_of_clamped_away(core):
    client,fake=core
    first=client.post('/api/user/3/swiggy/week',json={'budget':700,'meals':['dinner']}).get_json()['plan']
    client.post('/api/user/3/food-events',json={'date':clock.now().date().isoformat(),'meal':'dinner','amount':800,'event_key':'overspend-01'})
    revised=client.post('/api/user/3/swiggy/week/revise',json={'expected_version':first['version']}).get_json()['plan']
    assert revised['remaining_budget']==-100 and revised['overspent']==100
    assert revised['estimated_future']==0 and revised['estimated_total']==800
    assert revised['status']=='budget_insufficient'


def prepared(client,token):
    body={'restaurant_id':'r-1','restaurant_name':'Hotel Saravana Bhavan',
        'items':[{'item_id':'m0','item_name':'Mini Tiffin','quantity':1,'variants':{}}]}
    preview=output(rpc(client,token,'review_basket',body))
    output(rpc(client,token,'prepare_order',{**body,'expected_fingerprint':preview['fingerprint']}))


def test_agent_cannot_approve_human_purchase_and_receipt_is_counted_once(core):
    client,fake=core
    g=grant(client)
    prepared(client,g['token'])
    review=output(rpc(client,g['token'],'request_order_review'))
    assert 'fingerprint' not in review['quote']
    assert output(rpc(client,g['token'],'confirm_order',{'review_id':review['review_id']}))['status']=='pending'
    assert rpc(client,g['token'],'confirm_order',{'review_id':review['review_id'],'approved':True}).get_json()['result']['isError']
    stranger=create_app().test_client()
    endpoint='/api/user/3/agent-review/'+review['review_id']
    assert stranger.post(endpoint,json={'confirmation':'PLACE ORDER'},headers={'Authorization':'Bearer '+g['token']}).status_code==401
    assert 'place_food_order' not in fake.tool_calls()
    result=client.post(endpoint,json={'confirmation':'PLACE ORDER'})
    assert result.status_code==200,result.get_json()
    assert client.post(endpoint,json={'confirmation':'PLACE ORDER'}).status_code==409
    assert fake.tool_calls().count('place_food_order')==1
    assert output(rpc(client,g['token'],'confirm_order',{'review_id':review['review_id']}))['status']=='confirmed'
    rows=food_memory.events(3,clock.now().date().isoformat(),(clock.now().date()+dt.timedelta(days=1)).isoformat())
    assert len(rows)==1 and rows[0]['amount_paise']==live_planner.paise(result.get_json()['to_pay']) and rows[0]['meal']=='unassigned'
    output(rpc(client,g['token'],'assign_order_meal',{'order_id':result.get_json()['order_id'],'meal':'dinner'}))
    assert len(food_memory.events(3,rows[0]['meal_date'],(clock.now().date()+dt.timedelta(days=1)).isoformat()))==1
    g2=grant(client)
    assert rpc(client,g2['token'],'confirm_order',{'review_id':review['review_id']}).get_json()['result']['isError']


def test_changed_quote_and_revoked_grant_never_purchase(core):
    client,fake=core
    g=grant(client)
    prepared(client,g['token'])
    review=output(rpc(client,g['token'],'request_order_review'))
    fake.dishes['Mini Tiffin']+=100
    assert client.post('/api/user/3/agent-review/'+review['review_id'],json={'confirmation':'PLACE ORDER'}).status_code==409
    assert 'place_food_order' not in fake.tool_calls()


def test_export_and_delete_include_memory_but_never_connector_secrets(core):
    client,fake=core
    g=grant(client)
    client.post('/api/user/3/food-memory',json={'restaurant_id':'r-1','item_id':'m0','preference':'like'})
    exported=client.get('/api/user/3/data.json').get_json()['data']
    assert exported['food_memory']
    assert g['token'] not in json.dumps(exported) and 'token_hash' not in json.dumps(exported)
    assert client.delete('/api/user/3',json={'confirmation':'DELETE'}).status_code==200
    assert rpc(client,g['token'],'get_food_memory').status_code==401


def test_generic_price_units_require_verified_configuration(monkeypatch):
    from smartplate.integrations.swiggy_live import rupees
    monkeypatch.setattr(config,'SWIGGY_MENU_PRICE_UNIT','unknown')
    assert rupees({'price':12500}) is None
    assert rupees({'priceInPaise':12500})==125
    monkeypatch.setattr(config,'SWIGGY_MENU_PRICE_UNIT','paise')
    assert rupees({'price':12500})==125
    monkeypatch.setattr(config,'SWIGGY_MENU_PRICE_UNIT','rupees')
    assert rupees({'price':125})==125
    assert rupees({'price':float('nan')}) is None


def test_invalid_solver_incumbent_is_not_trusted():
    import pulp
    problem=pulp.LpProblem('invalid',pulp.LpMinimize)
    x=pulp.LpVariable('x',cat='Binary');problem+=x;problem+=x==1
    for bad in (.5, 2, None, float('nan')):
        x.varValue=bad
        assert not live_planner.valid_incumbent(problem)
    x.varValue=0
    assert not live_planner.valid_incumbent(problem)
    x.varValue=1
    assert live_planner.valid_incumbent(problem)
