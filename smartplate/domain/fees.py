"""What a Swiggy order really costs: item total + delivery + platform fee + packaging + GST.

A plan priced as `price + delivery` under-counts the bill. The repo's one real bill was
₹160 of food → ₹185.06 to pay (₹6 delivery, ₹19.06 "GST & Other Charges"), +15.6 %.
This module models the gap and LEARNS it from the user's own Swiggy bills:

  bill = item_total + delivery + other
  other = fixed + GST_FOOD_RATE × item_total

`other` is everything on the bill except the item total, the delivery line and
discounts: Swiggy's platform fee, the restaurant's packaging, the small-cart fee and
"GST & Other Charges". GST on restaurant food is 5 % by law (it scales with the food);
the rest is a per-order amount (`fixed`). One real bill is enough to learn `fixed`
for that restaurant: fixed = other − 5 % × item_total.

Which number a plan uses, best first (each says so via `basis`):
  1. "bill"      — this restaurant's own Swiggy bills at this address (median);
  2. "your_bills"— the user's other Swiggy bills at this address (median), for a
                   restaurant with no bill yet;
  3. "default"   — the documented research default below, labelled an estimate with a
                   range. Never presented as a fact.
A real bill for a meal (order_queue.to_pay) always replaces the estimate for that meal;
this module only estimates meals that have not been carted yet.

Research defaults (checked 14 Oct 2026; re-check when Swiggy changes fees):
  • Platform fee ₹17.58 per order incl. GST since 24 Mar 2026, also charged to Swiggy One
    members (Financial Express, Economic Times, Business Standard, 24 Mar 2026).
  • GST on restaurant food via an e-commerce operator: 5 %; delivery and platform fees
    18 % since 22 Sep 2025 (GST Council 56th meeting; TaxClue guide). Packaging billed
    by the restaurant is taxed with the food at 5 %.
  • Swiggy's average delivery fee ≈ ₹14.5 before GST (Morgan Stanley via Inc42, Sep
    2025) → ≈ ₹17.11 with 18 % GST. Real fees run from ₹0 (Swiggy One / offers) to
    distance- and rain-based ₹50+, so the range is wide on purpose.
  • Packaging: set by each restaurant; no public figure. ₹10 point, ₹0–30 range.
"""
import statistics

from .. import clock, db

GST_FOOD_RATE = 0.05
PLATFORM_FEE = 17.58                       # ₹ incl. GST, Swiggy from 24 Mar 2026
PACKAGING = 10.0                           # ₹ point estimate, restaurant-set
DELIVERY = round(14.5 * 1.18, 2)           # ₹17.11: average fee + 18 % GST
DEFAULT_FIXED = round(PLATFORM_FEE + PACKAGING * (1 + GST_FOOD_RATE), 2)   # 28.08
RANGES = {                                 # (low, high) used only while a figure is a default
    "delivery": (0.0, 60.0),
    "fixed": (15.0, 50.0),                 # platform ₹15–20 + packaging ₹0–30
}
KEEP_BILLS = 10                            # per restaurant and address; older ones are pruned

SOURCES = [
    {"what": "Swiggy platform fee ₹17.58 incl. GST (24 Mar 2026)",
     "url": "https://www.financialexpress.com/business/industry-swiggy-raises-platform-fee-to-rs-17-58-per-order-matches-zomatos-hike-4182811/"},
    {"what": "GST 5% on restaurant food via apps, 18% on delivery/platform fees from 22 Sep 2025",
     "url": "https://taxclue.in/guide/gst-on-food-delivery"},
    {"what": "Swiggy average delivery fee ≈ ₹14.5 (Morgan Stanley, Sep 2025)",
     "url": "https://inc42.com/buzz/zomato-swiggy-deliveries-to-get-costlier-with-new-18-gst/"},
]

_ITEM = "Item Total"
_DELIVERY = "Delivery Fee"
_DISCOUNT = "Discount"


