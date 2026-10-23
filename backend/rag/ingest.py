"""Split every policy PDF in data/policies/ into clauses and index them in ChromaDB.

    python -m backend.rag.ingest

Rerunning replaces each policy's clauses, so the index always matches the PDFs.
"""

import re
from pathlib import Path

from backend.rag.clauses import Clause, load_clauses
from backend.rag.search import CHROMA_DIR, CLAUSE_DIR, collection, save_clauses

POLICY_DIR = Path("data/policies")
CHUNK_CHARS = 800  # the embedding model reads about 256 tokens; longer clauses are cut into pieces
CHUNK_OVERLAP = 150
# List items ("i.", "ii.", "(a)", "b)") are indexed separately, so "i. Room rent up to 2% of sum
# insured" isn't drowned out by the ICU and surgeon-fee items listed next to it.
_LIST_ITEM = re.compile(r"\s(?=(?:\(?(?:[ivx]{1,4}|[a-h])\)|(?:[ivx]{1,4}|[a-h])\.)\s)")


def chunks(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Pieces of about `size` characters, cut at spaces, each overlapping the one before."""
    pieces, start = [], 0
    while start < len(text):
        end = len(text) if start + size >= len(text) else text.rfind(" ", start, start + size)
        if end <= start:
            end = start + size
        pieces.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(text.find(" ", end - overlap), start + 1)
    return [p for p in pieces if p]


def pieces(text: str) -> list[str]:
    """What gets embedded for one clause: each list item, cut into chunks if it is long."""
    return [piece for item in _LIST_ITEM.split(text) for piece in chunks(item)]


def ingest_policy(path: Path, chroma_dir: Path = CHROMA_DIR, clause_dir: Path = CLAUSE_DIR) -> list[Clause]:
    policy_id = path.stem
    clauses = load_clauses(path)
    save_clauses(clauses, policy_id, clause_dir)

    store = collection(chroma_dir)
    store.delete(where={"policy_id": policy_id})
    ids, documents, metadatas = [], [], []
    for clause in clauses:
        for n, piece in enumerate(pieces(clause.text)):
            ids.append(f"{policy_id}:{clause.clause_id}:{n}")
            documents.append(f"{clause.title}. {piece}")
            metadatas.append(
                {"policy_id": policy_id, "clause_id": clause.clause_id, "kind": clause.kind}
            )
    for start in range(0, len(ids), 1000):  # Chroma caps the batch size
        end = start + 1000
        store.add(ids=ids[start:end], documents=documents[start:end], metadatas=metadatas[start:end])
    return clauses


def main() -> None:
    paths = sorted(POLICY_DIR.glob("*.pdf"))
    if not paths:
        raise SystemExit(f"No policy PDFs in {POLICY_DIR}/")
    for path in paths:
        clauses = ingest_policy(path)
        print(f"{path.stem}: {len(clauses)} clauses")


if __name__ == "__main__":
    main()
