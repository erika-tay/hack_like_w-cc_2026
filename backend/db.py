"""SQLite connection helpers. The whole database is one file, created from schema.sql."""
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("HANDOFF_DB", ROOT / "backend" / "handoff.db"))
SCHEMA_PATH = ROOT / "schema.sql"


def get_conn() -> sqlite3.Connection:
    """Open a connection. Rows come back as dict-like objects (row["title"])."""
    # check_same_thread=False: the web server may open a connection in one thread and use it in
    # another. Each request gets its own connection, so there is no sharing between requests.
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(reset: bool = False) -> sqlite3.Connection:
    """Create the database from schema.sql if it doesn't exist yet.

    reset=True deletes the file first, which is what seed.py uses to start clean.
    """
    if reset and DB_PATH.exists():
        DB_PATH.unlink()
    conn = get_conn()
    exists = conn.execute("SELECT name FROM sqlite_master WHERE name = 'clients'").fetchone()
    if not exists:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.commit()
    return conn
