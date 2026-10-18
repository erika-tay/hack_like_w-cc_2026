"""Extraction: read one document, update the tracked items, and cite a real quote for every claim.

Flow for each document, oldest first:
  1. Show the model the people, the items tracked so far, and the new (redacted) document.
  2. The model answers with operations: create / update / mention.
  3. We check every operation before storing it:
       - the quote must really appear in the document (no quote, no item),
       - the owner name is matched to a real person via their aliases,
       - an update or mention must point at an item that exists.
  4. The document is marked 'done'.

If the AI is unavailable the document stays 'pending_ai' and can be retried later.

Run it:   python -m ai.extract            (from the backend folder)
Start over: python -m ai.extract --reset
"""
import argparse
import difflib
import json
import os
import re
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ValidationError, field_validator

from ai import llm
from ai.prompts import EXTRACTION_SCHEMA, EXTRACTION_SYSTEM, build_user_prompt


class Operation(BaseModel):
    op: Literal["create", "update", "mention"]
    item_id: Optional[int] = None
    item_type: Optional[Literal["commitment", "decision", "risk", "opportunity"]] = None
    summary: Optional[str] = None
    owner: Optional[str] = None
    side: Optional[Literal["adp", "client"]] = None
    due_date: Optional[str] = None
    status: Optional[Literal["open", "done", "blocked"]] = None
    rationale: Optional[str] = None
    severity: Optional[Literal["low", "medium", "high"]] = None
    quote: str

    @field_validator("due_date")
    @classmethod
    def _real_date_or_none(cls, v):
        if not v:
            return None
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            return None  # a malformed date becomes "no date" rather than sinking the whole operation
        return v


# ---------------------------------------------------------------- checks on what the model says

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def verify_quote(quote: str, text: str) -> str | None:
    """Return a quote that really appears in `text`, or None.

    Exact (whitespace- and case-insensitive) matches pass. If the model altered a few words, we
    fall back to the closest real line, provided it is close enough. Otherwise the claim is dropped.
    """
    q = quote.strip().strip("\"'")
    if not q:
        return None
    if _norm(q) in _norm(text):
        return q
    best_line, best_ratio = None, 0.0
    for line in text.split("\n"):
        if line.strip():
            ratio = difflib.SequenceMatcher(None, _norm(q), _norm(line)).ratio()
            if ratio > best_ratio:
                best_line, best_ratio = line.strip(), ratio
    return best_line if best_ratio >= 0.6 else None


def build_people_index(people: list[dict]) -> dict[str, dict]:
    index = {}
    for p in people:
        for key in [p["name"], *json.loads(p["aliases"] or "[]")]:
            index[key.strip().lower()] = p
    return index


def resolve_person(index: dict[str, dict], name: str | None) -> dict | None:
    """Match 'Priya', 'P. Raman' or a handle to a person. Unknown or ambiguous names return None."""
    if not name or not name.strip():
        return None
    n = name.strip().lower()
    if n in index:
        return index[n]
    first = n.split()[0].strip(".,")
    if len(first) >= 3:
        candidates = {p["id"]: p for key, p in index.items()
                      if "@" not in key and key.split()[0].strip(".,") == first}
        if len(candidates) == 1:
            return next(iter(candidates.values()))
    return None


# ---------------------------------------------------------------- storing operations

def _add_source(conn, item_id: int, doc_id: int, quote: str) -> None:
    exists = conn.execute(
        "SELECT 1 FROM item_sources WHERE item_id = ? AND document_id = ? AND quote = ?",
        (item_id, doc_id, quote),
    ).fetchone()
    if not exists:
        conn.execute("INSERT INTO item_sources (item_id, document_id, quote) VALUES (?, ?, ?)",
                     (item_id, doc_id, quote))


def _log_event(conn, item_id: int, doc_id: int, when, kind: str, field=None, old=None, new=None) -> None:
    conn.execute(
        "INSERT INTO item_events (item_id, document_id, kind, field, old_value, new_value, occurred_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", (item_id, doc_id, kind, field, old, new, when))


