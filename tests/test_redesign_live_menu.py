"""Dark redesign (8 Oct 2026): the full live menu keeps Swiggy's categories, bestseller marks,
real photos (only from Swiggy's image CDN), and what a dish *name* implies about allergens;
expenses use and label the real Swiggy bill. Fake MCP server only: no real account is touched."""
from test_followups import _connect
from test_swiggy_live import FakeLive, client, swiggy  # noqa: F401  (fixtures)

from smartplate import db
from smartplate.integrations import swiggy_live

PARAMS = "restaurant_id=r-1&restaurant_name=Hotel%20Saravana%20Bhavan%20%28Adyar%29"


def _ready(client, swiggy, uid=3):
    _connect(client, swiggy, uid=uid)
    assert client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"}).status_code == 200


def test_full_menu_keeps_categories_bestsellers_and_name_allergens(client, swiggy, monkeypatch):
    _ready(client, swiggy)
    swiggy.dishes = {"Peanut Chutney Dosa": 9000, "Paneer Butter Masala": 22000, "Filter Coffee": 4000}
    real_tool = swiggy.tool

    def tool(name, args):
        reply = real_tool(name, args)
        if name == "get_restaurant_menu":
            data = reply["structuredContent"]["data"]
            data["items"][0]["categories"] = ["Recommended", "Dosa"]
            data["items"][0]["isBestseller"] = True
            data["items"][2]["categories"] = ["Beverages"]
            data["categoryLabels"] = ["Recommended", "Dosa", "Mains", "Beverages"]
            data["truncated"] = True
            data["totalItems"] = 150
        return reply
    monkeypatch.setattr(swiggy, "tool", tool)
    menu = client.get(f"/api/user/3/swiggy/live-menu?{PARAMS}").get_json()
    assert menu["categories"] == ["Recommended", "Dosa", "Mains", "Beverages"]
    assert menu["truncated"] is True and menu["total_items"] == 150
    dosa, paneer, coffee = menu["items"]
    assert dosa["categories"] == ["Recommended", "Dosa"] and dosa["bestseller"] is True
    assert coffee["categories"] == ["Beverages"] and coffee["bestseller"] is False
    # the name implies peanut / dairy; nothing claims a dish is free of anything
    assert "peanut" in dosa["name_allergens"] and "dairy" in paneer["name_allergens"]
    assert all(i["image"] is None for i in menu["items"])          # the browse view has no images


def test_dish_photos_only_from_swiggys_https_image_cdn():
    ok = "https://media-assets.swiggy.com/swiggy/image/upload/fl_lossy/abc.png"
    assert swiggy_live._image({"imageUrl": ok}) == ok
    for bad in ("http://media-assets.swiggy.com/x.png", "https://evil.example/x.png",
                "https://media-assets.swiggy.com.evil.example/x.png", "javascript:alert(1)",
                "https://user@media-assets.swiggy.com/x.png", None, 42, "https://" + "a" * 700):
        assert swiggy_live._image({"imageUrl": bad}) is None, bad


def test_search_results_and_review_carry_the_real_photo(client, swiggy, monkeypatch):
    _ready(client, swiggy)
    swiggy.dishes = {"Mini Tiffin": 12500}
    photo = "https://media-assets.swiggy.com/swiggy/image/upload/mini-tiffin.jpg"
    real_tool = swiggy.tool

    def tool(name, args):
        reply = real_tool(name, args)
        if name == "search_menu":
            for item in reply["structuredContent"]["data"]["items"]:
                item["imageUrl"] = photo
        return reply
    monkeypatch.setattr(swiggy, "tool", tool)
    found = client.get(f"/api/user/3/swiggy/dishes?{PARAMS}&query=tiffin&offset=0").get_json()
    assert found["items"][0]["image"] == photo
    body = {"restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)",
            "item_id": "m0", "item_name": "Mini Tiffin"}
    preview = client.post("/api/user/3/swiggy/live-cart/preview", json=body).get_json()
    assert preview["image"] == photo and preview["orderable"] is True


def test_csp_allows_only_swiggys_image_host(client):
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "img-src 'self' data: https://media-assets.swiggy.com;" in csp


def test_expenses_use_and_label_the_real_swiggy_bill(client, swiggy):
    """Regression: "Update from this week" recorded the planner's estimate even when the
    meal's real Swiggy cart total was known, and nothing said which amounts were real."""
    _ready(client, swiggy)
    v = client.get("/api/user/3/plan").get_json()
    cell = next(c for d in v["grid"] for c in d["meals"].values()
                if c["kind"] == "delivery" and c["status"] == "active" and not c.get("past"))
    swiggy.dishes = {cell["item"]: 21000}
    from smartplate.domain import week_orders
    week_orders.record_cart(v["plan"]["id"], cell["session_id"],
                            {"to_pay": 245.0, "bill": None, "planned_cost": cell["cost"]})
    with db.cursor() as cur:
        cur.execute("UPDATE sessions SET status='confirmed' WHERE id=?", (cell["session_id"],))
    client.post(f"/api/plan/{v['plan']['id']}/receipts", json={})
    rows = client.get("/api/receipts/3").get_json()["rows"]
    row = next(r for r in rows if r["note"] == cell["item"])
    assert row["amount"] == 245.0 and row["real"] is True
