"""Ingest step 3: split clean text into chunks, embed them, and index them for search.

Two kinds of search live here, because they fail in different ways:
  * semantic_search: finds chunks that mean the same thing as the question (needs embeddings)
  * keyword_search:  finds chunks that contain the words (SQLite full-text, needs nothing else)

keyword_search is what keeps the app useful when the LLM is down.

Embeddings are computed locally. The preferred model is all-MiniLM-L6-v2 (sentence-transformers).
If that package or model isn't available, we fall back to a hashing embedder that needs no
download. It still works, but it only matches shared words, so it is keyword-like rather than
truly semantic. The model name is stored in the meta table, and search refuses to mix models.
"""
import hashlib
import math
import os
import re
from collections import Counter

import numpy as np

_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was", "were", "be",
    "it", "that", "this", "we", "our", "you", "your", "i", "do", "does", "did", "when", "what",
    "who", "how", "with", "at", "by", "as", "from", "about", "there", "any",
    "where", "why", "which", "stand", "keep", "kept", "did", "got", "get", "so", "if", "not", "but",
    "have", "has", "had", "can", "will", "would", "should", "could", "us", "me", "they", "them",
}


# ---------------------------------------------------------------- embedders

class HashingEmbedder:
    """Dependency-free fallback: hash words and word pairs into a fixed-size vector."""
    name = "hashing-1024"
    dim = 1024

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            tokens = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP]
            features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
            for feature, count in Counter(features).items():
                h = int.from_bytes(hashlib.md5(feature.encode()).digest()[:8], "big")
                sign = 1.0 if (h >> 63) & 1 else -1.0
                out[i, h % self.dim] += sign * (1 + math.log(count))
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return out / norms


class MiniLMEmbedder:
    """Preferred: a small local model (about 80 MB, runs fine on a laptop CPU)."""
    name = "all-MiniLM-L6-v2"

    def __init__(self):
        from sentence_transformers import SentenceTransformer  # heavy import, so it's lazy
        self.model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, normalize_embeddings=True), dtype=np.float32)


_embedder = None


def get_embedder():
    """EMBEDDER=auto (default) tries MiniLM then falls back; 'minilm' or 'hash' forces one."""
    global _embedder
    if _embedder is None:
        choice = os.environ.get("EMBEDDER", "auto")
        if choice in ("auto", "minilm"):
            try:
                _embedder = MiniLMEmbedder()
            except Exception as e:
                if choice == "minilm":
                    raise
                print(f"[embeddings] MiniLM unavailable ({type(e).__name__}); using hashing fallback. "
                      "Search will match shared words, not meaning.")
        if _embedder is None:
            _embedder = HashingEmbedder()
    return _embedder


# ---------------------------------------------------------------- chunking

def chunk_text(text: str, max_chars: int = 700, overlap_chars: int = 160) -> list[str]:
    """Pack whole lines (speaker turns, chat messages, paragraphs) into chunks of about max_chars.

    Never cuts a line in half, so a quote always stays intact for citations. A long line
    (like a contract clause) is split at sentence boundaries. The last line of each chunk is
    repeated at the start of the next one when it is short, so context isn't lost at the seam.
    """
    units: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if len(line) <= max_chars:
            units.append(line)
        else:
            units.extend(s for s in re.split(r"(?<=[.!?])\s+", line) if s)

    chunks: list[str] = []
    current: list[str] = []
    for unit in units:
        if current and sum(len(x) + 1 for x in current) + len(unit) > max_chars:
            chunks.append("\n".join(current))
            carry = current[-1] if len(current[-1]) <= overlap_chars else None
            current = [carry] if carry else []
        current.append(unit)
    if current:
        chunks.append("\n".join(current))
    return chunks


# ---------------------------------------------------------------- indexing

def index_document(conn, doc_id: int) -> int:
    """(Re)build the chunks, embeddings and keyword index for one document. Safe to re-run."""
    doc = conn.execute(
        "SELECT title, occurred_at, clean_text FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()

    # Remove old chunks, including their keyword-index entries, so re-indexing never duplicates.
    for row in conn.execute("SELECT id, text FROM chunks WHERE document_id = ?", (doc_id,)).fetchall():
        conn.execute("INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', ?, ?)",
                     (row["id"], row["text"]))
    conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))

    chunks = chunk_text(doc["clean_text"])
    embedder = get_embedder()
    date = (doc["occurred_at"] or "")[:10]
    # The embedding sees the title and date too, so "what did we decide in the Oct 8 meeting"
    # can match. The stored text stays verbatim, because citations quote it.
    vectors = embedder.encode([f"{doc['title']} ({date}): {c}" for c in chunks])

    for i, (text, vec) in enumerate(zip(chunks, vectors)):
        cur = conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, text, embedding) VALUES (?, ?, ?, ?)",
            (doc_id, i, text, vec.astype(np.float32).tobytes()),
        )
        conn.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)", (cur.lastrowid, text))

    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('embedder', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (embedder.name,),
    )
    conn.commit()
    return len(chunks)


# ---------------------------------------------------------------- search

def semantic_search(conn, query: str, client_id: int | None = None, k: int = 5) -> list[dict]:
    embedder = get_embedder()
    stored = conn.execute("SELECT value FROM meta WHERE key = 'embedder'").fetchone()
    if stored and stored["value"] != embedder.name:
        raise RuntimeError(
            f"Chunks were embedded with '{stored['value']}' but the current embedder is "
            f"'{embedder.name}'. Re-run seed.py (or re-index) so they match."
        )

    sql = ("SELECT c.id, c.document_id, c.text, c.embedding, d.title, d.occurred_at "
           "FROM chunks c JOIN documents d ON d.id = c.document_id")
    params: list = []
    if client_id is not None:
        sql += " WHERE d.client_id = ?"
        params.append(client_id)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return []

    matrix = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    scores = matrix @ embedder.encode([query])[0]  # vectors are normalized, so this is cosine similarity
    best = np.argsort(-scores)[:k]
    return [
        {"chunk_id": rows[i]["id"], "document_id": rows[i]["document_id"], "title": rows[i]["title"],
         "occurred_at": rows[i]["occurred_at"], "text": rows[i]["text"], "score": float(scores[i])}
        for i in best
    ]


def keyword_search(conn, query: str, client_id: int | None = None, k: int = 5) -> list[dict]:
    """Full-text search with no AI. Works even when the embedder and the LLM are both unavailable."""
    terms = [t for t in re.findall(r"[A-Za-z0-9]+", query.lower()) if t not in _STOP]
    if not terms:
        return []
    match = " OR ".join(f'"{t}"' for t in terms)  # quoting each term keeps user input from breaking FTS syntax

    sql = ("SELECT c.id, c.document_id, c.text, d.title, d.occurred_at, bm25(chunks_fts) AS score "
           "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
           "JOIN documents d ON d.id = c.document_id WHERE chunks_fts MATCH ?")
    params: list = [match]
    if client_id is not None:
        sql += " AND d.client_id = ?"
        params.append(client_id)
    sql += " ORDER BY score LIMIT ?"
    params.append(k)

    return [
        {"chunk_id": r["id"], "document_id": r["document_id"], "title": r["title"],
         "occurred_at": r["occurred_at"], "text": r["text"], "score": -float(r["score"])}  # bm25 is lower-is-better
        for r in conn.execute(sql, params).fetchall()
    ]