def apply_operations(conn, client_id: int, doc_id: int, text: str, raw_ops: list,
                     people_index: dict, valid_ids: set[int]) -> dict:
    result = {"created": 0, "updated": 0, "mentioned": 0, "rejected": []}
    when = conn.execute("SELECT occurred_at FROM documents WHERE id = ?", (doc_id,)).fetchone()["occurred_at"]

    for raw in raw_ops:
        try:
            op = Operation.model_validate(raw)
        except ValidationError:
            result["rejected"].append({"reason": "malformed operation", "op": raw})
            continue

        quote = verify_quote(op.quote, text)
        if quote is None:
            result["rejected"].append({"reason": "quote not found in document", "op": raw})
            continue

        owner = resolve_person(people_index, op.owner)
        side = owner["side"] if owner else op.side

        kind, target = op.op, (op.item_id if op.item_id in valid_ids else None)
        if kind in ("update", "mention") and target is None:
            if kind == "update" and op.item_type and op.summary:
                kind = "create"  # the model pointed at a missing id but described a whole new item
            else:
                result["rejected"].append({"reason": "unknown item_id", "op": raw})
                continue

        if kind == "create":
            if not (op.item_type and op.summary):
                result["rejected"].append({"reason": "create needs item_type and summary", "op": raw})
                continue
            item_id = conn.execute(
                "INSERT INTO items (client_id, item_type, summary, owner_id, side, due_date, status, "
                "rationale, severity, extracted_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'llm')",
                (client_id, op.item_type, op.summary, owner["id"] if owner else None, side,
                 op.due_date, op.status or "open", op.rationale, op.severity),
            ).lastrowid
            _add_source(conn, item_id, doc_id, quote)
            _log_event(conn, item_id, doc_id, when, "created", new=op.summary)
            result["created"] += 1

        elif kind == "update":
            old = conn.execute(
                "SELECT i.due_date, i.status, i.severity, p.name AS owner_name FROM items i "
                "LEFT JOIN people p ON p.id = i.owner_id WHERE i.id = ?", (target,)).fetchone()
            changes = {"summary": op.summary, "due_date": op.due_date, "status": op.status,
                       "rationale": op.rationale, "severity": op.severity}
            if owner:
                changes["owner_id"], changes["side"] = owner["id"], owner["side"]
            elif op.side:
                changes["side"] = op.side
            changes = {k: v for k, v in changes.items() if v is not None}  # only what the model gave us
            if changes:
                conn.execute(f"UPDATE items SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?",
                             (*changes.values(), target))
            # Record what actually moved. This is the "due date slipped twice" history.
            for field, new_val, old_val in (("due_date", op.due_date, old["due_date"]),
                                            ("status", op.status, old["status"]),
                                            ("severity", op.severity, old["severity"]),
                                            ("owner", owner["name"] if owner else None, old["owner_name"])):
                if new_val is not None and new_val != old_val:
                    _log_event(conn, target, doc_id, when, "changed", field, old_val, new_val)
            _add_source(conn, target, doc_id, quote)
            result["updated"] += 1

        else:  # mention
            _add_source(conn, target, doc_id, quote)
            result["mentioned"] += 1

    return result


# ---------------------------------------------------------------- running extraction

