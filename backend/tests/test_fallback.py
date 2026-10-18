"""Checks for the no-AI backup extractor and for snapshot save/load.  Run from the backend folder:

    python tests/test_fallback.py
"""
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import snapshot  # noqa: E402
from ai import llm  # noqa: E402
from ai.extract import extract_pending  # noqa: E402
from ai.fallback import extract_pending_with_rules, extract_with_rules  # noqa: E402
from db import get_conn  # noqa: E402
from test_extract import fresh  # noqa: E402  (also points the database and cache at temp files)


def rules_items(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM items WHERE extracted_by = 'rules'")]


def test_backup_extraction_finds_dated_promises():
    conn, _ = fresh()
    extract_pending_with_rules(conn)
    items = rules_items(conn)
    sep22 = [i for i in items if i["due_date"] == "2026-09-22" and "Priya" in i["summary"]]
    assert sep22 and sep22[0]["side"] == "client" and sep22[0]["owner_id"] is not None   # speaker became owner
    assert all(i["extracted_by"] == "rules" and i["confidence"] < 0.5 for i in items)    # labeled low confidence
    assert any(i["item_type"] == "risk" and "third time" in i["summary"] for i in items)
    # Every placeholder cites a real line, and none leaked raw personal data.
    for r in conn.execute("SELECT s.quote, d.clean_text FROM item_sources s JOIN documents d ON d.id = s.document_id "
                          "JOIN items i ON i.id = s.item_id WHERE i.extracted_by = 'rules'"):
        assert r["quote"] in r["clean_text"] and "123-45-6789" not in r["quote"]
    # Documents stay queued for the real AI.
    assert conn.execute("SELECT count(*) FROM documents WHERE ai_status = 'pending_ai'").fetchone()[0] == 7


def test_backup_extraction_is_idempotent():
    conn, _ = fresh()
    extract_pending_with_rules(conn)
    first = len(rules_items(conn))
    extract_pending_with_rules(conn)
    assert len(rules_items(conn)) == first                                                # re-running doesn't stack duplicates


def test_ai_replaces_backup_items():
    conn, _ = fresh()
    extract_pending_with_rules(conn)
    assert rules_items(conn)
    results = extract_pending(conn, use_cache=False)                                      # AI comes back
    assert all(r["status"] == "done" for r in results)
    assert rules_items(conn) == []                                                        # placeholders replaced
    assert conn.execute("SELECT count(*) FROM items WHERE extracted_by = 'llm'").fetchone()[0] == 3


def test_snapshot_roundtrip():
    conn, _ = fresh()
    extract_pending(conn, use_cache=False)
    before = {
        "items": [tuple(r) for r in conn.execute(
            "SELECT item_type, summary, due_date, status, side FROM items ORDER BY id")],
        "events": conn.execute("SELECT count(*) FROM item_events").fetchone()[0],
        "sources": conn.execute("SELECT count(*) FROM item_sources").fetchone()[0],
    }
    path = Path(tempfile.mkdtemp()) / "snap.json"
    assert snapshot.save(conn, path)["items"] == 3

    conn, _ = fresh()                                                                     # brand-new database, no items
    assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 0
    llm.set_enabled(False)                                                                # and no AI at all
    snapshot.load(conn, path)
    after = {
        "items": [tuple(r) for r in conn.execute(
            "SELECT item_type, summary, due_date, status, side FROM items ORDER BY id")],
        "events": conn.execute("SELECT count(*) FROM item_events").fetchone()[0],
        "sources": conn.execute("SELECT count(*) FROM item_sources").fetchone()[0],
    }
    assert after == before
    assert conn.execute("SELECT count(*) FROM documents WHERE ai_status != 'done'").fetchone()[0] == 0
    llm.set_enabled(True)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted((n, f) for n, f in globals().items() if n.startswith("test_")):
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failures += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    sys.exit(1 if failures else 0)
