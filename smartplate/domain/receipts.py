"""§5.3.14 — Receipt / expense surface.

Auto-tag meals as business vs personal and export CSV. Niche but extremely sticky
for users who claim meals as expenses. Tagging is heuristic (lunch on weekdays is
more likely business) and fully user-overridable.
"""
import csv
import datetime as dt
import io

from .. import db


def autocategory(day: int, meal: str) -> str:
    # Weekday lunches are the common business-expense case; everything else personal.
    return "business" if (meal == "lunch" and day < 5) else "personal"


def record(user_id: int, decision: dict, iso_date: str) -> int:
    cat = autocategory(decision["day"], decision["meal"])
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO receipts(user_id, decision_id, amount, category, note, iso_date) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, decision.get("id"), decision.get("cost", 0), cat,
             decision.get("item_name", ""), iso_date),
        )
        return cur.lastrowid


def list_for(user_id: int) -> list[dict]:
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM receipts WHERE user_id=? ORDER BY iso_date, id", (user_id,)
        ).fetchall()
    return [db.row_to_dict(r) for r in rows]


def set_category(receipt_id: int, category: str) -> None:
    with db.cursor() as cur:
        cur.execute("UPDATE receipts SET category=? WHERE id=?", (category, receipt_id))


def export_csv(user_id: int) -> str:
    rows = list_for(user_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "category", "amount", "note"])
    for r in rows:
        w.writerow([r["iso_date"], r["category"], f'{r["amount"]:.2f}', r["note"]])
    w.writerow([])
    business = sum(r["amount"] for r in rows if r["category"] == "business")
    w.writerow(["", "business total", f"{business:.2f}", ""])
    return buf.getvalue()
