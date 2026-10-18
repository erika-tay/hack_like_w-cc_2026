# Handoff

**Client memory for implementation teams.** Handoff reads the meetings, emails, chats and contracts of a client
implementation, and keeps a living record of what was promised, by whom, by when, what is at risk, and why decisions
were made. Every claim links back to the exact quote it came from. A new teammate can pick up an account in one page.

Built solo in 24 hours for the GT WACC Hackathon 2026, ADP "Best People Hack" track.

<!-- Add a screenshot of YOUR real run here: ![Handoff board](docs/board.png) -->

## The problem

On an implementation, the real state of the project is scattered across calls, email threads and chat. Promises slip
quietly, nobody owns the awkward questions, and when someone leaves or joins, the context goes with them.

## What it does

| Feature | What you see |
|---|---|
| **Commitments board** | Who owes what, split into *ADP owes*, *Client owes* and *Nobody owns these yet*. Overdue items are flagged, and an item whose due date has moved shows `moved 3x` with the old dates struck through. |
| **Runway** | Every open commitment on one timeline against go-live, with the history of where each date used to be. |
| **Ask, with citations** | Plain-English questions across all sources. Every answer shows verbatim quotes from the documents. A quote that is not really in a document is dropped. |
| **Do this next** | Prioritized follow-ups (chase the overdue item, assign the orphan item, address the high risk). |
| **Risks, decisions, opportunities** | Extracted automatically, each with its evidence. Decisions keep their rationale ("why did we do it this way?"). |
| **What changed** | A timeline of every slipped date, status change and new owner, and which document said so. |
| **Handoff brief** | One page for someone joining the account. |
| **Client portal** | A simulated second persona: the client sees what each side owes, without ADP's internal risks. |
| **PII protection** | SSNs, birth dates, pay, employee IDs and personal contact details are removed before any text reaches the AI. The Documents tab shows a before/after. |
| **Works when the AI is down** | See below. |

## How it works

```
document ──► clean ──► redact PII ──► chunk ──► embed + keyword index        (no AI in this half)
                            │
                            ▼  redacted text only
              LLM extraction (static prompt) ──► create / update / mention operations
                            │
                            ▼  every operation is checked: the quote must really appear in the document
              SQLite: items · item_sources (citations) · item_events (history)
                            │
              board · Ask · brief · timeline · client portal
```

- **One table, many views.** Commitments, decisions, risks and opportunities are all `items` rows with an owner, a side,
  a due date and cited sources. The board, risk list, brief and client portal are different queries over it.
- **Incremental memory.** The model reads one document at a time, in date order, and is shown the items already
  tracked. It answers with *create / update / mention*, which is how a promise made in meeting 1 is still there, and
  gets moved or closed, when meeting 5 is read.
- **Citations are verified, not trusted.** Quotes are checked against the document text. No real quote, no item.
- **Static prompt first.** The long fixed instructions come first and the changing material last, so a provider can
  reuse its cached prefix (the same idea as the KV cache in ADP's own pipeline).
- **Local retrieval.** Chunks are embedded locally (MiniLM, or a dependency-free hashing fallback) and also indexed for
  keyword search, so retrieval never depends on the LLM.

### When the AI is down

The top-right switch ("AI on") simulates an outage, and the same code paths run for a real one (no key, quota used up,
network failure).

- Uploads still succeed: the document is cleaned, redacted, indexed and queued as `pending_ai`.
- A rule-based backup extractor finds dated promises and flags them as low confidence.
- Ask falls back to keyword search with highlighted passages and related items.
- The board, runway, brief and *Do this next* are built from stored data with rules, not the model.
- When the AI returns, one click processes the queue, and the backup items are replaced.

## Run it

Python 3.10+.

```bash
cd backend
pip install numpy fastapi "uvicorn[standard]" python-multipart google-genai pydantic python-dotenv
cp ../.env.example ../.env        # add GEMINI_API_KEY (free, from Google AI Studio)
export KMP_DUPLICATE_LIB_OK=TRUE
python seed.py                    # builds the database from sample_data/
python -m ai.extract              # real extraction, about 7 AI requests
uvicorn main:app --port 8000      # open http://localhost:8000
```

**No API key?** The repo includes a recorded result of a real extraction run:

```bash
python seed.py && python snapshot.py load && uvicorn main:app --port 8000
```

Optional: `pip install sentence-transformers` for semantic search (downloads about 80 MB plus PyTorch), then set
`EMBEDDER=auto` in `.env`. The default `EMBEDDER=hash` matches shared words only, which is plenty for this demo corpus.

Tests (no key or network needed): `python tests/test_ingest.py`, `test_extract.py`, `test_fallback.py`, `test_app.py`.
Accuracy against the hand-written answer key: `python eval_extraction.py` (after a real extraction).

## The sample client

`sample_data/` is entirely fictional: Riverbend Health, moving about 500 employees onto payroll and time and attendance.
Seven documents (contract, kickoff, status meetings, email, two chat logs) with planted problems: a data file whose date
slips three times, a question about a timeclock API that nobody owns, two people with different go-live dates, and a
tax task that cannot happen before the file it depends on. `expected_items.json` is the answer key.

## Honest limits

- **Roles are simulated.** The ADP / Client switch hides pages and the API refuses them, but there is no login. A real
  product needs authentication and per-client permissions.
- **PII redaction is pattern-based.** It catches structured identifiers (SSN, DOB, pay, IDs, phones, emails) but not a
  stranger's name in free text. Production would add a named-entity model such as Presidio.
- **Free-tier AI.** Gemini free-tier content may be used by Google to improve its products. Use fictional data, or a
  paid project for anything real.
- **Extraction is imperfect.** Nothing is guessed silently: unowned stays unowned, and unverifiable quotes are dropped,
  but the model can still miss items. The eval script measures this on the sample data.
- **Not a security review.** Built for a hackathon demo.

## Project layout

```
schema.sql              the data model (SQLite)
sample_data/            fictional client, answer key, optional snapshot.json
backend/
  main.py               web API and page
  views.py              board, risks, timeline: all reads, no AI
  ingest/               clean, PII redaction, chunk, embed, search
  ai/                   llm.py (one door to Gemini), prompts, extract, ask, briefs, fallback
  tests/                unit and app tests with a scripted stand-in for the AI
  eval_extraction.py    scores extraction against the answer key
  snapshot.py           save and restore real extraction results
frontend/index.html     the single-page UI (no build step)
docs/                   demo script and project write-up
```
