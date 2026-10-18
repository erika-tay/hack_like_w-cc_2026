"""Checks for extraction, using a scripted stand-in for the AI (no key, no network).
Run from the backend folder:

    python tests/test_extract.py

These tests prove the plumbing: quote checking, owner matching, updates across documents,
the AI-down behavior, and caching. They cannot prove the real model extracts well. That is
what eval_extraction.py (against the answer key) is for, once your Gemini key is set up.
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = Path(tempfile.mkdtemp())
os.environ["HANDOFF_DB"] = str(_tmp / "test.db")        # never touch the real database or cache
os.environ["LLM_CACHE_DB"] = str(_tmp / "cache.db")
os.environ["LLM_CACHE"] = "1"
os.environ["EMBEDDER"] = "hash"

import seed  # noqa: E402
from ai import llm  # noqa: E402
from ai.extract import (_norm, build_people_index, extract_pending, resolve_person,  # noqa: E402
                        verify_quote)
from ai.prompts import build_user_prompt  # noqa: E402
from db import get_conn  # noqa: E402


class ScriptedProvider:
    name = "scripted"

    def __init__(self, responder):
        self.responder, self.calls = responder, 0

    def generate(self, system, user, schema, model):
        self.calls += 1
        return json.dumps(self.responder(user))


def _item_id(prompt: str, needle: str):
    m = re.search(rf"^#(\d+) \|.*{needle}", prompt, re.M | re.I)
    return int(m.group(1)) if m else None


def responder(prompt: str) -> dict:
    if "Title: Riverbend Health - Implementation Kickoff" in prompt:
        return {"operations": [
            {"op": "create", "item_type": "commitment", "summary": "Priya Raman: deliver the employee data file",
             "owner": "Priya", "due_date": "2026-09-22", "quote": "We can have it to you by September 22."},
            {"op": "create", "item_type": "commitment", "summary": "Check whether the timeclock vendor offers an API",
             "quote": "Someone should check with the vendor."},
            {"op": "create", "item_type": "decision", "summary": "Biweekly pay with Friday pay dates",
             "rationale": "Keeps the current schedule", "quote": "So, biweekly with Friday pay dates."},
            # A made-up claim: the quote is nowhere in the document, so it must be rejected.
            {"op": "create", "item_type": "commitment", "summary": "Dana will buy lunch", "owner": "Dana",
             "quote": "Dana promised to buy everyone lunch."},
        ]}
    if "Title: Weekly Status Meeting (Oct 8)" in prompt:
        data_id = _item_id(prompt, "employee data file")
        return {"operations": [
            {"op": "update", "item_id": data_id, "due_date": "2026-10-16",
             "quote": "I can get it to you by Friday, October 16."},
            {"op": "mention", "item_id": data_id,
             "quote": "That's about ten days later than the October 6 date from Slack."},
            # Points at an item that doesn't exist, so it must be rejected.
            {"op": "update", "item_id": 9999, "status": "done", "quote": "Great. Thanks, everyone."},
        ]}
    return {"operations": []}


def fresh():
    with contextlib.redirect_stdout(io.StringIO()):
        seed.main()
    llm.set_enabled(True)
    provider = ScriptedProvider(responder)
    llm.set_provider(provider)
    return get_conn(), provider


# ---------------------------------------------------------------- tests

def test_prompt_contents():
    conn, _ = fresh()
    doc = dict(conn.execute("SELECT * FROM documents WHERE title LIKE '%Oct 8%'").fetchone())
    client = dict(conn.execute("SELECT * FROM clients").fetchone())
    people = [dict(r) for r in conn.execute("SELECT * FROM people")]
    prompt = build_user_prompt(client, people, doc, [])
    assert "Thursday, 2026-10-08 14:00" in prompt           # weekday helps with "by Friday"
    assert "(none yet)" in prompt
    assert "also written as: Dana, Dana W., D. Whitfield" in prompt
    assert "@" not in prompt                                 # emails stay out of the prompt

    item = {"id": 4, "item_type": "risk", "severity": "high", "status": "open", "due_date": None,
            "owner": None, "summary": "Data file is late"}
    assert "#4 | risk/high | open | no date | unowned | Data file is late" in build_user_prompt(client, people, doc, [item])


def test_verify_quote():
    text = "[00:05:52] Priya Raman: We can have it to you by September 22.\n[00:06:00] Dana: Great."
    assert verify_quote("we can have it to you by   September 22.", text)          # whitespace and case don't matter
    assert verify_quote("We can have it to you by September 23.", text) == \
        "[00:05:52] Priya Raman: We can have it to you by September 22."          # lightly altered: snaps to the real line
    assert verify_quote("Dana promised to buy everyone lunch.", text) is None      # made up


def test_resolve_person():
    conn, _ = fresh()
    index = build_people_index([dict(r) for r in conn.execute("SELECT * FROM people")])
    assert resolve_person(index, "Priya")["name"] == "Priya Raman"
    assert resolve_person(index, "Marcus L.")["name"] == "Marcus Lee"
    assert resolve_person(index, "praman@riverbendhealth.example")["name"] == "Priya Raman"
    assert resolve_person(index, "Dana Whitfield (ADP)")["name"] == "Dana Whitfield"
    assert resolve_person(index, "the finance team") is None
    assert resolve_person(index, None) is None


def test_full_flow():
    conn, provider = fresh()
    results = extract_pending(conn, use_cache=False)
    assert len(results) == 7 and all(r["status"] == "done" for r in results)
    assert provider.calls == 7

    assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 3     # the made-up one was dropped
    assert sum(len(r["rejected"]) for r in results) == 2                      # made-up quote + unknown item id

    data = conn.execute("SELECT * FROM items WHERE summary LIKE '%employee data file%'").fetchone()
    assert data["due_date"] == "2026-10-16"                                   # moved by the Oct 8 meeting
    assert data["side"] == "client" and data["owner_id"] is not None          # side comes from the owner
    sources = conn.execute("SELECT count(*) FROM item_sources WHERE item_id = ?", (data["id"],)).fetchone()[0]
    assert sources == 3                                                       # created + updated + mentioned
    events = conn.execute("SELECT kind, field, old_value, new_value FROM item_events WHERE item_id = ? ORDER BY id",
                          (data["id"],)).fetchall()
    assert [e["kind"] for e in events] == ["created", "changed"]              # change history is recorded
    assert (events[1]["field"], events[1]["old_value"], events[1]["new_value"]) == ("due_date", "2026-09-22", "2026-10-16")

    timeclock = conn.execute("SELECT * FROM items WHERE summary LIKE '%timeclock%'").fetchone()
    assert timeclock["owner_id"] is None and timeclock["side"] is None        # nobody took it: stays unowned

    # The board's two flags, straight from SQL.
    overdue = [r["summary"] for r in conn.execute(
        "SELECT summary FROM items WHERE item_type='commitment' AND status='open' AND due_date < '2026-10-17'")]
    unowned = [r["summary"] for r in conn.execute(
        "SELECT summary FROM items WHERE item_type='commitment' AND status='open' AND owner_id IS NULL")]
    assert len(overdue) == 1 and "employee data file" in overdue[0]
    assert len(unowned) == 1 and "timeclock" in unowned[0]

    # No citation without a real quote behind it.
    for row in conn.execute("SELECT s.quote, d.clean_text FROM item_sources s JOIN documents d ON d.id = s.document_id"):
        assert _norm(row["quote"]) in _norm(row["clean_text"])

    assert extract_pending(conn, use_cache=False) == []                       # nothing left, nothing repeated


def test_ai_down_then_back():
    conn, provider = fresh()
    llm.set_enabled(False)
    results = extract_pending(conn, use_cache=False)
    assert len(results) == 1 and results[0]["status"] == "pending_ai"         # stopped at the first outage
    assert provider.calls == 0                                                # never even called
    assert conn.execute("SELECT count(*) FROM documents WHERE ai_status = 'pending_ai'").fetchone()[0] == 7
    assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 0

    llm.set_enabled(True)                                                     # AI comes back: the queue drains
    results = extract_pending(conn, use_cache=False)
    assert len(results) == 7 and all(r["status"] == "done" for r in results)


def test_provider_failures():
    class Raises:
        name = "raises"

        def __init__(self, exc):
            self.exc = exc

        def generate(self, *args):
            raise self.exc

    conn, _ = fresh()
    llm.set_provider(Raises(llm.LLMUnavailable("no key")))
    assert extract_pending(conn, use_cache=False)[0]["status"] == "pending_ai"   # outage: stays queued

    llm.set_provider(Raises(llm.LLMError("bad request")))
    results = extract_pending(conn, use_cache=False)
    # A broken request fails each document on its own (one bad document must not block the rest)...
    assert len(results) == 7 and all(r["status"] == "failed" for r in results)
    assert conn.execute("SELECT count(*) FROM documents WHERE ai_status = 'failed'").fetchone()[0] == 7
    # ...and failed documents are not silently retried. That takes a deliberate --reset.
    assert extract_pending(conn, use_cache=False) == []


def test_gemini_error_translation():
    import httpx
    from google.genai import errors

    def body(msg):
        return {"error": {"message": msg}}

    bad_key = llm._translate(errors.ClientError(401, body("bad key"), None))
    assert isinstance(bad_key, llm.LLMUnavailable) and not bad_key.transient   # retrying won't fix a bad key

    quota = llm._translate(errors.ClientError(429, body("quota"), None))
    assert isinstance(quota, llm.LLMUnavailable) and quota.transient           # worth a retry

    outage = llm._translate(errors.ServerError(503, body("overloaded"), None))
    assert isinstance(outage, llm.LLMUnavailable) and outage.transient

    assert isinstance(llm._translate(errors.ClientError(400, body("bad request"), None)), llm.LLMError)
    assert llm._translate(httpx.ConnectError("network down")).transient
    assert llm._translate(ValueError("a real bug")) is None                    # our own bugs are never disguised


def test_transient_errors_are_retried():
    class Flaky:
        name = "flaky"
        calls = 0

        def generate(self, *args):
            Flaky.calls += 1
            if Flaky.calls <= 2:
                raise llm.LLMUnavailable("HTTP 503", transient=True)
            return json.dumps({"ok": True})

    real_sleep, llm.time.sleep = llm.time.sleep, lambda s: None   # don't actually wait
    try:
        llm.set_provider(Flaky())
        assert llm.generate_json("sys", "retry test", {"type": "object"}, use_cache=False) == {"ok": True}
        assert Flaky.calls == 3
    finally:
        llm.time.sleep = real_sleep


def test_cache_and_bad_json():
    provider = ScriptedProvider(lambda prompt: {"hello": "world"})
    llm.set_enabled(True)
    llm.set_provider(provider)
    before = llm.stats()["cache_hits"]
    assert llm.generate_json("sys", "cache test prompt", {"type": "object"}) == {"hello": "world"}
    assert llm.generate_json("sys", "cache test prompt", {"type": "object"}) == {"hello": "world"}
    assert provider.calls == 1 and llm.stats()["cache_hits"] == before + 1   # second call was free

    class NotJson:
        name = "notjson"
        calls = 0

        def generate(self, *args):
            NotJson.calls += 1
            return "this is not json"

    llm.set_provider(NotJson())
    for _ in range(2):
        try:
            llm.generate_json("sys", "bad json prompt", {"type": "object"})
            raise AssertionError("expected LLMError")
        except llm.LLMError:
            pass
    assert NotJson.calls == 2                                                  # a bad reply is never cached


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
