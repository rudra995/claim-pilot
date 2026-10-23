import re
import sqlite3
from pathlib import Path

import pytest

from backend.db.db import SCHEMA_PATH, init_db
from backend.schemas import ClaimState

TABLES = {
    "patients", "policies", "rules", "admissions", "bill_lines",
    "documents", "claims", "claim_messages", "audit_log",
}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return init_db(tmp_path / "test.db")


def test_creates_all_tables_from_scratch(conn: sqlite3.Connection) -> None:
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names


def test_create_is_idempotent(tmp_path: Path) -> None:
    init_db(tmp_path / "test.db").close()
    init_db(tmp_path / "test.db").close()


def test_state_check_matches_enum() -> None:
    check = re.search(r"state IN \((.*?)\)\)", SCHEMA_PATH.read_text(), re.S)
    assert check is not None
    assert set(re.findall(r"'(\w+)'", check.group(1))) == {s.value for s in ClaimState}


def _seed_claim(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO patients VALUES ('P1', 'Test Patient', 40, 'M', 'Pune')")
    conn.execute("INSERT INTO policies VALUES ('POL1', 'Demo', 'Plan', 300000, '2022-01-01')")
    conn.execute(
        "INSERT INTO admissions VALUES ('A1', 'P1', 'POL1', 'appendectomy', 'K35.8', 'single',"
        " '2026-10-01', '2026-10-03', 'TPA-A')"
    )
    conn.execute("INSERT INTO claims (claim_id, admission_id, state) VALUES ('C1', 'A1', 'ADMITTED')")


def test_rejects_unknown_state(conn: sqlite3.Connection) -> None:
    _seed_claim(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE claims SET state = 'MADE_UP' WHERE claim_id = 'C1'")


def test_foreign_keys_enforced(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO claims (claim_id, admission_id, state) VALUES ('C9', 'nope', 'ADMITTED')")


@pytest.mark.parametrize("table", ["claim_messages", "audit_log"])
def test_logs_are_append_only(conn: sqlite3.Connection, table: str) -> None:
    _seed_claim(conn)
    if table == "claim_messages":
        conn.execute(
            "INSERT INTO claim_messages (claim_id, type, sender, round, body_json)"
            " VALUES ('C1', 'SUBMIT', 'hospital', 1, '{}')"
        )
    else:
        conn.execute("INSERT INTO audit_log (claim_id, actor, action) VALUES ('C1', 'test', 'created')")
    for sql in (f"UPDATE {table} SET claim_id = 'C1'", f"DELETE FROM {table}"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(sql)
