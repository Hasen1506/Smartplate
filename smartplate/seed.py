"""Demo seed data — a Chennai catalog + users/contexts that exercise all 17 gaps.

The demo week starts on the upcoming Monday so calendar/weather/festival signals
always line up with the plan grid, whenever you run it.
"""
import datetime as dt

from . import db


def demo_week_start() -> str:
    today = dt.date.today()
    monday = today + dt.timedelta(days=(7 - today.weekday()) % 7 or 7)
    return monday.isoformat()


def _clear():
    tables = ["users", "households", "restaurants", "menu_items", "plans", "sessions",
              "decisions", "orders", "calendar_events", "leftovers", "weather",
              "surge_history", "festivals", "community_templates", "receipts", "grocery_baskets"]
    with db.cursor() as cur:
        for t in tables:
            cur.execute(f"DELETE FROM {t}")


def seed_all(optimize_starter: bool = True) -> dict:
    _clear()
    _households()
    _users()
    rmap = _restaurants()
    _menu(rmap)
    _contexts()
    _community()
    plan_id = _starter_plan()
    if optimize_starter and plan_id:
        from .kernel import optimizer
        optimizer.optimize(plan_id)
    return {"plan_id": plan_id, "week_start": demo_week_start()}


def _households():
    with db.cursor() as cur:
        cur.execute("INSERT INTO households(id, name, split) VALUES (1, 'Flat 3B', 'even')")


def _users():
    rows = [
        # id, name, city, diet, budget, floor, mode, allergens, medical, nutri, health, carbon, household
        (1, "Hasen", "Chennai", "nonveg", 2800, 4.0, "survival",
         '["peanut"]', '["diabetes"]', '{}',
         '{"protein_floor_g":60,"veg_servings":2,"fasting_start_min":1290,"fasting_end_min":480}', 0.3, None),
        (2, "Meera", "Chennai", "vegan", 1500, 4.2, "balanced",
         '["dairy"]', '[]', '{"kcal":1800}', '{"protein_floor_g":55,"veg_servings":3}', 0.6, 1),
        (3, "Arjun", "Chennai", "nonveg", 2500, 3.8, "comfort",
         '[]', '[]', '{}', '{}', 0.0, 1),
    ]
    with db.cursor() as cur:
        cur.executemany(
            "INSERT INTO users(id,name,city,diet,weekly_budget,rating_floor,mode,allergens,"
            "medical,nutrition_targets,health_targets,carbon_pref,household_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def _restaurants() -> dict:
    rows = [
        # name, rating, cuisines, delivery_fee, eta, flaky
        ("Adyar Ananda Bhavan", 4.3, '["south indian","veg"]', 25, 30, 1),  # big chain, hits §1.2
        ("Junior Kuppanna", 4.4, '["chettinad","nonveg"]', 35, 40, 0),
        ("Third Wave Coffee", 4.1, '["cafe","continental"]', 40, 35, 1),   # flaky chain (§1.2)
        ("Starbucks", 4.0, '["cafe"]', 50, 30, 1),                          # flaky chain (§1.2)
        ("FreshMenu", 4.2, '["healthy","salads"]', 30, 35, 0),
        ("Hotel Saravana Bhavan", 4.5, '["south indian","veg"]', 20, 25, 0),
        ("Faasos", 3.9, '["rolls","nonveg"]', 25, 30, 0),
        ("Sangeetha Veg", 4.2, '["south indian","veg"]', 20, 30, 0),
    ]
    rmap = {}
    with db.cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO restaurants(name,rating,city,cuisines,delivery_fee,eta_min,flaky)"
                " VALUES (?,?, 'Chennai', ?,?,?,?)",
                (r[0], r[1], r[2], r[3], r[4], r[5]))
            rmap[r[0]] = cur.lastrowid
    return rmap


