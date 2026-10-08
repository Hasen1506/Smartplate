"""Flask JSON API and vanilla JavaScript UI; no frontend build step."""
import os
import secrets

from flask import Flask, Response, g, jsonify, redirect, request, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from . import access, accounts, config, everyday, profile_data, push, ratelimit, service
from .domain import epicure, flavour, learning, live_catalog, models, sentiment, week_orders
from .domain.checkout import CheckoutConflict
from .integrations import calendar_sync, swiggy_connect, swiggy_live, swiggy_mcp
from .kernel import agent_brain
from .runtime import initialize, user_lock

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
SWIGGY_COOKIE = "sp_swiggy_oauth"


MISSING = {   # (code, what is gone) for an id in the URL that this server no longer has
    "user_id": ("profile_missing", "This profile"),
    "plan_id": ("plan_missing", "This plan"),
    "session_id": ("session_missing", "This meal"),
}


def _missing(key: str, value) -> tuple:
    """A 404 that says what is gone and why, never a bare "not found".

    On a server whose storage is erased by restarts (Render's free temporary disk) the
    likely cause is a restart or redeploy, and the person's next step is to create the
    profile again or add it with its recovery code; a Retry can never bring it back."""
    code, what = MISSING[key]
    reason = ("The server's temporary storage was erased by a restart or redeploy."
              if config.storage_status().get("persistent") is False else
              "It may have been deleted.")
    step = ("Create your profile again, or add it with its recovery code."
            if key == "user_id" else "Reload SmartPlate to open your current plan.")
    return jsonify(error=f"{what} is no longer on this server. {reason} {step}", code=code,
                   missing=key, id=value), 404


