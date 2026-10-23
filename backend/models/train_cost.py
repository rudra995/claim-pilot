"""Cost and length-of-stay estimator (design section 7).

    python -m backend.models.train_cost

XGBoost with quantile loss, one model per percentile: typical (50th) and high (90th) for the total
bill and for days in hospital. The bill is modelled in log-rupees, which keeps the trees even
across ₹15,000 cataracts and ₹6,00,000 bypasses; quantiles survive the log unchanged. Stays are
whole days, so the high estimate is rounded up.
"""

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from backend.models.features import COST_FEATURES, time_split, training_data

MODEL_DIR = Path("models")
METRICS_PATH = MODEL_DIR / "metrics.json"
SEED = 2026
QUANTILES = {"p50": 0.5, "p90": 0.9}
TARGETS = {"bill": "billed", "los": "los"}


def model_path(target: str, quantile: str, model_dir: Path = MODEL_DIR) -> Path:
    return model_dir / f"cost_{target}_{quantile}.ubj"


def fit_quantile(x: pd.DataFrame, y: pd.Series, alpha: float) -> XGBRegressor:
    model = XGBRegressor(
        objective="reg:quantileerror",
        quantile_alpha=alpha,
        n_estimators=300,
        learning_rate=0.05,
        max_depth=3,  # shallow, well-filled leaves: deeper trees hug the training bills and under-cover
        min_child_weight=10,
        tree_method="hist",
        enable_categorical=True,
        random_state=SEED,
    )
    return model.fit(x, y)


def train(df: pd.DataFrame) -> tuple[dict[str, XGBRegressor], dict[str, Any]]:
    train_df, test_df, cutoff = time_split(df)
    models: dict[str, XGBRegressor] = {}
    metrics: dict[str, Any] = {
        "train_rows": len(train_df),
        "test_rows": len(test_df),
        "test_from": cutoff.isoformat(),
    }
    for target, column in TARGETS.items():
        y_train = np.log(train_df[column]) if target == "bill" else train_df[column]
        predictions = {}
        for name, alpha in QUANTILES.items():
            model = fit_quantile(train_df[COST_FEATURES], y_train, alpha)
            models[f"{target}_{name}"] = model
            predicted = model.predict(test_df[COST_FEATURES])
            predictions[name] = from_model(target, name, predicted)
        actual = test_df[column].to_numpy()
        metrics[target] = {
            "p50_mean_abs_error": round(float(np.mean(np.abs(actual - predictions["p50"]))), 2),
            "share_under_p90": round(float(np.mean(actual <= predictions["p90"])), 3),
            "share_under_p50": round(float(np.mean(actual <= predictions["p50"])), 3),
        }
    return models, metrics


def from_model(target: str, quantile: str, predicted: np.ndarray) -> np.ndarray:
    """Model output in real units: rupees for the bill, whole days for the stay (the high one rounded up)."""
    if target == "bill":
        return np.exp(predicted)
    days = np.ceil(predicted - 1e-6) if quantile == "p90" else np.round(predicted)
    return np.maximum(days, 1)


def save_metrics(section: str, metrics: dict[str, Any], path: Path = METRICS_PATH) -> None:
    """Write one model's metrics into metrics.json, keeping the other model's."""
    all_metrics = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    all_metrics[section] = metrics
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(all_metrics, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    models, metrics = train(training_data())
    MODEL_DIR.mkdir(exist_ok=True)
    for key, model in models.items():
        target, quantile = key.split("_")
        model.save_model(model_path(target, quantile))
    save_metrics("cost", metrics)
    bill, los = metrics["bill"], metrics["los"]
    print(f"Trained on {metrics['train_rows']} admissions, tested on {metrics['test_rows']} from {metrics['test_from']}")
    print(f"  bill: {bill['share_under_p90']:.1%} under the 90th-percentile estimate;"
          f" typical estimate off by Rs. {math.floor(bill['p50_mean_abs_error']):,} on average")
    print(f"  stay: {los['share_under_p90']:.1%} under the 90th-percentile estimate;"
          f" typical estimate off by {los['p50_mean_abs_error']:.1f} days on average")


if __name__ == "__main__":
    main()
