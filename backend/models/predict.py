"""Use the trained models on one claim: cost estimate, and denial risk with reasons and fixes.

Train first with `python -m backend.models.train_cost` and `python -m backend.models.train_denial`.
"""

from dataclasses import dataclass
from functools import cache

import numpy as np
import xgboost as xgb

from backend.models.features import (
    COST_FEATURES,
    DENIAL_FEATURES,
    FLAGS,
    cost_features,
    denial_features,
    frame,
)
from backend.models.fixes_map import FIXES
from backend.models.train_cost import QUANTILES, TARGETS, from_model
from backend.models.train_cost import model_path as cost_model_path
from backend.models.train_denial import CLASSES
from backend.models.train_denial import model_path as denial_model_path
from backend.schemas import Case, ClaimState

TOP_REASONS = 3
_BAD_OUTCOMES = [CLASSES.index(ClaimState.PARTIALLY_APPROVED.value), CLASSES.index(ClaimState.DENIED.value)]


@dataclass(frozen=True)
class CostEstimate:
    bill_typical: int  # rupees, 50th percentile
    bill_high: int  # rupees, 90th percentile
    stay_typical: int  # days
    stay_high: int


@dataclass(frozen=True)
class Reason:
    flag: str  # a feature from features.FLAGS, e.g. "missing_xray_report"
    impact: float  # SHAP value towards a partial approval or denial, in log-odds
    fix: str


@dataclass(frozen=True)
class DenialRisk:
    probabilities: dict[str, float]  # per outcome
    risk: float  # chance the claim is not fully approved
    reasons: list[Reason]  # raised flags pushing towards a cut or denial, strongest first


def estimate_cost(case: Case) -> CostEstimate:
    x = frame([cost_features(case)], COST_FEATURES)
    values = {
        f"{target}_{quantile}": float(from_model(target, quantile, _cost_model(target, quantile).predict(x))[0])
        for target in TARGETS
        for quantile in QUANTILES
    }
    return CostEstimate(
        bill_typical=_round100(values["bill_p50"]),
        bill_high=_round100(max(values["bill_p90"], values["bill_p50"])),
        stay_typical=int(values["los_p50"]),
        stay_high=int(max(values["los_p90"], values["los_p50"])),
    )


def denial_risk(case: Case, top: int = TOP_REASONS) -> DenialRisk:
    x = frame([denial_features(case)], DENIAL_FEATURES)
    model = _denial_model()
    proba = model.predict_proba(x)[0]
    # (classes, features + bias): each feature's push towards each outcome
    shap = model.get_booster().predict(xgb.DMatrix(x, enable_categorical=True), pred_contribs=True)[0]
    reasons = []
    for flag in FLAGS:
        if not x.at[0, flag]:
            continue
        column = DENIAL_FEATURES.index(flag)
        impact = float(max(shap[c][column] for c in _BAD_OUTCOMES))
        if impact > 0:
            reasons.append(Reason(flag, round(impact, 3), FIXES[flag]))
    reasons.sort(key=lambda r: r.impact, reverse=True)
    return DenialRisk(
        probabilities={c: round(float(p), 3) for c, p in zip(CLASSES, proba)},
        risk=round(float(1 - proba[CLASSES.index(ClaimState.APPROVED.value)]), 3),
        reasons=reasons[:top],
    )


@cache
def _cost_model(target: str, quantile: str) -> xgb.XGBRegressor:
    model = xgb.XGBRegressor()
    model.load_model(_existing(cost_model_path(target, quantile), "train_cost"))
    return model


@cache
def _denial_model() -> xgb.XGBClassifier:
    model = xgb.XGBClassifier()
    model.load_model(_existing(denial_model_path(), "train_denial"))
    return model


def _existing(path, trainer: str):
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run python -m backend.models.{trainer}")
    return path


def _round100(amount: float) -> int:
    return int(np.round(amount / 100) * 100)
