"""Turn tracked items into compact text lines the model can read. Shared by Ask and the briefs.

Overdue-ness and due-date history are worked out here in code, not left to the model, because
models are unreliable at date arithmetic and the app must agree with its own board.
"""
from datetime import date

import views


def item_line(it: dict) -> str:
    kind = it["item_type"] + (f"/{it['severity']}" if it.get("severity") else "")
    status = it["status"] + (f" OVERDUE by {it['days_overdue']} day(s)" if it["overdue"] else "")
    owner = it["owner"]["name"] if it["owner"] else ("unowned" if it["item_type"] == "commitment" else "-")
    parts = [f"#{it['id']}", kind, status, f"due {views.pretty_date(it['due_date'])}", owner, it["summary"]]
    if it["due_history"] and len(it["due_history"]) > 1:
        parts.append("due-date history: " + " -> ".join(views.pretty_date(d) for d in it["due_history"]))
    docs = sorted({s["doc_id"] for s in it["sources"]})
    if docs:
        parts.append("docs " + ",".join(str(d) for d in docs))
    return " | ".join(parts)


def all_items(conn, client_id: int, as_of: date) -> list[dict]:
    return views.load_items(conn, client_id, as_of)


def items_block(items: list[dict]) -> str:
    return "\n".join(item_line(i) for i in items) if items else "(none yet)"


def today_line(as_of: date) -> str:
    return f"TODAY: {as_of.isoformat()} ({as_of:%A})"
