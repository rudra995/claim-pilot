"""SQLite connection and table creation. Run `python -m backend.db.db` to create claimpilot.db."""

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB_PATH = Path("claimpilot.db")


def connect(path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())


def init_db(path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = connect(path)
    create_tables(conn)
    return conn


if __name__ == "__main__":
    init_db().close()
    print(f"Created {DEFAULT_DB_PATH}")
