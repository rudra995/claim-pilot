"""ClaimPilot assistant: answers questions from cited sources only, and checks every answer before it is shown.

Two audiences, one method:

- A patient asks about their cover and claim. Sources are their own policy clauses, the claim
  updates a billing officer has already published, a glossary, and reference policy wordings from
  policy search (clearly labelled as not their policy). Anything that needs judgement about the
  claim (why it was cut, will it be approved, can it be changed) is not answered by the assistant:
  it becomes a question in the officer's queue, and the officer's reply is what the patient sees.
  That keeps the rule that nothing new about a claim reaches a patient without a person.
- A billing officer asks about a claim or the desk. Sources are the claim's state, the insurer
  thread with each deduction's clause, the agents' findings, and the policy. Read-only: it never
  sends, approves or changes anything.

Every answer carries numbered sources [S1], [S2]... An LLM may phrase the answer from those
sources; the verifier then checks that every number in it appears in a source, every citation
exists, and nothing promises an outcome. If there is no model, or the check fails, the answer is
built from the sources' own words instead. The LLM never does arithmetic: every rupee shown comes
from a source, and those come from the rules engine.
"""

import re
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from backend import llm
from backend.db import audit
from backend.db.db import connect
from backend.exchange import messages as exchange
from backend.privacy import Masker
from backend.schemas import Case

ACTOR = "assistant"
REFERENCE_DISTANCE = 0.72  # policy-search hits further than this aren't relevant enough to show

GLOSSARY = {
    "tpa": "A TPA (third party administrator) processes claims for the insurer: it reviews the hospital's request and decides what is paid.",
    "pre-auth": "Pre-authorisation is the approval the hospital asks for at admission, with an estimated cost.",
    "preauth": "Pre-authorisation is the approval the hospital asks for at admission, with an estimated cost.",
    "enhancement": "An enhancement is a request for more money when the bill grows past what was approved.",
    "cashless": "Cashless means the insurer pays the hospital directly; you pay only what the policy doesn't cover.",
    "co-pay": "A co-pay is a fixed share of every claim that you always pay yourself.",
    "copay": "A co-pay is a fixed share of every claim that you always pay yourself.",
    "sub-limit": "A sub-limit is a cap on what the policy pays for one procedure or item.",
    "waiting period": "A waiting period is the time after the policy starts during which some conditions aren't covered.",
    "non-payable": "Non-payable items are things insurers never pay for, such as gloves, masks and admission kits.",
    "proportionate deduction": "If the room costs more than the policy's room limit, linked charges (doctor fees, nursing, operation theatre) are paid in the same ratio as the room rent. Implants and medicines usually aren't cut.",
    "sum insured": "The sum insured is the most the policy pays in a policy year.",
    "deduction": "A deduction is the part of the bill the insurer refuses to pay.",
    "final approval": "Final approval is the insurer's approval of the final bill at discharge.",
}

# Words that point a question at a kind of policy rule.
RULE_WORDS = {
    "room_rent_cap": ["room", "rent", "ward", "suite", "deluxe", "bed"],
    "icu_cap": ["icu", "intensive"],
    "co_pay": ["co-pay", "copay", "co pay", "my share", "percentage"],
    "waiting_period": ["waiting", "wait", "months", "pre-existing", "since"],
    "non_payable": ["gloves", "mask", "kit", "consumable", "non-payable", "non payable", "not payable"],
    "sub_limit": ["sub-limit", "sublimit", "limit", "cap", "maximum", "upto", "up to"],
    "exclusion": ["exclude", "excluded", "exclusion", "not covered", "cosmetic", "covered"],
}

# Questions about the claim that need a person's judgement. The assistant hands these off.
HANDOFF = re.compile(
    r"\b(why (was|is|did|has)\b.*\b(cut|reduc|deduct|den|reject|lower|less)|appeal|dispute|denied|denial|reject|"
    r"will (my|the|it|this)\b.*\bapprov|guarantee|refund|complain|mistake|wrong|unfair|negotiat|"
    r"talk to|speak to|call me|human|officer|change (my|the) (claim|bill|room))",
    re.IGNORECASE,
)
STATUS = re.compile(r"\b(status|update|progress|stage|where is|approved yet|any news|what('s| is) happening|when will)\b", re.IGNORECASE)
MONEY = re.compile(r"(how much|₹|rupee|\bpay\b|\bcost|estimate|amount|bill|my share|out of pocket|price)", re.IGNORECASE)
GREETING = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|namaste)\b[\s!.]*$", re.IGNORECASE)
# Affirmative promises only: "not a payment guarantee" is the kind of caveat we want to keep.
PROMISE = re.compile(
    r"\b((we|i|they|the insurer|it) (guarantee|promise)s?|guaranteed|will (definitely|surely|certainly) be|will be (fully )?(approved|paid|covered)|100% covered)\b",
    re.IGNORECASE,
)
NUMBER = re.compile(r"(?<![A-Za-z\d])\d[\d,]*(?:\.\d+)?")
CITE = re.compile(r"\[(S\d+)\]")