def _menu(rmap: dict):
    # (restaurant, name, price, cuisine, kcal, prot, carb, fat, sugar, veg, allergens, tags, carbon, rating, pop, reviews)
    M = [
        ("Adyar Ananda Bhavan", "Mini Tiffin", 90, "thali", 520, 14, 78, 14, 6, 1, [], ["light","veg"], 0.8, 4.4, 0.7,
         ["fresh and hot", "great value", "consistent quality"]),
        ("Adyar Ananda Bhavan", "Ghee Pongal", 110, "thali", 610, 12, 84, 22, 4, 1, ["dairy"], ["comfort","veg"], 0.9, 4.3, 0.6,
         ["comfort food done right", "a bit oily"]),
        ("Adyar Ananda Bhavan", "Peanut Chikki Sweet", 60, "salad", 480, 8, 60, 22, 38, 1, ["peanut"], ["festive"], 0.7, 4.2, 0.5,
         ["tasty", "too sweet for me"]),
        ("Junior Kuppanna", "Chicken Chettinad + Rice", 220, "chicken", 780, 42, 70, 30, 5, 0, [], ["comfort","nonveg"], 1.9, 4.5, 0.8,
         ["amazing flavour", "generous portion", "authentic"]),
        ("Junior Kuppanna", "Mutton Biryani", 320, "mutton", 950, 38, 96, 40, 6, 0, [], ["comfort","festive","nonveg"], 5.2, 4.4, 0.7,
         ["best biryani", "a little overpriced", "loved it"]),
        ("Junior Kuppanna", "Egg Curry Meals", 160, "egg", 700, 26, 78, 26, 7, 0, ["egg"], ["comfort"], 1.1, 4.2, 0.6,
         ["solid meal", "value for money"]),
        ("Third Wave Coffee", "Filter Coffee + Vada", 80, "continental", 360, 9, 44, 14, 6, 1, ["gluten"], ["light"], 0.8, 4.1, 0.6,
         ["quick and tasty", "good value", "hot and fresh"]),
        ("Third Wave Coffee", "Cold Brew + Sandwich", 280, "continental", 520, 18, 54, 24, 12, 0, ["gluten","dairy"], ["light"], 1.5, 4.1, 0.5,
         ["great coffee", "sandwich was soggy", "late delivery"]),
        ("Third Wave Coffee", "Peanut Butter Toast", 180, "continental", 430, 14, 40, 22, 10, 1, ["peanut","gluten"], ["light"], 0.9, 4.0, 0.4,
         ["nice", "small portion"]),
        ("Starbucks", "Cappuccino + Muffin", 360, "cafe", 540, 10, 62, 26, 34, 1, ["dairy","gluten"], ["light"], 1.3, 4.0, 0.5,
         ["pricey", "consistent", "tiny muffin"]),
        ("FreshMenu", "Grilled Chicken Salad", 240, "chicken", 420, 38, 22, 18, 6, 0, [], ["light","nonveg"], 1.4, 4.3, 0.6,
         ["fresh and filling", "great for diet", "loved the dressing"]),
        ("FreshMenu", "Quinoa Buddha Bowl", 250, "vegan", 480, 20, 60, 16, 8, 1, [], ["light","veg"], 0.6, 4.2, 0.5,
         ["healthy and tasty", "fresh"]),
        ("FreshMenu", "Paneer Tikka Bowl", 230, "veg", 560, 26, 48, 24, 9, 1, ["dairy"], ["veg"], 1.0, 4.1, 0.5,
         ["good", "bland today"]),
        ("Hotel Saravana Bhavan", "Veg Meals", 130, "thali", 700, 18, 104, 18, 8, 1, [], ["comfort","veg","festive"], 0.9, 4.6, 0.8,
         ["authentic", "generous", "loved it", "value"]),
        ("Hotel Saravana Bhavan", "Masala Dosa", 95, "south indian", 480, 10, 70, 16, 4, 1, [], ["light","veg"], 0.7, 4.5, 0.7,
         ["crisp and fresh", "great", "hot"]),
        ("Hotel Saravana Bhavan", "Rava Kesari Sweet", 70, "salad", 420, 6, 72, 12, 40, 1, ["dairy"], ["festive"], 0.6, 4.3, 0.5,
         ["festive favourite", "very sweet"]),
        ("Faasos", "Chicken Tikka Roll", 150, "chicken", 520, 28, 48, 22, 5, 0, ["gluten"], ["nonveg"], 1.2, 3.9, 0.6,
         ["tasty", "sometimes cold"]),
        ("Faasos", "Veg Kathi Roll", 110, "veg", 460, 12, 56, 16, 6, 1, ["gluten"], ["veg"], 0.7, 3.9, 0.5,
         ["decent", "quick"]),
        ("Sangeetha Veg", "Curd Rice", 80, "south indian", 380, 10, 58, 8, 5, 1, ["dairy"], ["light","veg"], 0.5, 4.2, 0.6,
         ["comforting", "fresh"]),
        ("Sangeetha Veg", "Vegan Sambar Rice", 90, "vegan", 520, 14, 88, 8, 6, 1, [], ["light","veg"], 0.5, 4.2, 0.6,
         ["clean and tasty", "fresh", "great value"]),
        ("Sangeetha Veg", "Parotta + Veg Kurma", 120, "south indian", 720, 14, 92, 28, 7, 1, ["gluten","dairy"], ["comfort","veg"], 0.9, 4.1, 0.6,
         ["comfort classic", "a bit oily"]),
    ]
    with db.cursor() as cur:
        for m in M:
            cur.execute(
                "INSERT INTO menu_items(restaurant_id,name,price,cuisine,kcal,protein_g,carbs_g,"
                "fat_g,sugar_g,veg,allergens,tags,carbon_kg,item_rating,popularity,reviews) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rmap[m[0]], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8], m[9],
                 db.jd(m[10]), db.jd(m[11]), m[12], m[13], m[14], db.jd(m[15])))


