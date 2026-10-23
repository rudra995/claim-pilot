"""Phase 5 checks: cost coverage, denial ROC-AUC, Case 2's reasons, and the features behind them."""

import json
from pathlib import Path

import pandas as pd
import pytest

from backend.models import train_cost, train_denial
from backend.models.features import DENIAL_FEATURES, FLAGS, denial_features, frame, time_split, training_data
from backend.models.fixes_map import FIXES
from backend.schemas import Case


def load(name: str) -> Case:
    return Case.model_validate_json(Path("fixtures", name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def data() -> pd.DataFrame:
    return training_data()


@pytest.fixture(scope="module")
def trained() -> None:
    """predict.py loads models from models/; train them there if this machine hasn't yet."""
    if not all(train_cost.model_path(t, q).exists() for t in train_cost.TARGETS for q in train_cost.QUANTILES):
        train_cost.main()
    if not train_denial.model_path().exists():
        train_denial.main()


def test_split_is_by_date(data: pd.DataFrame) -> None:
    train, test, cutoff = time_split(data)
    assert (len(train), len(test)) == (800, 200)
    assert train["admit_date"].max() <= cutoff <= test["admit_date"].min()


def test_85_to_95_percent_of_test_bills_under_the_high_estimate(data: pd.DataFrame) -> None:
    _, metrics = train_cost.train(data)
    assert 0.85 <= metrics["bill"]["share_under_p90"] <= 0.95


def test_denial_model_roc_auc_above_075(data: pd.DataFrame) -> None:
    _, metrics = train_denial.train(data)
    print(f"denial ROC-AUC: {metrics['roc_auc_denied']}")
    assert metrics["roc_auc_denied"] > 0.75


def test_metrics_file_is_saved() -> None:
    metrics = json.loads(Path("models/metrics.json").read_text(encoding="utf-8"))
    assert {"cost", "denial"} <= set(metrics)


def test_case2_top_reasons_include_xray_report_and_room_cap(trained: None) -> None:
    from backend.models.predict import denial_risk

    risk = denial_risk(load("case2.json"))
    flags = [reason.flag for reason in risk.reasons]
    assert {"missing_xray_report", "room_over_cap"} <= set(flags)
    assert next(r.fix for r in risk.reasons if r.flag == "missing_xray_report") == "Attach the pre-op X-ray report"


def test_clean_case_is_low_risk(trained: None) -> None:
    from backend.models.predict import denial_risk

    risk = denial_risk(load("case1.json"))
    assert risk.risk < 0.2 and risk.reasons == []


def test_cost_estimate_is_a_sensible_range(trained: None) -> None:
    from backend.models.predict import estimate_cost

    for name in ("case1.json", "case2.json", "case3.json"):
        case = load(name)
        estimate = estimate_cost(case)
        billed = sum(line.amount for line in case.bill_lines)
        assert estimate.bill_typical <= estimate.bill_high and estimate.stay_typical <= estimate.stay_high
        assert billed / 3 < estimate.bill_typical < billed * 3, name


def test_case2_flags() -> None:
    flags = denial_features(load("case2.json"))
    assert flags["missing_xray_report"] == flags["room_over_cap"] == flags["non_payables_billed"] == 1
    assert flags["admission_reason_missing"] == flags["coding_mismatch"] == 0


def test_case3_flags_missing_admission_reason() -> None:
    assert denial_features(load("case3.json"))["admission_reason_missing"] == 1


def test_gender_is_never_a_feature() -> None:
    assert not any("gender" in feature for feature in DENIAL_FEATURES)


def test_unknown_procedure_becomes_missing() -> None:
    case = load("case1.json")
    case = case.model_copy(update={"admission": case.admission.model_copy(update={"procedure": "new_thing"})})
    x = frame([denial_features(case)], DENIAL_FEATURES)
    assert x["procedure"].isna().all()


def test_every_flag_has_a_fix() -> None:
    assert set(FIXES) == set(FLAGS)