@dataclass(frozen=True)
class Source:
    id: str
    kind: str  # policy, claim, glossary, reference, thread, agent, desk
    label: str
    text: str


def inr(amount: int | None) -> str:
    """Whole rupees in Indian grouping: 240000 -> ₹2,40,000. Formatting only, no arithmetic on amounts."""
    if amount is None:
        return "not set"
    digits = str(abs(int(amount)))
    head, groups = digits[:-3], [digits[-3:]]
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ("-" if amount < 0 else "") + "₹" + ",".join(groups)


# ---- Verification ------------------------------------------------------------------------------


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUMBER.findall(CITE.sub("", text))}


def verify(answer: str, sources: list[Source]) -> list[str]:
    """Problems that stop an answer being shown. An empty list means it passed."""
    problems = []
    ids = {s.id for s in sources}
    cited = set(CITE.findall(answer))
    if not cited:
        problems.append("cites no source")
    if unknown := cited - ids:
        problems.append(f"cites sources that don't exist: {', '.join(sorted(unknown))}")
    known = set().union(*(_numbers(f"{s.label} {s.text}") for s in sources)) if sources else set()
    if unbacked := _numbers(answer) - known:
        problems.append(f"states numbers no source contains: {', '.join(sorted(unbacked))}")
    if PROMISE.search(answer):
        problems.append("promises an outcome")
    return problems


def _finish(intent: str, sources: list[Source], extractive: str, prompt_facts: str | None, *,
            system: str, db_path: Path | str | None = None, claim_id: str | None = None,
            masker: Masker | None = None, handoff: dict | None = None) -> dict[str, Any]:
    """Phrase with the LLM if it can, check it, and fall back to the sources' own words if not."""
    checks: list[str] = []
    method = "extractive"
    text = extractive
    if prompt_facts and sources:
        numbered = "\n".join(f"[{s.id}] {s.label}: {s.text}" for s in sources)
        prompt = f"Sources:\n{numbered}\n\nQuestion: {prompt_facts}\n\nAnswer using only the sources. Cite [S#] after each fact."
        try:
            if db_path is not None and claim_id is not None:
                with llm.for_claim(db_path, claim_id, ACTOR, masker or Masker()):
                    drafted = llm.complete(prompt, system=system, seed=7, temperature=0.1).strip()
            else:
                drafted = llm.complete(prompt, system=system, seed=7, temperature=0.1).strip()
            problems = verify(drafted, sources)
            if problems:
                checks.append("Model answer rejected: " + "; ".join(problems))
            else:
                text, method = drafted, "llm"
        except llm.LLMUnavailable:
            checks.append("No model available: answer built from the sources' own words")
    problems = verify(text, sources) if sources else []
    if problems:  # an extractive answer that fails means the composer is wrong; never show it unchecked
        checks.append("Answer failed its own check: " + "; ".join(problems))
    return {
        "answer": text,
        "sources": [asdict(s) for s in sources],
        "verified": bool(sources) and not problems,
        "method": method,
        "intent": intent,
        "checks": checks,
        "handoff": handoff,
    }


# ---- Sources -----------------------------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z][a-z\-]+", text.lower()) if len(t) > 2}


def policy_sources(case: Case, question: str, start: int, limit: int = 2, prefix: str = "Your policy") -> list[Source]:
    """The case's own policy clauses most related to the question."""
    q = question.lower()
    wanted = {rule_type for rule_type, words in RULE_WORDS.items() if any(w in q for w in words)}
    q_tokens = _tokens(question)
    scored = []
    for rule in case.policy.rules:
        score = (3 if rule.type.value in wanted else 0) + len(q_tokens & _tokens(rule.clause_text))
        if score >= 2:
            scored.append((score, rule))
    scored.sort(key=lambda pair: -pair[0])
    seen, sources = set(), []
    for _, rule in scored:
        if rule.clause_text in seen:
            continue
        seen.add(rule.clause_text)
        sources.append(Source(f"S{start + len(sources)}", "policy", f"{prefix}, clause {rule.clause_id}", rule.clause_text))
        if len(sources) == limit:
            break
    return sources


