"""Writes to the append-only audit log: every agent output, retry, escalation and state change.

Each write opens its own short connection, so agents running in parallel can log safely.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic_core import to_json

from backend.db.db import connect


def input_hash(value: Any) -> str:
    """A fingerprint of what an agent was given, so a run can be traced and compared."""
    return hashlib.sha256(to_json(value, fallback=str)).hexdigest()


def log(
    db_path: Path | str,
    claim_id: str,
    actor: str,
    action: str,
    output: Any,
    inputs: Any = None,
) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO audit_log (claim_id, actor, action, input_hash, output_json) VALUES (?, ?, ?, ?, ?)",
                (
                    claim_id,
                    actor,
                    action,
                    None if inputs is None else input_hash(inputs),
                    json.dumps(output, ensure_ascii=False, default=str),
                ),
            )
    finally:
        conn.close()


def events(db_path: Path | str, claim_id: str) -> list[dict[str, Any]]:
    """A claim's audit trail, oldest first, with output_json parsed."""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT event_id, actor, action, input_hash, output_json, created_at"
            " FROM audit_log WHERE claim_id = ? ORDER BY event_id",
            (claim_id,),
        ).fetchall()
    finally:
        conn.close()
    return [{**dict(row), "output": json.loads(row["output_json"])} for row in rows]