def create_app() -> Flask:
    if config.LIVE_ORDERS and os.environ.get("RENDER"):
        durable = bool(config.DATABASE_URL) or config.DB_PATH.startswith("/var/data/")
        if (config.SWIGGY_PROVIDER != "live" or not durable
                or not config.SECRET or not config.PUBLIC_URL.startswith("https://")):
            raise RuntimeError("Live orders on Render require the live provider, a durable database "
                               "(DATABASE_URL or /var/data), "
                               "stable SMARTPLATE_SECRET and HTTPS SMARTPLATE_PUBLIC_URL")
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
        args = request.view_args or {}
        for key, table in (('plan_id', 'plans'), ('user_id', 'users'), ('session_id', 'sessions')):
            if key in args:
                from . import db
                with db.cursor() as cur:
                    found = cur.execute(f'SELECT id FROM {table} WHERE id=?', (args[key],)).fetchone()
                if not found:
                    return _missing(key, args[key])
        if request.path.startswith('/api/'):
            presented = request.headers.get(access.HEADER)
            body = request.get_json(silent=True) if request.method == 'POST' else None
            owners = access.owners_of(args, body if isinstance(body, dict) else None,
                                      request.args.get('user_id', type=int))
            if not all(access.allowed(uid, presented) for uid in owners):
                return jsonify(error='This profile is private. Open it on the device that created it, '
                                     'or add it with its recovery code.'), 401
            # Serialize per profile, not per process (M-05): plan edits and checkout for
            # one person stay ordered while other people's requests run in parallel.
            if '/swiggy' in request.path and request.path.startswith(('/api/user/', '/api/session/')):
                # App-side quota on Swiggy routes so a script can't exhaust the shared MCP
                # limit for everyone (M-06); placement has its own tighter limit.
                for uid in owners:
                    ratelimit.check(f"swiggy:{uid}", 120, 600)
                    if request.path.endswith('/swiggy/checkout') and request.method == 'POST':
                        ratelimit.check(f"swiggy-place:{uid}", 5, 3600)
            locks = [user_lock(uid) for uid in sorted(o for o in owners if o is not None)]
            for lock in locks:
                lock.acquire()
            g.user_locks = locks

    @app.teardown_request
    def release_user_locks(error):
        for lock in reversed(g.pop('user_locks', [])):
            lock.release()

    @app.errorhandler(ValueError)
    def invalid_input(error):
        # ValueError is the app's "your input can't be used" signal; its text is written for people.
        return jsonify(error=str(error)), 400

    @app.errorhandler(KeyError)
    def missing_field(error):
        # Usually a missing request field; never echo internal key names or data (L-07).
        app.logger.info("KeyError on %s: %r", request.path, error)
        return jsonify(error="A required field is missing or unknown."), 400

    @app.errorhandler(TypeError)
    def server_bug(error):
        # A TypeError is a server bug, not a user mistake: log it and answer 500 (L-07).
        app.logger.exception("Unhandled TypeError on %s", request.path)
        return jsonify(error="Something went wrong on our side. Try again."), 500

    @app.errorhandler(ratelimit.TooMany)
    def too_many(error):
        response = jsonify(error=str(error))
        response.headers['Retry-After'] = str(error.wait_s)
        return response, 429

    @app.errorhandler(swiggy_connect.SwiggyError)
    def swiggy_error(error):
        if error.code in swiggy_connect.NOT_CONNECTED_CODES:
            # The user's own sign-in is missing or no longer accepted: a state they fix
            # by connecting, not an upstream failure (502 pages ops and misleads monitoring).
            uid = (request.view_args or {}).get("user_id")
            return jsonify(error=error.code, code=error.code, message=str(error),
                           action={"label": "Connect Swiggy", "act": "swiggy-connect"},
                           connect_url=f"/api/user/{uid}/swiggy/connect" if uid else None), 409
        response = jsonify(error=str(error), code=error.code, retry_after=error.retry_after)
        if error.retry_after is not None:
            response.headers['Retry-After'] = str(error.retry_after)
        if error.code == 'swiggy_cart_other_address':
            return response, 409        # the user's cart is for another of their addresses: their choice, not an outage
        return response, 429 if error.code == 'swiggy_rate_limited' else 502

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.after_request
    def response_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'; form-action 'self'; connect-src 'self'; worker-src 'self'; "
            "img-src 'self' data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com")
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        if request.is_secure or (config.PUBLIC_URL or '').startswith('https://'):
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'   # L-11
        return response

    # ---- UI ---- #
    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/healthz")
    def healthz():
        # Liveness stays 200; it also says honestly whether user data survives a restart.
        return jsonify(ok=True, database=config.storage_status())

    @app.get("/readyz")
    def readyz():
        # Render should send traffic only while the configured data store is usable.
        from . import db
        try:
            with db.cursor() as cur:
                cur.execute("SELECT 1 FROM users LIMIT 1").fetchone()
            if not config.DATABASE_URL and (
                    not os.access(os.path.dirname(os.path.abspath(config.DB_PATH)), os.W_OK)
                    or not os.access(config.DB_PATH, os.W_OK)):
                raise OSError("database storage is read-only")
        except Exception:
            return jsonify(ok=False, database=config.storage_status()), 503
        return jsonify(ok=True, database=config.storage_status())

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
            "swiggy_provider": config.SWIGGY_PROVIDER,
            "swiggy_redirect_approved": config.SWIGGY_REDIRECT_APPROVED,
            "storage": config.storage_status(),
            "order_edit_window_min": config.ORDER_EDIT_WINDOW_MIN,
            "modes": config.MODE_LABELS,
            "mode_outcomes": {k: v["outcome"] for k, v in config.MODE_META.items()},
            "note": "v1.1 plans with a MILP solver — no per-decision LLM cost (see FEASIBILITY.md).",
            "weather_provider": config.WEATHER_PROVIDER,
            "catalog_city": everyday.CITY,
            "features": _FEATURE_MAP,
            # Epicure ingredient embeddings: swaps, "more like this", cuisine tilt
            "epicure": {"available": epicure.get() is not None, "cuisines": flavour.CUISINES},
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
        return (jsonify(view), 200) if view else _missing("plan_id", plan_id)

    @app.post("/api/plan/<int:plan_id>/optimize")
    def optimize(plan_id):
        body = request.get_json(force=True, silent=True) or {}
        service.reoptimize(plan_id, body.get("mode"))
        return jsonify(service.plan_view(plan_id))

    # ---- order any meal / the whole week (domain/week_orders) ---- #
    @app.get("/api/plan/<int:plan_id>/order-queue")
    def order_queue(plan_id):
        return jsonify(week_orders.queue_view(plan_id))

    @app.post("/api/plan/<int:plan_id>/order-queue")
    def set_order_queue(plan_id):
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(week_orders.set_queue(plan_id, body.get("meals"), body.get("days")))

    @app.post("/api/session/<int:session_id>/order/cart")
    def order_check_cart(session_id):
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(week_orders.check_cart(session_id, body.get("expected_fingerprint")))
        except swiggy_live.CartChanged as exc:
            return jsonify(error="cart_changed", message=str(exc)), 409

    @app.post("/api/session/<int:session_id>/order/place")
    def order_place(session_id):
        body = request.get_json(force=True, silent=True) or {}
        session = models.get_session(session_id)
        if not session:
            raise ValueError("Meal not found")
        user_id = models.get_plan(session["plan_id"])["user_id"]
        try:
            return jsonify(week_orders.place(session_id, user_id, body.get("expected_fingerprint"),
                                             over_budget_ok=body.get("over_budget_ok") is True))
        except swiggy_live.CartChanged as exc:
            return jsonify(error="cart_changed", message=str(exc)), 409
        except week_orders.OverBudget as exc:
            return jsonify(error="over_budget", code="over_budget", message=str(exc), budget=exc.budget), 409

    @app.post("/api/plan/<int:plan_id>/replan-remaining")
    def replan_remaining(plan_id):
        """Re-plan the open meals around the checked carts' real totals (budget guard)."""
        if not models.get_plan(plan_id):
            raise ValueError("Plan not found")
        body = request.get_json(force=True, silent=True) or {}
        sid = body.get("session_id")
        return jsonify(week_orders.replan_remaining(plan_id, sid if isinstance(sid, int) else None))

    @app.post("/api/plan/<int:plan_id>/live-menus")
    def plan_from_live_menus(plan_id):
        """Read the user's live Swiggy menus and re-plan from them (409 when not connected)."""
        plan = models.get_plan(plan_id)
        if not plan:
            raise ValueError("Plan not found")
        ratelimit.check(f"live-menus:{plan['user_id']}", 10, 3600)
        result = live_catalog.refresh(plan["user_id"])
        service.reoptimize(plan_id)
        return jsonify({**service.plan_view(plan_id), "refreshed": result})

    @app.post("/api/plan/<int:plan_id>/sample-menus")
    def plan_from_sample_menus(plan_id):
        """Go back to the sample catalogue (clears the user's live catalogue)."""
        plan = models.get_plan(plan_id)
        if not plan:
            raise ValueError("Plan not found")
        live_catalog.clear(plan["user_id"])
        service.reoptimize(plan_id)
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
        return (jsonify(preview), 200) if preview else _missing("plan_id", plan_id)

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
        return (jsonify(result), 200) if result else _missing("plan_id", plan_id)

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
        # One counted adopt per address per template per day; repeats still answer (L-14).
        try:
            ratelimit.check(f"adopt:{request.remote_addr}:{template_id}", 1, 86400)
            count = True
        except ratelimit.TooMany:
            count = False
        found = service.adopt_template(template_id, count=count)
        return (jsonify(found), 200) if found else (jsonify(error="That shared plan is no longer available.", code="template_missing"), 404)

    @app.post("/api/plan/<int:plan_id>/save-template")
    def save_template(plan_id):
        body = request.get_json(force=True, silent=True) or {}
        tid = service.save_template(plan_id, body.get("title", "My plan"), show_name=body.get("show_name") is True)
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
        plan_id = body.get("plan_id")
        if isinstance(plan_id, bool) or not isinstance(plan_id, int) or plan_id <= 0:
            raise ValueError("Choose a valid plan for this profile")
        plan = models.get_plan(plan_id)
        if not plan or plan["user_id"] != user_id:
            raise ValueError("Choose a plan belonging to this profile")
        ics = body.get("ics")
        if not isinstance(ics, str) or not ics.strip():
            raise ValueError("Provide a calendar export")
        try:
            n = calendar_sync.ingest_ics(user_id, ics, plan["week_start"])
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Invalid calendar export") from error
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

    @app.post("/api/user/<int:user_id>/account/rotate-key")
    def rotate_profile_key(user_id):
        return jsonify(accounts.rotate_key(user_id, request.get_json().get("confirmation")))

    @app.post("/api/user/<int:user_id>/devices/<int:device_id>/remove")
    def remove_device(user_id, device_id):
        return jsonify(accounts.remove_device(user_id, device_id))

    @app.post("/api/user/<int:user_id>/signout")
    def sign_out(user_id):
        return jsonify(accounts.sign_out(user_id, _presented()))

    @app.get("/api/user/<int:user_id>/data.json")
    def export_profile(user_id):
        response = jsonify(profile_data.export(user_id))
        response.headers['Content-Disposition'] = f'attachment; filename=smartplate-profile-{user_id}.json'
        return response

    @app.delete("/api/user/<int:user_id>")
    def delete_profile(user_id):
        return jsonify(profile_data.delete(user_id, request.get_json().get("confirmation")))

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

    # ---- Swiggy sign-in, live browsing, cart and gated COD ordering ---- #
    def _public_base():
        # X-Forwarded-* are honoured only through ProxyFix when BEHIND_PROXY is on; the
        # host is then still checked against PUBLIC_URL / ALLOWED_HOSTS before use (M-04).
        if config.PUBLIC_URL:
            return config.PUBLIC_URL
        return request.host_url.rstrip("/")

    def _swiggy_callback_url():
        return f"{_public_base()}{config.SWIGGY_CALLBACK_PATH}"

    @app.get("/api/user/<int:user_id>/swiggy")
    def swiggy_status(user_id):
        return jsonify({**swiggy_connect.status(user_id),
                        "callback_url": _swiggy_callback_url(),
                        "order_enabled": config.LIVE_ORDERS})

    @app.post("/api/user/<int:user_id>/swiggy/connect")
    def swiggy_start(user_id):
        ratelimit.check(f"swiggy-connect:{user_id}", 10, 3600)
        nonce = secrets.token_urlsafe(32)
        response = jsonify(authorize_url=swiggy_connect.start(user_id, _swiggy_callback_url(), nonce))
        # Ties Swiggy's redirect back to THIS browser (H-01). Lax is sent on the top-level
        # GET redirect from Swiggy; the value never leaves the server except as this cookie.
        response.set_cookie(SWIGGY_COOKIE, nonce, max_age=int(swiggy_connect.PENDING_TTL.total_seconds()),
                            httponly=True, secure=request.is_secure or _swiggy_callback_url().startswith("https://"),
                            samesite="Lax", path="/")
        return response

    @app.post("/api/user/<int:user_id>/swiggy/discover")
    def swiggy_discover(user_id):
        return jsonify({**swiggy_connect.discover(user_id), "order_enabled": config.LIVE_ORDERS})

    # swiggy_live.py: addresses, live menus, cart and explicitly approved COD.
    @app.get("/api/user/<int:user_id>/swiggy/addresses")
    def swiggy_addresses(user_id):
        return jsonify(swiggy_live.addresses(user_id))

    @app.post("/api/user/<int:user_id>/swiggy/addresses/refresh")
    def swiggy_refresh_addresses(user_id):
        # After the user adds an address in Swiggy: a fresh list, and no stale default.
        return jsonify(swiggy_live.refresh_addresses(user_id))

    @app.post("/api/user/<int:user_id>/swiggy/address")
    def swiggy_choose_address(user_id):
        return jsonify({**swiggy_live.choose_address(user_id, request.get_json().get("address_id")),
                        "order_enabled": config.LIVE_ORDERS})

    @app.get("/api/user/<int:user_id>/swiggy/menu")
    def swiggy_menu(user_id):
        name = request.args.get("restaurant", "")
        if not name:
            raise ValueError("Say which restaurant")
        return jsonify(swiggy_live.menu_for(user_id, name, fresh=request.args.get("fresh") == "1"))

    @app.get("/api/user/<int:user_id>/swiggy/restaurants")
    def swiggy_live_restaurants(user_id):
        return jsonify(swiggy_live.search_live_restaurants(user_id, request.args.get("query", "")))

    @app.get("/api/user/<int:user_id>/swiggy/favourites")
    def swiggy_live_favourites(user_id):
        return jsonify(swiggy_live.live_favourites(user_id))

    @app.post("/api/user/<int:user_id>/swiggy/favourites")
    def swiggy_toggle_live_favourite(user_id):
        body = request.get_json()
        return jsonify(swiggy_live.toggle_live_favourite(user_id, str(body.get("restaurant_id") or ""),
                                                        str(body.get("restaurant_name") or "")))

    @app.get("/api/user/<int:user_id>/swiggy/live-menu")
    def swiggy_live_menu(user_id):
        return jsonify(swiggy_live.live_menu(user_id, request.args.get("restaurant_id", ""),
                                            request.args.get("restaurant_name", "")))

    @app.get("/api/user/<int:user_id>/swiggy/dishes")
    def swiggy_dishes(user_id):
        offset = request.args.get("offset", "0")
        if not offset.isdigit():
            raise ValueError("Invalid menu page")
        return jsonify(swiggy_live.search_live_dishes(user_id, request.args.get("restaurant_id", ""),
            request.args.get("restaurant_name", ""), request.args.get("query", ""), int(offset)))

    @app.post("/api/user/<int:user_id>/swiggy/live-cart/preview")
    def swiggy_live_cart_preview(user_id):
        body = request.get_json()
        return jsonify(swiggy_live.live_cart_preview(user_id, str(body.get("restaurant_id") or ""),
                          str(body.get("restaurant_name") or ""), str(body.get("item_id") or ""),
                          str(body.get("item_name") or "")))

    @app.post("/api/user/<int:user_id>/swiggy/live-cart")
    def swiggy_live_fill_cart(user_id):
        body = request.get_json()
        try:
            return jsonify(swiggy_live.fill_live_cart(user_id, str(body.get("restaurant_id") or ""),
                          str(body.get("restaurant_name") or ""), str(body.get("item_id") or ""),
                          str(body.get("item_name") or ""), body.get("expected_fingerprint")))
        except swiggy_live.CartChanged as exc:
            return jsonify(error="cart_changed", message=str(exc)), 409

    @app.get("/api/user/<int:user_id>/swiggy/live-cart")
    def swiggy_current_cart(user_id):
        return jsonify(swiggy_live.current_live_cart(user_id))

    @app.get("/api/user/<int:user_id>/swiggy/checkout/preview")
    def swiggy_checkout_preview(user_id):
        return jsonify(swiggy_live.live_checkout_preview(user_id))

    @app.post("/api/user/<int:user_id>/swiggy/checkout")
    def swiggy_checkout(user_id):
        try:
            return jsonify(swiggy_live.place_live_order(user_id, request.get_json().get("expected_fingerprint")))
        except swiggy_live.CartChanged as exc:
            return jsonify(error="cart_changed", message=str(exc)), 409

    @app.get("/api/user/<int:user_id>/swiggy/orders/<order_id>")
    def swiggy_order_status(user_id, order_id):
        return jsonify(swiggy_live.live_order_status(user_id, order_id))

    @app.post("/api/user/<int:user_id>/swiggy/attempts/resolve")
    def swiggy_resolve_attempt(user_id):
        return jsonify(swiggy_live.resolve_attempt(user_id, request.get_json().get("confirmation")))

    @app.get("/api/user/<int:user_id>/swiggy/order-history")
    def swiggy_order_history(user_id):
        return jsonify(swiggy_live.live_order_history(user_id))

    @app.get("/api/session/<int:session_id>/swiggy-cart/preview")
    def swiggy_cart_preview(session_id):
        return jsonify(swiggy_live.cart_preview(session_id))

    @app.post("/api/session/<int:session_id>/swiggy-cart")
    def swiggy_fill_cart(session_id):
        try:
            return jsonify(week_orders.check_single_cart(session_id, request.get_json().get("expected_fingerprint")))
        except swiggy_live.CartChanged as exc:
            return jsonify(error="cart_changed", message=str(exc)), 409

    @app.post("/api/user/<int:user_id>/swiggy/disconnect")
    def swiggy_disconnect(user_id):
        live_catalog.clear(user_id)          # no live menus without a live connection
        return jsonify(swiggy_connect.disconnect(user_id))

    @app.get("/swiggy/callback")
    @app.get("/auth/swiggy/callback")
    def swiggy_callback():
        # Swiggy redirects the browser here; the single-use `state` ties it to the
        # profile that started sign-in, so no profile key is needed on this hop.
        # Only fixed error codes go into the URL; the page maps them to its own text, so a
        # crafted link can't show arbitrary warnings (L-13).
        def done(location):
            response = redirect(location)
            response.delete_cookie(SWIGGY_COOKIE, path="/")
            return response
        if request.args.get("error"):
            code = "denied" if request.args["error"] == "access_denied" else "failed"
            return done(f"/?tab=more&swiggy_error={code}")
        try:
            swiggy_connect.finish(request.args.get("state", ""), request.args.get("code", ""),
                                  request.cookies.get(SWIGGY_COOKIE))
        except swiggy_connect.SwiggyError as exc:
            code = {"swiggy_browser_mismatch": "other_browser"}.get(exc.code, "failed")
            if "expired or was already used" in str(exc):
                code = "expired"
            app.logger.info("Swiggy callback refused: %s", exc)
            return done(f"/?tab=more&swiggy_error={code}")
        except Exception:                                # L-08: never a raw JSON page mid-redirect
            app.logger.exception("Swiggy callback failed")
            return done("/?tab=more&swiggy_error=failed")
        return done("/?tab=more&swiggy=connected")

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
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(everyday.rate(session_id, body.get("score"), body.get("reasons")))

    @app.get("/api/user/<int:user_id>/learned")
    def learned(user_id):
        return jsonify({"learned": learning.chips(user_id), "reasons": learning.REASONS})

    @app.post("/api/user/<int:user_id>/learned/undo")
    def learned_undo(user_id):
        learning.undo(user_id, (request.get_json(force=True, silent=True) or {}).get("key", ""))
        view = service.current_plan(user_id)
        service.reoptimize(view["plan"]["id"])
        return jsonify({"learned": learning.chips(user_id), "plan": service.plan_view(view["plan"]["id"])})

    @app.post("/api/plan/<int:plan_id>/swap")
    def plan_swap(plan_id):
        body = request.get_json()
        a, b = body.get("a"), body.get("b")
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (a, b)):
            raise ValueError("Send the two meal ids to swap as a and b")
        return jsonify(everyday.swap(plan_id, a, b))

    # ---- household (domain/household.py) ---- #
    @app.post("/api/user/<int:user_id>/household")
    def household_create(user_id):
        return jsonify(service.household_action(user_id, "create", request.get_json()))

    @app.patch("/api/user/<int:user_id>/household")
    def household_update(user_id):
        return jsonify(service.household_action(user_id, "update", request.get_json()))

    @app.post("/api/user/<int:user_id>/household/leave")
    def household_leave(user_id):
        return jsonify(service.household_action(user_id, "leave"))

    @app.post("/api/user/<int:user_id>/household/members")
    def household_add(user_id):
        return jsonify(service.household_action(user_id, "add", request.get_json()))

    @app.patch("/api/user/<int:user_id>/household/members/<int:member_id>")
    def household_edit(user_id, member_id):
        return jsonify(service.household_action(user_id, "edit", request.get_json(), member_id))

    @app.delete("/api/user/<int:user_id>/household/members/<int:member_id>")
    def household_remove(user_id, member_id):
        return jsonify(service.household_action(user_id, "remove", None, member_id))

    @app.post("/api/session/<int:session_id>/eaters")
    def session_eaters(session_id):
        body = request.get_json()
        if "eaters" not in body:
            raise ValueError("Send eaters: the people eating this meal, or null for the usual people")
        return jsonify(service.set_eaters(session_id, body["eaters"]))

    @app.post("/api/plan/<int:plan_id>/grocery-have")
    def grocery_have(plan_id):
        return jsonify(service.set_grocery_have(plan_id, request.get_json()))

    @app.get("/api/plan/<int:plan_id>/recap")
    def week_recap(plan_id):
        return jsonify(service.week_recap(plan_id))

    @app.get("/api/plan/<int:plan_id>/swaps")
    def swap_options(plan_id):
        return jsonify(service.swap_options(plan_id, request.args.get("token", "")))

    @app.post("/api/plan/<int:plan_id>/grocery-swap")
    def grocery_swap(plan_id):
        return jsonify(service.set_grocery_swap(plan_id, request.get_json()))

    @app.post("/api/session/<int:session_id>/more-like")
    def session_more_like(session_id):
        return jsonify(everyday.more_like(session_id))

    @app.post("/api/user/<int:user_id>/more-like/forget")
    def forget_more_like(user_id):
        name = request.get_json().get("name")
        if not isinstance(name, str):
            raise ValueError("Send the dish name to forget")
        return jsonify(everyday.forget_more_like(user_id, name))

    @app.get("/credits")
    def credits():
        return send_from_directory(STATIC_DIR, "credits.html")

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