# --------------------------------------------------------------------------- #
# Learning from a real bill
# --------------------------------------------------------------------------- #
def components(bill: dict | None, item_total: float | None = None) -> dict | None:
    """Split one Swiggy bill (swiggy_live.bill_breakdown) into item_total / delivery /
    other, in rupees. None when the bill can't teach anything reliably: no payable
    total, or no item total (neither on the bill nor passed in as the reviewed menu
    total). Discounts are left out of `other` (they vary order to order)."""
    if not isinstance(bill, dict) or not isinstance(bill.get("to_pay"), (int, float)):
        return None
    lines = {l.get("label"): float(l.get("amount") or 0) for l in bill.get("lines") or [] if isinstance(l, dict)}
    items = lines.get(_ITEM, item_total)
    if not items or items <= 0:
        return None
    delivery = lines.get(_DELIVERY)
    discount = abs(lines.get(_DISCOUNT, 0.0))
    other = round(float(bill["to_pay"]) + discount - items - (delivery or 0.0), 2)
    if other < 0:                       # a total below its own items: unreadable, learn nothing
        return None
    return {"item_total": round(items, 2), "delivery": None if delivery is None else round(delivery, 2),
            "other": other, "to_pay": round(float(bill["to_pay"]), 2), "discount": round(discount, 2)}


def fixed_part(comp: dict) -> float:
    """The per-order part of `other` (platform fee, packaging): what is left after the 5 % food GST."""
    return round(max(0.0, comp["other"] - GST_FOOD_RATE * comp["item_total"]), 2)


def record_bill(user_id: int, address_id, provider_id, restaurant_name: str, bill: dict | None,
                item_total: float | None = None) -> dict | None:
    """Keep what a real Swiggy cart bill says about this restaurant's fees."""
    comp = components(bill, item_total)
    if comp is None or not provider_id or not address_id:
        return None
    now = clock.now().isoformat(timespec="seconds")
    with db.cursor() as cur:
        cur.execute("INSERT OR REPLACE INTO swiggy_bill_fees(user_id, address_id, provider_id, restaurant_name, "
                    "item_total, delivery, other, to_pay, lines, seen_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (user_id, str(address_id), str(provider_id), restaurant_name or "", comp["item_total"],
                     comp["delivery"], comp["other"], comp["to_pay"], db.jd(bill.get("lines") or []), now))
        old = cur.execute("SELECT seen_ts FROM swiggy_bill_fees WHERE user_id=? AND address_id=? AND provider_id=? "
                          "ORDER BY seen_ts DESC", (user_id, str(address_id), str(provider_id))).fetchall()
        for row in old[KEEP_BILLS:]:
            cur.execute("DELETE FROM swiggy_bill_fees WHERE user_id=? AND address_id=? AND provider_id=? AND seen_ts=?",
                        (user_id, str(address_id), str(provider_id), row["seen_ts"]))
    return comp


# --------------------------------------------------------------------------- #
# The model the planner reads
# --------------------------------------------------------------------------- #
def _address_id(user_id: int) -> str | None:
    with db.cursor() as cur:
        try:
            row = cur.execute("SELECT address_id FROM swiggy_connections WHERE user_id=?", (user_id,)).fetchone()
        except Exception:                  # the Swiggy tables don't exist until first connect
            return None
    return row["address_id"] if row else None


