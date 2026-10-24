"""Routes for the assistant (backend/assistant.py): patients ask under /api/portal, officers under /api/assistant.

The portal's sign-in middleware already limits patients to /api/portal/ and their own claims.
"""

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from backend import assistant
from backend.api import portal, store
from backend.db import audit
from backend.db.db import connect


class Ask(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    claim_id: str | None = None


class Reply(BaseModel):
    answer: str = Field(min_length=3, max_length=1500)


def install(app, db_path: Path | str, describe: Callable[[str | None], list[dict[str, Any]]]) -> None:
    """`describe(claim_id)` gives the officer-facing summaries of claims (all of them when claim_id is None)."""
    # load the embedding model in the background so the first question doesn't wait for it
    threading.Thread(target=assistant._embed, args=(["warm up"],), daemon=True).start()

    def latest_claim(patient_id: str) -> str | None:
        with connect(db_path) as conn:
            row = conn.execute(
                "SELECT c.claim_id FROM claims c JOIN admissions a USING (admission_id) WHERE a.patient_id = ? ORDER BY c.rowid DESC LIMIT 1",
                (patient_id,),
            ).fetchone()
        return row["claim_id"] if row else None

    def case_for(claim_id: str | None):
        try:
            return store.load(db_path, claim_id) if claim_id else None
        except KeyError:
            return None

    # ---- Patients -------------------------------------------------------------------------------

    @app.post("/api/portal/assistant/ask")
    def patient_ask(body: Ask) -> dict[str, Any]:
        account = portal.user("patient")
        claim_id = body.claim_id or latest_claim(account["patient_id"])
        if claim_id:
            portal.own_claim(db_path, claim_id)  # 404 unless it's theirs
        return assistant.ask_patient(db_path, body.question, claim_id, case_for(claim_id), account["patient_id"])

    @app.get("/api/portal/assistant/history")
    def patient_history(claim_id: str | None = None) -> dict[str, Any]:
        account = portal.user("patient")
        claim_id = claim_id or latest_claim(account["patient_id"])
        if not claim_id:
            return {"claim_id": None, "history": []}
        portal.own_claim(db_path, claim_id)
        return {"claim_id": claim_id, "history": assistant.patient_history(db_path, claim_id)}

    # ---- Officers -------------------------------------------------------------------------------

    @app.post("/api/assistant/ask")
    def officer_ask(body: Ask) -> dict[str, Any]:
        officer = portal.user("officer")["display_name"]
        claim = None
        latest: dict[str, dict] = {}
        if body.claim_id:
            rows = describe(body.claim_id)
            if not rows:
                raise HTTPException(404, f"No claim {body.claim_id}")
            claim = rows[0]
            for event in audit.events(db_path, body.claim_id):
                if event["action"] == "agent_output":
                    latest[event["actor"]] = event["output"]
        result = assistant.ask_officer(db_path, body.question, claim, case_for(body.claim_id), latest, describe(None) if claim is None else [])
        if body.claim_id:
            audit.log(db_path, body.claim_id, assistant.ACTOR, "assistant_exchange",
                      {"audience": "officer", "asked_by": f"human:{officer}", "question": body.question, **result})
        return result

    @app.get("/api/patient-desk/{claim_id}/questions")
    def questions(claim_id: str) -> list[dict[str, Any]]:
        portal.user("officer")
        portal.own_claim(db_path, claim_id)
        return assistant.open_questions(db_path, claim_id)

    @app.post("/api/patient-desk/{claim_id}/questions/{question_id}/answer")
    def answer(claim_id: str, question_id: str, body: Reply) -> dict[str, Any]:
        officer = portal.user("officer")["display_name"]
        portal.own_claim(db_path, claim_id)
        try:
            return assistant.answer_question(db_path, claim_id, question_id, body.answer.strip(), officer)
        except KeyError:
            raise HTTPException(404, "Question not found")
        except ValueError as error:
            raise HTTPException(409, str(error))
