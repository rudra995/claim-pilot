"""Search policy clauses (design section 6). Answers come back as whole clauses, with their ids as evidence.

Definitions ("Room Rent means the amount charged...") are left out unless asked for: they match
almost any query about their term, and would push out the clauses that set limits and exclusions.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import chromadb
from chromadb.api.models.Collection import Collection

from backend.rag.clauses import Clause

CHROMA_DIR = Path("data/chroma")
CLAUSE_DIR = Path("data/clauses")  # every clause's full text, per policy, written by ingest
COLLECTION = "policy_clauses"


@dataclass(frozen=True)
class ClauseHit:
    policy_id: str
    clause_id: str
    title: str
    page: int
    text: str  # the whole clause
    distance: float  # lower is closer


def collection(chroma_dir: Path = CHROMA_DIR) -> Collection:
    """The clause collection, using Chroma's default embedding model (all-MiniLM-L6-v2)."""
    client = chromadb.PersistentClient(path=str(chroma_dir))
    return client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


def save_clauses(clauses: list[Clause], policy_id: str, clause_dir: Path = CLAUSE_DIR) -> None:
    clause_dir.mkdir(parents=True, exist_ok=True)
    (clause_dir / f"{policy_id}.json").write_text(
        json.dumps([asdict(c) for c in clauses], indent=1, ensure_ascii=False), encoding="utf-8"
    )


def load_clauses(policy_id: str, clause_dir: Path = CLAUSE_DIR) -> dict[str, Clause]:
    data = json.loads((clause_dir / f"{policy_id}.json").read_text(encoding="utf-8"))
    return {c["clause_id"]: Clause(**c) for c in data}


def search(
    query: str,
    policy_id: str | None = None,
    k: int = 3,
    include_definitions: bool = False,
    chroma_dir: Path = CHROMA_DIR,
    clause_dir: Path = CLAUSE_DIR,
) -> list[ClauseHit]:
    """The k clauses closest to the query, best first, optionally within one policy."""
    filters: list[dict] = []
    if policy_id:
        filters.append({"policy_id": policy_id})
    if not include_definitions:
        filters.append({"kind": {"$ne": "definition"}})
    result = collection(chroma_dir).query(
        query_texts=[query],
        n_results=k * 8,  # several chunks can come from one clause
        where=filters[0] if len(filters) == 1 else ({"$and": filters} if filters else None),
    )
    hits: dict[tuple[str, str], ClauseHit] = {}
    texts: dict[str, dict[str, Clause]] = {}
    for meta, distance in zip(result["metadatas"][0], result["distances"][0]):
        key = (str(meta["policy_id"]), str(meta["clause_id"]))
        if key in hits:
            continue
        clauses = texts.setdefault(key[0], load_clauses(key[0], clause_dir))
        clause = clauses[key[1]]
        hits[key] = ClauseHit(clause.policy_id, clause.clause_id, clause.title, clause.page, clause.text, distance)
        if len(hits) == k:
            break
    return list(hits.values())
