"""Backup extraction that works with NO AI: regular expressions over the redacted text.

When the LLM is unavailable, a document is still ingested, searchable, and gets a few
low-confidence items from simple patterns:
  * "I'll / we can ... by September 22" style sentences  -> commitments (speaker is the owner)
  * milestone bullets like "- Sep 22: Data file delivered (Priya's team)" -> commitments
  * a few warning phrases ("losing confidence", "third time", "escalate") -> medium risks

These are stored with extracted_by = 'rules' and confidence about 0.4, and the UI labels them
"backup". The document stays 'pending_ai', so when the AI returns, the real extraction replaces
them (see drop_rule_items_for_document in extract.py).

It will miss a lot and it can't tell done from open. That is the point: degraded, honest, still useful.
"""
import re
from datetime import datetime

from ai.extract import build_people_index, resolve_person

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DATE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", re.I)
_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_PROMISE = re.compile(
    r"\b(i'll|i will|we'll|we will|can have it|can get it|will have|will send|will be ready|"
    r"can finish|will review|i can get|have it to you|should have it)\b", re.I)
_BULLET = re.compile(r"^\s*[-*]\s*((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2})\s*:\s*(.+)$", re.I)
_SPEAKER = re.compile(r"^(?:\[[^\]]+\]\s*)?([^:\[\]]{2,40}):\s*(.+)$")   # "[00:05] Priya Raman: ..." or "Priya Raman: ..."
_RISK_WORDS = re.compile(
    r"\b(losing confidence|third time|escalate|still nothing|haven't heard|no update|behind schedule|"
    r"going to be late|is going to be late|delay(?:ed)?|slip(?:ped)?|not sure who|can't speak to)\b", re.I)
_SKIP = re.compile(r"^(Title|Type|Date|Attendees|From|To|Cc|Subject):", re.I)


def _to_iso(text: str, doc_year: int) -> str | None:
    m = _ISO.search(text)
    if m:
        return m.group(0)
    m = _DATE.search(text)
    if not m:
        return None
    try:
        return datetime(doc_year, _MONTHS[m.group(1).lower()[:3]], int(m.group(2))).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _email_sender(text: str) -> str | None:
    m = re.search(r"^From:\s*([^<\n]+)", text, re.M)
    return m.group(1).strip() if m else None


def _short(s: str, n: int = 110) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def extract_with_rules(conn, doc_id: int) -> dict:
    doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    people = [dict(r) for r in conn.execute("SELECT * FROM people WHERE client_id = ?", (doc["client_id"],))]
    index = build_people_index(people)
    year = int((doc["occurred_at"] or "2026")[:4])
    text = doc["clean_text"] or ""
    sender = resolve_person(index, _email_sender(doc["raw_text"])) if doc["doc_type"] == "email" else None

    # Re-running replaces this document's earlier placeholders instead of stacking duplicates.
    conn.execute("DELETE FROM items WHERE extracted_by = 'rules' AND id IN "
                 "(SELECT item_id FROM item_sources WHERE document_id = ?)", (doc_id,))

    made, seen, risks = 0, set(), 0
    for line in (l.strip() for l in text.split("\n")):
        if not line or _SKIP.match(line):
            continue
        owner, body = None, line
        m = _SPEAKER.match(line)
        speaker = resolve_person(index, m.group(1)) if m else None
        if speaker:                       # only a known team member counts as a speaker; "Note: ..." does not
            owner, body = speaker, m.group(2)
        elif doc["doc_type"] == "email":
            owner = sender

        found = None
        if (b := _BULLET.match(line)):                       # "- Sep 22: Data file delivered (Priya's team)"
            due, label = _to_iso(b.group(1), year), b.group(2)
            paren = re.search(r"\(([^)]+)\)\s*$", label)
            who = resolve_person(index, re.sub(r"'s team$", "", paren.group(1)).strip()) if paren else None
            found = ("commitment", who, due, re.sub(r"\s*\([^)]*\)\s*$", "", label))
        elif _PROMISE.search(body) and (due := _to_iso(body, year)):
            found = ("commitment", owner, due, body)

        if found:
            kind, who, due, label = found
            key = (who["id"] if who else None, due, label[:40].lower())
            if key in seen:
                continue
            seen.add(key)
            summary = f"{who['name']}: {_short(label)}" if who else _short(label)
            item_id = conn.execute(
                "INSERT INTO items (client_id, item_type, summary, owner_id, side, due_date, status, confidence, "
                "extracted_by) VALUES (?, 'commitment', ?, ?, ?, ?, 'open', 0.4, 'rules')",
                (doc["client_id"], summary, who["id"] if who else None, who["side"] if who else None, due)).lastrowid
            conn.execute("INSERT INTO item_sources (item_id, document_id, quote) VALUES (?, ?, ?)", (item_id, doc_id, line))
            conn.execute("INSERT INTO item_events (item_id, document_id, kind, new_value, occurred_at) "
                         "VALUES (?, ?, 'created', ?, ?)", (item_id, doc_id, summary, doc["occurred_at"]))
            made += 1
        elif risks < 3 and _RISK_WORDS.search(body):
            risks += 1
            who = owner.get("name") if owner else None
            summary = _short(f"Possible risk: {body}")
            item_id = conn.execute(
                "INSERT INTO items (client_id, item_type, summary, status, severity, confidence, extracted_by) "
                "VALUES (?, 'risk', ?, 'open', 'medium', 0.35, 'rules')", (doc["client_id"], summary)).lastrowid
            conn.execute("INSERT INTO item_sources (item_id, document_id, quote) VALUES (?, ?, ?)", (item_id, doc_id, line))
            conn.execute("INSERT INTO item_events (item_id, document_id, kind, new_value, occurred_at) "
                         "VALUES (?, ?, 'created', ?, ?)", (item_id, doc_id, summary, doc["occurred_at"]))
            made += 1
    conn.commit()
    return {"doc_id": doc_id, "items": made}


def extract_pending_with_rules(conn, client_id: int | None = None) -> list[dict]:
    sql, params = "SELECT id FROM documents WHERE ai_status = 'pending_ai'", []
    if client_id is not None:
        sql, params = sql + " AND client_id = ?", [client_id]
    return [extract_with_rules(conn, r["id"]) for r in conn.execute(sql + " ORDER BY occurred_at, id", params).fetchall()]
