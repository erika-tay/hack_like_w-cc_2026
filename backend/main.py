"""The web app: JSON API plus the single-page UI.  Run from the backend folder:

    uvicorn main:app --port 8000        then open http://localhost:8000

Routes only. The logic lives in views.py (reads), ingest/ (no AI) and ai/ (everything that calls the model).
The ADP / Client switch is a SIMULATED role: it hides internal pages in the UI and the API refuses them,
but there is no login. A real product would put real authentication and per-client permissions here.
"""
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

import views
from ai import ask as ask_mod
from ai import briefs, llm
from ai.extract import extract_document, extract_pending
from ai.fallback import extract_with_rules
from db import ROOT, get_conn, init_db
from ingest.chunk_embed import get_embedder
from ingest.pipeline import ingest_document, preview_redaction

FRONTEND = ROOT / "frontend" / "index.html"
DOC_TYPES = ("meeting_transcript", "email", "chat", "contract", "other")
MAX_DOC_CHARS = 60_000


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = init_db()
    if not conn.execute("SELECT 1 FROM clients LIMIT 1").fetchone():
        print("\n!! The database is empty. Stop the server and run:  python seed.py\n")
    conn.close()
    yield


app = FastAPI(title="Handoff", lifespan=lifespan)


def db():
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


def cid(conn) -> int:
    row = conn.execute("SELECT id FROM clients ORDER BY id LIMIT 1").fetchone()
    if not row:
        raise HTTPException(503, "No client loaded. Run `python seed.py` in the backend folder.")
    return row["id"]


def adp_only(view: str) -> None:
    if view == "client":
        raise HTTPException(403, "The client portal doesn't include this. Switch to the ADP view.")


# ---------------------------------------------------------------- pages and status

@app.get("/", response_class=HTMLResponse)
def home():
    if not FRONTEND.exists():
        return HTMLResponse("<p>frontend/index.html not found.</p>", status_code=404)
    return FileResponse(FRONTEND)


@app.get("/api/status")
def status(conn=Depends(db)):
    pending = conn.execute("SELECT count(*) FROM documents WHERE ai_status = 'pending_ai'").fetchone()[0]
    return {"ai": llm.stats(), "model": llm.default_model(), "embedder": get_embedder().name,
            "as_of": views.as_of_date().isoformat(), "pending_docs": pending}


class AiSwitch(BaseModel):
    enabled: bool


@app.post("/api/ai")
def switch_ai(body: AiSwitch):
    """The demo's 'turn the AI off' switch."""
    llm.set_enabled(body.enabled)
    return {"enabled": llm.is_enabled()}


# ---------------------------------------------------------------- reads

@app.get("/api/board")
def api_board(view: str = "adp", conn=Depends(db)):
    data = views.board(conn, cid(conn), views.as_of_date())
    if view == "client":
        data["counts"].pop("high_risks", None)
    return data


@app.get("/api/risks")
def api_risks(view: str = "adp", conn=Depends(db)):
    adp_only(view)
    return views.risks(conn, cid(conn), views.as_of_date())


@app.get("/api/decisions")
def api_decisions(conn=Depends(db)):
    return views.decisions(conn, cid(conn), views.as_of_date())


@app.get("/api/opportunities")
def api_opportunities(view: str = "adp", conn=Depends(db)):
    adp_only(view)
    return views.opportunities(conn, cid(conn), views.as_of_date())


@app.get("/api/timeline")
def api_timeline(since: str | None = None, view: str = "adp", conn=Depends(db)):
    adp_only(view)
    return views.timeline(conn, cid(conn), since)


@app.get("/api/documents")
def api_documents(conn=Depends(db)):
    return views.documents(conn, cid(conn))


@app.get("/api/documents/{doc_id}/redaction")
def api_redaction(doc_id: int, view: str = "adp", conn=Depends(db)):
    adp_only(view)
    if not conn.execute("SELECT 1 FROM documents WHERE id = ?", (doc_id,)).fetchone():
        raise HTTPException(404, "No such document.")
    return preview_redaction(conn, doc_id)


# ---------------------------------------------------------------- AI features

class Question(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    view: str = "adp"


@app.post("/api/ask")
def api_ask(body: Question, conn=Depends(db)):
    adp_only(body.view)
    if not body.question.strip():
        raise HTTPException(422, "Ask a question first.")
    return ask_mod.ask(conn, cid(conn), body.question, views.as_of_date())


@app.get("/api/brief")
def api_brief(view: str = "adp", conn=Depends(db)):
    adp_only(view)
    return briefs.handoff_brief(conn, cid(conn), views.as_of_date())


@app.get("/api/next-actions")
def api_next_actions(view: str = "adp", conn=Depends(db)):
    adp_only(view)
    return briefs.next_actions(conn, cid(conn), views.as_of_date())


# ---------------------------------------------------------------- adding documents

class NewDocument(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    doc_type: str = "other"
    occurred_at: str | None = None
    text: str = Field(min_length=1)


@app.post("/api/documents")
def api_add_document(body: NewDocument, conn=Depends(db)):
    """Ingest always succeeds (clean, redact, index). Extraction is best effort:
    if the AI is unavailable, the document is queued and the no-AI backup extractor runs instead."""
    if body.doc_type not in DOC_TYPES:
        raise HTTPException(422, f"doc_type must be one of {', '.join(DOC_TYPES)}")
    if len(body.text) > MAX_DOC_CHARS:
        raise HTTPException(413, f"That document is too long (limit {MAX_DOC_CHARS:,} characters).")
    when = body.occurred_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        datetime.strptime(when[:10], "%Y-%m-%d")
    except ValueError:
        raise HTTPException(422, "occurred_at must look like 2026-10-18 or 2026-10-18 14:30")

    doc_id = conn.execute(
        "INSERT INTO documents (client_id, title, doc_type, occurred_at, raw_text) VALUES (?, ?, ?, ?, ?)",
        (cid(conn), body.title.strip(), body.doc_type, when, body.text)).lastrowid
    conn.commit()
    ingest = ingest_document(conn, doc_id)
    extraction = extract_document(conn, doc_id)
    backup = extract_with_rules(conn, doc_id)["items"] if extraction["status"] == "pending_ai" else 0
    return {"doc_id": doc_id, "ingest": ingest, "extraction": extraction, "backup_items": backup}


@app.post("/api/process")
def api_process(conn=Depends(db)):
    """Retry every document waiting for the AI (after it comes back)."""
    results = extract_pending(conn, cid(conn))
    return {"results": results, "still_pending": sum(1 for r in results if r["status"] == "pending_ai")}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000)
