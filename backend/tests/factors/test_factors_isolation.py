"""Cross-stock isolation: a factor value for one symbol must never depend on
another symbol's data.

The wide-form (column-per-symbol) rolling design makes this a structural
property; these tests pin it explicitly so a future refactor that introduces
cross-sectional leakage is caught.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from app.factors import FactorLab, FactorSpec

_FACTOR_SAMPLE = [
    "momentum_20d",
    "volatility_20d",
    "max_drawdown_20d",
    "amount_ratio_5d_20d",
    "price_volume_corr_20d",
    "up_day_ratio_20d",
]


def _sample_panel(factor_lab: FactorLab, bars: pd.DataFrame) -> pd.DataFrame:
    return factor_lab.compute(bars, [FactorSpec(n) for n in _FACTOR_SAMPLE])


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

    # perturb 600000 heavily (scale prices + shuffle volume)
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


def test_each_symbol_uses_only_its_own_history(factor_lab, build_bars):
    # Symbol A has a clear uptrend; symbol B has a clear downtrend.  momentum
    # for A must be positive and for B negative -- they must not average out.
    n = 25
    up = [10 * (1.01**i) for i in range(n)]
    down = [10 * (0.99**i) for i in range(n)]
    bars = build_bars({"000001": up, "600000": down})
    out = factor_lab.compute(bars, [FactorSpec("momentum_20d")])
    a = out.xs("000001", level="symbol")["momentum_20d"].dropna().iloc[-1]
    b = out.xs("600000", level="symbol")["momentum_20d"].dropna().iloc[-1]
    assert a > 0
    assert b < 0
