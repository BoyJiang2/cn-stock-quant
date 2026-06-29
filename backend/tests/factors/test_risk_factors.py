"""Hand-calculated values, cross-stock isolation and no-look-ahead tests for
the risk-adjusted / return-distribution factor family:

* ``sharpe_20d``      -- rolling mean / std of daily simple returns
* ``return_skew_20d`` -- rolling sample skewness of daily simple returns
* ``vwap_gap_20d``    -- close vs rolling ``sum(amount)/sum(volume)`` VWAP

Every assertion is derived either by a closed-form hand calc on a tiny,
fully determined input or by a structural property (isolation /
truncation) that pins the implementation to the no-look-ahead contract the
rest of the factor lab guarantees.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from app.factors import BUILTIN_FACTOR_NAMES, FactorLab, FactorSpec


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _last_value(panel: pd.DataFrame, symbol: str, factor: str) -> float:
    series = panel.xs(symbol, level="symbol")[factor].dropna()
    assert not series.empty, f"no non-NaN values for {factor}/{symbol}"
    return float(series.iloc[-1])


def _prices_from_returns(returns: list[float], start: float = 100.0) -> list[float]:
    """Build a close price series whose ``pct_change`` recovers *returns* exactly."""
    px = [start]
    for r in returns:
        px.append(px[-1] * (1.0 + r))
    return px


# ---------------------------------------------------------------------------
# Registration / discovery
# ---------------------------------------------------------------------------

def test_new_factors_registered_and_listed():
    for name in ("sharpe_20d", "return_skew_20d", "vwap_gap_20d"):
        assert name in BUILTIN_FACTOR_NAMES, f"{name} missing from BUILTIN_FACTOR_NAMES"


def test_compute_all_includes_new_factors(build_bars, factor_lab):
    bars = build_bars({"000001": list(range(10, 90)), "600000": list(range(20, 100))})
    out = factor_lab.compute_all(bars)
    for name in ("sharpe_20d", "return_skew_20d", "vwap_gap_20d"):
        assert name in out.columns, f"{name} missing from compute_all output"


# ---------------------------------------------------------------------------
# sharpe_20d — hand calcs
# ---------------------------------------------------------------------------

def test_sharpe_20d_zero_mean_returns_zero(build_bars, factor_lab):
    # 20 returns alternating +0.02 / -0.02 → mean = 0 → sharpe = 0
    returns = [0.02 if i % 2 == 0 else -0.02 for i in range(20)]
    bars = build_bars({"000001": _prices_from_returns(returns)})
    out = factor_lab.compute(bars, [FactorSpec("sharpe_20d")])
    assert math.isclose(_last_value(out, "000001", "sharpe_20d"), 0.0, abs_tol=1e-12)


def test_sharpe_20d_positive_hand_calc(build_bars, factor_lab):
    # 20 returns: [+0.03, -0.01] repeated 10 times.
    #   mean = (10*0.03 + 10*(-0.01)) / 20 = 0.01
    #   deviations: +0.02 (×10) and -0.02 (×10) → sum sq = 20 * 0.0004 = 0.008
    #   std(ddof=1) = sqrt(0.008 / 19)
    #   sharpe = 0.01 / sqrt(0.008 / 19)
    returns = [0.03, -0.01] * 10
    bars = build_bars({"000001": _prices_from_returns(returns)})
    out = factor_lab.compute(bars, [FactorSpec("sharpe_20d")])
    expected = 0.01 / math.sqrt(0.008 / 19.0)
    assert math.isclose(_last_value(out, "000001", "sharpe_20d"), expected, rel_tol=1e-12)


def test_sharpe_20d_constant_prices_yield_nan(build_bars, factor_lab):
    # constant price → zero dispersion → sharpe undefined, not infinite
    bars = build_bars({"000001": [10.0] * 21})
    out = factor_lab.compute(bars, [FactorSpec("sharpe_20d")])
    series = out.xs("000001", level="symbol")["sharpe_20d"]
    assert series.isna().all(), "zero-dispersion window must not produce an infinite Sharpe"


def test_sharpe_20d_warmup_is_nan(build_bars, factor_lab):
    # 20-day Sharpe needs 21 closes (20 returns); the first 20 dates are NaN.
    bars = build_bars({"000001": list(range(1, 31))})  # 30 days
    out = factor_lab.compute(bars, [FactorSpec("sharpe_20d")])
    series = out.xs("000001", level="symbol")["sharpe_20d"]
    assert series.iloc[:20].isna().all()
    assert series.iloc[20:].notna().all()


# ---------------------------------------------------------------------------
# return_skew_20d — hand calcs / sign contracts
# ---------------------------------------------------------------------------

def test_return_skew_20d_symmetric_is_zero(build_bars, factor_lab):
    # symmetric +/-0.02 → third central moment = 0 → skew = 0 exactly
    returns = [0.02 if i % 2 == 0 else -0.02 for i in range(20)]
    bars = build_bars({"000001": _prices_from_returns(returns)})
    out = factor_lab.compute(bars, [FactorSpec("return_skew_20d")])
    assert math.isclose(_last_value(out, "000001", "return_skew_20d"), 0.0, abs_tol=1e-12)


def test_return_skew_20d_positive_outlier_is_positive(build_bars, factor_lab):
    # 19 small positive returns + one large positive outlier → right tail
    returns = [0.01] * 19 + [0.10]
    bars = build_bars({"000001": _prices_from_returns(returns)})
    out = factor_lab.compute(bars, [FactorSpec("return_skew_20d")])
    assert _last_value(out, "000001", "return_skew_20d") > 0.0


def test_return_skew_20d_negative_outlier_is_negative(build_bars, factor_lab):
    # 19 small positive returns + one large negative outlier → left tail
    returns = [0.01] * 19 + [-0.10]
    bars = build_bars({"000001": _prices_from_returns(returns)})
    out = factor_lab.compute(bars, [FactorSpec("return_skew_20d")])
    assert _last_value(out, "000001", "return_skew_20d") < 0.0


def test_return_skew_20d_warmup_is_nan(build_bars, factor_lab):
    # 20-day skew needs 21 closes (20 returns); the first 20 dates are NaN.
    bars = build_bars({"000001": list(range(1, 31))})  # 30 days
    out = factor_lab.compute(bars, [FactorSpec("return_skew_20d")])
    series = out.xs("000001", level="symbol")["return_skew_20d"]
    assert series.iloc[:20].isna().all()
    assert series.iloc[20:].notna().all()


# ---------------------------------------------------------------------------
# vwap_gap_20d — hand calcs
# ---------------------------------------------------------------------------

def test_vwap_gap_20d_zero_when_close_equals_vwap(build_bars, factor_lab):
    # close = amount / volume = 100 everywhere → vwap = 100 → gap = 0
    bars = build_bars(
        {"000001": [100.0] * 20},
        volume=10.0,
        amount=1000.0,
    )
    out = factor_lab.compute(bars, [FactorSpec("vwap_gap_20d")])
    assert math.isclose(_last_value(out, "000001", "vwap_gap_20d"), 0.0, abs_tol=1e-12)


def test_vwap_gap_20d_hand_calc_above_vwap(build_bars, factor_lab):
    # 19 days close=100, day 20 close=110; amount=1000, volume=10 constant.
    # vwap = sum(1000)/sum(10) = 100 → gap = (110 - 100)/100 = 0.10
    bars = build_bars(
        {"000001": [100.0] * 19 + [110.0]},
        volume=10.0,
        amount=1000.0,
    )
    out = factor_lab.compute(bars, [FactorSpec("vwap_gap_20d")])
    assert math.isclose(_last_value(out, "000001", "vwap_gap_20d"), 0.10, rel_tol=1e-12)


def test_vwap_gap_20d_below_vwap_is_negative(build_bars, factor_lab):
    # close below the rolling VWAP → negative gap
    bars = build_bars(
        {"000001": [100.0] * 19 + [90.0]},
        volume=10.0,
        amount=1000.0,
    )
    out = factor_lab.compute(bars, [FactorSpec("vwap_gap_20d")])
    assert _last_value(out, "000001", "vwap_gap_20d") < 0.0


def test_vwap_gap_20d_warmup_is_nan(build_bars, factor_lab):
    bars = build_bars({"000001": list(range(1, 31))})  # 30 days
    out = factor_lab.compute(bars, [FactorSpec("vwap_gap_20d")])
    series = out.xs("000001", level="symbol")["vwap_gap_20d"]
    assert series.iloc[:19].isna().all()
    assert series.iloc[19:].notna().all()


def test_vwap_gap_20d_zero_volume_yields_nan(build_bars, factor_lab):
    # zero volume over the window → vwap undefined → NaN (no division crash)
    bars = build_bars(
        {"000001": [100.0] * 21},
        volume=0.0,
        amount=0.0,
    )
    out = factor_lab.compute(bars, [FactorSpec("vwap_gap_20d")])
    series = out.xs("000001", level="symbol")["vwap_gap_20d"]
    assert series.isna().all(), "zero total volume must produce NaN, not crash"


# ---------------------------------------------------------------------------
# Cross-stock isolation: a factor value for one symbol must never depend on
# another symbol's data.  This is a structural property of the wide-form
# rolling design; pin it so a future refactor that introduces cross-sectional
# leakage is caught.
# ---------------------------------------------------------------------------

_NEW_FACTORS = ["sharpe_20d", "return_skew_20d", "vwap_gap_20d"]


def _sample_panel(factor_lab: FactorLab, bars: pd.DataFrame) -> pd.DataFrame:
    return factor_lab.compute(bars, [FactorSpec(n) for n in _NEW_FACTORS])


def _prices(seed: int, n: int = 80) -> list[float]:
    rng = np.random.default_rng(seed)
    px = 10.0
    out = [px]
    for _ in range(n - 1):
        px *= 1 + rng.normal(0, 0.02)
        out.append(px)
    return out


def test_other_symbol_perturbation_does_not_affect_target(factor_lab, build_bars):
    bars_a = build_bars({"000001": _prices(1), "600000": _prices(2)})

    # perturb 600000 heavily (scale prices + reshuffle volume/amount)
    bars_b = bars_a.copy()
    mask = bars_b["symbol"] == "600000"
    bars_b.loc[mask, "close"] *= 3.7
    bars_b.loc[mask, "high"] *= 3.7
    bars_b.loc[mask, "low"] *= 3.7
    bars_b.loc[mask, "open"] *= 3.7
    rng = np.random.default_rng(99)
    bars_b.loc[mask, "volume"] = rng.permutation(bars_b.loc[mask, "volume"].to_numpy())
    bars_b.loc[mask, "amount"] = bars_b.loc[mask, "close"] * bars_b.loc[mask, "volume"]

    panel_a = _sample_panel(factor_lab, bars_a)
    panel_b = _sample_panel(factor_lab, bars_b)

    a_a = panel_a.xs("000001", level="symbol")
    a_b = panel_b.xs("000001", level="symbol")
    assert_frame_equal(a_a, a_b)


def test_presence_of_other_symbol_does_not_affect_target(factor_lab, build_bars):
    prices = _prices(3)
    bars_solo = build_bars({"000001": prices})
    bars_multi = build_bars({"000001": prices, "600000": _prices(4), "300001": _prices(5)})

    solo = _sample_panel(factor_lab, bars_solo).xs("000001", level="symbol")
    multi = _sample_panel(factor_lab, bars_multi).xs("000001", level="symbol")
    assert_frame_equal(solo, multi)


# ---------------------------------------------------------------------------
# Truncation consistency / no look-ahead: a factor value at date t computed
# on a prefix ending at t equals the value computed on the full dataset.
# Appending future data must not change any already-computed value.
# ---------------------------------------------------------------------------

def _truncation_bars(seed: int, n: int = 90) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    px = 100.0
    vol = 1_000_000.0
    start = pd.Timestamp("2024-01-01")
    for i in range(n):
        r = rng.normal(0, 0.02)
        px *= 1 + r
        vol *= 1 + rng.normal(0, 0.05)
        rows.append({
            "symbol": "000001",
            "trade_date": start + pd.Timedelta(days=i),
            "open": px,
            "high": px * (1 + abs(rng.normal(0, 0.005))),
            "low": px * (1 - abs(rng.normal(0, 0.005))),
            "close": px,
            "volume": abs(vol),
            "amount": abs(px * vol),
        })
    return pd.DataFrame(rows)


def test_truncation_consistency_prefix_equals_full(factor_lab):
    bars = _truncation_bars(11)
    full = _sample_panel(factor_lab, bars)

    cut = 60
    prefix = _sample_panel(factor_lab, bars.iloc[:cut].reset_index(drop=True))

    overlap = prefix.index
    assert_frame_equal(prefix, full.loc[overlap])


def test_future_perturbation_leaves_past_unchanged(factor_lab):
    bars = _truncation_bars(12)
    base = _sample_panel(factor_lab, bars)

    # corrupt the last 20 rows (strictly future relative to date index 59)
    perturbed = bars.copy()
    idx = perturbed.index[-20:]
    perturbed.loc[idx, "close"] *= 2.0
    perturbed.loc[idx, "amount"] *= 5.0
    after = _sample_panel(factor_lab, perturbed)

    cut = 60
    base_idx = base.index.get_level_values("trade_date")
    after_idx = after.index.get_level_values("trade_date")
    cutoff = base.index.get_level_values("trade_date").unique()[cut]
    assert_frame_equal(
        base.loc[base_idx < cutoff],
        after.loc[after_idx < cutoff],
    )