def _contexts():
    """Weather, surge history, festivals, calendar, leftovers — all aligned to the demo week."""
    with db.cursor() as cur:
        # Weather: Tue rainy, Wed hot (§5.2.10)
        weather = [("Chennai", 0, "clear", 31), ("Chennai", 1, "rain", 27),
                   ("Chennai", 2, "hot", 38), ("Chennai", 3, "clear", 32),
                   ("Chennai", 4, "clear", 33), ("Chennai", 5, "rain", 28),
                   ("Chennai", 6, "clear", 31)]
        cur.executemany("INSERT INTO weather(city,day,condition,temp_c) VALUES (?,?,?,?)", weather)

        # Surge history: rainy slots cost more (§5.2.11)
        surge = [("Chennai", 1, "lunch", "rain", 1.55), ("Chennai", 1, "dinner", "rain", 1.7),
                 ("Chennai", 5, "dinner", "rain", 1.6), ("Chennai", 2, "lunch", "hot", 1.2),
                 ("Chennai", 0, "dinner", "clear", 1.25)]
        cur.executemany(
            "INSERT INTO surge_history(city,day,meal,condition,multiplier) VALUES (?,?,?,?,?)", surge)

        # Festivals in the demo week (§5.2.9): a feast (Fri) and a fast (Sat)
        start = dt.date.fromisoformat(demo_week_start())
        fri = (start + dt.timedelta(days=4)).isoformat()
        sat = (start + dt.timedelta(days=5)).isoformat()
        cur.executemany(
            "INSERT INTO festivals(name,iso_date,effect,note) VALUES (?,?,?,?)",
            [("Varalakshmi Vratham", fri, "feast", "festive meals favoured"),
             ("Fasting Observance", sat, "fast", "daytime sessions suspended")])

        # Calendar for Hasen (user 1): client lunch Tue (conflict→time-shift), travel Thu (suspend) (§5.1.2)
        cur.executemany(
            "INSERT INTO calendar_events(user_id,day,start_min,end_min,kind,title) VALUES (?,?,?,?,?,?)",
            [(1, 1, 765, 840, "busy", "Client lunch sync"),
             (1, 3, 0, 1439, "travel", "Bangalore offsite")])

        # Leftovers for Hasen: cooked dal → Wed dinner covered (§5.2.8)
        cur.execute("INSERT INTO leftovers(user_id,day,meal,label,servings) VALUES (1,2,'dinner','Home dal + rice',2)")


def _community():
    payload = db.jd({"sessions": [
        {"day": 0, "meal": "lunch", "kind": "delivery", "item": "Veg Meals", "cost": 150},
        {"day": 0, "meal": "dinner", "kind": "cook", "item": "Dal + rice", "cost": 45}],
        "saved": dt.date.today().isoformat()})
    with db.cursor() as cur:
        cur.executemany(
            "INSERT INTO community_templates(author,title,city,budget,mode,payload,adopts) "
            "VALUES (?,?,?,?,?,?,?)",
            [("campus_survivor", "₹1500/week student survival", "Chennai", 1500, "survival", payload, 142),
             ("veg_athlete", "High-protein veg week", "Chennai", 2200, "balanced", payload, 88)])


def _starter_plan() -> int:
    import datetime as _dt
    from .kernel import scheduler
    ws = demo_week_start()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO plans(user_id, week_start, mode, status, created_ts) VALUES (1, ?, 'survival', 'active', ?)",
            (ws, _dt.datetime.now().isoformat()))
        plan_id = cur.lastrowid
    scheduler.build_week(plan_id, ws)
    return plan_id


if __name__ == "__main__":
    db.init_db()
    print(seed_all())
