"""Phase 2 checks: 1,000 admissions, planted-problem shares, identical reruns."""

import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from backend.db.db import init_db
from backend.rules.engine import assess
from backend.schemas import ClaimState
from data.generator.admissions import SHARES, Generated, Problem, detect_problems
from data.generator.generate import N_ADMISSIONS, generate, write_db, write_files
from data.generator.outcomes import ClaimLabel

Results = list[tuple[Generated, ClaimLabel]]


@pytest.fixture(scope="module")
def results() -> Results:
    return generate()


@pytest.fixture(scope="module")
def db(results: Results, tmp_path_factory: pytest.TempPathFactory) -> sqlite3.Connection:
    conn = init_db(tmp_path_factory.mktemp("db") / "gen.db")
    write_db(conn, [g.case for g, _ in results])
    return conn


def test_thousand_admissions_in_database(db: sqlite3.Connection) -> None:
    assert db.execute("SELECT COUNT(*) FROM admissions").fetchone()[0] == N_ADMISSIONS
    for table in ("patients", "policies"):
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == N_ADMISSIONS


def test_about_fifteen_bill_lines_each(db: sqlite3.Connection) -> None:
    lines = db.execute("SELECT COUNT(*) FROM bill_lines").fetchone()[0]
    assert 14 <= lines / N_ADMISSIONS <= 16


@pytest.mark.parametrize("problem", list(Problem))
def test_planted_problem_share_within_3_points(results: Results, problem: Problem) -> None:
    # Count what the engine and checklist actually find in the data, not just the label.
    found = Counter(
        next(iter(detect_problems(g.case)), Problem.CLEAN) for g, _ in results
    )
    assert abs(found[problem] / len(results) - SHARES[problem]) <= 0.03


def test_each_admission_has_exactly_its_planted_problem(results: Results) -> None:
    for generated, label in results:
        expected = set() if generated.problem == Problem.CLEAN else {generated.problem}
        assert detect_problems(generated.case) == expected, generated.case.case_id
        assert label.planted_problem == generated.problem


def test_rerun_gives_identical_data(results: Results, db: sqlite3.Connection, tmp_path: Path) -> None:
    again = generate()
    first, second = tmp_path / "first", tmp_path / "second"
    write_files(first, results)
    write_files(second, again)
    for name in ("cases.jsonl", "labels.jsonl"):
        assert (first / name).read_bytes() == (second / name).read_bytes()

    conn = init_db(tmp_path / "again.db")
    write_db(conn, [g.case for g, _ in again])
    assert list(conn.iterdump()) == list(db.iterdump())


def test_rerun_replaces_only_generated_rows(results: Results, tmp_path: Path) -> None:
    conn = init_db(tmp_path / "mixed.db")
    conn.execute("INSERT INTO patients VALUES ('PAT-0001', 'Demo Patient', 34, 'F', 'Bengaluru')")
    cases = [g.case for g, _ in results[:20]]
    write_db(conn, cases)
    write_db(conn, cases)
    assert conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 21


def test_rerun_refuses_to_orphan_claims(results: Results, tmp_path: Path) -> None:
    conn = init_db(tmp_path / "claims.db")
    cases = [g.case for g, _ in results[:5]]
    write_db(conn, cases)
    with conn:
        conn.execute(
            "INSERT INTO claims (claim_id, admission_id, state) VALUES ('C1', ?, 'ADMITTED')",
            (cases[0].admission.admission_id,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        write_db(conn, cases)
    assert conn.execute("SELECT COUNT(*) FROM admissions").fetchone()[0] == 5


def test_outcomes_follow_tpa_profiles(results: Results) -> None:
    labels = [label for _, label in results if not label.noise]
    xray = Counter((l.tpa_id, l.outcome) for l in labels if l.problem_detail == "xray_report")
    reason = Counter((l.tpa_id, l.outcome) for l in labels if l.problem_detail == "reason_for_admission")
    assert xray and {o for (tpa, o) in xray if tpa == "TPA-A"} == {ClaimState.DENIED}
    assert {o for (tpa, o) in xray if tpa != "TPA-A"} == {ClaimState.APPROVED}
    assert reason and {o for (tpa, o) in reason if tpa == "TPA-B"} == {ClaimState.PARTIALLY_APPROVED}
    assert {o for (tpa, o) in reason if tpa != "TPA-B"} == {ClaimState.APPROVED}


def test_tpa_c_cuts_pharmacy_when_room_over_cap(results: Results) -> None:
    tpa_c = [
        (g, l) for g, l in results
        if l.tpa_id == "TPA-C" and g.problem == Problem.ROOM_OVER_CAP and g.case.policy.product != "Family Health Shield"
    ]
    assert tpa_c
    for generated, label in tpa_c:
        assert label.approved_amount < assess(generated.case).payable


def test_labels_are_sane(results: Results) -> None:
    labels = [label for _, label in results]
    assert {l.outcome for l in labels} == {
        ClaimState.APPROVED, ClaimState.PARTIALLY_APPROVED, ClaimState.DENIED,
    }
    assert 0 < sum(l.noise for l in labels) < 0.05 * len(labels)
    for l in labels:
        assert 0 <= l.approved_amount <= l.billed
        assert (l.approved_amount == 0) == (l.outcome == ClaimState.DENIED)
        if l.outcome != ClaimState.APPROVED:
            assert l.reasons