def glossary_sources(question: str, start: int) -> list[Source]:
    q = question.lower()
    found, seen = [], set()
    for term, meaning in GLOSSARY.items():
        if term in q and meaning not in seen:
            seen.add(meaning)
            found.append(Source(f"S{start + len(found)}", "glossary", f"Glossary: {term}", meaning))
    return found[:2]


def reference_sources(question: str, start: int) -> list[Source]:
    """Clauses from the reference policy wordings in policy search. Never the patient's own policy."""
    try:
        from backend.rag.search import search
        hits = search(question, k=2)
    except Exception:  # policy search not built on this machine: answer without it
        return []
    return [
        Source(f"S{start + i}", "reference", f"Reference wording (not your policy): {h.policy_id.replace('_', ' ')}, clause {h.clause_id}",
               re.sub(r"\s+", " ", h.text)[:420])
        for i, h in enumerate(h for h in hits if h.distance <= REFERENCE_DISTANCE)
    ]


def published_update(db_path: Path | str, claim_id: str) -> dict | None:
    """The latest claim update a billing officer approved for the patient, if any."""
    import json
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT snapshot_json, approved_by, created_at FROM portal_updates WHERE claim_id = ? ORDER BY update_id DESC LIMIT 1",
            (claim_id,),
        ).fetchone()
    if row is None:
        return None
    return {**json.loads(row["snapshot_json"]), "approved_by": row["approved_by"], "published_at": row["created_at"]}


# ---- Patients ----------------------------------------------------------------------------------

PATIENT_SYSTEM = (
    "You answer a hospital patient's question about their health insurance in plain, warm English, in at most "
    "four short sentences. Use only the numbered sources. Put [S#] after every fact. Never state a number that is "
    "not in a source and never calculate. Never promise that anything will be approved or paid. Say clearly when "
    "a source is a reference wording rather than the patient's own policy. If the sources don't answer the "
    "question, say you can't confirm it and that the billing officer can help."
)


def ask_patient(db_path: Path | str, question: str, claim_id: str | None, case: Case | None, patient_id: str) -> dict[str, Any]:
    """Answer a patient, or hand the question to their billing officer."""
    question = question.strip()
    if GREETING.match(question):
        return _record(db_path, claim_id, patient_id, question, _finish("greeting", [], "Hello! I can explain your cover, your claim's latest reviewed update, and insurance terms. Ask me anything, and I'll pass questions about decisions to your billing officer.", None, system=PATIENT_SYSTEM))

    update = published_update(db_path, claim_id) if claim_id else None
    if claim_id and HANDOFF.search(question):
        return _record(db_path, claim_id, patient_id, question, _handoff(db_path, claim_id, patient_id, question, "judgement"))

    sources: list[Source] = []
    if (STATUS.search(question) or MONEY.search(question)) and claim_id:
        if update is None:
            text = ("Your billing officer hasn't published an update on this claim yet. Each update is reviewed by a person before you see it, "
                    "so I can't share figures or progress before then.")
            return _record(db_path, claim_id, patient_id, question, _finish("status", [], text, None, system=PATIENT_SYSTEM))
        est = update["estimate"]
        sources.append(Source("S1", "claim", f"Update published {update['published_at'][:10]} by {update['approved_by']}",
                              f"Status: {update['status']}. Current bill {inr(est['billed'])}; estimated insurer share {inr(est['insurer'])}; "
                              f"estimated patient share {inr(est['patient'])}. {update['estimate_label']}"))
        if MONEY.search(question):
            text = (f"From the latest update your billing officer reviewed, the bill is {inr(est['billed'])}: the insurer's share is about "
                    f"{inr(est['insurer'])} and yours about {inr(est['patient'])} [S1]. This is an estimate, not a payment guarantee [S1].")
            intent = "money"
        else:
            text = f"The latest reviewed update says: {update['status']} [S1]. It was published on {update['published_at'][:10]} by {update['approved_by']} [S1]."
            intent = "status"
        sources += policy_sources(case, question, len(sources) + 1, limit=1) if case and MONEY.search(question) else []
        return _record(db_path, claim_id, patient_id, question, _finish(intent, sources, text, question, system=PATIENT_SYSTEM, db_path=db_path, claim_id=claim_id, masker=Masker.for_case(case) if case else None))

    if case:
        sources += policy_sources(case, question, 1)
    sources += glossary_sources(question, len(sources) + 1)
    if not any(s.kind == "policy" for s in sources):
        sources += reference_sources(question, len(sources) + 1)
    if not sources:
        if claim_id:
            return _record(db_path, claim_id, patient_id, question, _handoff(db_path, claim_id, patient_id, question, "unanswered"))
        return _finish("unanswered", [], "I couldn't find that in your policy or the glossary. Please ask the hospital billing desk.", None, system=PATIENT_SYSTEM)

    lines = []
    for s in sources:
        if s.kind == "policy":
            lines.append(f"{s.label} says: “{s.text}” [{s.id}]")
        elif s.kind == "glossary":
            lines.append(f"{s.text} [{s.id}]")
        else:
            lines.append(f"This isn't from your policy, but a reference wording says: “{s.text[:260]}…” [{s.id}]")
    if any(s.kind == "reference" for s in sources) and not any(s.kind == "policy" for s in sources):
        lines.append("Your own policy may differ; your billing officer can confirm.")
    text = " ".join(lines)
    return _record(db_path, claim_id, patient_id, question, _finish("policy", sources, text, question, system=PATIENT_SYSTEM, db_path=db_path, claim_id=claim_id, masker=Masker.for_case(case) if case else None))


