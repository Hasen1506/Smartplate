"""Flask JSON API and vanilla JavaScript UI; no frontend build step."""
import os
import datetime as dt

from flask import Flask, Response, g, jsonify, redirect, request, send_from_directory, session
from werkzeug.exceptions import HTTPException

from . import auth, config, db, service
from .domain import models, sentiment
from .domain.checkout import CheckoutConflict
from .kernel import agent_brain
from .integrations import calendar_sync, live_catalog, swiggy_discovery, swiggy_mcp, swiggy_oauth
from .runtime import initialize, state_lock

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _owns_plan(plan_id, user_id: int) -> bool:
    with db.cursor() as cur:
        row = cur.execute('SELECT id FROM plans WHERE id=? AND user_id=?',
                          (plan_id, user_id)).fetchone()
    return row is not None


def _owns_session(session_id, user_id: int) -> bool:
    with db.cursor() as cur:
        row = cur.execute('SELECT s.id FROM sessions s JOIN plans p ON p.id=s.plan_id '
                          'WHERE s.id=? AND p.user_id=?', (session_id, user_id)).fetchone()
    return row is not None


def create_app() -> Flask:
    if config.APP_MODE != 'demo':
        required = {'DATABASE_URL': config.DATABASE_URL,
                    'SMARTPLATE_SESSION_SECRET': config.SESSION_SECRET,
                    'SUPABASE_URL': config.SUPABASE_URL,
                    'SUPABASE_PUBLISHABLE_KEY': config.SUPABASE_PUBLISHABLE_KEY,
                    'SMARTPLATE_PUBLIC_BASE_URL': config.PUBLIC_BASE_URL,
                    'SMARTPLATE_INVITED_EMAILS': config.INVITED_EMAILS}
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError('Configure public mode: ' + ', '.join(missing))
    initialize()
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
    app.secret_key = config.SESSION_SECRET or ("local-demo-only" if config.APP_MODE == "demo" else None)
    if config.APP_MODE != "demo" and not config.SESSION_SECRET:
        raise RuntimeError("SMARTPLATE_SESSION_SECRET must be set for public mode")
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=config.APP_MODE != "demo",
                      PERMANENT_SESSION_LIFETIME=dt.timedelta(hours=12))
    app.config['MAX_CONTENT_LENGTH'] = 256 * 1024

    @app.before_request
    def validate_request():
        # The trial is same-origin and single-process. Prevent another browser
        # origin from using the private forwarded port to edit the plan.
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            if request.headers.get('Sec-Fetch-Site') == 'cross-site':
                return jsonify(error='Cross-site requests are not allowed'), 403
            if not request.is_json or not isinstance(request.get_json(silent=True), dict):
                return jsonify(error='Send a JSON object'), 400
        if request.path.startswith('/api/'):
            state_lock.acquire()
            g.state_locked = True
            if config.APP_MODE != "demo" and request.path not in (
                    "/api/meta", "/api/health", "/api/auth/request-code",
                    "/api/auth/verify-code", "/api/auth/session"):
                user_id = auth.current_user_id()
                if user_id is None:
                    return jsonify(error="Sign in to continue"), 401
                g.user_id = user_id
        args = request.view_args or {}
        for key, table in (('plan_id', 'plans'), ('user_id', 'users'), ('session_id', 'sessions')):
            if key in args:
                with db.cursor() as cur:
                    found = cur.execute(f'SELECT id FROM {table} WHERE id=?', (args[key],)).fetchone()
                if not found:
                    return jsonify(error='not found'), 404
        if config.APP_MODE != "demo" and request.path.startswith('/api/') and hasattr(g, "user_id"):
            if 'user_id' in args and args['user_id'] != g.user_id:
                return jsonify(error='not found'), 404
            if 'plan_id' in args and not _owns_plan(args['plan_id'], g.user_id):
                return jsonify(error='not found'), 404
            if 'session_id' in args and not _owns_session(args['session_id'], g.user_id):
                return jsonify(error='not found'), 404
            body = request.get_json(silent=True) if request.method in ('POST','PUT','PATCH','DELETE') else None
            if isinstance(body, dict):
                if 'user_id' in body and body['user_id'] != g.user_id:
                    return jsonify(error='not found'), 404
                if 'plan_id' in body and not _owns_plan(body['plan_id'], g.user_id):
                    return jsonify(error='not found'), 404
            if request.path.startswith('/api/demo/'):
                return jsonify(error='not found'), 404

    @app.teardown_request
    def release_state_lock(error):
        if g.pop('state_locked', False):
            state_lock.release()

    @app.errorhandler(ValueError)
    @app.errorhandler(KeyError)
    @app.errorhandler(TypeError)
    def invalid_input(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.errorhandler(swiggy_discovery.DiscoveryError)
    def discovery_error(error):
        return jsonify(error=str(error)), 503

    @app.after_request
    def response_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    # ---- UI ---- #
    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.post('/api/auth/request-code')
    def request_code():
        auth.request_code(str(request.json.get('email', '')))
        return jsonify(ok=True)

    @app.post('/api/auth/verify-code')
    def verify_code():
        return jsonify(auth.verify_code(str(request.json.get('email', '')),
                                       str(request.json.get('code', ''))))

    @app.get('/api/auth/session')
    def auth_session():
        user_id = auth.current_user_id()
        return jsonify(authenticated=user_id is not None, user_id=user_id,
                       mode=config.APP_MODE)

    @app.post('/api/auth/logout')
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get('/api/swiggy/status')
    def swiggy_status():
        return jsonify(swiggy_oauth.status(g.user_id))

    @app.post('/api/swiggy/connect')
    def swiggy_connect():
        return jsonify(url=swiggy_oauth.authorization_url(g.user_id))

    @app.get('/api/swiggy/callback')
    def swiggy_callback():
        if request.args.get('error'):
            return redirect('/?connection_error=authorization')
        swiggy_oauth.complete(g.user_id, request.args.get('code', ''),
                              request.args.get('state', ''))
        return redirect('/?connected=1')

    @app.post('/api/swiggy/disconnect')
    def swiggy_disconnect():
        swiggy_oauth.disconnect(g.user_id)
        return jsonify(ok=True)

    @app.get('/api/swiggy/addresses')
    def swiggy_addresses():
        return jsonify(live_catalog.addresses(g.user_id, int(request.args.get('page', '1'))))

    @app.post('/api/swiggy/address')
    def swiggy_address():
        return jsonify(live_catalog.select_address(g.user_id, request.json.get('address_id', '')))

    @app.get('/api/swiggy/restaurants')
    def swiggy_restaurants():
        return jsonify(live_catalog.search(g.user_id, request.args.get('query', ''),
                                           int(request.args.get('offset', '0'))))

    @app.get('/api/swiggy/restaurant/<provider_id>/menu')
    def swiggy_menu(provider_id):
        return jsonify(live_catalog.browse_menu(g.user_id, provider_id))

    @app.post('/api/swiggy/restaurant/<provider_id>/select')
    def swiggy_select(provider_id):
        selected = request.json.get('selected')
        if not isinstance(selected, bool):
            raise ValueError('selected must be true or false')
        live_catalog.select_restaurant(g.user_id, provider_id, selected)
        return jsonify(selected=selected)

    @app.get('/api/swiggy/selected')
    def swiggy_selected():
        return jsonify(live_catalog.selected_restaurants(g.user_id))

    # ---- meta / cost transparency ---- #
    @app.get("/api/meta")
    def meta():
        brain = agent_brain.get_brain()
        return jsonify({
            "version": "1.1.0",
            "brain": brain.name,
            "brain_cost_per_decision": brain.cost_per_decision,
            "swiggy_provider": 'simulated' if config.APP_MODE == 'demo' else 'live discovery',
            "app_mode": config.APP_MODE,
            "order_edit_window_min": config.ORDER_EDIT_WINDOW_MIN,
            "modes": config.MODE_LABELS,
            "mode_outcomes": {k: v["outcome"] for k, v in config.MODE_META.items()},
            "note": "v1.1 plans with a MILP solver — no per-decision LLM cost (see FEASIBILITY.md).",
            "features": _FEATURE_MAP,
        })

    # ---- users / plans ---- #
    @app.get("/api/users")
    def users():
        return jsonify(models.list_users() if config.APP_MODE == "demo" else
                       [u for u in models.list_users() if u['id'] == g.user_id])

    @app.get('/api/user/<int:user_id>/plan')
    def current_plan(user_id):
        return jsonify(service.current_plan(user_id))

    @app.get('/api/user/<int:user_id>/profile')
    def profile(user_id):
        return jsonify(service.profile_view(user_id))

    @app.patch('/api/user/<int:user_id>')
    def update_preferences(user_id):
        return jsonify(service.update_preferences(user_id, request.get_json()))

    @app.get('/api/plan/<int:plan_id>/orders')
    def order_history(plan_id):
        return jsonify(service.order_history(plan_id))

    @app.post("/api/plan")
    def create_plan():
        body = request.get_json(force=True, silent=True) or {}
        pid = service.create_plan(int(body["user_id"]), body.get("mode"), body.get("schedule"))
        return jsonify(service.plan_view(pid))

    @app.get("/api/plan/<int:plan_id>")
    def get_plan(plan_id):
        view = service.plan_view(plan_id)
        return (jsonify(view), 200) if view else (jsonify({"error": "not found"}), 404)

    @app.post("/api/plan/<int:plan_id>/optimize")
    def optimize(plan_id):
        body = request.get_json(force=True, silent=True) or {}
        service.reoptimize(plan_id, body.get("mode"))
        return jsonify(service.plan_view(plan_id))

    @app.get("/api/plan/<int:plan_id>/recommend-budget")
    def recommend_budget(plan_id):
        return jsonify(service.recommend_budget(plan_id))

    @app.post("/api/intake")
    def intake_estimate():
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(service.estimate_intake(body.get("text", "")))

    @app.post("/api/user/<int:user_id>/intake")
    def log_intake(user_id):
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(service.log_intake(
            user_id, body.get("text", ""), iso_date=body.get("date"),
            meal=body.get("meal", ""), source=body.get("source", "manual")))

    @app.get("/api/user/<int:user_id>/ledger")
    def nutrition_ledger(user_id):
        return jsonify(service.nutrition_ledger(user_id))

    @app.post("/api/plan/<int:plan_id>/command")
    def plan_command(plan_id):
        body = request.get_json(force=True, silent=True) or {}
        result = service.command(plan_id, body.get("text", ""))
        return jsonify({"result": result, "plan": service.plan_view(plan_id)})

    @app.post("/api/session/<int:session_id>/status")
    def session_status(session_id):
        body = request.get_json(force=True, silent=True) or {}
        service.set_session_status(session_id, body["status"], body.get("note", ""))
        return jsonify({"ok": True})

    @app.post("/api/session/<int:session_id>/kind")
    def session_kind(session_id):
        return jsonify(service.set_session_kind(session_id, request.json.get("kind")))

    # ---- execution: orders, substitution, idempotency ---- #
    @app.get("/api/plan/<int:plan_id>/execute/preview")
    def execute_preview(plan_id):
        if config.APP_MODE != 'demo':
            return jsonify(error='Open Swiggy to review current prices and order'), 503
        preview = service.execution_preview(plan_id)
        return (jsonify(preview), 200) if preview else (jsonify({"error": "not found"}), 404)

    @app.post("/api/plan/<int:plan_id>/execute")
    def execute(plan_id):
        body = request.get_json(silent=True) or {}
        if config.APP_MODE != 'demo' or config.SWIGGY_PROVIDER != 'simulated':
            return jsonify(error='Open Swiggy to review current prices and order'), 503
        if body.get('expected_fingerprint') is None or body.get('max_total') is None:
            return jsonify(error='checkout_conflict', message='Review the orders and approve the total first',
                           preview=service.execution_preview(plan_id)), 409
        try:
            result = service.execute(
                plan_id, expected_fingerprint=body.get("expected_fingerprint"),
                max_total=body.get("max_total"))
        except CheckoutConflict as exc:
            return jsonify({"error": "checkout_conflict", "message": str(exc),
                            "preview": service.execution_preview(plan_id)}), 409
        return (jsonify(result), 200) if result else (jsonify({"error": "not found"}), 404)

    # ---- reverse mode / cooking coach ---- #
    @app.get("/api/plan/<int:plan_id>/basket")
    def basket(plan_id):
        return jsonify(service.grocery_basket(plan_id))

    # ---- community ---- #
    @app.get("/api/community")
    def community_list():
        if config.APP_MODE != 'demo':
            return jsonify(error='not found'), 404
        return jsonify(service.list_community(request.args.get("city")))

    @app.post("/api/community/<int:template_id>/adopt")
    def community_adopt(template_id):
        if config.APP_MODE != 'demo':
            return jsonify(error='not found'), 404
        return jsonify(service.adopt_template(template_id) or {"error": "not found"})

    @app.post("/api/plan/<int:plan_id>/save-template")
    def save_template(plan_id):
        if config.APP_MODE != 'demo':
            return jsonify(error='not found'), 404
        body = request.get_json(force=True, silent=True) or {}
        tid = service.save_template(plan_id, body.get("title", "My plan"))
        return jsonify({"template_id": tid})

    # ---- receipts ---- #
    @app.post("/api/plan/<int:plan_id>/receipts")
    def gen_receipts(plan_id):
        if config.APP_MODE != 'demo':
            return jsonify(error='No orders have been placed by SmartPlate'), 503
        return jsonify(service.record_receipts(plan_id))

    @app.get("/api/receipts/<int:user_id>")
    def receipts(user_id):
        return jsonify(service.receipts_view(user_id))

    @app.get("/api/receipts/<int:user_id>/export.csv")
    def receipts_csv(user_id):
        from .domain.receipts import export_csv
        return Response(export_csv(user_id), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=smartplate_expenses.csv"})

    # ---- calendar (.ics ingest) ---- #
    @app.post("/api/user/<int:user_id>/calendar/ics")
    def ingest_ics(user_id):
        body = request.get_json(force=True, silent=True) or {}
        plan = models.get_plan(int(body["plan_id"]))
        n = calendar_sync.ingest_ics(user_id, body.get("ics", ""), plan["week_start"])
        return jsonify({"events_added": n})

    # ---- sentiment demo (shows the free, local NLP) ---- #
    @app.post("/api/sentiment")
    def sentiment_demo():
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(sentiment.aggregate(body.get("reviews", [])))

    # ---- idempotency demo: place the same order twice ---- #
    @app.post("/api/demo/idempotency")
    def idempotency_demo():
        prov = swiggy_mcp.SimulatedSwiggyProvider(seed=1)
        decision = {"id": -1, "cost": 199.0}
        restaurant = {"id": 1, "name": "Demo Diner", "flaky": 0}
        item = {"id": 1, "name": "Demo Meal"}
        kw = dict(user_id=1, plan_id=1, session_id=999, trigger_ts="2026-06-15T13:00:00")
        first = swiggy_mcp.place_order(decision, restaurant, item, provider=prov, **kw).as_dict()
        second = swiggy_mcp.place_order(decision, restaurant, item, provider=prov, **kw).as_dict()
        return jsonify({"first": first, "second": second,
                        "same_order_id": first["provider_order_id"] == second["provider_order_id"],
                        "second_was_deduped": second["deduped"]})

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "app": "smartplate", "version": "1.1.0"})

    return app


