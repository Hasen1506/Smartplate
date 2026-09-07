"""Flask JSON API and vanilla JavaScript UI; no frontend build step."""
import os

from flask import Flask, Response, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from . import config, service
from .domain import models, sentiment
from .domain.checkout import CheckoutConflict
from .kernel import agent_brain
from .integrations import calendar_sync, swiggy_mcp
from .runtime import initialize, state_lock

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def create_app() -> Flask:
    initialize()
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
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
        args = request.view_args or {}
        for key, table in (('plan_id', 'plans'), ('user_id', 'users'), ('session_id', 'sessions')):
            if key in args:
                from . import db
                with db.cursor() as cur:
                    found = cur.execute(f'SELECT id FROM {table} WHERE id=?', (args[key],)).fetchone()
                if not found:
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
