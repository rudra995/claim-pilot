"""Store one case (patient, policy, rules, admission, bill, documents) so a claim can be opened on it.

Rows that already exist are kept, so the same fixture can be run again and again.
"""

import sqlite3

from backend.schemas import Case


def save_case(conn: sqlite3.Connection, case: Case) -> None:
    p, pol, a = case.patient, case.policy, case.admission
    with conn:
        conn.execute("INSERT OR IGNORE INTO patients VALUES (?, ?, ?, ?, ?)", (p.patient_id, p.name, p.age, p.gender, p.city))
        conn.execute(
            "INSERT OR IGNORE INTO policies VALUES (?, ?, ?, ?, ?)",
            (pol.policy_id, pol.insurer, pol.product, pol.sum_insured, pol.start_date.isoformat()),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO rules VALUES (?, ?, ?, ?, ?, ?)",
            [(r.rule_id, pol.policy_id, r.type.value, r.model_dump_json(), r.clause_id, int(r.verified_by_human))
             for r in pol.rules],
        )
        conn.execute(
            "INSERT OR IGNORE INTO admissions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (a.admission_id, a.patient_id, a.policy_id, a.procedure, a.icd10, a.room_category,
             a.admit_date.isoformat(), a.discharge_date.isoformat(), a.tpa_id),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO bill_lines VALUES (?, ?, ?, ?, ?, ?)",
            [(l.line_id, a.admission_id, l.category.value, l.description, l.quantity, l.amount) for l in case.bill_lines],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO documents (doc_id, admission_id, type, file_path) VALUES (?, ?, ?, ?)",
            [(d.doc_id, a.admission_id, d.type.value, d.file_path) for d in case.documents],
        )
