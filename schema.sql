-- Handoff: client context memory for implementation teams
-- SQLite-compatible; ports to Postgres with minor changes (see notes at bottom).

PRAGMA foreign_keys = ON;

-- One row per client implementation (e.g. "Riverbend Health", onboarding to payroll)
CREATE TABLE clients (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    go_live     DATE,                 -- target go-live date
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- People on either side of the engagement. Aliases let "Priya" and "P. Raman"
-- resolve to the same person (lightweight entity resolution, no graph needed).
CREATE TABLE people (
    id          INTEGER PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES clients(id),
    name        TEXT NOT NULL,
    side        TEXT NOT NULL CHECK (side IN ('adp', 'client')),
    role        TEXT,
    aliases     TEXT DEFAULT '[]'     -- JSON array of alternate names / emails
);

-- Every raw input: transcript, email, chat log, contract.
-- raw_text is the original; clean_text is cleaned + PII-redacted and is the only
-- version the LLM ever sees.
CREATE TABLE documents (
    id           INTEGER PRIMARY KEY,
    client_id    INTEGER NOT NULL REFERENCES clients(id),
    title        TEXT NOT NULL,
    doc_type     TEXT CHECK (doc_type IN
                   ('meeting_transcript','email','chat','contract','other')),
    occurred_at  TIMESTAMP,           -- when the meeting/email happened (not upload time)
    raw_text     TEXT NOT NULL,
    clean_text   TEXT,
    -- AI-down support: ingest always succeeds; extraction can wait.
    ai_status    TEXT NOT NULL DEFAULT 'pending_ai'
                   CHECK (ai_status IN ('pending_ai','done','failed')),
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Chunks of clean_text for retrieval (RAG). embedding is a float32 blob;
-- with a corpus this small, cosine similarity in Python is plenty.
CREATE TABLE chunks (
    id           INTEGER PRIMARY KEY,
    document_id  INTEGER NOT NULL REFERENCES documents(id),
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    embedding    BLOB
);

-- The heart of the app. Commitments, decisions, risks, and opportunities are all
-- "items": every feature (board, risk radar, decision log, handoff brief,
-- accountability portal) is just a different view over this table.
CREATE TABLE items (
    id            INTEGER PRIMARY KEY,
    client_id     INTEGER NOT NULL REFERENCES clients(id),
    item_type     TEXT NOT NULL CHECK (item_type IN
                    ('commitment','decision','risk','opportunity')),
    summary       TEXT NOT NULL,            -- one-line description
    owner_id      INTEGER REFERENCES people(id),   -- NULL = unowned (we flag these)
    side          TEXT CHECK (side IN ('adp','client')),
    due_date      DATE,                     -- commitments
    status        TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open','done','blocked')),
    rationale     TEXT,                     -- decisions: why we did it this way
    severity      TEXT CHECK (severity IN ('low','medium','high')),  -- risks
    confidence    REAL,                     -- 0..1; lower for regex-fallback items
    extracted_by  TEXT NOT NULL DEFAULT 'llm' CHECK (extracted_by IN ('llm','rules')),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Citations: each item points back to the exact quote in the exact document.
-- One item can have several sources (e.g. promised in a meeting, repeated in email).
CREATE TABLE item_sources (
    id           INTEGER PRIMARY KEY,
    item_id      INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    document_id  INTEGER NOT NULL REFERENCES documents(id),
    quote        TEXT NOT NULL             -- verbatim span from clean_text
);

-- Change history: what moved, when, and which document said so. This powers the
-- "what changed since I last looked" timeline and the "due date slipped twice" flag.
CREATE TABLE item_events (
    id           INTEGER PRIMARY KEY,
    item_id      INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    document_id  INTEGER NOT NULL REFERENCES documents(id),
    kind         TEXT NOT NULL CHECK (kind IN ('created','changed')),
    field        TEXT,                     -- due_date, status, owner, severity (changed only)
    old_value    TEXT,
    new_value    TEXT,
    occurred_at  TIMESTAMP                 -- the document's date, not the processing time
);

CREATE INDEX idx_events_item       ON item_events(item_id);
CREATE INDEX idx_items_client_type ON items(client_id, item_type, status);
CREATE INDEX idx_items_due         ON items(due_date);
CREATE INDEX idx_docs_status       ON documents(ai_status);
CREATE INDEX idx_chunks_doc        ON chunks(document_id);

-- Keyword search that works with no AI at all (the "AI is down" fallback).
-- The porter tokenizer matches word forms ("delay" finds "delayed", "approve" finds "approves").
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    text, content='chunks', content_rowid='id', tokenize='porter unicode61'
);

-- Small key/value settings. Used to record which embedding model produced the
-- vectors in chunks.embedding, so we never compare vectors from different models.
CREATE TABLE meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- Notes
--  * Overdue = status='open' AND due_date < date('now')
--  * Unowned = owner_id IS NULL
--  * Postgres port: INTEGER PRIMARY KEY -> SERIAL, BLOB -> vector(n) with pgvector,
--    fts5 table -> tsvector column + GIN index.
