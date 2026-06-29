from datetime import date, timedelta

import math
import pandas as pd

from app.factors import evaluate, forward_returns, percentile_rank, winsorize_mad


def _bars(symbol: str, closes: list[float]) -> pd.DataFrame:
    start = date(2024, 1, 1)
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "trade_date": start + timedelta(days=i),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000.0,
                "amount": 10000.0,
            }
            for i, close in enumerate(closes)
        ]
    )


def test_forward_returns_uses_next_close_as_entry():
    labels = forward_returns(_bars("000001", [10.0, 20.0, 30.0, 60.0]), horizons=(1, 2))

    first = labels.loc[(date(2024, 1, 1), "000001")]
    assert first["fwd_1d"] == 0.5
    assert first["fwd_2d"] == 2.0
    assert math.isnan(labels.loc[(date(2024, 1, 3), "000001"), "fwd_1d"])


def test_evaluate_reports_perfect_rank_ic_and_ordered_groups():
    dates = [date(2024, 1, 1), date(2024, 1, 2)]
    symbols = [f"{i:06d}" for i in range(1, 6)]
    index = pd.MultiIndex.from_product([dates, symbols], names=["trade_date", "symbol"])
    values = [1, 2, 3, 4, 5] * 2
    factor = pd.Series(values, index=index, name="factor")
    returns = pd.Series([value / 100 for value in values], index=index, name="fwd_1d")

    report = evaluate(factor, returns, n_groups=5)

    assert math.isclose(report["ic_mean"], 1.0)
    assert math.isclose(report["rankic_mean"], 1.0)
    assert report["group_returns"][5] > report["group_returns"][1]
    assert math.isclose(report["long_short_return"], 0.04)
    assert report["n_dates"] == 2


def test_evaluate_turnover_uses_half_l1_weight_change():
    dates = [date(2024, 1, 1), date(2024, 1, 2)]
    symbols = ["000001", "000002", "000003", "000004"]
    index = pd.MultiIndex.from_product([dates, symbols], names=["trade_date", "symbol"])
    factor = pd.Series([1, 2, 3, 4, 4, 3, 2, 1], index=index)
    returns = pd.Series([0.0] * 8, index=index)

    report = evaluate(factor, returns, n_groups=2)

    assert math.isclose(report["turnover"][1], 1.0)
    assert math.isclose(report["turnover"][2], 1.0)
    assert math.isclose(report["long_short_turnover"], 1.0)


def test_cross_sectional_preprocessing_is_per_date():
    index = pd.MultiIndex.from_product(
        [[date(2024, 1, 1), date(2024, 1, 2)], ["A", "B", "C"]],
        names=["trade_date", "symbol"],
    )
    values = pd.Series([1.0, 2.0, 100.0, 10.0, 20.0, 30.0], index=index, name="factor")

    ranked = percentile_rank(values)
    clipped = winsorize_mad(values, k=1.0)

    assert ranked.loc[(date(2024, 1, 1), "C"), "factor"] == 1.0
    assert ranked.loc[(date(2024, 1, 2), "C"), "factor"] == 1.0
    assert clipped.loc[(date(2024, 1, 1), "C"), "factor"] < 100.0
    assert clipped.loc[(date(2024, 1, 2), "C"), "factor"] == 30.0