def model(user_id: int, address_id: str | None = None) -> dict:
    """{"restaurants": {provider_id: {"fixed", "delivery", "bills"}}, "user": {...} | None}
    learned from this user's bills at the current (or given) delivery address."""
    address_id = address_id or _address_id(user_id)
    if not address_id:
        return {"restaurants": {}, "user": None, "address_id": None}
    with db.cursor() as cur:
        try:
            rows = cur.execute("SELECT * FROM swiggy_bill_fees WHERE user_id=? AND address_id=? ORDER BY seen_ts",
                               (user_id, str(address_id))).fetchall()
        except Exception:
            rows = []
        # Delivery fees learned before this module existed (live_catalog.record_fee).
        try:
            older = cur.execute("SELECT provider_id, delivery_fee FROM swiggy_delivery_fees "
                                "WHERE user_id=? AND address_id=?", (user_id, str(address_id))).fetchall()
        except Exception:
            older = []
    per, all_fixed, all_delivery = {}, [], []
    for r in rows:
        comp = {"item_total": r["item_total"], "other": r["other"]}
        p = per.setdefault(r["provider_id"], {"fixed": [], "delivery": []})
        p["fixed"].append(fixed_part(comp))
        if r["delivery"] is not None:
            p["delivery"].append(r["delivery"])
    for r in older:
        p = per.setdefault(r["provider_id"], {"fixed": [], "delivery": []})
        if not p["delivery"]:
            p["delivery"].append(float(r["delivery_fee"]))
    out = {}
    for pid, p in per.items():
        out[pid] = {"fixed": round(statistics.median(p["fixed"]), 2) if p["fixed"] else None,
                    "delivery": round(statistics.median(p["delivery"]), 2) if p["delivery"] else None,
                    "bills": len(p["fixed"])}
        all_fixed += p["fixed"]
        all_delivery += p["delivery"]
    user = None
    if all_fixed or all_delivery:
        user = {"fixed": round(statistics.median(all_fixed), 2) if all_fixed else None,
                "fixed_range": (min(all_fixed), max(all_fixed)) if all_fixed else None,
                "delivery": round(statistics.median(all_delivery), 2) if all_delivery else None,
                "delivery_range": (min(all_delivery), max(all_delivery)) if all_delivery else None}
    return {"restaurants": out, "user": user, "address_id": address_id}


def pricing_for(provider_id, m: dict) -> dict:
    """How one restaurant's orders are priced: delivery and the fixed per-order fees, each
    with where the number came from and a range while it is not from this restaurant's bill."""
    known = (m.get("restaurants") or {}).get(str(provider_id)) if provider_id else None
    user = m.get("user") or {}
    if known and known.get("delivery") is not None:
        delivery = {"amount": known["delivery"], "basis": "bill", "estimated": False}
    elif user.get("delivery") is not None:
        lo, hi = user["delivery_range"]
        delivery = {"amount": user["delivery"], "basis": "your_bills", "estimated": True, "range": [lo, hi]}
    else:
        delivery = {"amount": DELIVERY, "basis": "default", "estimated": True, "range": list(RANGES["delivery"])}
    if known and known.get("fixed") is not None:
        fixed = {"amount": known["fixed"], "basis": "bill", "estimated": False}
    elif user.get("fixed") is not None:
        lo, hi = user["fixed_range"]
        fixed = {"amount": user["fixed"], "basis": "your_bills", "estimated": True, "range": [lo, hi]}
    else:
        fixed = {"amount": DEFAULT_FIXED, "basis": "default", "estimated": True, "range": list(RANGES["fixed"])}
    return {"delivery": delivery, "fixed": fixed, "gst_rate": GST_FOOD_RATE}


def annotate(user_id: int, items: list[dict]) -> list[dict]:
    """Live menu rows priced with the user's learned fees: `delivery_fee` (+ estimated flag)
    and `fees` (the per-order charges model) on every row."""
    m = model(user_id)
    out = []
    for it in items:
        p = pricing_for(it.get("provider_id") or it.get("restaurant_provider_id"), m)
        out.append({**it, "delivery_fee": p["delivery"]["amount"], "delivery_fee_estimated": p["delivery"]["estimated"],
                    "fees": p})
    return out


def charges(item: dict, item_total: float) -> float:
    """Platform fee + packaging + GST on top of the item total and delivery, in rupees.
    0 for a row that carries no fee model (the labelled test-fixture catalogue)."""
    p = item.get("fees") if isinstance(item, dict) else None
    if not p:
        return 0.0
    return round(p["fixed"]["amount"] + p["gst_rate"] * max(0.0, item_total), 2)