def _handoff(db_path: Path | str, claim_id: str, patient_id: str, question: str, why: str) -> dict[str, Any]:
    question_id = secrets.token_hex(8)
    audit.log(db_path, claim_id, f"patient:{patient_id}", "patient_question", {"question_id": question_id, "question": question, "why": why})
    text = ("That needs your billing officer's judgement, so I've sent your question to them. Their answer will appear here once they reply."
            if why == "judgement" else
            "I couldn't find a reliable answer in your policy or claim updates, so I've sent your question to your billing officer. Their answer will appear here.")
    return {**_finish("handoff", [], text, None, system=PATIENT_SYSTEM), "handoff": {"question_id": question_id, "status": "waiting"}}


def _record(db_path: Path | str, claim_id: str | None, patient_id: str, question: str, result: dict[str, Any]) -> dict[str, Any]:
    if claim_id:
        audit.log(db_path, claim_id, ACTOR, "assistant_exchange", {"audience": "patient", "asked_by": f"patient:{patient_id}", "question": question, **result})
    return result


def patient_history(db_path: Path | str, claim_id: str) -> list[dict[str, Any]]:
    """The patient's past questions on this claim, with any officer replies attached."""
    events = audit.events(db_path, claim_id)
    answers = {e["output"]["question_id"]: {**e["output"], "answered_at": e["created_at"]} for e in events if e["action"] == "patient_question_answered"}
    history = []
    for e in events:
        if e["action"] == "assistant_exchange" and e["output"].get("audience") == "patient":
            item = {**e["output"], "asked_at": e["created_at"]}
            if item.get("handoff"):
                reply = answers.get(item["handoff"]["question_id"])
                item["handoff"] = {**item["handoff"], "status": "answered" if reply else "waiting", "reply": reply}
            history.append(item)
    return history


def open_questions(db_path: Path | str, claim_id: str) -> list[dict[str, Any]]:
    """Patient questions handed to the officer, newest first, with any answer."""
    events = audit.events(db_path, claim_id)
    answered = {e["output"]["question_id"]: {**e["output"], "answered_at": e["created_at"]} for e in events if e["action"] == "patient_question_answered"}
    return [
        {**e["output"], "asked_at": e["created_at"], "asked_by": e["actor"], "answer": answered.get(e["output"]["question_id"])}
        for e in reversed(events) if e["action"] == "patient_question"
    ]


def answer_question(db_path: Path | str, claim_id: str, question_id: str, answer: str, officer: str) -> dict[str, Any]:
    """The officer's reply to a handed-off question. Writing it is the human approval."""
    questions = {q["question_id"]: q for q in open_questions(db_path, claim_id)}
    if question_id not in questions:
        raise KeyError(question_id)
    if questions[question_id]["answer"]:
        raise ValueError("This question has already been answered")
    audit.log(db_path, claim_id, f"human:{officer}", "patient_question_answered", {"question_id": question_id, "answer": answer, "officer": officer})
    return {"question_id": question_id, "answered": True}


