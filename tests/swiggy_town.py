"""A neighbourhood of restaurants for browser walkthroughs and tests: several places near
the delivery address, each with a full menu (categories, veg marks, bestsellers, dishes
with options), answering Swiggy's documented Food tool shapes. It stands in for Swiggy in
tests only; nothing real is touched. Names are invented."""
from test_swiggy_live import FakeLive, ok

# id: (name, area, rating, eta, cuisines, open, [(dish, paise, veg, category, bestseller, options)])
PLACES = {
    "r-101": ("Annapoorna Tiffin Room", "Adyar", 4.4, 25, ["South Indian"], True, [
        ("Ghee Roast Dosa", 14000, True, "Dosa", True, False),
        ("Masala Dosa", 12000, True, "Dosa", False, False),
        ("Rava Onion Dosa", 13500, True, "Dosa", False, False),
        ("Idli (2 pcs)", 6000, True, "Tiffin", False, False),
        ("Medu Vada", 5500, True, "Tiffin", False, False),
        ("Ven Pongal", 9000, True, "Tiffin", True, False),
        ("Mini Tiffin", 16000, True, "Combos", False, False),
        ("South Indian Meals", 19000, True, "Meals", True, False),
        ("Curd Rice", 9500, True, "Rice", False, False),
        ("Lemon Rice", 9000, True, "Rice", False, False),
        ("Filter Coffee", 4000, True, "Beverages", False, False),
        ("Kesari", 6000, True, "Sweets", False, False),
    ]),
    "r-102": ("Biryani Junction", "Kotturpuram", 4.2, 35, ["Biryani", "Mughlai"], True, [
        ("Chicken Dum Biryani", 28000, False, "Biryani", True, False),
        ("Mutton Biryani", 36000, False, "Biryani", False, False),
        ("Veg Biryani", 21000, True, "Biryani", False, False),
        ("Paneer Biryani", 24000, True, "Biryani", False, False),
        ("Chicken 65", 22000, False, "Starters", True, False),
        ("Gobi Manchurian", 17000, True, "Starters", False, False),
        ("Egg Curry", 16000, False, "Curries", False, False),
        ("Raita", 4000, True, "Sides", False, False),
        ("Family Pack Biryani", 89000, False, "Combos", False, True),
    ]),
    "r-103": ("Green Bowl Kitchen", "Besant Nagar", 4.5, 30, ["Healthy Food", "Salads"], True, [
        ("Quinoa Veg Bowl", 26000, True, "Bowls", True, False),
        ("Paneer Tikka Salad", 24000, True, "Salads", False, False),
        ("Grilled Chicken Salad", 29000, False, "Salads", True, False),
        ("Rajma Rice Bowl", 19000, True, "Bowls", False, False),
        ("Dal Khichdi", 17000, True, "Bowls", False, False),
        ("Sprouts Chaat", 12000, True, "Snacks", False, False),
        ("Build Your Own Bowl", 22000, True, "Bowls", False, True),
        ("Cold Pressed Juice", 15000, True, "Beverages", False, False),
    ]),
    "r-104": ("Punjab Da Dhaba", "Thiruvanmiyur", 4.1, 40, ["North Indian", "Punjabi"], True, [
        ("Paneer Butter Masala", 22000, True, "Curries", True, False),
        ("Dal Makhani", 19000, True, "Curries", True, False),
        ("Chole Bhature", 16000, True, "Mains", False, False),
        ("Butter Naan", 5000, True, "Breads", False, False),
        ("Aloo Paratha", 12000, True, "Breads", False, False),
        ("Butter Chicken", 27000, False, "Curries", False, False),
        ("Veg Thali", 23000, True, "Thali", False, False),
        ("Lassi", 8000, True, "Beverages", False, False),
    ]),
    "r-105": ("Wok Street", "Adyar", 3.9, 30, ["Chinese"], True, [
        ("Veg Hakka Noodles", 17000, True, "Noodles", True, False),
        ("Chicken Fried Rice", 21000, False, "Rice", False, False),
        ("Veg Fried Rice", 17000, True, "Rice", False, False),
        ("Chilli Paneer", 20000, True, "Starters", False, False),
    ]),
    "r-106": ("Night Owl Parotta", "Adyar", 4.0, 30, ["Chettinad"], False, [
        ("Kothu Parotta", 15000, True, "Parotta", True, False),
    ]),
}
QUERY_WORDS = {"meals": None}          # "meals" is Swiggy's broad query: every place answers


