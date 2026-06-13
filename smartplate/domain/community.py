"""§5.3.13 — Community plan templates.

Power users save Survival/Tight-Week plans; others browse and clone them.
A simple network-effect surface. Moderation hooks are left as TODOs for scale.
"""
import datetime as dt

from .. import db


def list_templates(city: str | None = None) -> list[dict]:
    with db.cursor() as cur:
        if city:
            rows = cur.execute(
                "SELECT * FROM community_templates WHERE city=? ORDER BY adopts DESC", (city,)
            ).fetchall()
        else:
            rows = cur.execute(
                "SELECT * FROM community_templates ORDER BY adopts DESC"
            ).fetchall()
    out = []
    for r in rows:
        d = db.row_to_dict(r)
        d["payload"] = db.jl(d["payload"], {})
        out.append(d)
    return out


def save_template(author: str, title: str, plan: dict, decisions: list[dict]) -> int:
    payload = {
        "sessions": [
            {"day": d["day"], "meal": d["meal"], "kind": d["chosen_kind"],
             "item": d.get("item_name"), "cost": d.get("cost", 0)}
            for d in decisions
        ],
        "saved": dt.date.today().isoformat(),
    }
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO community_templates(author, title, city, budget, mode, payload, adopts) "
            "VALUES (?,?,?,?,?,?,0)",
            (author, title, plan.get("city", ""), plan.get("budget", 0),
             plan.get("mode", "survival"), db.jd(payload)),
        )
        return cur.lastrowid


def adopt(template_id: int) -> dict | None:
    with db.cursor() as cur:
        cur.execute("UPDATE community_templates SET adopts = adopts + 1 WHERE id=?", (template_id,))
        row = cur.execute("SELECT * FROM community_templates WHERE id=?", (template_id,)).fetchone()
    if not row:
        return None
    d = db.row_to_dict(row)
    d["payload"] = db.jl(d["payload"], {})
    return d