# ---- Officers ----------------------------------------------------------------------------------

OFFICER_SYSTEM = (
    "You are a copilot for a hospital billing officer. Answer in at most five short sentences, precisely and "
    "plainly. Use only the numbered sources and put [S#] after every fact. Never calculate or state a number that "
    "is not in a source. You can't send, approve or change anything; say what the officer could do instead."
)
STATE_WORDS = {
    "ADMITTED": "being prepared", "PREAUTH_DRAFT": "being prepared", "RISK_CHECK": "in the risk check",
    "AWAITING_HUMAN": "waiting for your approval", "SUBMITTED": "with the insurer", "QUERY_RAISED": "answering an insurer query",
    "APPROVED": "approved", "PARTIALLY_APPROVED": "partly approved", "DENIED": "denied", "IN_TREATMENT": "in treatment",
    "DISCHARGE_DRAFT": "being discharged", "FINAL_APPROVED": "finally approved, awaiting payment", "SETTLED": "settled",
    "CLOSED": "closed",
}
DESK = re.compile(r"\b(how many|which (claims|patients)|list|queue|today|all claims|desk|pending)\b", re.IGNORECASE)

# Light hints on top of semantic search: a question that clearly asks "why was it cut" should
# prefer the insurer's decisions, "what should I fix" the risk checker, and so on.
HINTS = [
    (re.compile(r"\b(why|cut|deduct|reduc|denied|deny|partial|appeal|lower|short)\b", re.IGNORECASE), {"thread", "appeal", "reconciliation"}),
    (re.compile(r"\b(risk|flag|fix|problem|issue|missing|wrong|before (i )?send)\b", re.IGNORECASE), {"denial_risk"}),
    (re.compile(r"\b(next|status|stage|state|where|stuck|waiting)\b", re.IGNORECASE), {"claim"}),
    (re.compile(r"\b(policy|clause|cover|covered|limit|cap|sub-limit|co-pay|copay|exclu|waiting period|room rent)\b", re.IGNORECASE), {"policy"}),
    (re.compile(r"\b(estimate|typical|cost|bill|stay|days)\b", re.IGNORECASE), {"cost_estimator", "discharge"}),
    (re.compile(r"\b(paid|payment|settle|recover|reconcil)\b", re.IGNORECASE), {"reconciliation"}),
    (re.compile(r"\b(diagnos|icd|code|coded)\b", re.IGNORECASE), {"clinical_coder"}),
    (re.compile(r"\b(document|report|x-ray|xray|upload|attach|query|asked)\b", re.IGNORECASE), {"document_reader", "query_responder", "thread"}),
]
HINT_BOOST = 0.15
MIN_SCORE = 0.22  # cosine similarity below this isn't about the question
TOP_K = 4
NO_MATCH = ("Nothing in this claim's record covers that. Try asking about the policy, the insurer's decisions, "
            "risks to fix, costs, documents or the diagnosis.")

AGENT_NAMES = {
    "document_reader": "Document reader", "clinical_coder": "Clinical coder", "policy": "Policy checker",
    "cost_estimator": "Cost estimator", "preauth": "Pre-auth writer", "denial_risk": "Risk checker",
    "query_responder": "Query responder", "discharge": "Discharge writer", "appeal": "Appeal writer",
    "reconciliation": "Reconciliation",
}


@dataclass(frozen=True)
class Chunk:
    """One retrievable piece of the claim record: what an officer could be asking about."""
    kind: str  # Source.kind shown to the officer
    topic: str  # what HINTS match: an agent name, "thread", "policy", "claim", "glossary", "desk"
    label: str
    text: str


_vectors: dict[str, list[float]] = {}  # embeddings by text; the record changes slowly, so they're reused
_embedder: Any = None


def _embed(texts: list[str]) -> list[list[float]] | None:
    """Sentence embeddings from Chroma's default model (all-MiniLM-L6-v2), or None if it can't load."""
    global _embedder
    try:
        if _embedder is None:
            from chromadb.utils import embedding_functions
            _embedder = embedding_functions.DefaultEmbeddingFunction()
        missing = [t for t in dict.fromkeys(texts) if t not in _vectors]
        if missing:
            for text, vector in zip(missing, _embedder(missing)):
                _vectors[text] = [float(x) for x in vector]
        return [_vectors[t] for t in texts]
    except Exception:  # model not downloaded on this machine: rank by shared words instead
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return dot / norm if norm else 0.0


