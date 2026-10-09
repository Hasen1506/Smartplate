"""The rules behind every Swiggy step, in plain words, and which one a failure came from.

Swiggy's own builder rules (docs/vendor/swiggy/README.md) and Ziggy's safety rules both
stop an order now and then. A person should never be left with "Swiggy said: 422": each
SwiggyError is matched to one rule here, the API sends that rule with the error, and the
last few problems are kept on the profile (Me → Swiggy → How ordering works) so a
repeated cause is easy to see. Nothing here talks to Swiggy.
"""
from __future__ import annotations

import datetime as dt
import logging
import re

from .. import clock, db

# whose: "swiggy" (Swiggy's builder terms or its servers) or "ziggy" (Ziggy's own safety rule)
RULES = [
    {"id": "you_review", "whose": "ziggy", "title": "You review every cart",
     "plain": "Ziggy shows the live dish and price before anything goes into your Swiggy cart. If the dish, "
              "price, address or your plan changes in between, you review it again.",
     "fix": "Tap the meal again and check the new details."},
    {"id": "one_meal", "whose": "swiggy", "title": "One meal at a time",
     "plain": "Swiggy doesn't allow ordering a whole week in one go or ordering while nobody is watching. "
              "Ziggy plans the week, and you add each meal to your cart when it's time.",
     "fix": "Use order reminders so you don't miss the order-by time."},
    {"id": "one_cart", "whose": "swiggy", "title": "One restaurant, one cart",
     "plain": "A Swiggy cart holds dishes from one restaurant, and it's the same cart as in your Swiggy app. "
              "Ziggy only adds to an empty cart, so it never mixes its dish with something you added yourself.",
     "fix": "Open Swiggy, place or clear the cart that's there, then try again."},
    {"id": "address", "whose": "swiggy", "title": "Menus and carts belong to one address",
     "plain": "Prices, delivery fees and what's open all depend on the address. Swiggy ties your cart to the "
              "address it was started for.",
     "fix": "Choose the same address in Ziggy as in Swiggy, or clear the cart in Swiggy."},
    {"id": "exact_dish", "whose": "ziggy", "title": "Only the exact dish",
     "plain": "Ziggy adds a dish only when Swiggy confirms exactly one match at that restaurant. Similar names "
              "are never guessed.",
     "fix": "Pick the dish from the restaurant's live menu, or choose it in Swiggy."},
    {"id": "open_in_stock", "whose": "swiggy", "title": "Open and in stock right now",
     "plain": "Restaurants close and dishes sell out. Swiggy has to confirm both at the moment you order.",
     "fix": "Pick another dish or place, or try closer to the meal."},
    {"id": "choices", "whose": "ziggy", "title": "Dishes with choices are set up in Swiggy",
     "plain": "Sizes, add-ons and other options change what you get and what you pay, so Ziggy doesn't "
              "choose them for you.",
     "fix": "Add the dish in Swiggy and pick the options there."},
    {"id": "ingredients", "whose": "ziggy", "title": "Swiggy menus don't list ingredients",
     "plain": "With an allergy, a medical rule or a vegan diet, Ziggy can't confirm a dish is safe from the "
              "menu alone, so it won't fill the cart. A vegetarian dish also has to carry Swiggy's veg mark.",
     "fix": "Check the dish with the restaurant and order it in Swiggy."},
    {"id": "cart_cap", "whose": "swiggy", "title": "₹1,000 per order for now",
     "plain": "While Ziggy is in Swiggy's developer programme, an order placed through Ziggy can't be over "
              "₹1,000.",
     "fix": "Pay larger orders in Swiggy."},
    {"id": "payment", "whose": "swiggy", "title": "You pay in Swiggy",
     "plain": "Ziggy never handles your money. Where Swiggy allows it, Ziggy can place a Cash on Delivery "
              "order; everything else is paid in Swiggy.",
     "fix": "Open Swiggy to pay with UPI, a card or a wallet."},
    {"id": "no_blind_retry", "whose": "swiggy", "title": "An order is never retried blindly",
     "plain": "Placing an order can't be safely repeated: if the answer was unclear, the order may already "
              "exist. Ziggy checks your Swiggy orders before anything is tried again.",
     "fix": "Look at your orders in Swiggy first."},
    {"id": "sign_in", "whose": "swiggy", "title": "Swiggy sign-ins expire",
     "plain": "You sign in on Swiggy's own page. The sign-in lasts about 5 days and Swiggy can end it sooner. "
              "Ziggy never sees your password or OTP.",
     "fix": "Connect Swiggy again."},
    {"id": "approval", "whose": "swiggy", "title": "Swiggy approves each app",
     "plain": "Swiggy lets a new app use its tools step by step. Until it approves this server, sign-in or "
              "placing orders may be switched off.",
     "fix": "Order in Swiggy for now. Nothing on your side needs to change."},
    {"id": "rate_limit", "whose": "swiggy", "title": "A limited number of requests",
     "plain": "Swiggy limits how often an app may ask it for menus and carts, and Ziggy keeps well under that "
              "for each profile. Order tracking refreshes at most every 10 seconds.",
     "fix": "Wait a minute and try again."},
    {"id": "swiggy_down", "whose": "swiggy", "title": "Swiggy didn't answer as expected",
     "plain": "Swiggy's servers were slow, busy or replied in a shape Ziggy couldn't safely read. Ziggy stops "
              "rather than guess.",
     "fix": "Try again in a moment. If it keeps happening, use the Swiggy app."},
]
BY_ID = {r["id"]: r for r in RULES}

