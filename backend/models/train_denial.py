"""Denial-risk model (design section 7).

    python -m backend.models.train_denial

An XGBoost classifier predicts whether a claim is approved, partially approved or denied, from the
claim details, document-completeness flags, rules-engine flags, the TPA and summary text flags.
Explanations are exact TreeSHAP values from XGBoost itself (`pred_contribs`), which handle the
categorical features directly.
"""

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from xgboost import XGBClassifier

from backend.models.features import DENIAL_FEATURES, time_split, training_data
from backend.models.train_cost import MODEL_DIR, SEED, save_metrics
from backend.schemas import ClaimState

CLASSES = [ClaimState.APPROVED.value, ClaimState.PARTIALLY_APPROVED.value, ClaimState.DENIED.value]
DENIED = CLASSES.index(ClaimState.DENIED.value)


def model_path(model_dir: Path = MODEL_DIR) -> Path:
    return model_dir / "denial.ubj"


def fit(x: pd.DataFrame, y: np.ndarray) -> XGBClassifier:
    model = XGBClassifier(
        objective="multi:softprob",
        n_estimators=200,
        learning_rate=0.1,
        max_depth=4,
        min_child_weight=2,
        tree_method="hist",
        enable_categorical=True,
        random_state=SEED,
    )
    return model.fit(x, y)


def train(df: pd.DataFrame) -> tuple[XGBClassifier, dict[str, Any]]:
    train_df, test_df, cutoff = time_split(df)
    y_train = train_df["outcome"].map(CLASSES.index).to_numpy()
    y_test = test_df["outcome"].map(CLASSES.index).to_numpy()
    model = fit(train_df[DENIAL_FEATURES], y_train)

    proba = model.predict_proba(test_df[DENIAL_FEATURES])
    predicted = proba.argmax(axis=1)
    denied = y_test == DENIED
    metrics = {
        "train_rows": len(train_df),
        "test_rows": len(test_df),
        "test_from": cutoff.isoformat(),
        "roc_auc_denied": round(float(roc_auc_score(denied, proba[:, DENIED])), 3),
        "roc_auc_not_approved": round(float(roc_auc_score(y_test != 0, 1 - proba[:, 0])), 3),
        "roc_auc_macro_ovr": round(float(roc_auc_score(y_test, proba, multi_class="ovr")), 3),
        "recall_denied": round(float(np.mean(predicted[denied] == DENIED)), 3),
        "precision_denied": round(float(np.mean(denied[predicted == DENIED])), 3),
        "accuracy": round(float(np.mean(predicted == y_test)), 3),
        "test_outcomes": {c: int(np.sum(y_test == i)) for i, c in enumerate(CLASSES)},
    }
    return model, metrics


def main() -> None:
    model, metrics = train(training_data())
    MODEL_DIR.mkdir(exist_ok=True)
    model.save_model(model_path())
    save_metrics("denial", metrics)
    print(f"Trained on {metrics['train_rows']} admissions, tested on {metrics['test_rows']} from {metrics['test_from']}")
    print(f"  ROC-AUC, denied vs not: {metrics['roc_auc_denied']}")
    print(f"  ROC-AUC, not fully approved vs approved: {metrics['roc_auc_not_approved']}")
    print(f"  recall on denials: {metrics['recall_denied']:.1%}, precision: {metrics['precision_denied']:.1%}")


if __name__ == "__main__":
    main()