def retrieve(question: str, chunks: list[Chunk], k: int = TOP_K) -> list[Chunk]:
    """The chunks most related to the question: semantic similarity plus a small boost from HINTS."""
    if not chunks:
        return []
    texts = [f"{c.label}. {c.text}" for c in chunks]
    vectors = _embed([question, *texts])
    if vectors is not None:
        q = vectors[0]
        similarity = [_cosine(q, v) for v in vectors[1:]]
    else:
        q_tokens = _tokens(question)
        similarity = [len(q_tokens & _tokens(t)) / (len(q_tokens) or 1) for t in texts]
    wanted = set().union(*(topics for pattern, topics in HINTS if pattern.search(question)))
    asked = question.lower()

    def boost(c: Chunk) -> float:
        if c.topic == "glossary":  # the exact term asked about, longest match first
            term = c.label.split(": ", 1)[1]
            return 0.1 + 0.01 * len(term) if term in asked else 0.0
        return HINT_BOOST if c.topic in wanted else 0.0

    scored = sorted(((sim + boost(c), i) for i, (sim, c) in enumerate(zip(similarity, chunks))), reverse=True)
    picked: list[Chunk] = []
    for score, i in scored:
        if len(picked) == k or score < MIN_SCORE:
            break
        # skip a long chunk that mostly repeats one already picked (a finding quoting its clause, say)
        words = _tokens(chunks[i].text.split(" (source:")[0])
        if len(words) >= 8 and any(len(words & _tokens(p.text)) > 0.8 * len(words) for p in picked):
            continue
        picked.append(chunks[i])
    return picked


def _claim_chunks(db_path: Path | str, claim: dict, case: Case | None, latest: dict[str, dict]) -> list[Chunk]:
    claim_id = claim["claim_id"]
    chunks = [Chunk("claim", "claim", f"Claim {claim_id}",
                    f"{claim['patient']}, {claim['procedure'].replace('_', ' ')}, {claim['insurer']} via {claim['tpa_id']}. "
                    f"State: {STATE_WORDS.get(claim['state'], claim['state'])}. Requested {inr(claim['requested'])}; "
                    f"approved {inr(claim['approved'])}. Last step: {claim.get('last_reason') or 'none recorded'}.")]

    for m in exchange.thread(db_path, claim_id):
        who = "Insurer" if m.sender.value == "insurer" else "Hospital"
        label = f"{who} {m.type.value.replace('_', ' ').lower()}, {m.stage.value} round {m.round}"
        parts = []
        if (amount := getattr(m, "approved_amount", None)) is not None:
            parts.append(f"Approved {inr(amount)}.")
        if (amount := getattr(m, "requested_amount", None)) is not None:
            parts.append(f"Requested {inr(amount)}.")
        if deductions := getattr(m, "deductions", None):
            parts.append("Deductions: " + "; ".join(f"{d.item.split(':')[0]} {inr(d.amount)} (clause {d.clause})" for d in deductions) + ".")
        for field in ("missing", "reason", "answer", "argument"):
            if value := getattr(m, field, None):
                parts.append(f"{field.capitalize()}: {value}.")
        if clause := getattr(m, "clause", None):
            parts.append(f"Clause {clause}.")
        if parts:
            chunks.append(Chunk("thread", "thread", label, " ".join(parts)))

    for actor, record in latest.items():
        if actor.startswith("tpa_"):  # the insurer's own reviewers: the hospital only sees their messages
            continue
        output = record.get("output") or {}
        name = AGENT_NAMES.get(actor, actor.replace("_", " ").capitalize())
        for finding in output.get("findings", []):
            if actor == "denial_risk" and finding["issue"] != "Denial risk":
                continue  # each flagged item is added below, with its fix
            evidence = finding["evidence"].split("; model weight")[0]
            chunks.append(Chunk("agent", actor, f"{name}: {finding['issue']}", f"{evidence} (source: {finding['source']})"))
        if actor == "denial_risk":
            items = (record.get("data") or {}).get("items", [])
            if not items:
                chunks.append(Chunk("agent", actor, "Risk checker: nothing to fix", "No issues found on the latest package; nothing is flagged to fix before sending."))
            for item in items:
                chunks.append(Chunk("agent", actor, f"Risk checker: {item['issue']}", f"{item['evidence'].split('; model weight')[0]}. Fix: {item['fix']}"))

    if case is not None:
        seen = set()
        for rule in case.policy.rules:
            if rule.clause_text not in seen:
                seen.add(rule.clause_text)
                chunks.append(Chunk("policy", "policy", f"Policy, clause {rule.clause_id}", rule.clause_text))
    return chunks


