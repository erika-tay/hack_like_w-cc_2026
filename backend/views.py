"""Read-only queries behind every screen. Plain SQL and date math: no AI anywhere in this file.

Everything the commitments board shows (overdue, unowned, "date moved twice") is computed here from
the items table and its change history, so it keeps working when the AI is down.

"As of" is the date we judge overdue against. It defaults to today; set HANDOFF_AS_OF=2026-10-17
to pin it (useful if you record the demo video on a different day than the sample data assumes).
"""
import os
from datetime import date, datetime

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, None: 3}


def as_of_date() -> date:
    raw = os.environ.get("HANDOFF_AS_OF")
    return datetime.strptime(raw, "%Y-%m-%d").date() if raw else date.today()


def _parse(s) -> date | None:
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date() if s else None
    except ValueError:
        return None


def pretty_date(s) -> str:
    d = _parse(s)
    return f"{d:%b} {d.day}" if d else "no date"


def _sources_for(conn, ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {i: [] for i in ids}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    for r in conn.execute(
            f"SELECT s.item_id, s.quote, d.id AS doc_id, d.title, d.doc_type, d.occurred_at "
            f"FROM item_sources s JOIN documents d ON d.id = s.document_id WHERE s.item_id IN ({marks}) "
            f"ORDER BY d.occurred_at, s.id", ids):
        out[r["item_id"]].append({"doc_id": r["doc_id"], "title": r["title"], "doc_type": r["doc_type"],
                                  "date": (r["occurred_at"] or "")[:10], "quote": r["quote"]})
    return out


def _due_history_for(conn, ids: list[int]) -> dict[int, list[str]]:
    """The sequence of due dates an item has had, oldest first, e.g. ['2026-09-22', '2026-10-06', '2026-10-16']."""
    out: dict[int, list[str]] = {i: [] for i in ids}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    for r in conn.execute(
            f"SELECT item_id, old_value, new_value FROM item_events WHERE field = 'due_date' "
            f"AND item_id IN ({marks}) ORDER BY occurred_at, id", ids):
        if not out[r["item_id"]] and r["old_value"]:
            out[r["item_id"]].append(r["old_value"])
        out[r["item_id"]].append(r["new_value"])
    return out


def load_items(conn, client_id: int, as_of: date, types: tuple[str, ...] | None = None) -> list[dict]:
    sql = ("SELECT i.*, p.name AS owner_name, p.role AS owner_role FROM items i "
           "LEFT JOIN people p ON p.id = i.owner_id WHERE i.client_id = ?")
    params: list = [client_id]
    if types:
        sql += f" AND i.item_type IN ({','.join('?' * len(types))})"
        params += list(types)
    rows = [dict(r) for r in conn.execute(sql + " ORDER BY i.id", params)]
    ids = [r["id"] for r in rows]
    sources, history = _sources_for(conn, ids), _due_history_for(conn, ids)

    for r in rows:
        due, is_open = _parse(r["due_date"]), r["status"] != "done"
        r["owner"] = ({"name": r["owner_name"], "role": r["owner_role"], "side": r["side"]}
                      if r["owner_id"] else None)
        r["sources"] = sources[r["id"]]
        r["due_history"] = history[r["id"]]
        r["slips"] = max(len(history[r["id"]]) - 1, 0)
        r["overdue"] = bool(is_open and due and due < as_of and r["item_type"] == "commitment")
        r["days_overdue"] = (as_of - due).days if r["overdue"] else 0
        r["due_soon"] = bool(is_open and due and 0 <= (due - as_of).days <= 7 and r["item_type"] == "commitment")
        r["unowned"] = bool(is_open and r["item_type"] == "commitment" and not r["owner_id"])
        r["first_seen"] = min((s["date"] for s in sources[r["id"]]), default=None)
        for k in ("owner_name", "owner_role"):
            del r[k]
    return rows


def _client(conn, client_id: int, as_of: date) -> dict:
    c = dict(conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone())
    go = _parse(c["go_live"])
    c["days_to_go_live"] = (go - as_of).days if go else None
    return c


def _commitment_order(it: dict):
    return (0 if it["overdue"] else 1, -it["days_overdue"], it["due_date"] or "9999-99-99", it["id"])


def board(conn, client_id: int, as_of: date) -> dict:
    items = load_items(conn, client_id, as_of, ("commitment",))
    open_items = sorted((i for i in items if i["status"] != "done"), key=_commitment_order)
    columns = {"adp": [], "client": [], "unassigned": []}
    for it in open_items:
        columns[it["side"] if it["side"] in ("adp", "client") else "unassigned"].append(it)
    risks = [r for r in load_items(conn, client_id, as_of, ("risk",)) if r["status"] != "done"]
    return {
        "client": _client(conn, client_id, as_of),
        "as_of": as_of.isoformat(),
        "counts": {
            "open_commitments": len(open_items),
            "overdue": sum(i["overdue"] for i in open_items),
            "unowned": sum(i["unowned"] for i in open_items),
            "slipped": sum(1 for i in open_items if i["slips"] > 0),
            "high_risks": sum(1 for r in risks if r["severity"] == "high"),
            "done": len(items) - len(open_items),
        },
        "columns": columns,
        "done": sorted((i for i in items if i["status"] == "done"), key=lambda i: i["due_date"] or ""),
    }


def risks(conn, client_id: int, as_of: date) -> list[dict]:
    rows = load_items(conn, client_id, as_of, ("risk",))
    return sorted(rows, key=lambda r: (SEVERITY_ORDER.get(r["severity"], 3), -len(r["sources"]), r["id"]))


def decisions(conn, client_id: int, as_of: date) -> list[dict]:
    return sorted(load_items(conn, client_id, as_of, ("decision",)), key=lambda r: (r["first_seen"] or "", r["id"]))


def opportunities(conn, client_id: int, as_of: date) -> list[dict]:
    return sorted(load_items(conn, client_id, as_of, ("opportunity",)), key=lambda r: (r["first_seen"] or "", r["id"]))


def timeline(conn, client_id: int, since: str | None = None) -> list[dict]:
    """What changed, newest first: new items and moved dates / status / owners / severity."""
    out = []
    for r in conn.execute(
            "SELECT e.*, i.item_type, i.summary, d.title AS doc_title, d.doc_type FROM item_events e "
            "JOIN items i ON i.id = e.item_id JOIN documents d ON d.id = e.document_id "
            "WHERE i.client_id = ? ORDER BY e.occurred_at DESC, e.id DESC", (client_id,)):
        when = (r["occurred_at"] or "")[:10]
        if since and when < since:
            continue
        if r["kind"] == "created":
            text = f"New {r['item_type']}: {r['summary']}"
        elif r["field"] == "due_date":
            text = f"Due date moved {pretty_date(r['old_value'])} → {pretty_date(r['new_value'])}: {r['summary']}"
        elif r["field"] == "status":
            text = f"Marked {r['new_value']}: {r['summary']}"
        elif r["field"] == "owner":
            text = f"Owner {r['old_value'] or 'none'} → {r['new_value']}: {r['summary']}"
        else:
            text = f"{r['field'].capitalize()} {r['old_value'] or 'unset'} → {r['new_value']}: {r['summary']}"
        out.append({"item_id": r["item_id"], "item_type": r["item_type"], "kind": r["kind"], "field": r["field"],
                    "date": when, "text": text, "doc_id": r["document_id"], "doc_title": r["doc_title"]})
    return out


def documents(conn, client_id: int) -> list[dict]:
    return [dict(r) | {"date": (r["occurred_at"] or "")[:10]} for r in conn.execute(
        "SELECT d.id, d.title, d.doc_type, d.occurred_at, d.ai_status, "
        "(SELECT count(*) FROM chunks c WHERE c.document_id = d.id) AS chunks, "
        "(SELECT count(DISTINCT s.item_id) FROM item_sources s WHERE s.document_id = d.id) AS items "
        "FROM documents d WHERE d.client_id = ? ORDER BY d.occurred_at, d.id", (client_id,))]


def people(conn, client_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, name, side, role FROM people WHERE client_id = ? ORDER BY side, name", (client_id,))]
