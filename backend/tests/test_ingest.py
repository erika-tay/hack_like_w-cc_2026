"""Checks for the ingest pipeline. No pytest needed. Run from the backend folder:

    python tests/test_ingest.py
"""
import contextlib
import io
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["HANDOFF_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")  # never touch the real database

import seed  # noqa: E402
from db import ROOT, get_conn  # noqa: E402
from ingest.chunk_embed import chunk_text, keyword_search, semantic_search  # noqa: E402
from ingest.clean import clean_text  # noqa: E402
from ingest.pii import redact  # noqa: E402

SAMPLE = ROOT / "sample_data"
TEAM_EMAILS = ["praman@riverbendhealth.example", "dwhitfield@adp-team.example"]


def body_of(name: str) -> str:
    return seed.parse_doc(SAMPLE / name)[3]


def test_email_cleaning():
    cleaned = clean_text(body_of("02_email_implementation_plan.txt"), "email")
    assert "CONFIDENTIALITY" not in cleaned
    assert "Sent from my iPhone" not in cleaned
    assert "looking forward" not in cleaned  # quoted earlier message
    assert "Sep 22: Complete employee data file" in cleaned  # real content survives


def test_chat_reactions_become_tags():
    cleaned = clean_text(body_of("03_chat_early.txt"), "chat")
    assert "👍" not in cleaned and "🙏" not in cleaned
    assert "Marcus L.: [thumbs-up]" in cleaned


def test_employee_data_is_redacted():
    row = ("EMP-10482 | Jane Doe | SSN 123-45-6789 | DOB 04/12/1988 | Salary $84,500 | "
           "jane.doe@riverbendhealth.example | 614-555-0177")
    result = redact(row, allow_emails=TEAM_EMAILS)
    for secret in ["10482", "Jane", "123-45-6789", "04/12/1988", "84,500", "jane.doe@", "555-0177"]:
        assert secret not in result.text, f"{secret} leaked: {result.text}"
    assert set(result.counts) == {"EMP_ID", "NAME", "SSN", "DOB", "SALARY", "EMAIL", "PHONE"}


def test_team_and_business_data_is_kept():
    text = "Priya Raman <praman@riverbendhealth.example> approved the fixed fee of $62,500."
    result = redact(text, allow_emails=TEAM_EMAILS, allow_names=["Priya Raman"])
    assert result.text == text


def test_phone_numbers_redacted_but_dates_are_not():
    assert redact("Office: 555-0142").text == "Office: [PHONE]"
    line = "[2026-10-02 10:30] Greg H.: Bumping this again."
    assert redact(line).text == line


def test_chunks_keep_lines_whole():
    text = "\n".join(f"line {i} " + "x" * 100 for i in range(40))
    chunks = chunk_text(text, max_chars=700)
    assert len(chunks) > 1
    assert all(len(c) < 900 for c in chunks)
    joined = "\n".join(chunks)
    assert all(f"line {i} " in joined for i in range(40))  # nothing lost


def test_seeded_database():
    with contextlib.redirect_stdout(io.StringIO()):
        seed.main()
    conn = get_conn()

    # Raw text keeps the SSN (it's the original), but nothing downstream does.
    assert conn.execute("SELECT count(*) FROM documents WHERE raw_text LIKE '%123-45-6789%'").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM documents WHERE clean_text LIKE '%123-45-6789%'").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM chunks WHERE text LIKE '%123-45-6789%'").fetchone()[0] == 0

    # Ingest never marks anything as analyzed.
    assert conn.execute("SELECT count(*) FROM documents WHERE ai_status != 'pending_ai'").fetchone()[0] == 0

    keyword = keyword_search(conn, "timeclock vendor API", k=3)
    assert keyword and "timeclock" in keyword[0]["text"].lower()

    semantic = semantic_search(conn, "does the timeclock vendor have an API?", k=3)
    assert any("timeclock" in h["text"].lower() for h in semantic)

    stemmed = keyword_search(conn, "delayed", k=5)  # porter stemming: "delayed" should find "delay"/"delayed" text
    assert any("delay" in h["text"].lower() for h in stemmed)


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