_FEATURE_MAP = {
    "5.1": [
        {"id": 1, "name": "Allergy & medical hard-exclusions", "where": "domain/allergens.py"},
        {"id": 2, "name": "Calendar integration", "where": "integrations/calendar_sync.py"},
        {"id": 3, "name": "Idempotency keys on order placement", "where": "integrations/swiggy_mcp.py"},
        {"id": 4, "name": "Explainability log per decision", "where": "kernel/explainability.py"},
        {"id": 5, "name": "Pause / snooze / cooked", "where": "kernel/scheduler.py"},
    ],
    "5.2": [
        {"id": 6, "name": "Nutrition layer", "where": "domain/nutrition.py"},
        {"id": 7, "name": "Household / group mode", "where": "domain/household.py"},
        {"id": 8, "name": "Leftover & home-cooking awareness", "where": "domain/leftovers.py"},
        {"id": 9, "name": "Festival / cultural calendar", "where": "domain/festivals.py"},
        {"id": 10, "name": "Weather adaptation", "where": "domain/weather.py"},
        {"id": 11, "name": "Surge prediction", "where": "domain/surge.py"},
    ],
    "5.3": [
        {"id": 12, "name": "Reverse mode (Instamart cook days)", "where": "domain/reverse_mode.py"},
        {"id": 13, "name": "Community plan templates", "where": "domain/community.py"},
        {"id": 14, "name": "Receipt / expense surface", "where": "domain/receipts.py"},
        {"id": 15, "name": "Health / longevity integration", "where": "domain/health.py"},
        {"id": 16, "name": "Carbon / sustainability scoring", "where": "domain/carbon.py"},
        {"id": 17, "name": "Cooking-mode coach", "where": "domain/cooking_coach.py"},
    ],
}
