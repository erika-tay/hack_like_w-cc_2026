"""Checks for the board, API, Ask box, brief and next actions, using the scripted (test-only) AI.
Run from the backend folder:

    python tests/test_app.py

The numbers asserted here come from tests/scripted_data.py, a hand-written stand-in for the model.
They prove the code around the model is right (dates, flags, citation checks, fallbacks). They say
nothing about how good the real model is: that is what eval_extraction.py measures.
"""
import contextlib
import io
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
_tmp = Path(tempfile.mkdtemp())
os.environ.update({"HANDOFF_DB": str(_tmp / "t.db"), "LLM_CACHE_DB": str(_tmp / "c.db"), "LLM_CACHE": "0",
                   "EMBEDDER": "hash", "HANDOFF_AS_OF": "2026-10-17"})

from fastapi.testclient import TestClient  # noqa: E402

import seed  # noqa: E402
from ai import llm  # noqa: E402
from ai.extract import extract_pending  # noqa: E402
from db import get_conn  # noqa: E402
from main import app  # noqa: E402
from scripted_data import RichProvider  # noqa: E402


def setup(extract=True):
    with contextlib.redirect_stdout(io.StringIO()):
        seed.main()
    provider = RichProvider()
    llm.set_enabled(True)
    llm.set_provider(provider)
    results = extract_pending(get_conn(), use_cache=False) if extract else []
    return provider, results


def by_summary(items, needle):
    return next(i for i in items if needle in i["summary"])


# ---------------------------------------------------------------- tests

def test_scripted_extraction_is_clean():
    _, results = setup()
    assert all(r["status"] == "done" for r in results)
    assert sum(len(r["rejected"]) for r in results) == 0                     # every scripted quote is real
    counts = dict(get_conn().execute("SELECT item_type, count(*) FROM items GROUP BY item_type").fetchall())
    assert counts == {"commitment": 9, "decision": 3, "risk": 8, "opportunity": 2}


def test_board_flags():
    setup()
    with TestClient(app) as c:
        b = c.get("/api/board").json()
        assert b["counts"] == {"open_commitments": 6, "overdue": 3, "unowned": 1, "slipped": 2, "high_risks": 3, "done": 3}
        assert b["client"]["days_to_go_live"] == 16                         # Oct 17 -> Nov 2
        client_col = [i["summary"] for i in b["columns"]["client"]]
        assert client_col[0].startswith("Priya Raman: confirm final Pennsylvania")  # 3 days overdue sorts above 1 day overdue
        assert [i["summary"] for i in b["columns"]["unassigned"]] == ["Check whether the timeclock vendor offers an API for time import"]

        data = by_summary(b["columns"]["client"], "employee data file")
        assert data["overdue"] and data["days_overdue"] == 1
        assert data["due_history"] == ["2026-09-22", "2026-10-06", "2026-10-08", "2026-10-16"] and data["slips"] == 3
        assert len(data["sources"]) >= 5 and all(s["quote"] for s in data["sources"])

        tax = by_summary(b["columns"]["adp"], "tax configuration")
        assert tax["overdue"] and tax["days_overdue"] == 8 and tax["slips"] == 1
        parallel = by_summary(b["columns"]["adp"], "parallel")
        assert not parallel["overdue"] and not parallel["due_soon"]         # Oct 26 is 9 days out


def test_client_view_hides_internal_pages():
    setup()
    with TestClient(app) as c:
        assert "high_risks" not in c.get("/api/board?view=client").json()["counts"]
        for path in ("/api/risks", "/api/opportunities", "/api/timeline", "/api/brief", "/api/next-actions"):
            assert c.get(path + "?view=client").status_code == 403, path
        assert c.post("/api/ask", json={"question": "anything", "view": "client"}).status_code == 403
        assert c.get("/api/risks").status_code == 200 and c.get("/api/decisions").status_code == 200


def test_risks_decisions_timeline_documents():
    setup()
    with TestClient(app) as c:
        risks = c.get("/api/risks").json()
        assert [r["severity"] for r in risks] == ["high"] * 3 + ["medium"] * 4 + ["low"]    # worst first
        assert len(c.get("/api/decisions").json()) == 3

        since = c.get("/api/timeline?since=2026-10-08").json()
        assert since and all(e["date"] >= "2026-10-08" for e in since)
        assert any("Due date moved Oct 8 → Oct 16" in e["text"] for e in since)
        assert any("Marked done" in e["text"] for e in since)

        docs = c.get("/api/documents").json()
        assert len(docs) == 7 and all(d["ai_status"] == "done" for d in docs)
        red = c.get(f"/api/documents/{docs[3]['id']}/redaction").json()     # the early chat log, with the pasted record
        assert "123-45-6789" in red["before"] and "123-45-6789" not in red["after"] and red["spans"]
        assert c.get("/api/documents/999/redaction").status_code == 404