CODES = {"swiggy_not_connected": "sign_in", "swiggy_auth_expired": "sign_in",
         "swiggy_browser_mismatch": "sign_in", "swiggy_rate_limited": "rate_limit",
         "swiggy_cart_other_address": "address", "swiggy_item_unmatched": "exact_dish"}

# First match wins: specific causes before the generic "Swiggy didn't answer".
PATTERNS = [
    ("no_blind_retry", r"unresolved|already attempted|completed order|never retried|order list|before retrying"),
    ("one_cart", r"cart already has items|differs from the item|one exact dish in the swiggy cart|"
                 r"in swiggy's cart|dish and quantity in the live cart"),
    ("you_review", r"^review |review it again|review the (live|exact|current)|approval expired"),
    ("ingredients", r"ingredient|medical|vegetarian"),
    ("cart_cap", r"₹1,000|builders club limit"),
    ("payment", r"cash on delivery|payment"),
    ("approval", r"placement is disabled|public address isn't configured|pre-registered|register|needs https"),
    ("sign_in", r"sign-in|access token|private ziggy profile|shared profile"),
    ("rate_limit", r"limiting requests|too many"),
    ("exact_dish", r"exact|couldn't find|no dishes ziggy can plan"),
    ("address", r"address"),
    ("choices", r"options|add-ons|customi[sz]ation"),
    ("open_in_stock", r"in stock|closed"),
]
_COMPILED = [(rule, re.compile(p, re.I)) for rule, p in PATTERNS]
KEEP = 20
WINDOW = dt.timedelta(days=7)


def classify(error) -> str:
    """The rule a SwiggyError (or its message) came from; swiggy_down when nothing fits."""
    code = getattr(error, "code", None)
    if code in CODES:
        return CODES[code]
    text = str(error)
    for rule, rx in _COMPILED:
        if rx.search(text):
            return rule
    return "swiggy_down"


def explain(error) -> dict:
    rule = BY_ID[classify(error)]
    return {k: rule[k] for k in ("id", "whose", "title", "plain", "fix")}


def note(user_id: int | None, error) -> None:
    """Keep this problem on the profile (best effort: logging a problem never fails a request)."""
    if not user_id:
        return
    try:
        from . import swiggy_connect
        swiggy_connect.init_schema()
        now = clock.now()
        with db.cursor() as cur:
            if not cur.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
                return
            cur.execute("INSERT OR REPLACE INTO swiggy_issues(user_id, ts, rule, message) VALUES (?,?,?,?)",
                        (user_id, now.isoformat(timespec="microseconds"), classify(error), str(error)[:300]))
            old = cur.execute("SELECT ts FROM swiggy_issues WHERE user_id=? ORDER BY ts DESC",
                              (user_id,)).fetchall()[KEEP:]
            for row in old:
                cur.execute("DELETE FROM swiggy_issues WHERE user_id=? AND ts=?", (user_id, row["ts"]))
    except Exception:                                   # noqa: BLE001 — never mask the real error
        logging.getLogger(__name__).warning("Could not record a Swiggy problem")


def overview(user_id: int) -> dict:
    """Every rule, how often each stopped this profile in the last 7 days, and the recent problems."""
    from . import swiggy_connect
    swiggy_connect.init_schema()
    with db.cursor() as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT ts, rule, message FROM swiggy_issues WHERE user_id=? ORDER BY ts DESC", (user_id,)).fetchall()]
    since = (clock.now() - WINDOW).isoformat(timespec="microseconds")
    hits = {}
    for r in rows:
        if r["ts"] >= since:
            hits[r["rule"]] = hits.get(r["rule"], 0) + 1
    return {"rules": [{**r, "hits_7d": hits.get(r["id"], 0)} for r in RULES],
            "issues": [{"at": r["ts"][:19], "rule": r["rule"], "title": BY_ID.get(r["rule"], BY_ID["swiggy_down"])["title"],
                        "message": r["message"]} for r in rows[:10]]}
