"""Query responder: answers an insurer's query with the documents it asks for (design section 4).

It looks for each requested document among the claim's documents. If one isn't there, it says so
in the draft and asks a person, who can find the record before anything is sent.
"""

from backend.agents.base import HUMAN, AgentResult
from backend.agents.hospital.intake import Reading
from backend.schemas import AgentOutput, DocumentType, Finding, Query, Response
from data.generator.render import TITLES

AGENT = "query_responder"
SOURCE = "Claim document store"


def run(claim_id: str, query: Query, reading: Reading, round: int) -> AgentResult:
    asked = query.missing.lower()
    wanted = [doc_type for doc_type in DocumentType if TITLES[doc_type].lower() in asked]
    found = [doc for doc_type in wanted if (doc := reading.get(doc_type)) is not None]
    not_found = [TITLES[t] for t in wanted if reading.get(t) is None]
    if not wanted:  # the query asks for something that isn't one of our document types
        not_found = [query.missing.removeprefix("Please send: ")]

    sentences = []
    if found:
        sentences.append("Attached as requested: " + ", ".join(f"{TITLES[doc.type]} {doc.doc_id}" for doc in found) + ".")
    if not_found:
        sentences.append("Not yet in the claim file, being retrieved: " + "; ".join(not_found) + ".")
    response = Response(
        claim_id=claim_id, stage=query.stage, round=round, answer=" ".join(sentences), documents=[doc.doc_id for doc in found]
    )

    findings = [Finding(issue="Insurer asked", evidence=f"{query.missing} ({query.reason})", source=f"Insurer query, round {query.round}")]
    findings += [Finding(issue=f"Found {TITLES[doc.type]}", evidence=f"Attaching {doc.doc_id}", source=doc.source()) for doc in found]
    findings += [Finding(issue=f"Not found: {name}", evidence="No such document in the claim; a person needs to find it", source=SOURCE) for name in not_found]
    return AgentResult(
        output=AgentOutput(
            agent=AGENT,
            claim_id=claim_id,
            decision="document_missing" if not_found else "answered",
            confidence=0.5 if not_found else 0.95,
            findings=findings,
            next=HUMAN if not_found else "human_approval",
        ),
        data=response,
    )