def _tracked_items(conn, client_id: int) -> list[dict]:
    # Only AI-extracted items go back into the prompt. Items from the no-AI backup extractor are
    # low-confidence placeholders; they must not steer the model, and they get replaced below.
    rows = conn.execute(
        "SELECT i.id, i.item_type, i.status, i.due_date, i.summary, i.severity, p.name AS owner "
        "FROM items i LEFT JOIN people p ON p.id = i.owner_id "
        "WHERE i.client_id = ? AND i.extracted_by = 'llm' ORDER BY i.id",
        (client_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def drop_rule_items_for_document(conn, doc_id: int) -> None:
    """Remove backup-extractor placeholders for a document the AI has now processed properly."""
    conn.execute("DELETE FROM items WHERE extracted_by = 'rules' AND id IN "
                 "(SELECT item_id FROM item_sources WHERE document_id = ?)", (doc_id,))


def extract_document(conn, doc_id: int, *, force: bool = False, use_cache: bool = True) -> dict:
    doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if doc["ai_status"] == "done" and not force:
        return {"doc_id": doc_id, "status": "skipped"}

    client = dict(conn.execute("SELECT * FROM clients WHERE id = ?", (doc["client_id"],)).fetchone())
    people = [dict(r) for r in conn.execute("SELECT * FROM people WHERE client_id = ?", (doc["client_id"],))]
    items = _tracked_items(conn, doc["client_id"])

    prompt = build_user_prompt(client, people, dict(doc), items)
    try:
        data = llm.generate_json(EXTRACTION_SYSTEM, prompt, EXTRACTION_SCHEMA,
                                 model=os.environ.get("GEMINI_EXTRACT_MODEL"), use_cache=use_cache)
    except llm.LLMUnavailable as e:
        return {"doc_id": doc_id, "status": "pending_ai", "reason": str(e)}  # stays queued for a retry
    except llm.LLMError as e:
        conn.execute("UPDATE documents SET ai_status = 'failed' WHERE id = ?", (doc_id,))
        conn.commit()
        return {"doc_id": doc_id, "status": "failed", "reason": str(e)}

    ops = data.get("operations") if isinstance(data, dict) else None
    if not isinstance(ops, list):
        conn.execute("UPDATE documents SET ai_status = 'failed' WHERE id = ?", (doc_id,))
        conn.commit()
        return {"doc_id": doc_id, "status": "failed", "reason": "reply had no 'operations' list"}

    result = apply_operations(conn, doc["client_id"], doc_id, doc["clean_text"], ops,
                              build_people_index(people), {i["id"] for i in items})
    drop_rule_items_for_document(conn, doc_id)
    conn.execute("UPDATE documents SET ai_status = 'done' WHERE id = ?", (doc_id,))
    conn.commit()
    return {"doc_id": doc_id, "status": "done", **result}


def extract_pending(conn, client_id: int | None = None, *, use_cache: bool = True) -> list[dict]:
    """Process every 'pending_ai' document, oldest first. Stops at the first outage so we don't
    hammer a service that is down; the remaining documents simply stay pending."""
    sql = "SELECT id FROM documents WHERE ai_status = 'pending_ai'"
    params: list = []
    if client_id is not None:
        sql += " AND client_id = ?"
        params.append(client_id)
    sql += " ORDER BY occurred_at, id"

    results = []
    for row in conn.execute(sql, params).fetchall():
        outcome = extract_document(conn, row["id"], use_cache=use_cache)
        results.append(outcome)
        if outcome["status"] == "pending_ai":
            break
    return results


def reset_extraction(conn, client_id: int) -> None:
    """Forget all extracted items for a client and queue every document again."""
    conn.execute("DELETE FROM items WHERE client_id = ?", (client_id,))  # sources go with them (ON DELETE CASCADE)
    conn.execute("UPDATE documents SET ai_status = 'pending_ai' WHERE client_id = ?", (client_id,))
    conn.commit()


def main() -> None:
    from db import get_conn

    parser = argparse.ArgumentParser(description="Extract commitments, decisions, risks and opportunities.")
    parser.add_argument("--reset", action="store_true", help="forget extracted items and start over")
    parser.add_argument("--no-cache", action="store_true", help="ignore cached AI replies (spends quota)")
    args = parser.parse_args()

    conn = get_conn()
    client_id = conn.execute("SELECT id FROM clients ORDER BY id LIMIT 1").fetchone()["id"]
    if args.reset:
        reset_extraction(conn, client_id)

    for r in extract_pending(conn, client_id, use_cache=not args.no_cache):
        title = conn.execute("SELECT title FROM documents WHERE id = ?", (r["doc_id"],)).fetchone()["title"]
        detail = (f"created={r['created']} updated={r['updated']} mentioned={r['mentioned']} "
                  f"rejected={len(r['rejected'])}") if r["status"] == "done" else r.get("reason", "")
        print(f"{r['status']:10s} {title[:48]:48s} {detail}")

    counts = conn.execute("SELECT item_type, count(*) FROM items WHERE client_id = ? GROUP BY item_type",
                          (client_id,)).fetchall()
    print("Items:", {row[0]: row[1] for row in counts}, "| AI:", llm.stats())


if __name__ == "__main__":
    main()
