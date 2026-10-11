"""Nothing invented is shown to a real user as fact (each test failed before its fix).

1. Live Swiggy prices were multiplied by a made-up "surge" (seeded multipliers, or a flat
   ×1.15 lunch / ×1.25 dinner fallback plus a rain bump). A real ₹180 thali was budgeted at
   ₹225+, the reasons said "Surge ×1.25 priced in" or "Time-shifted to 12:30 … saved ₹40",
   and the week claimed "Ordering a little earlier saves ₹X". Swiggy publishes no surge
   data, so a live dish now costs exactly Swiggy's price plus the delivery fee, with no
   surge or time-shift claim.
2. The community list was seeded with invented members ("campus_survivor", "veg_athlete")
   and invented adoption counts (142, 88), shown beside real shared weeks. A fresh app now
   lists only weeks real people shared, and an empty list says so plainly.
"""
from test_roadmap_b_live_menus import RECORDED_MENU, _live_user, client, swiggy  # noqa: F401 (fixtures)
from smartplate import service
from smartplate.domain import household, models


def test_live_dishes_cost_swiggys_price_with_no_invented_surge(client, swiggy):
    pid = _live_user(client, swiggy, 3)
    view = client.post(f"/api/plan/{pid}/live-menus", json={}).get_json()
    assert view["source"]["kind"] == "live"
    menu = {it["id"]: it for it in models.menu_for_user(models.get_user(3))}
    rows = [d for d in models.decisions_for_plan(pid) if d["chosen_kind"] == "delivery"]
    assert rows, "a live week plans deliveries"
    user, members = models.get_user(3), models.get_household_members(1)
    for d in rows:
        item = menu[d["item_id"]]
        portions = len(household.eaters(d, members, user))      # Arjun and Meera: a portion each
        assert portions == 2
        assert d["cost"] == round(item["price"] * portions + item["delivery_fee"], 2), (d["item_name"], d["cost"])
        assert d["surge_mult"] == 1.0 and not d.get("time_shift")
        assert not any("Surge" in r or "surge" in r for r in d["reasons"]), d["reasons"]
    view = client.get(f"/api/plan/{pid}").get_json()
    assert view["surge_saved"] == 0
    assert not any("earlier saves" in (h.get("title") or "") for h in view.get("heads_up") or [])


def test_community_lists_no_invented_members_or_counts(client):
    listed = client.get("/api/community").get_json()
    assert listed == []
    assert not {"campus_survivor", "veg_athlete"} & {t["author"] for t in service.list_community()}


def test_budget_recommendation_uses_swiggys_price_for_live_dishes(seeded):
    from smartplate.kernel import recommender
    live = {"price": 180, "delivery_fee": 30, "city": "live:3", "source": "live"}
    assert recommender._expected_cost(live, "dinner") == 210
    sample = {**live, "city": "Chennai", "source": "sample"}
    assert recommender._expected_cost(sample, "dinner") > 210      # the labelled sample keeps its demo surge


def test_invented_members_are_removed_from_an_existing_database_on_start(seeded):
    from smartplate import db, everyday, runtime
    with db.cursor() as cur:                                   # a database seeded by an earlier trial
        cur.executemany("INSERT INTO community_templates(author,title,city,budget,mode,payload,adopts) "
                        "VALUES (?,?,'Chennai',1500,'survival','{}',?)",
                        [("campus_survivor", "₹1500/week student survival", 142),
                         ("veg_athlete", "High-protein veg week", 88)])
    v = everyday.create_profile({"name": "Real", "diet": "veg", "weekly_budget": 2000})
    service.save_template(v["plan"]["id"], "My real week")
    runtime.initialize()
    assert [t["title"] for t in service.list_community()] == ["My real week"]
