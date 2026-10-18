"""Create the database and load the sample client. Run from the backend folder:

    python seed.py

It wipes and rebuilds handoff.db each time, so it is safe to re-run during development.
Documents are loaded and made searchable, but NOT yet analyzed: they stay 'pending_ai'
until the extraction step runs.
"""
import json
from pathlib import Path

from db import ROOT, init_db
from ingest.chunk_embed import get_embedder
from ingest.pipeline import ingest_document

SAMPLE_DIR = ROOT / "sample_data"


def parse_doc(path: Path) -> tuple[str, str, str, str]:
    """Sample files start with Title/Type/Date lines, then a blank line, then the body."""
    header, _, body = path.read_text(encoding="utf-8").partition("\n\n")
    meta = dict(line.split(": ", 1) for line in header.splitlines() if ": " in line)
    return meta["Title"], meta["Type"], meta["Date"], body.strip()


def main() -> None:
    conn = init_db(reset=True)
    data = json.loads((SAMPLE_DIR / "client.json").read_text(encoding="utf-8"))

    client_id = conn.execute(
        "INSERT INTO clients (name, go_live) VALUES (?, ?)",
        (data["client"]["name"], data["client"]["go_live"]),
    ).lastrowid
    for p in data["people"]:
        conn.execute(
            "INSERT INTO people (client_id, name, side, role, aliases) VALUES (?, ?, ?, ?, ?)",
            (client_id, p["name"], p["side"], p["role"], json.dumps(p["aliases"], ensure_ascii=False)),
        )
    conn.commit()

    print(f"Embedder: {get_embedder().name}")
    for path in sorted(SAMPLE_DIR.glob("[0-9][0-9]_*.txt")):
        title, doc_type, date, body = parse_doc(path)
        doc_id = conn.execute(
            "INSERT INTO documents (client_id, title, doc_type, occurred_at, raw_text) VALUES (?, ?, ?, ?, ?)",
            (client_id, title, doc_type, date, body),
        ).lastrowid
        info = ingest_document(conn, doc_id)
        print(f"  {path.name:38s} chunks={info['chunks']:<2d} redacted={info['redactions'] or '-'}")
    print("Done. Documents are searchable and waiting for extraction (ai_status = pending_ai).")


if __name__ == "__main__":
    main()