def _glossary_chunks() -> list[Chunk]:
    seen, chunks = set(), []
    for term, meaning in GLOSSARY.items():
        if meaning not in seen:
            seen.add(meaning)
            chunks.append(Chunk("glossary", "glossary", f"Glossary: {term}", meaning))
    return chunks


def _desk_chunks(rows: list[dict]) -> list[Chunk]:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    by_state = "; ".join(f"{STATE_WORDS.get(state, state)}: {n}" for state, n in sorted(counts.items())) or "none"
    chunks = [Chunk("desk", "desk", "Claims on this desk", f"Total {len(rows)} claims. By state: {by_state}")]
    waiting = [r for r in rows if r["state"] == "AWAITING_HUMAN"]
    if waiting:
        chunks.append(Chunk("desk", "desk", "Waiting for a person",
                            "; ".join(f"{r['patient']} ({r['claim_id']}): {r.get('last_reason') or 'needs review'}" for r in waiting[:6])))
    for r in rows[:30]:
        chunks.append(Chunk("desk", "desk", f"Claim {r['claim_id']}",
                            f"{r['patient']}, {r['procedure'].replace('_', ' ')}: {STATE_WORDS.get(r['state'], r['state'])}. "
                            f"Requested {inr(r['requested'])}; approved {inr(r['approved'])}. Last step: {r.get('last_reason') or 'none'}."))
    return chunks


def _compose(picked: list[Chunk], refs: list[Source]) -> tuple[list[Source], str]:
    """Number the retrieved chunks as sources and quote them, most relevant first."""
    sources = [Source(f"S{i + 1}", c.kind, c.label, c.text) for i, c in enumerate(picked)]
    for ref in refs:
        sources.append(Source(f"S{len(sources) + 1}", ref.kind, ref.label, ref.text))
    lines = []
    for n, s in enumerate(sources):
        text = s.text if len(s.text) <= 320 else s.text[:320].rsplit(" ", 1)[0] + "…"
        lines.append(f"{'Best match ·' if n == 0 else '•'} {s.label}: {text} [{s.id}]")
    return sources, "\n".join(lines)


def ask_officer(db_path: Path | str, question: str, claim: dict | None, case: Case | None, latest: dict[str, dict], rows: list[dict]) -> dict[str, Any]:
    """Answer an officer by retrieving the parts of the claim record (or the desk) closest to the question.

    Retrieval is semantic (sentence embeddings over the claim's state, insurer thread, agent findings,
    policy clauses and glossary), nudged by a few keyword hints. The top chunks become the numbered
    sources; the answer quotes them, or an LLM phrases them and the verifier checks it.
    """
    question = question.strip()
    if claim is None:
        chunks = _desk_chunks(rows) + _glossary_chunks()
        if DESK.search(question):  # the desk summary leads any question about the queue
            picked = chunks[:2] + [c for c in retrieve(question, chunks[2:], k=2) if c not in chunks[:2]]
            refs = []
        else:
            picked = retrieve(question, chunks)
            refs = reference_sources(question, 0) if not any(c.kind == "glossary" for c in picked[:1]) else []
            if not picked and not refs:
                return _finish("desk", [], NO_MATCH, None, system=OFFICER_SYSTEM)
        sources, text = _compose(picked, refs[:1])
        return _finish("desk", sources, text, question, system=OFFICER_SYSTEM)

    chunks = _claim_chunks(db_path, claim, case, latest) + _glossary_chunks()
    picked = retrieve(question, chunks)
    if not picked:
        return _finish("claim", [], NO_MATCH, None, system=OFFICER_SYSTEM)
    sources, text = _compose(picked, [])
    return _finish("claim", sources, text, question, system=OFFICER_SYSTEM, db_path=db_path, claim_id=claim["claim_id"],
                   masker=Masker.for_case(case) if case else None)