def breakdown(item: dict, item_total: float) -> dict | None:
    """The estimated bill for an order of `item_total` from this row's restaurant:
    each line, the total, whether any line is an estimate and the total's range."""
    p = item.get("fees") if isinstance(item, dict) else None
    if not p:
        return None
    gst = round(p["gst_rate"] * item_total, 2)
    d, f = p["delivery"], p["fixed"]
    total = round(item_total + d["amount"] + f["amount"] + gst, 2)
    lo = item_total + (d.get("range") or [d["amount"]])[0] + (f.get("range") or [f["amount"]])[0] + gst
    hi = item_total + (d.get("range") or [0, d["amount"]])[-1] + (f.get("range") or [0, f["amount"]])[-1] + gst
    estimated = d["estimated"] or f["estimated"]
    return {"items": round(item_total, 2), "delivery": d["amount"], "fees": f["amount"], "gst": gst,
            "total": total, "estimated": estimated,
            "range": [round(lo, 2), round(hi, 2)] if estimated else [total, total],
            "basis": {"delivery": d["basis"], "fees": f["basis"]},
            "label": (f"~₹{total:.0f} incl. ₹{d['amount'] + f['amount'] + gst:.0f} delivery, fees & GST (estimate, "
                      f"₹{lo:.0f}–₹{hi:.0f})") if estimated else
                     f"₹{total:.0f} incl. ₹{d['amount'] + f['amount'] + gst:.0f} delivery, fees & GST (from your bills here)"}


def defaults() -> dict:
    """The research defaults and where they come from (for the UI's "how we estimate")."""
    return {"platform_fee": PLATFORM_FEE, "packaging": PACKAGING, "delivery": DELIVERY,
            "gst_food_rate": GST_FOOD_RATE, "fixed": DEFAULT_FIXED, "ranges": RANGES, "sources": SOURCES,
            "note": "Estimates until your first Swiggy bill from a restaurant; then that bill's numbers are used."}


# --------------------------------------------------------------------------- #
# Unit-flip guard (critique 51)
# --------------------------------------------------------------------------- #
REPRICE_LIMIT = 3.0       # a menu whose typical price moves 3× or more between reads is a unit flip, not a price change


def reprice_ratio(old: dict, new: dict) -> dict:
    """{restaurant id: median new/old price} over dishes present in both reads.
    Keys of `old`/`new` are (restaurant id, item id) → rupees."""
    by_place = {}
    for key, price in new.items():
        before = old.get(key)
        if before and before > 0 and price and price > 0:
            by_place.setdefault(key[0], []).append(price / before)
    return {place: statistics.median(r) for place, r in by_place.items()}


def reprice_guard(old: dict, new: dict) -> None:
    """Refuse a menu refresh whose prices moved ≥ 3× (or ≤ ⅓) for a restaurant: Swiggy's
    menu `price` has no documented unit, and a silent paise/rupee flip would multiply or
    divide a whole week's budget by 100. The previous catalogue is kept and the person is
    told; nothing is re-planned on the suspect prices."""
    for place, ratio in reprice_ratio(old, new).items():
        if ratio >= REPRICE_LIMIT or ratio <= 1 / REPRICE_LIMIT:
            from ..integrations.swiggy_connect import SwiggyError
            moved = f"changed {ratio:.0f}×" if ratio >= 1 else f"dropped to 1/{1 / ratio:.0f}"
            raise SwiggyError(f"Swiggy's prices for one of your restaurants {moved} since the last read. That looks "
                              "like a unit change (paise vs rupees), not a real price change, so Ziggy kept your "
                              "previous menus. Check the prices in Swiggy, then refresh again.",
                              code="price_unit_changed", http_status=409)
