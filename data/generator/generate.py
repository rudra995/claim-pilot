"""Generate the synthetic dataset. Same seed, same data, on every machine.

    python -m data.generator.generate [--db claimpilot.db] [--out data/generated]

Writes patients, policies, rules, admissions, bill lines and document lists to the database, and to
`out`: cases.jsonl (full structured cases, the ground truth documents are written from in phase 3)
and labels.jsonl (planted problem and claim outcome per admission).
Rerunning replaces only generated rows (ids starting SYN-); anything else in the database is kept.
"""

import argparse
import random
import sqlite3
from collections import Counter
from pathlib import Path

from backend.db.db import DEFAULT_DB_PATH, init_db
from backend.schemas import Case
from data.generator.admissions import ID_PREFIX, SHARES, Generated, Problem, make_admission
from data.generator.outcomes import ClaimLabel, adjudicate

SEED = 2026
N_ADMISSIONS = 1000
DEFAULT_OUT_DIR = Path("data/generated")

# Child tables first, so foreign keys hold while deleting.
_GENERATED_ROWS = [
    ("documents", "admission_id"),
    ("bill_lines", "admission_id"),
    ("admissions", "admission_id"),
    ("rules", "policy_id"),
    ("policies", "policy_id"),
    ("patients", "patient_id"),
]


def generate(n: int = N_ADMISSIONS, seed: int = SEED) -> list[tuple[Generated, ClaimLabel]]:
    rng = random.Random(seed)
    results = []
    for i, problem in enumerate(_problem_plan(rng, n), start=1):
        generated = make_admission(rng, i, problem)
        results.append((generated, adjudicate(generated, rng)))
    return results


def write_db(conn: sqlite3.Connection, cases: list[Case]) -> None:
    """Replace all generated rows in one transaction. Fails, changing nothing, if claims point at them."""
    with conn:
        for table, column in _GENERATED_ROWS:
            conn.execute(f"DELETE FROM {table} WHERE {column} LIKE ?", (f"{ID_PREFIX}%",))
        for case in cases:
            p, pol, a = case.patient, case.policy, case.admission
            conn.execute("INSERT INTO patients VALUES (?, ?, ?, ?, ?)", (p.patient_id, p.name, p.age, p.gender, p.city))
            conn.execute(
                "INSERT INTO policies VALUES (?, ?, ?, ?, ?)",
                (pol.policy_id, pol.insurer, pol.product, pol.sum_insured, pol.start_date.isoformat()),
            )
            conn.executemany(
                "INSERT INTO rules VALUES (?, ?, ?, ?, ?, ?)",
                [(r.rule_id, pol.policy_id, r.type.value, r.model_dump_json(), r.clause_id, int(r.verified_by_human))
                 for r in pol.rules],
            )
            conn.execute(
                "INSERT INTO admissions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (a.admission_id, a.patient_id, a.policy_id, a.procedure, a.icd10, a.room_category,
                 a.admit_date.isoformat(), a.discharge_date.isoformat(), a.tpa_id),
            )
            conn.executemany(
                "INSERT INTO bill_lines VALUES (?, ?, ?, ?, ?, ?)",
                [(l.line_id, a.admission_id, l.category.value, l.description, l.quantity, l.amount)
                 for l in case.bill_lines],
            )
            conn.executemany(
                "INSERT INTO documents (doc_id, admission_id, type, file_path) VALUES (?, ?, ?, ?)",
                [(d.doc_id, a.admission_id, d.type.value, d.file_path) for d in case.documents],
            )


def write_files(out_dir: Path, results: list[tuple[Generated, ClaimLabel]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cases.jsonl").write_text(
        "".join(g.case.model_dump_json() + "\n" for g, _ in results), encoding="utf-8"
    )
    (out_dir / "labels.jsonl").write_text(
        "".join(label.model_dump_json() + "\n" for _, label in results), encoding="utf-8"
    )


def _problem_plan(rng: random.Random, n: int) -> list[Problem]:
    """Exactly the planted-problem shares from design section 8, in random order."""
    counts = {problem: round(share * n) for problem, share in SHARES.items()}
    counts[Problem.CLEAN] += n - sum(counts.values())
    plan = [problem for problem, count in counts.items() for _ in range(count)]
    rng.shuffle(plan)
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    results = generate()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(args.db)
    try:
        write_db(conn, [g.case for g, _ in results])
    finally:
        conn.close()
    write_files(args.out, results)

    labels = [label for _, label in results]
    print(f"Wrote {len(results)} admissions to {args.db} and {args.out}/")
    for problem, count in Counter(l.planted_problem for l in labels).most_common():
        print(f"  {problem:<25} {count / len(labels):6.1%}")
    for outcome, count in Counter(l.outcome for l in labels).most_common():
        print(f"  {outcome:<25} {count / len(labels):6.1%}")


if __name__ == "__main__":
    main()
