"""The whole ingest path in one place: raw document in, searchable redacted chunks out.

It never calls an LLM and never touches documents.ai_status, so uploads always succeed
and extraction can happen later (or be retried if the AI is down).
"""
import json

from .chunk_embed import index_document
from .clean import clean_text
from .pii import redact


def team_allowlist(conn, client_id: int) -> tuple[list[str], list[str]]:
    """Names and work emails of the project team. These are kept; everything else is redacted."""
    emails, names = [], []
    for p in conn.execute("SELECT name, aliases FROM people WHERE client_id = ?", (client_id,)):
        names.append(p["name"])
        for alias in json.loads(p["aliases"] or "[]"):
            (emails if "@" in alias else names).append(alias)
    return emails, names


def ingest_document(conn, doc_id: int) -> dict:
    doc = conn.execute(
        "SELECT client_id, doc_type, raw_text FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    emails, names = team_allowlist(conn, doc["client_id"])

    cleaned = clean_text(doc["raw_text"], doc["doc_type"])
    result = redact(cleaned, emails, names)

    # clean_text holds the redacted version: it is the only text the LLM will ever see.
    conn.execute("UPDATE documents SET clean_text = ? WHERE id = ?", (result.text, doc_id))
    conn.commit()
    chunks = index_document(conn, doc_id)
    return {"doc_id": doc_id, "chunks": chunks, "redactions": result.counts}


def preview_redaction(conn, doc_id: int) -> dict:
    """Data for the before/after view: the cleaned text, the redacted text, and what was removed where."""
    doc = conn.execute(
        "SELECT client_id, doc_type, raw_text FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    emails, names = team_allowlist(conn, doc["client_id"])
    cleaned = clean_text(doc["raw_text"], doc["doc_type"])
    result = redact(cleaned, emails, names)
    return {
        "before": cleaned,
        "after": result.text,
        "spans": [{"kind": r.kind, "start": r.start, "end": r.end} for r in result.redactions],
    }
