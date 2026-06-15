"""Flask app — JSON API + serves the single-file React UI.

No build step: the frontend is plain React loaded from a CDN and served from
/static, so `python run.py` is the whole setup.
"""
import os

from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS

from . import config, service
from .db import init_db
from .domain import models, sentiment
from .kernel import agent_brain
from .integrations import calendar_sync, swiggy_mcp

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def create_app() -> Flask:
    init_db()
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
    CORS(app)

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
            "swiggy_provider": config.SWIGGY_PROVIDER,
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
    @app.post("/api/plan/<int:plan_id>/execute")
    def execute(plan_id):
        return jsonify(service.execute(plan_id))

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
        return jsonify({"ok": True, "version": "1.1.0"})

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
