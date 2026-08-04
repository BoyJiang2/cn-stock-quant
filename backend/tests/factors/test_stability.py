from datetime import date, timedelta

import pandas as pd

from app.factors.stability import evaluate_factor_stability


def _series(sign: float) -> tuple[pd.Series, pd.Series]:
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(90)]
    index = pd.MultiIndex.from_product([dates, ["000001", "000002", "000003"]], names=["trade_date", "symbol"])
    factor = pd.Series([0.0, 1.0, 2.0] * len(dates), index=index)
    labels = pd.Series([0.0, sign * 0.01, sign * 0.02] * len(dates), index=index)
    return factor, labels


def test_stability_requires_positive_evidence_in_each_consecutive_fold():
    factor, labels = _series(1.0)
    status, reasons, folds = evaluate_factor_stability(factor, labels, n_groups=3, fold_count=3)
    assert status == "stable"
    assert len(folds) == 3
    assert "all usable" in reasons[0]


def test_stability_marks_a_negative_fold_unstable():
    factor, labels = _series(1.0)
    middle = labels.index.get_level_values("trade_date").isin(sorted(labels.index.get_level_values("trade_date").unique())[30:60])
    labels.loc[middle] *= -1
    status, _, _ = evaluate_factor_stability(factor, labels, n_groups=3, fold_count=3)
    assert status == "unstable"