class TownFake(FakeLive):
    """FakeLive's sign-in and checkout, with many restaurants and full menus."""

    def __init__(self):
        super().__init__()
        self.cart_rid, self.cart_items = None, []          # [(menu_item_id, quantity)]
        self.searches = []

    @staticmethod
    def _items(rid):
        return [(f"{rid}-i{n}",) + row for n, row in enumerate(PLACES[rid][6])]

    def _row(self, rid):
        name, area, rating, eta, cuisines, is_open, _ = PLACES[rid]
        return {"id": rid, "name": name, "cuisines": cuisines, "avgRating": rating, "areaName": area,
                "deliveryTimeMinutes": eta, "availabilityStatus": "OPEN" if is_open else "CLOSED"}

    def _find(self, item_id):
        for rid in PLACES:
            for row in self._items(rid):
                if row[0] == item_id:
                    return rid, row
        return None, None

    def tool(self, name, args):
        if name == "search_restaurants":
            q = args["query"].strip().lower()
            self.searches.append(q)
            hits = [rid for rid, p in PLACES.items()
                    if q in QUERY_WORDS or q in p[0].lower() or any(q in c.lower() for c in p[4])
                    or any(q in d[0].lower() for d in p[6])]
            return ok({"restaurants": [self._row(rid) for rid in hits], "dishes": []})
        if name == "get_restaurant_menu":
            rid = args["restaurantId"]
            if rid not in PLACES:
                return {"structuredContent": {"success": False, "error": {"message": "Restaurant not found"}},
                        "isError": True}
            p = PLACES[rid]
            items = [{"id": iid, "name": d, "price": paise, "inStock": 1, "isVeg": veg, "hasVariants": opts,
                      "hasAddons": False, "categories": [cat], "isBestseller": best}
                     for iid, d, paise, veg, cat, best, opts in self._items(rid)]
            cats = list(dict.fromkeys(i["categories"][0] for i in items))
            return ok({"restaurant": {"id": rid, "name": p[0], "areaName": p[1], "avgRating": p[2], "isOpen": p[5]},
                       "items": items, "categoryLabels": cats, "totalItems": len(items),
                       "totalCategories": len(cats)})
        if name == "search_menu":
            q = args["query"].strip().lower()
            scope = args.get("restaurantIdOfAddedItem")
            veg_only = args.get("vegFilter") == 1
            items = []
            for rid in PLACES:
                if scope and rid != scope:
                    continue
                for iid, d, paise, veg, cat, best, opts in self._items(rid):
                    if q in d.lower() and (veg or not veg_only):
                        items.append({"menu_item_id": iid, "name": d, "price": paise, "restaurant_id": rid,
                                      "restaurant_name": PLACES[rid][0], "inStock": 1, "hasVariants": opts,
                                      "hasAddons": False, "isVeg": veg, "isBestseller": best})
            return ok({"items": items, "query": args["query"], "totalItems": len(items), "hasMore": False})
        if name == "update_food_cart":
            rid = args["restaurantId"]
            assert rid in PLACES, rid
            rows = [(i["menu_item_id"], i["quantity"]) for i in args["cartItems"]]
            assert all(self._find(i)[0] == rid for i, _ in rows), "dish from another restaurant"
            self.cart_rid, self.cart_items = rid, rows
            self.cart = rows[0][0] if rows else None
            return ok({"statusCode": 0, "statusMessage": "Cart updated"})
        if name == "get_food_cart":
            if not self.cart_items:
                return {"structuredContent": {"success": True, "data": {"addressId": args["addressId"],
                                                                        "data": {"items": []}}}}
            out, total = [], 0.0
            for iid, qty in self.cart_items:
                _, (_, d, paise, veg, *_rest) = self._find(iid)
                line = paise / 100 * qty
                total += line
                out.append({"menu_item_id": iid, "name": d, "quantity": qty, "subtotal": line, "total": line,
                            "final_price": line, "is_veg": veg, "in_stock": True})
            return {"structuredContent": {"success": True, "data": {"addressId": args["addressId"], "data": {
                "restaurant": {"id": self.cart_rid, "name": PLACES[self.cart_rid][0]}, "items": out,
                "pricing": {"item_total": total, "delivery_charge": 35, "to_pay": total + 35}}}}}
        if name == "flush_food_cart":
            self.cart_rid, self.cart_items, self.cart = None, [], None
            return ok({"statusCode": 0})
        return super().tool(name, args)
