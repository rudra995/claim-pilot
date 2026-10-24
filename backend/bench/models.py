"""An honest look at the denial-risk model: against simple rules, calibration, leakage, and subgroups.

    python -m backend.bench.models        (writes reports/model_report.json)

The labels are synthetic: the generator decides each outcome from the same flags the model sees,
the TPA's hidden habits and 3% noise. So a high score shows the pipeline learns planted patterns,
not that it predicts real insurers. Comparing with a one-line rule shows how much the model adds.
"""

import json
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import brier_score_loss, roc_auc_score

from backend.models.features import DENIAL_FEATURES, FLAGS, time_split, training_data
from backend.models.train_denial import CLASSES, fit

OUT = Path("reports/model_report.json")
MIN_GROUP = 30  # below this many claims, a subgroup rate is shown but no conclusion is drawn


def _binary(y: np.ndarray, score: np.ndarray, threshold: float = 0.5) -> dict[str, Any]:
    predicted = score >= threshold
    positives = y.sum()
    return {
        "roc_auc": round(float(roc_auc_score(y, score)), 3) if 0 < positives < len(y) else None,
        "recall": round(float(predicted[y].mean()), 3) if positives else None,
        "precision": round(float(y[predicted].mean()), 3) if predicted.any() else None,
        "flagged_share": round(float(predicted.mean()), 3),
    }


def _groups(name: str, values: np.ndarray, y: np.ndarray, flagged: np.ndarray) -> list[dict[str, Any]]:
    rows = []
    for group in sorted(set(values)):
        mask = values == group
        n = int(mask.sum())
        negatives, positives = mask & ~y, mask & y
        rows.append({
            "by": name, "group": str(group), "n": n,
            "not_approved_rate": round(float(y[mask].mean()), 3),
            "flagged_rate": round(float(flagged[mask].mean()), 3),
            "false_positive_rate": round(float(flagged[negatives].mean()), 3) if negatives.any() else None,
            "false_negative_rate": round(float((~flagged[positives]).mean()), 3) if positives.any() else None,
            "conclusive": n >= MIN_GROUP,
        })
    return rows


def report() -> dict[str, Any]:
    from data.generator.generate import generate  # gender is not a model feature; it is added only for the subgroup check

    df = training_data()  # rows in generator order
    df["gender"] = [generated.case.patient.gender for generated, _ in generate()]
    train_df, test_df, cutoff = time_split(df)
    model = fit(train_df[DENIAL_FEATURES], train_df["outcome"].map(CLASSES.index).to_numpy())
    proba = model.predict_proba(test_df[DENIAL_FEATURES])
    y_not_approved = (test_df["outcome"] != "APPROVED").to_numpy()
    y_denied = (test_df["outcome"] == "DENIED").to_numpy()
    model_not_approved = 1 - proba[:, 0]
    model_denied = proba[:, CLASSES.index("DENIED")]

    any_flag = test_df[FLAGS].to_numpy().any(axis=1).astype(float)
    hard_flags = [f for f in FLAGS if f.startswith("missing_") or f in ("coding_mismatch", "waiting_period_breached", "excluded_procedure")]
    any_hard = test_df[hard_flags].to_numpy().any(axis=1).astype(float)

    bins = np.linspace(0, 1, 6)
    which = np.clip(np.digitize(model_not_approved, bins) - 1, 0, 4)
    reliability = [
        {"bin": f"{bins[i]:.1f}-{bins[i + 1]:.1f}", "n": int((which == i).sum()),
         "mean_predicted": round(float(model_not_approved[which == i].mean()), 3) if (which == i).any() else None,
         "observed": round(float(y_not_approved[which == i].mean()), 3) if (which == i).any() else None}
        for i in range(5)
    ]
    ages = test_df["age"].to_numpy()
    bands = np.where(ages < 40, "under 40", np.where(ages < 60, "40-59", "60+"))
    gender = test_df["gender"].to_numpy()
    flagged = model_not_approved >= 0.5

    return {
        "data": {"train_rows": len(train_df), "test_rows": len(test_df), "split": f"time-based: test admissions from {cutoff.isoformat()}"},
        "not_fully_approved": {
            "model": _binary(y_not_approved, model_not_approved),
            "rule_any_flag": _binary(y_not_approved, any_flag),
            "brier_model": round(float(brier_score_loss(y_not_approved, model_not_approved)), 4),
            "brier_rule": round(float(brier_score_loss(y_not_approved, any_flag)), 4),
        },
        "denied": {"model": _binary(y_denied, model_denied), "rule_any_hard_flag": _binary(y_denied, any_hard)},
        "reliability_not_fully_approved": reliability,
        "subgroups": _groups("age band", bands, y_not_approved, flagged) + _groups("gender", gender, y_not_approved, flagged),
        "caveats": [
            "Labels are synthetic and decided by the generator from the same flags the model uses, plus the TPA's habits and 3% noise: a near-perfect score is expected and says nothing about real insurers.",
            "The simple rules come close because the outcome is mostly a function of those flags; the model's extra value here is ranking and per-claim reasons (SHAP), not accuracy.",
            "Calibration is checked on 200 synthetic test claims only; probabilities should not be read as real-world denial chances.",
            "Gender is excluded from the features, but that alone does not make the model fair; subgroup error rates are reported, and groups under 30 claims are marked inconclusive.",
            "In a hospital the model would be retrained and re-validated on that hospital's own claim history before any use.",
        ],
    }


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    result = report()
    OUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("not_fully_approved", "denied")}, indent=2))


if __name__ == "__main__":
    main()
