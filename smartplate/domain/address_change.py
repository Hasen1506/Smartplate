"""After the delivery address changes, the week must not keep places that don't deliver there.

W10 scenario finding (Oct 2026): switching to the Minjur address left Wok Street in the
week until the user re-planned by hand. Restaurants deliver to a limited area, so a new
address means: read the live menus again for it (places that don't deliver there drop
out, with Swiggy's reason), re-plan the open meals from what does deliver, and tell the
user in one sentence which places were taken out. Past and carted meals are untouched
(the optimizer keeps them fixed)."""
from __future__ import annotations

import logging

from .. import db
from . import live_catalog, models


def snapshot(user_id: int) -> dict:
    """What to compare against: call before the address is switched."""
    return {"address_id": live_catalog._address_id(user_id), "had_live": live_catalog.has_live(user_id)}


def _latest_plan(user_id: int) -> int | None:
    with db.cursor() as cur:
        row = cur.execute("SELECT id FROM plans WHERE user_id=? ORDER BY week_start DESC, id DESC LIMIT 1",
                          (user_id,)).fetchone()
    return row["id"] if row else None


def _open_delivery_places(plan_id: int) -> set[str]:
    from ..kernel import scheduler
    from ..kernel.optimizer import now
    at = now()
    return {d.get("restaurant_name") for d in models.decisions_for_plan(plan_id)
            if d["chosen_kind"] == "delivery" and d["session_status"] == "active"
            and not scheduler.is_past(d, at) and d.get("restaurant_name")}


def revalidate(user_id: int, before: dict) -> dict | None:
    """Re-check the week for the address now chosen. None when nothing needed doing."""
    from .. import service
    from ..integrations.swiggy_connect import SwiggyError
    current = live_catalog._address_id(user_id)
    if not before.get("had_live") or current == before.get("address_id"):
        return None
    plan_id = _latest_plan(user_id)
    if plan_id is None:
        return None
    was = _open_delivery_places(plan_id)
    skipped, failed = [], None
    try:
        skipped = live_catalog.refresh(user_id).get("skipped") or []
    except SwiggyError as exc:
        if exc.code in ("swiggy_not_connected", "swiggy_auth_expired"):
            raise
        failed = str(exc)
    except Exception:                       # noqa: BLE001 - the address change itself succeeded
        logging.getLogger(__name__).exception("Re-reading menus after an address change failed")
        failed = "unexpected"
    service.reoptimize(plan_id)           # stale picks from the old address can't stay planned
    now_planned = _open_delivery_places(plan_id)
    dropped = sorted(was - now_planned)
    far = {s["name"] for s in skipped}
    if failed:
        note = ("Couldn't read menus for the new address yet, so restaurant meals from the old address "
                "were taken out of your week. Tap Plan again in a moment.")
    elif dropped:
        names = ", ".join(dropped)
        one = len(dropped) == 1
        if set(dropped) <= far:
            why = "doesn't deliver to" if one else "don't deliver to"
        else:
            why = "isn't on Swiggy's list for" if one else "aren't on Swiggy's list for"
        note = f"{names} {why} your new address, so {'it was' if one else 'they were'} taken out of your week."
    else:
        note = "Your week still works for the new address."
    return {"plan_id": plan_id, "dropped": dropped, "not_delivering": sorted(far), "note": note}
