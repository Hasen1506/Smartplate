"""Offers and value on a Swiggy menu (UX audit #9, 16 Oct 2026).

Pure functions, no network:
  • `restaurant_offer` reads the offer text Swiggy shows on a restaurant ("20% OFF UPTO ₹50",
    "₹100 OFF ABOVE ₹399", "ITEMS AT ₹99") into numbers and one plain label. Ziggy never
    claims an offer is applied: Swiggy decides that at checkout.
  • `mark_value` badges one "Best value" dish per menu: the most food for the money (energy
    and protein estimated from the dish name, live_catalog.estimate) among whole-meal dishes
    priced within the person's per-meal budget. Swiggy's own bestseller flag is kept as is.
  • `coupons` reads `fetch_food_coupons` (called only when the person is at the cart).
"""
import re

OFFER_KEYS = ("aggregatedDiscountInfo", "aggregatedDiscountInfoV3", "aggregatedDiscountInfoV2", "discountInfo",
              "offer", "offers", "discount", "discountText", "offerText")
_PCT = re.compile(r"(\d{1,2})\s*%\s*off(?:\s*(?:up\s*to|upto)\s*(?:₹|rs\.?|inr)?\s*([\d,]+))?", re.I)
_FLAT = re.compile(r"(?:flat\s*)?(?:₹|rs\.?|inr)\s*([\d,]+)\s*off(?:\s*(?:above|on orders? above|over)\s*(?:₹|rs\.?|inr)?\s*([\d,]+))?", re.I)
_ITEMS_AT = re.compile(r"items?\s*(?:at|@)\s*(?:₹|rs\.?|inr)\s*([\d,]+)", re.I)
_FREE_DEL = re.compile(r"free\s*delivery", re.I)


def _n(text) -> int | None:
    try:
        return int(str(text).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _texts(value, depth=0) -> list[str]:
    """Every short string inside an offer field (Swiggy nests header / subHeader / description)."""
    if depth > 3:
        return []
    if isinstance(value, str):
        v = " ".join(value.split())
        return [v] if 2 <= len(v) <= 120 else []
    if isinstance(value, dict):
        out = []
        for k in ("header", "subHeader", "description", "title", "text", "label", "discountTag"):
            out += _texts(value.get(k), depth + 1)
        return out
    if isinstance(value, list):
        return [t for v in value[:4] for t in _texts(v, depth + 1)]
    return []


def parse_offer(text: str) -> dict | None:
    """{"label", "kind", "pct", "cap", "flat", "min_order", "items_at", "text"} or None."""
    text = " ".join(str(text or "").split())[:160]
    if not text:
        return None
    m = _PCT.search(text)
    if m:
        pct, cap = _n(m.group(1)), _n(m.group(2))
        tail = re.search(r"above\s*(?:₹|rs\.?)?\s*([\d,]+)", text, re.I)
        out = {"kind": "percent", "pct": pct, "cap": cap, "min_order": _n(tail.group(1)) if tail else None}
        out["label"] = f"{pct}% off" + (f" up to ₹{cap}" if cap else "") + (f" above ₹{out['min_order']}" if out["min_order"] else "")
        return {**out, "text": text}
    m = _FLAT.search(text)
    if m:
        flat, above = _n(m.group(1)), _n(m.group(2))
        return {"kind": "flat", "flat": flat, "min_order": above, "text": text,
                "label": f"₹{flat} off" + (f" above ₹{above}" if above else "")}
    m = _ITEMS_AT.search(text)
    if m:
        return {"kind": "items_at", "items_at": _n(m.group(1)), "text": text, "label": f"Items at ₹{_n(m.group(1))}"}
    if _FREE_DEL.search(text):
        return {"kind": "free_delivery", "text": text, "label": "Free delivery"}
    return None


def restaurant_offer(row: dict) -> dict | None:
    """The first offer Swiggy shows on a restaurant record that reads as one."""
    if not isinstance(row, dict):
        return None
    lowered = {k.lower(): k for k in row}
    texts = []
    for key in OFFER_KEYS:
        real = lowered.get(key.lower())
        if real is not None:
            joined = " ".join(_texts(row[real]))
            if joined:
                texts.append(joined)
    for t in texts:
        found = parse_offer(t)
        if found:
            return found
    return None


def offer_saving(offer: dict | None, subtotal: float) -> float | None:
    """What the offer would take off a dish subtotal if Swiggy applies it (never claimed as applied)."""
    if not offer or not subtotal:
        return None
    if offer.get("min_order") and subtotal < offer["min_order"]:
        return 0.0
    if offer["kind"] == "percent" and offer.get("pct"):
        off = subtotal * offer["pct"] / 100
        return round(min(off, offer["cap"]) if offer.get("cap") else off, 2)
    if offer["kind"] == "flat" and offer.get("flat"):
        return float(min(offer["flat"], subtotal))
    return None


def _value_score(item: dict) -> float | None:
    from ..domain import live_catalog
    price = item.get("price")
    if not price or price <= 0:
        return None
    est = live_catalog.estimate(item["name"], item.get("veg"), item.get("description"))
    if not est:
        return None
    tags = set(live_catalog.section_tags(est.get("tags"), item.get("categories"), item["name"]))
    if "side" in tags or "dessert" in tags:
        return None
    # energy and protein per rupee, protein weighted so a filling, protein-rich plate wins
    return (est["kcal"] / 600 + est["protein_g"] / 25) / float(price)


def mark_value(items: list[dict], per_meal: float | None) -> dict | None:
    """Badge the best-value dish (in place: item["best_value"] = True) and say why."""
    if not per_meal or per_meal <= 0:
        return None
    best, best_score = None, 0.0
    for it in items:
        if it.get("in_stock") is False or it.get("price") is None or it["price"] > per_meal:
            continue
        score = _value_score(it)
        if score is None:
            continue
        score *= 1.05 if it.get("bestseller") else 1.0
        if score > best_score:
            best, best_score = it, score
    if not best:
        return None
    best["best_value"] = True
    return {"item_id": best["id"], "per_meal": round(per_meal),
            "why": f"Most food for the money among dishes within your ₹{per_meal:,.0f} a meal "
                   "(estimated from the dish name; dish price before fees)."}


def coupons(data) -> list[dict]:
    """Coupons Swiggy lists for the cart: code, what it says, the discount when stated."""
    from .swiggy_schema import _lists
    out, seen = [], set()
    for rows in _lists(data):
        for r in rows:
            if not isinstance(r, dict):
                continue
            code = r.get("code") or r.get("couponCode") or r.get("coupon_code")
            title = r.get("title") or r.get("description") or r.get("header") or r.get("message")
            if not code or not isinstance(code, str) or code in seen:
                continue
            seen.add(code)
            disc = r.get("discountAmount") or r.get("discount_amount") or r.get("discount")
            out.append({"code": code[:40], "title": str(title or "")[:140],
                        "discount": float(disc) if isinstance(disc, (int, float)) and not isinstance(disc, bool) else None,
                        "min_order": r.get("minOrder") if isinstance(r.get("minOrder"), (int, float)) else None})
    return out[:6]
