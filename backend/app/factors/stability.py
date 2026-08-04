"""Consecutive-period stability diagnostics for a single factor."""

from __future__ import annotations

from math import isfinite
from typing import Literal

import numpy as np
import pandas as pd

from app.factors.evaluation import evaluate

StabilityStatus = Literal["stable", "watch", "unstable"]


def _finite(value: object) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if isfinite(numeric) else None


def evaluate_factor_stability(
    factor: pd.Series,
    forward_return: pd.Series,
    *,
    n_groups: int,
    fold_count: int = 3,
) -> tuple[StabilityStatus, list[str], list[dict[str, object]]]:
    """Evaluate consecutive, non-overlapping date folds without random splits."""
    if fold_count < 2:
        raise ValueError("fold_count must be >= 2")
    dates = factor.index.get_level_values("trade_date").unique().intersection(
        forward_return.index.get_level_values("trade_date").unique()
    ).sort_values()
    folds: list[dict[str, object]] = []
    for fold_dates in np.array_split(dates.to_numpy(), fold_count):
        if len(fold_dates) == 0:
            continue
        mask = factor.index.get_level_values("trade_date").isin(fold_dates)
        label_mask = forward_return.index.get_level_values("trade_date").isin(fold_dates)
        report = evaluate(factor[mask], forward_return[label_mask], n_groups=n_groups)
        folds.append({
            "start_date": str(fold_dates[0]), "end_date": str(fold_dates[-1]),
            "n_dates": report["n_dates"], "rankic_mean": _finite(report["rankic_mean"]),
            "rankic_ir": _finite(report["rankic_ir"]),
            "long_short_return": _finite(report["long_short_return"]),
            "long_short_turnover": _finite(report["long_short_turnover"]),
        })
    usable = [fold for fold in folds if int(fold["n_dates"]) >= 20]
    if len(usable) < fold_count:
        return "watch", [f"only {len(usable)}/{fold_count} folds have at least 20 valid dates"], folds
    failing = [fold for fold in usable if fold["rankic_mean"] is None or fold["rankic_mean"] <= 0 or fold["long_short_return"] is None or fold["long_short_return"] <= 0]
    if failing:
        return "unstable", [f"{len(failing)}/{len(usable)} folds have non-positive RankIC or long-short return"], folds
    return "stable", ["all usable consecutive folds have positive RankIC and long-short return"], folds
