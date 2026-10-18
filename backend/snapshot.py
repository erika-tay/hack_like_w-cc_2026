"""Save and restore extraction results, so the app can run without calling any AI.

    python snapshot.py save       # after a good real extraction: writes sample_data/snapshot.json
    python snapshot.py load       # restores items, citations and history into a freshly seeded database

Why it exists:
  * Demo safety net. If the free quota runs out or the wifi fails, `load` brings back the exact
    results of your last real run in a second.
  * Judges. Commit snapshot.json, and anyone who clones the repo can run the app with no API key:
        python seed.py && python snapshot.py load && uvicorn main:app

The snapshot is the real output of the model from a real run. It is NOT hand-written.
"""
import json
import sys

from db import ROOT, get_conn

PATH = ROOT / "sample_data" / "snapshot.json"


def save(conn, path=PATH) -> dict:
    items = [dict(r) for r in conn.execute(
        "SELECT i.*, p.name AS owner_name FROM items i LEFT JOIN people p ON p.id = i.owner_id "
        "WHERE i.extracted_by = 'llm' ORDER BY i.id")]
    out = []
    for it in items:
        sources = [{"doc": r["title"], "quote": r["quote"]} for r in conn.execute(
            "SELECT d.title, s.quote FROM item_sources s JOIN documents d ON d.id = s.document_id "
            "WHERE s.item_id = ? ORDER BY s.id", (it["id"],))]
        events = [{"doc": r["title"], "kind": r["kind"], "field": r["field"], "old": r["old_value"],
                   "new": r["new_value"], "at": r["occurred_at"]} for r in conn.execute(
            "SELECT d.title, e.kind, e.field, e.old_value, e.new_value, e.occurred_at FROM item_events e "
            "JOIN documents d ON d.id = e.document_id WHERE e.item_id = ? ORDER BY e.id", (it["id"],))]
        out.append({k: it[k] for k in ("item_type", "summary", "owner_name", "side", "due_date", "status",
                                       "rationale", "severity", "confidence")} | {"sources": sources, "events": events})
    path.write_text(json.dumps({"items": out}, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"items": len(out), "path": str(path)}


def load(conn, path=PATH) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    client_id = conn.execute("SELECT id FROM clients ORDER BY id LIMIT 1").fetchone()["id"]
    docs = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM documents WHERE client_id = ?", (client_id,))}
    people = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM people WHERE client_id = ?", (client_id,))}

    conn.execute("DELETE FROM items WHERE client_id = ?", (client_id,))
    for it in data["items"]:
        item_id = conn.execute(
            "INSERT INTO items (client_id, item_type, summary, owner_id, side, due_date, status, rationale, severity, "
            "confidence, extracted_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'llm')",
            (client_id, it["item_type"], it["summary"], people.get(it["owner_name"]), it["side"], it["due_date"],
             it["status"], it["rationale"], it["severity"], it["confidence"])).lastrowid
        for s in it["sources"]:
            if s["doc"] in docs:
                conn.execute("INSERT INTO item_sources (item_id, document_id, quote) VALUES (?, ?, ?)",
                             (item_id, docs[s["doc"]], s["quote"]))
        for e in it["events"]:
            if e["doc"] in docs:
                conn.execute("INSERT INTO item_events (item_id, document_id, kind, field, old_value, new_value, occurred_at) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?)", (item_id, docs[e["doc"]], e["kind"], e["field"], e["old"], e["new"], e["at"]))
    conn.execute("UPDATE documents SET ai_status = 'done' WHERE client_id = ?", (client_id,))
    conn.commit()
    return {"items": len(data["items"])}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    conn = get_conn()
    if cmd == "save":
        print("Saved:", save(conn))
    elif cmd == "load":
        if not PATH.exists():
            sys.exit(f"No snapshot at {PATH}. Run a real extraction, then `python snapshot.py save`.")
        print("Loaded:", load(conn))
    else:
        sys.exit("usage: python snapshot.py save | load")
