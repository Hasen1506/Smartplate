"""Flask JSON API and vanilla JavaScript UI; no frontend build step."""
import os

from urllib.parse import quote

from flask import Flask, Response, g, jsonify, redirect, request, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from . import access, accounts, config, everyday, push, ratelimit, service
from .domain import models, sentiment
from .domain.checkout import CheckoutConflict
from .kernel import agent_brain
from .integrations import calendar_sync, swiggy_connect, swiggy_live, swiggy_mcp
from .runtime import initialize, state_lock

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def create_app() -> Flask:
    initialize()
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
    app.config['MAX_CONTENT_LENGTH'] = 256 * 1024
    if config.BEHIND_PROXY:                  # one trusted hop sets X-Forwarded-For/Proto/Host
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

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
        args = request.view_args or {}
        for key, table in (('plan_id', 'plans'), ('user_id', 'users'), ('session_id', 'sessions')):
            if key in args:
                from . import db
                with db.cursor() as cur:
                    found = cur.execute(f'SELECT id FROM {table} WHERE id=?', (args[key],)).fetchone()
                if not found:
                    return jsonify(error='not found'), 404
        if request.path.startswith('/api/'):
            presented = request.headers.get(access.HEADER) or (
                request.args.get('key') if request.method == 'GET' else None)
            body = request.get_json(silent=True) if request.method == 'POST' else None
            owners = access.owners_of(args, body if isinstance(body, dict) else None,
                                      request.args.get('user_id', type=int))
            if not all(access.allowed(uid, presented) for uid in owners):
                return jsonify(error='This profile is private. Open it on the device that created it, '
                                     'or add it with its recovery code.'), 401

    @app.teardown_request
    def release_state_lock(error):
        if g.pop('state_locked', False):
            state_lock.release()

    @app.errorhandler(ValueError)
    @app.errorhandler(KeyError)
    @app.errorhandler(TypeError)
    def invalid_input(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(ratelimit.TooMany)
    def too_many(error):
        response = jsonify(error=str(error))
        response.headers['Retry-After'] = str(error.wait_s)
        return response, 429

    @app.errorhandler(swiggy_connect.SwiggyError)
    def swiggy_error(error):
        return jsonify(error=str(error)), 502

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

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

    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True)

    # Installable app: the manifest and the service worker are served from the root
    # so the worker's scope covers the whole app.
    @app.get("/manifest.webmanifest")
    def manifest():
        return send_from_directory(STATIC_DIR, "manifest.webmanifest", mimetype="application/manifest+json")

    @app.get("/sw.js")
    def service_worker():
        response = send_from_directory(STATIC_DIR, "sw.js", mimetype="text/javascript")
        response.headers["Cache-Control"] = "no-cache"
        return response

    # ---- meta / cost transparency ---- #
    @app.get("/api/meta")
    def meta():
        brain = agent_brain.get_brain()
        return jsonify({
            "version": "1.1.0",
            "brain": brain.name,
            "brain_cost_per_decision": brain.cost_per_decision,
            "swiggy_provider": 'simulated' if config.SWIGGY_PROVIDER == 'simulated' else 'unavailable',
            "order_edit_window_min": config.ORDER_EDIT_WINDOW_MIN,
            "modes": config.MODE_LABELS,
            "mode_outcomes": {k: v["outcome"] for k, v in config.MODE_META.items()},
            "note": "v1.1 plans with a MILP solver — no per-decision LLM cost (see FEASIBILITY.md).",
            "weather_provider": config.WEATHER_PROVIDER,
            "catalog_city": everyday.CITY,
            "features": _FEATURE_MAP,
        })

    # ---- users / plans ---- #
    @app.get("/api/users")
    def users():
        return jsonify(models.list_users())

    @app.get('/api/user/<int:user_id>/plan')
    def current_plan(user_id):
        return jsonify(service.current_plan(user_id))

    @app.patch('/api/user/<int:user_id>')
    def update_preferences(user_id):
        return jsonify(service.update_preferences(user_id, request.get_json()))

    @app.get('/api/plan/<int:plan_id>/orders')
    def order_history(plan_id):
        return jsonify(service.order_history(plan_id))

    @app.post("/api/plan")
    def create_plan():
        body = request.get_json(force=True, silent=True) or {}
        pid = service.create_plan(int(body["user_id"]), body.get("mode"))
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

    # ---- execution: orders, substitution, idempotency ---- #
    @app.get("/api/plan/<int:plan_id>/execute/preview")
    def execute_preview(plan_id):
        preview = service.execution_preview(plan_id)
        return (jsonify(preview), 200) if preview else (jsonify({"error": "not found"}), 404)

    @app.post("/api/plan/<int:plan_id>/execute")
    def execute(plan_id):
        body = request.get_json(silent=True) or {}
        if config.SWIGGY_PROVIDER != 'simulated':
            return jsonify(error='Live Swiggy checkout is not connected. The demo catalog cannot be ordered on Swiggy.'), 503
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
        return jsonify(service.list_community(request.args.get("city")))

    @app.post("/api/community/<int:template_id>/adopt")
    def community_adopt(template_id):
        return jsonify(service.adopt_template(template_id) or {"error": "not found"})

    @app.post("/api/plan/<int:plan_id>/save-template")
    def save_template(plan_id):
        body = request.get_json(force=True, silent=True) or {}
        tid = service.save_template(plan_id, body.get("title", "My plan"))
        return jsonify({"template_id": tid})

    # ---- receipts ---- #
    @app.post("/api/plan/<int:plan_id>/receipts")
    def gen_receipts(plan_id):
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

    # ---- everyday flows: set up, shortlist, pick, move, confirm, rate ---- #
    @app.get("/api/restaurants")
    def restaurants():
        split = lambda k: [v for v in request.args.get(k, "").split(",") if v]   # noqa: E731
        uid = request.args.get("user_id", type=int)
        return jsonify(everyday.list_restaurants(uid, diet=request.args.get("diet"),
                                                 allergens_=split("allergens"), medical=split("medical")))

    @app.post("/api/suggest-budget")
    def suggest_budget():
        return jsonify(everyday.suggest_budget(request.get_json()))

    @app.post("/api/profiles")
    def create_profile():
        ratelimit.check(f"profiles:{request.remote_addr}", 20, 3600)
        return jsonify(everyday.create_profile(request.get_json())), 201

    # ---- sign-in (accounts.py): a name + password that opens a private profile anywhere ---- #
    def _presented():
        return request.headers.get(access.HEADER)

    @app.post("/api/signin")
    def sign_in():
        return jsonify(accounts.sign_in(request.get_json(), request.remote_addr or ""))

    @app.get("/api/user/<int:user_id>/account")
    def account(user_id):
        return jsonify(accounts.summary(user_id, _presented()))

    @app.post("/api/user/<int:user_id>/account")
    def set_account(user_id):
        return jsonify(accounts.set_login(user_id, request.get_json(), _presented()))

    @app.post("/api/user/<int:user_id>/devices/<int:device_id>/remove")
    def remove_device(user_id, device_id):
        return jsonify(accounts.remove_device(user_id, device_id))

    @app.post("/api/user/<int:user_id>/signout")
    def sign_out(user_id):
        return jsonify(accounts.sign_out(user_id, _presented()))

    @app.patch("/api/user/<int:user_id>/setup")
    def update_setup(user_id):
        return jsonify(everyday.update_setup(user_id, request.get_json()))

    @app.post("/api/user/<int:user_id>/favourites/<int:restaurant_id>")
    def toggle_favourite(user_id, restaurant_id):
        return jsonify(everyday.toggle_favourite(user_id, restaurant_id))

    @app.get("/api/user/<int:user_id>/reminders")
    def reminders_list(user_id):
        from .domain import reminders
        return jsonify(reminders.upcoming(service.current_plan(user_id)))

    @app.get("/api/user/<int:user_id>/reminders.ics")
    def reminders_ics(user_id):
        from .domain import reminders
        view = service.current_plan(user_id)
        body = reminders.to_ics(reminders.upcoming(view), plan_id=view["plan"]["id"], name=view["user"]["name"])
        return Response(body, mimetype="text/calendar", headers={
            "Content-Disposition": "attachment; filename=smartplate-reminders.ics"})

    # ---- push reminders at the order-by time (push.py) ---- #
    @app.get("/api/user/<int:user_id>/push")
    def push_status(user_id):
        return jsonify(push.status(user_id, request.args.get("endpoint")))

    @app.post("/api/user/<int:user_id>/push/subscribe")
    def push_subscribe(user_id):
        return jsonify(push.subscribe(user_id, request.get_json()))

    @app.post("/api/user/<int:user_id>/push/unsubscribe")
    def push_unsubscribe(user_id):
        return jsonify(push.unsubscribe(user_id, request.get_json()))

    @app.post("/api/user/<int:user_id>/push/test")
    def push_test(user_id):
        ratelimit.check(f"push-test:{user_id}", 5, 600)
        return jsonify(push.test_message(user_id))

    # ---- Swiggy sign-in + read-only discovery (no ordering) ---- #
    def _public_base():
        if config.PUBLIC_URL:
            return config.PUBLIC_URL
        proto = request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip()
        host = request.headers.get("X-Forwarded-Host", request.host).split(",")[0].strip()
        return f"{proto}://{host}"

    @app.get("/api/user/<int:user_id>/swiggy")
    def swiggy_status(user_id):
        return jsonify(swiggy_connect.status(user_id))

    @app.post("/api/user/<int:user_id>/swiggy/connect")
    def swiggy_start(user_id):
        return jsonify(authorize_url=swiggy_connect.start(user_id, f"{_public_base()}/swiggy/callback"))

    @app.post("/api/user/<int:user_id>/swiggy/discover")
    def swiggy_discover(user_id):
        return jsonify(swiggy_connect.discover(user_id))

    # gates 2–3 (swiggy_live.py): addresses, live menus, fill the cart. Never order or pay.
    @app.get("/api/user/<int:user_id>/swiggy/addresses")
    def swiggy_addresses(user_id):
        return jsonify(swiggy_live.addresses(user_id))

    @app.post("/api/user/<int:user_id>/swiggy/address")
    def swiggy_choose_address(user_id):
        return jsonify(swiggy_live.choose_address(user_id, request.get_json().get("address_id")))

    @app.get("/api/user/<int:user_id>/swiggy/menu")
    def swiggy_menu(user_id):
        name = request.args.get("restaurant", "")
        if not name:
            raise ValueError("Say which restaurant")
        return jsonify(swiggy_live.menu_for(user_id, name, fresh=request.args.get("fresh") == "1"))

    @app.post("/api/session/<int:session_id>/swiggy-cart")
    def swiggy_fill_cart(session_id):
        return jsonify(swiggy_live.fill_cart(session_id))

    @app.post("/api/user/<int:user_id>/swiggy/disconnect")
    def swiggy_disconnect(user_id):
        return jsonify(swiggy_connect.disconnect(user_id))

    @app.get("/swiggy/callback")
    def swiggy_callback():
        # Swiggy redirects the browser here; the single-use `state` ties it to the
        # profile that started sign-in, so no profile key is needed on this hop.
        if request.args.get("error"):
            return redirect("/?tab=more&swiggy_error=" + quote(request.args.get("error_description")
                                                               or request.args["error"])[:300])
        try:
            with state_lock:
                swiggy_connect.finish(request.args.get("state", ""), request.args.get("code", ""))
        except swiggy_connect.SwiggyError as exc:
            return redirect("/?tab=more&swiggy_error=" + quote(str(exc))[:300])
        return redirect("/?tab=more&swiggy=connected")

    @app.get("/api/user/<int:user_id>/calendar")
    def upcoming_calendar(user_id):
        return jsonify(everyday.upcoming_calendar(user_id, request.args.get("days", 60, type=int)))

    @app.get("/api/session/<int:session_id>/options")
    def session_options(session_id):
        return jsonify(everyday.options(session_id))

    @app.post("/api/session/<int:session_id>/choose")
    def session_choose(session_id):
        return jsonify(everyday.choose(session_id, request.get_json()))

    @app.post("/api/session/<int:session_id>/confirm")
    def session_confirm(session_id):
        return jsonify(everyday.confirm(session_id))

    @app.post("/api/session/<int:session_id>/rate")
    def session_rate(session_id):
        return jsonify(everyday.rate(session_id, request.get_json().get("score")))

    @app.post("/api/plan/<int:plan_id>/swap")
    def plan_swap(plan_id):
        body = request.get_json()
        a, b = body.get("a"), body.get("b")
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (a, b)):
            raise ValueError("Send the two meal ids to swap as a and b")
        return jsonify(everyday.swap(plan_id, a, b))

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