def test_ask_checks_citations():
    provider, _ = setup()
    with TestClient(app) as c:
        a = c.post("/api/ask", json={"question": "Why did the employee data file slip, and is it overdue?"}).json()
        assert a["mode"] == "ai"
        assert len(a["citations"]) == 1 and "October 16" in a["citations"][0]["quote"]    # the made-up quote was dropped
        assert "[#9999]" not in a["answer"] and len(a["item_refs"]) == 1                  # invented item reference removed
        assert a["unverified"] is False
        # The model was handed code-computed dates, not left to work them out.
        assert "OVERDUE by 1 day(s)" in provider.last_user
        assert "due-date history: Sep 22 -> Oct 6 -> Oct 8 -> Oct 16" in provider.last_user
        assert "TODAY: 2026-10-17 (Saturday)" in provider.last_user
        assert c.post("/api/ask", json={"question": "   "}).status_code == 422


def test_ask_without_ai():
    setup()
    llm.set_enabled(False)
    with TestClient(app) as c:
        a = c.post("/api/ask", json={"question": "timeclock vendor API"}).json()
        assert a["mode"] == "keyword" and a["answer"] is None and "switched off" in a["notice"].lower()
        assert a["hits"] and any("timeclock" in h["snippet"].lower() for h in a["hits"])
        assert any("timeclock" in i["summary"].lower() for i in a["items"])
    llm.set_enabled(True)


def test_brief_and_actions_with_ai_are_validated():
    setup()
    with TestClient(app) as c:
        brief = c.get("/api/brief").json()
        assert brief["mode"] == "ai" and len(brief["sections"]) == 1                      # empty section and empty bullet dropped
        assert len(brief["sections"][0]["bullets"]) == 1 and 9999 not in brief["sections"][0]["bullets"][0]["item_ids"]
        actions = c.get("/api/next-actions").json()
        assert actions["mode"] == "ai" and 9999 not in actions["actions"][0]["item_ids"]


def test_brief_and_actions_without_ai():
    setup()
    llm.set_enabled(False)
    with TestClient(app) as c:
        brief = c.get("/api/brief").json()
        assert brief["mode"] == "rules" and "switched off" in brief["notice"].lower()
        titles = [s["title"] for s in brief["sections"]]
        assert {"At risk", "What ADP owes", "What the client owes", "Nobody owns these", "Decisions already made"} <= set(titles)
        assert "3 overdue" in brief["summary"] and "1 with no owner" in brief["summary"]
        assert "days away" in brief["summary"]

        actions = c.get("/api/next-actions").json()
        assert actions["mode"] == "rules" and len(actions["actions"]) == 3
        first = actions["actions"][0]
        assert first["action"] == "Follow up with Tomás Rivera on: finish state and local tax configuration"   # no repeated name
        assert first["why"] == "8 day(s) overdue (was due Oct 9); the date has moved 1 time(s)."               # most overdue first, "Oct" keeps its capital
        assert "timeclock" in actions["actions"][2]["action"].lower() and actions["actions"][2]["action"].startswith("Assign an owner")
        assert all(a["urgency"] == "today" for a in actions["actions"])
    llm.set_enabled(True)


def test_upload_while_ai_is_down_then_recover():
    setup()
    with TestClient(app) as c:
        assert c.post("/api/ai", json={"enabled": False}).json() == {"enabled": False}
        body = {"title": "Quick call with Greg", "doc_type": "meeting_transcript", "occurred_at": "2026-10-17 09:00",
                "text": "Greg Hallman: I'll send the timeclock vendor contact by October 20.\nDana Whitfield: Great, thanks.\n"
                        "SSN 123-45-6789 for Jane Doe was in the export."}
        up = c.post("/api/documents", json=body).json()
        assert up["extraction"]["status"] == "pending_ai" and up["backup_items"] >= 1
        assert up["ingest"]["redactions"].get("SSN") == 1                                  # ingest always works
        assert c.get("/api/status").json()["pending_docs"] == 1

        conn = get_conn()
        backup = conn.execute("SELECT summary, due_date, owner_id, side, confidence FROM items WHERE extracted_by = 'rules'").fetchall()
        assert any(r["due_date"] == "2026-10-20" and r["owner_id"] and r["side"] == "client" for r in backup)   # speaker = owner, even without a timestamp
        assert all(r["confidence"] < 0.5 for r in backup)

        assert c.post("/api/ai", json={"enabled": True}).json() == {"enabled": True}
        done = c.post("/api/process").json()
        assert done["still_pending"] == 0 and done["results"][0]["status"] == "done"
        assert conn.execute("SELECT count(*) FROM items WHERE extracted_by = 'rules'").fetchone()[0] == 0   # replaced by the AI's pass
        assert c.get("/api/status").json()["pending_docs"] == 0

        assert c.post("/api/documents", json={**body, "doc_type": "bogus"}).status_code == 422
        assert c.post("/api/documents", json={**body, "occurred_at": "yesterday"}).status_code == 422
        assert c.post("/api/documents", json={**body, "text": "x" * 70_000}).status_code == 413


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
