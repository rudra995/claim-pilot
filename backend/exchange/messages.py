"""The claim exchange: the only channel between the hospital and the simulated insurer (design section 5).

An append-only thread of messages per claim, stored in claim_messages. The two sides share no
memory and never import each other: each posts here and reads what the other posted. The exchange
enforces the protocol, so neither side can break it by mistake:

- a stage opens with SUBMIT in round 1; every RESPONSE or APPEAL starts the next round
- the insurer answers each hospital message exactly once, in the same round
- there is no round after MAX_ROUNDS, and nothing after APPROVE or FINAL
- the hospital can post only while the claim is SUBMITTED, which needs a person's approval
"""

from pathlib import Path

from backend.db import audit
from backend.db.db import connect
from backend.schemas import (
    MAX_ROUNDS,
    Appeal,
    Approve,
    ClaimMessage,
    ClaimState,
    Deny,
    Document,
    Final,
    PartialApprove,
    Query,
    Response,
    Sender,
    Stage,
    Submit,
    claim_message_adapter,
)


class ProtocolError(ValueError):
    pass


class RoundLimitReached(ProtocolError):
    """The stage has used all its rounds; a person decides from here."""


def post(db_path: Path | str, message: ClaimMessage) -> int:
    """Check the message against the thread so far and append it. Returns its msg_id."""
    earlier = thread(db_path, message.claim_id, message.stage)
    if message.sender == Sender.HOSPITAL:
        _check_hospital(db_path, message, earlier)
    else:
        _check_insurer(message, earlier)

    conn = connect(db_path)
    try:
        with conn:
            msg_id = conn.execute(
                "INSERT INTO claim_messages (claim_id, type, sender, round, body_json) VALUES (?, ?, ?, ?, ?)",
                (message.claim_id, message.type.value, message.sender.value, message.round, message.model_dump_json()),
            ).lastrowid
    finally:
        conn.close()
    audit.log(
        db_path, message.claim_id, message.sender.value, "message_sent",
        {"msg_id": msg_id, "type": message.type, "stage": message.stage, "round": message.round},
    )
    return msg_id


def attach(db_path: Path | str, claim_id: str, documents: list[Document]) -> None:
    """Send document files with a message: the exchange's attachment channel. Each is recorded once."""
    sent = {d["doc_id"] for d in attachments(db_path, claim_id)}
    for doc in documents:
        if doc.doc_id not in sent:
            audit.log(db_path, claim_id, Sender.HOSPITAL.value, "exchange_attachment", doc.model_dump(mode="json", exclude={"fields"}))


def attachments(db_path: Path | str, claim_id: str) -> list[dict]:
    """Documents sent on the exchange for a claim (id, type, file), oldest first."""
    return [e["output"] for e in audit.events(db_path, claim_id) if e["action"] == "exchange_attachment"]


def thread(db_path: Path | str, claim_id: str, stage: Stage | None = None) -> list[ClaimMessage]:
    """A claim's messages, oldest first; only one stage's if `stage` is given."""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT body_json FROM claim_messages WHERE claim_id = ? ORDER BY msg_id", (claim_id,)
        ).fetchall()
    finally:
        conn.close()
    messages = [claim_message_adapter.validate_json(row["body_json"]) for row in rows]
    return [m for m in messages if stage is None or m.stage == stage]


def latest(db_path: Path | str, claim_id: str) -> ClaimMessage | None:
    messages = thread(db_path, claim_id)
    return messages[-1] if messages else None


def next_round(db_path: Path | str, claim_id: str, stage: Stage) -> int:
    """The round the hospital's next message in this stage would open."""
    earlier = thread(db_path, claim_id, stage)
    return earlier[-1].round + 1 if earlier else 1


def submissions(db_path: Path | str) -> list[Submit]:
    """Every SUBMIT on the exchange, across all claims."""
    conn = connect(db_path)
    try:
        rows = conn.execute("SELECT body_json FROM claim_messages WHERE type = 'SUBMIT' ORDER BY msg_id").fetchall()
    finally:
        conn.close()
    return [Submit.model_validate_json(row["body_json"]) for row in rows]


def _check_hospital(db_path: Path | str, message: ClaimMessage, earlier: list[ClaimMessage]) -> None:
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT state FROM claims WHERE claim_id = ?", (message.claim_id,)).fetchone()
    finally:
        conn.close()
    if row is None or row["state"] != ClaimState.SUBMITTED:
        raise ProtocolError(f"{message.claim_id}: the hospital can only send an approved claim (state SUBMITTED)")

    if isinstance(message, Submit):
        if earlier:
            raise ProtocolError(f"{message.claim_id}: {message.stage} was already submitted")
        if message.round != 1:
            raise ProtocolError("a SUBMIT opens round 1")
        return

    last = earlier[-1] if earlier else None
    if last is None or last.sender != Sender.INSURER:
        raise ProtocolError(f"{message.claim_id}: nothing from the insurer to answer")
    if isinstance(last, (Approve, Final)):
        raise ProtocolError(f"{message.claim_id}: the insurer has closed {message.stage} with {last.type}")
    if last.round >= MAX_ROUNDS:
        raise RoundLimitReached(f"{message.claim_id}: {MAX_ROUNDS} rounds used; a person decides")
    if message.round != last.round + 1:
        raise ProtocolError(f"expected round {last.round + 1}, got {message.round}")
    if isinstance(message, Response) and not isinstance(last, Query):
        raise ProtocolError("a RESPONSE answers a QUERY")
    if isinstance(message, Appeal) and not isinstance(last, (PartialApprove, Deny)):
        raise ProtocolError("an APPEAL answers a PARTIAL_APPROVE or DENY")


def _check_insurer(message: ClaimMessage, earlier: list[ClaimMessage]) -> None:
    last = earlier[-1] if earlier else None
    if last is None or last.sender != Sender.HOSPITAL:
        raise ProtocolError(f"{message.claim_id}: no hospital message waiting for an answer")
    if message.round != last.round:
        raise ProtocolError(f"the answer belongs to round {last.round}, got {message.round}")
