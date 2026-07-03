"""Truncation consistency and future-perturbation (no look-ahead) tests.

* **Truncation consistency**: a factor value at date *t* computed on a prefix
  of the data ending at *t* equals the value computed on the full (longer)
  dataset.  Appending future data must not change any already-computed value.
* **Future perturbation**: mutating data strictly after *t* must not change
  the factor value at *t*.

Both are direct consequences of using exclusively trailing rolling windows.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from app.factors import FactorLab, FactorSpec

_FACTORS = [
    "momentum_5d",
    "momentum_20d",
    "momentum_60d",
    "ma_gap_60d",
    "reversal_5d",
    "reversal_10d",
    "volatility_20d",
    "volatility_60d",
    "downside_volatility_20d",
    "max_drawdown_60d",
    "atr_pct_14d",
    "intraday_range_20d",
    "log_amount_20d",
    "amount_ratio_5d_20d",
    "volume_ratio_5d_20d",
    "amount_stability_20d",
    "amihud_illiquidity_20d",
    "price_volume_corr_20d",
    "up_day_ratio_20d",
    "money_flow_proxy_20d",
    "amount_volatility_20d",
    "low_vol_reversal_20d",
    "breakout_strength_20d",
    "drawdown_recovery_20d",
    "close_position_20d",
    "price_efficiency_20d",
    "intraday_momentum_20d",
    "overnight_gap_20d",
    "tail_risk_20d",
    "upper_shadow_20d",
    "lower_shadow_20d",
    "close_location_20d",
    "rsv_20d",
    "amount_shock_z_20d",
    "linear_slope_20d",
    "trend_rsquare_20d",
    "trend_residual_20d",
    "volume_return_divergence_20d",
    "price_rank_20d",
]


def _bars(seed: int, n: int = 90) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    px = 10.0
    vol = 100_000.0
    start = pd.Timestamp("2024-01-01")
    for i in range(n):
        r = rng.normal(0, 0.02)
        px *= 1 + r
        vol *= 1 + rng.normal(0, 0.05)
        rows.append(
            {
                "symbol": "000001",
                "trade_date": start + pd.Timedelta(days=i),
                "open": px,
                "high": px * (1 + abs(rng.normal(0, 0.005))),
                "low": px * (1 - abs(rng.normal(0, 0.005))),
                "close": px,
                "volume": abs(vol),
                "amount": abs(px * vol),
            }
        )
    return pd.DataFrame(rows)


def _compute(factor_lab, bars):
    return factor_lab.compute(bars, [FactorSpec(n) for n in _FACTORS])


def test_truncation_consistency_prefix_equals_full(factor_lab):
    bars = _bars(11)
    full = _compute(factor_lab, bars)

    cut = 60
    prefix = _compute(factor_lab, bars.iloc[:cut].reset_index(drop=True))

    # every value computed on the prefix must match the full dataset on the
    # overlapping dates.
    overlap = prefix.index
    assert_frame_equal(prefix, full.loc[overlap])


def test_future_perturbation_leaves_past_unchanged(factor_lab):
    bars = _bars(12)
    base = _compute(factor_lab, bars)

    # corrupt the last 20 rows (strictly future relative to date index 59)
    perturbed = bars.copy()
    idx = perturbed.index[-20:]
    perturbed.loc[idx, "close"] *= 2.0
    perturbed.loc[idx, "high"] *= 2.0
    perturbed.loc[idx, "low"] *= 2.0
    perturbed.loc[idx, "amount"] *= 5.0
    after = _compute(factor_lab, perturbed)

    cut = 60
    assert_frame_equal(base.loc[base.index.get_level_values("trade_date") < base.index.get_level_values("trade_date").unique()[cut]],
                       after.loc[after.index.get_level_values("trade_date") < after.index.get_level_values("trade_date").unique()[cut]])


def test_warmup_period_yields_nan(factor_lab, build_bars):
    # A 20-day return needs 21 prices: close_t / close_{t-20} - 1.
    bars = build_bars({"000001": list(range(1, 31))})  # 30 days
    out = factor_lab.compute(bars, [FactorSpec("momentum_20d")])
    series = out.xs("000001", level="symbol")["momentum_20d"]
    assert series.iloc[:20].isna().all()
    assert series.iloc[20:].notna().all()
