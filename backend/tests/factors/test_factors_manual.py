"""Hand-calculated factor values.

Every assertion here is derived by hand (closed form) from a tiny, fully
determined input so that the vectorised implementation is pinned to an
independent expected value -- not just to pandas re-derivation.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.factors import BUILTIN_FACTOR_NAMES, FACTOR_DIRECTIONS, FactorLab, FactorSpec


def _last_value(panel: pd.DataFrame, symbol: str, factor: str) -> float:
    series = panel.xs(symbol, level="symbol")[factor].dropna()
    assert not series.empty, f"no non-NaN values for {factor}/{symbol}"
    return float(series.iloc[-1])


def test_output_is_multiindex_with_all_builtin_factors(build_bars, factor_lab):
    bars = build_bars({"000001": list(range(10, 90)), "600000": list(range(20, 100))})
    out = factor_lab.compute_all(bars)
    assert out.index.names == ["trade_date", "symbol"]
    assert list(out.columns) == BUILTIN_FACTOR_NAMES
    assert isinstance(out.index, pd.MultiIndex)
    # every (trade_date, symbol) combo present
    assert out.shape[0] == 80 * 2


def test_second_batch_factors_registered_with_directions():
    expected = {
        "linear_slope_20d": 1,
        "trend_rsquare_20d": 1,
        "trend_residual_20d": -1,
        "volume_return_divergence_20d": 1,
        "price_rank_20d": 1,
    }
    for name, direction in expected.items():
        assert name in BUILTIN_FACTOR_NAMES
        assert FACTOR_DIRECTIONS[name] == direction


# ---------------------------------------------------------------------------
# Momentum family
# ---------------------------------------------------------------------------

def test_momentum_5d_hand_calc(build_bars, factor_lab):
    # close goes 10..15 over 6 days; 5d return at last = 15/10 - 1 = 0.5
    bars = build_bars({"000001": [10, 11, 12, 13, 14, 15]})
    out = factor_lab.compute(bars, [FactorSpec("momentum_5d")])
    assert math.isclose(_last_value(out, "000001", "momentum_5d"), 0.5, rel_tol=1e-12)


def test_momentum_20d_and_60d_hand_calc(build_bars, factor_lab):
    # linear prices 10, 11, ..., 70 (61 days)
    prices = [10 + i for i in range(61)]
    bars = build_bars({"000001": prices})
    out = factor_lab.compute(bars, [FactorSpec("momentum_20d"), FactorSpec("momentum_60d")])
    # momentum_20d at last = 70/50 - 1 = 0.4
    assert math.isclose(_last_value(out, "000001", "momentum_20d"), 0.4, rel_tol=1e-12)
    # momentum_60d at last = 70/10 - 1 = 6.0
    assert math.isclose(_last_value(out, "000001", "momentum_60d"), 6.0, rel_tol=1e-12)


def test_momentum_20d_skip_5d_hand_calc(build_bars, factor_lab):
    # 26 linear prices 10..35; skip5/window20 at last = close[20]/close[0]-1 = 30/10-1 = 2.0
    prices = [10 + i for i in range(26)]
    bars = build_bars({"000001": prices})
    out = factor_lab.compute(bars, [FactorSpec("momentum_20d_skip_5d")])
    assert math.isclose(_last_value(out, "000001", "momentum_20d_skip_5d"), 2.0, rel_tol=1e-12)


def test_ma_gap_20d_hand_calc(build_bars, factor_lab):
    # 19 days at 10, day 20 at 12 -> MA20 = (19*10 + 12)/20 = 10.1
    prices = [10.0] * 19 + [12.0]
    bars = build_bars({"000001": prices})
    out = factor_lab.compute(bars, [FactorSpec("ma_gap_20d")])
    ma = (19 * 10.0 + 12.0) / 20.0
    expected = (12.0 - ma) / ma
    assert math.isclose(_last_value(out, "000001", "ma_gap_20d"), expected, rel_tol=1e-12)


def test_reversal_5d_hand_calc(build_bars, factor_lab):
    # flat 10 then drop to 8 -> reversal = close[0]/close[5] - 1 = 10/8 - 1 = 0.25
    bars = build_bars({"000001": [10, 10, 10, 10, 10, 8]})
    out = factor_lab.compute(bars, [FactorSpec("reversal_5d")])
    assert math.isclose(_last_value(out, "000001", "reversal_5d"), 0.25, rel_tol=1e-12)


def test_reversal_10d_hand_calc(build_bars, factor_lab):
    # flat 10 then drop to 8 -> reversal = close[0]/close[10] - 1 = 10/8 - 1 = 0.25
    bars = build_bars({"000001": [10.0] * 10 + [8.0]})
    out = factor_lab.compute(bars, [FactorSpec("reversal_10d")])
    assert math.isclose(_last_value(out, "000001", "reversal_10d"), 0.25, rel_tol=1e-12)


# ---------------------------------------------------------------------------
# Volatility / drawdown family
# ---------------------------------------------------------------------------

def test_volatility_20d_hand_calc(build_bars, factor_lab):
    # Build 21 prices whose 20 daily returns alternate exactly +0.02 / -0.02.
    px = [100.0]
    for i in range(20):
        r = 0.02 if i % 2 == 0 else -0.02
        px.append(px[-1] * (1 + r))
    bars = build_bars({"000001": px})
    out = factor_lab.compute(bars, [FactorSpec("volatility_20d")])
    # sample std (ddof=1) of 20 values that are +/-0.02 with zero mean
    expected = 0.02 * math.sqrt(20.0 / 19.0)
    assert math.isclose(_last_value(out, "000001", "volatility_20d"), expected, rel_tol=1e-9)


def test_downside_volatility_20d_hand_calc(build_bars, factor_lab):
    px = [100.0]
    for i in range(20):
        r = 0.02 if i % 2 == 0 else -0.02
        px.append(px[-1] * (1 + r))
    bars = build_bars({"000001": px})
    out = factor_lab.compute(bars, [FactorSpec("downside_volatility_20d")])
    # only the 10 negative returns (-0.02) count: sqrt(mean(0.02^2 * 10 / 20))
    expected = math.sqrt((0.02**2) * 10 / 20)
    assert math.isclose(
        _last_value(out, "000001", "downside_volatility_20d"), expected, rel_tol=1e-9
    )


def test_max_drawdown_20d_hand_calc(build_bars, factor_lab):
    # rise 10 -> 20 over 11 days, then flat at 15 for 9 days (20 total).
    # within the 20-day window: peak=20, trough=15 -> drawdown = 15/20 - 1 = -0.25
    prices = [10 + i for i in range(11)] + [15.0] * 9
    assert len(prices) == 20
    bars = build_bars({"000001": prices})
    out = factor_lab.compute(bars, [FactorSpec("max_drawdown_20d")])
    assert math.isclose(_last_value(out, "000001", "max_drawdown_20d"), -0.25, rel_tol=1e-12)


# ---------------------------------------------------------------------------
# Range / ATR family
# ---------------------------------------------------------------------------

def test_intraday_range_20d_hand_calc(build_bars, factor_lab):
    # constant close=100, high=101, low=99 -> (high-low)/close = 0.02 every day
    n = 20
    bars = build_bars(
        {"000001": [100.0] * n},
        high={"000001": [101.0] * n},
        low={"000001": [99.0] * n},
    )
    out = factor_lab.compute(bars, [FactorSpec("intraday_range_20d")])
    assert math.isclose(_last_value(out, "000001", "intraday_range_20d"), 0.02, rel_tol=1e-12)


def test_atr_pct_14d_hand_calc(build_bars, factor_lab):
    # close=100 constant, high=101, low=99 -> TR = max(2, |101-100|, |99-100|) = 2
    # ATR14 = 2, atr_pct = 2/100 = 0.02
    n = 15
    bars = build_bars(
        {"000001": [100.0] * n},
        high={"000001": [101.0] * n},
        low={"000001": [99.0] * n},
    )
    out = factor_lab.compute(bars, [FactorSpec("atr_pct_14d")])
    assert math.isclose(_last_value(out, "000001", "atr_pct_14d"), 0.02, rel_tol=1e-12)


# ---------------------------------------------------------------------------
# Liquidity / volume family
# ---------------------------------------------------------------------------

def test_log_amount_20d_hand_calc(build_bars, factor_lab):
    # constant amount=1000 for 20 days -> log(1000)
    bars = build_bars(
        {"000001": [10.0] * 20},
        volume=100.0,
        amount=1000.0,
    )
    out = factor_lab.compute(bars, [FactorSpec("log_amount_20d")])
    assert math.isclose(_last_value(out, "000001", "log_amount_20d"), math.log(1000), rel_tol=1e-9)


def test_amount_ratio_5d_20d_hand_calc(build_bars, factor_lab):
    # 15 days amount=100, then 5 days amount=200 -> 5d MA=200, 20d MA=125 -> 1.6
    amounts = [100.0] * 15 + [200.0] * 5
    bars = build_bars(
        {"000001": [10.0] * 20},
        amount_by_symbol={"000001": amounts},
    )
    out = factor_lab.compute(bars, [FactorSpec("amount_ratio_5d_20d")])
    assert math.isclose(_last_value(out, "000001", "amount_ratio_5d_20d"), 1.6, rel_tol=1e-9)


def test_volume_ratio_5d_20d_hand_calc(build_bars, factor_lab):
    volumes = [100.0] * 15 + [200.0] * 5
    bars = build_bars(
        {"000001": [10.0] * 20},
        volume_by_symbol={"000001": volumes},
    )
    out = factor_lab.compute(bars, [FactorSpec("volume_ratio_5d_20d")])
    assert math.isclose(_last_value(out, "000001", "volume_ratio_5d_20d"), 1.6, rel_tol=1e-9)


def test_amount_stability_20d_hand_calc(build_bars, factor_lab):
    # amount alternates 100/200 -> mean=150, std(ddof=1)=50*sqrt(20/19)
    amounts = [100.0 if i % 2 == 0 else 200.0 for i in range(20)]
    bars = build_bars(
        {"000001": [10.0] * 20},
        amount_by_symbol={"000001": amounts},
    )
    out = factor_lab.compute(bars, [FactorSpec("amount_stability_20d")])
    std = 50.0 * math.sqrt(20.0 / 19.0)
    expected = 150.0 / std
    assert math.isclose(_last_value(out, "000001", "amount_stability_20d"), expected, rel_tol=1e-9)


def test_amihud_illiquidity_20d_hand_calc(build_bars, factor_lab):
    # |return|=0.01 every day, amount=1000 -> mean(0.01/1000) = 1e-5
    px = [100.0]
    for _ in range(20):
        px.append(px[-1] * 1.01)
    bars = build_bars({"000001": px}, volume=100.0, amount=1000.0)
    out = factor_lab.compute(bars, [FactorSpec("amihud_illiquidity_20d")])
    assert math.isclose(_last_value(out, "000001", "amihud_illiquidity_20d"), 1e-5, rel_tol=1e-9)


def test_price_volume_corr_20d_perfect_positive(build_bars, factor_lab):
    # returns increase linearly; volume is an affine function of the return
    # -> corr(return, volume) = 1 exactly.
    returns = [0.01 * (i - 9) for i in range(20)]  # -0.09 .. +0.10
    px = [100.0]
    for r in returns:
        px.append(px[-1] * (1 + r))
    volumes = [100_000.0 + 1_000_000.0 * r for r in [0.0] + returns]
    bars = build_bars({"000001": px}, volume_by_symbol={"000001": volumes})
    out = factor_lab.compute(bars, [FactorSpec("price_volume_corr_20d")])
    assert math.isclose(_last_value(out, "000001", "price_volume_corr_20d"), 1.0, abs_tol=1e-9)


def test_up_day_ratio_20d_hand_calc(build_bars, factor_lab):
    # 20 returns: 12 up, 8 down -> ratio = 0.6
    signs = [1] * 12 + [-1] * 8
    px = [100.0]
    for s in signs:
        r = s * 0.01
        px.append(px[-1] * (1 + r))
    bars = build_bars({"000001": px})
    out = factor_lab.compute(bars, [FactorSpec("up_day_ratio_20d")])
    assert math.isclose(_last_value(out, "000001", "up_day_ratio_20d"), 0.6, rel_tol=1e-9)


def test_money_flow_proxy_20d_hand_calc(build_bars, factor_lab):
    # 12 positive-return days and 8 negative-return days, constant amount.
    signs = [1] * 12 + [-1] * 8
    px = [100.0]
    for s in signs:
        px.append(px[-1] * (1 + s * 0.01))
    bars = build_bars({"000001": px}, amount=1000.0)
    out = factor_lab.compute(bars, [FactorSpec("money_flow_proxy_20d")])
    expected = (12 - 8) / 20
    assert math.isclose(
        _last_value(out, "000001", "money_flow_proxy_20d"), expected, rel_tol=1e-9
    )


def test_close_position_20d_hand_calc(build_bars, factor_lab):
    # Close at 15 inside a trailing [10, 20] channel -> 0.5.
    prices = [15.0] * 20
    bars = build_bars(
        {"000001": prices},
        high={"000001": [20.0] * 20},
        low={"000001": [10.0] * 20},
    )
    out = factor_lab.compute(bars, [FactorSpec("close_position_20d")])
    assert math.isclose(_last_value(out, "000001", "close_position_20d"), 0.5, rel_tol=1e-12)


def test_price_efficiency_20d_hand_calc(build_bars, factor_lab):
    # Monotonic +1% path: net move equals path length, so efficiency is 1.
    px = [100.0]
    for _ in range(20):
        px.append(px[-1] * 1.01)
    bars = build_bars({"000001": px})
    out = factor_lab.compute(bars, [FactorSpec("price_efficiency_20d")])
    assert math.isclose(_last_value(out, "000001", "price_efficiency_20d"), 1.0, rel_tol=1e-9)


def test_amount_volatility_20d_hand_calc(build_bars, factor_lab):
    # Constant proportional amount growth -> constant log diff -> zero volatility.
    amounts = [1000.0 * (1.01**i) for i in range(21)]
    bars = build_bars({"000001": [10.0] * 21}, amount_by_symbol={"000001": amounts})
    out = factor_lab.compute(bars, [FactorSpec("amount_volatility_20d")])
    assert math.isclose(_last_value(out, "000001", "amount_volatility_20d"), 0.0, abs_tol=1e-12)


def test_shadow_and_close_location_20d_hand_calc(build_bars, factor_lab):
    # open=close=10, high=14, low=8:
    # upper=(14-10)/(14-8)=2/3, lower=(10-8)/(14-8)=1/3,
    # close_location=(2*10-14-8)/(14-8)=-1/3.
    bars = build_bars(
        {"000001": [10.0] * 20},
        high={"000001": [14.0] * 20},
        low={"000001": [8.0] * 20},
    )
    factors = [
        FactorSpec("upper_shadow_20d"),
        FactorSpec("lower_shadow_20d"),
        FactorSpec("close_location_20d"),
    ]
    out = factor_lab.compute(bars, factors)
    assert math.isclose(_last_value(out, "000001", "upper_shadow_20d"), 2.0 / 3.0, rel_tol=1e-12)
    assert math.isclose(_last_value(out, "000001", "lower_shadow_20d"), 1.0 / 3.0, rel_tol=1e-12)
    assert math.isclose(_last_value(out, "000001", "close_location_20d"), -1.0 / 3.0, rel_tol=1e-12)


def test_rsv_20d_hand_calc(build_bars, factor_lab):
    # close at 15 inside a trailing [10, 20] channel -> (15-10)/(20-10) = 0.5.
    bars = build_bars(
        {"000001": [15.0] * 20},
        high={"000001": [20.0] * 20},
        low={"000001": [10.0] * 20},
    )
    out = factor_lab.compute(bars, [FactorSpec("rsv_20d")])
    assert math.isclose(_last_value(out, "000001", "rsv_20d"), 0.5, rel_tol=1e-12)


def test_amount_shock_z_20d_hand_calc(build_bars, factor_lab):
    # 19 days at 100 and one day at 200:
    # mean=105, sample variance=(19*5^2 + 95^2)/19=500, z=95/sqrt(500).
    amounts = [100.0] * 19 + [200.0]
    bars = build_bars({"000001": [10.0] * 20}, amount_by_symbol={"000001": amounts})
    out = factor_lab.compute(bars, [FactorSpec("amount_shock_z_20d")])
    expected = 95.0 / math.sqrt(500.0)
    assert math.isclose(_last_value(out, "000001", "amount_shock_z_20d"), expected, rel_tol=1e-12)


def test_linear_slope_20d_hand_calc(build_bars, factor_lab):
    # close = 1..20 over the 20-day window -> OLS slope = 1, scaled by last close 20.
    bars = build_bars({"000001": list(range(1, 21))})
    out = factor_lab.compute(bars, [FactorSpec("linear_slope_20d")])
    assert math.isclose(_last_value(out, "000001", "linear_slope_20d"), 1.0 / 20.0, rel_tol=1e-12)


def test_trend_rsquare_20d_perfect_line_is_one(build_bars, factor_lab):
    bars = build_bars({"000001": list(range(10, 30))})
    out = factor_lab.compute(bars, [FactorSpec("trend_rsquare_20d")])
    assert math.isclose(_last_value(out, "000001", "trend_rsquare_20d"), 1.0, rel_tol=1e-12)


def test_trend_residual_20d_hand_calc(build_bars, factor_lab):
    # 19 days at 10 and one day at 30.
    # OLS slope = 190 / 665 = 38/133, fitted last = 1824/133,
    # residual = 30 - 1824/133 = 2166/133, scaled by 30 = 361/665.
    bars = build_bars({"000001": [10.0] * 19 + [30.0]})
    out = factor_lab.compute(bars, [FactorSpec("trend_residual_20d")])
    expected = 361.0 / 665.0
    assert math.isclose(_last_value(out, "000001", "trend_residual_20d"), expected, rel_tol=1e-12)


def test_volume_return_divergence_20d_perfect_positive(build_bars, factor_lab):
    # Make log(volume).diff() exactly equal to the 20 daily simple returns.
    returns = [0.01 * (i - 9) for i in range(20)]  # -0.09 .. +0.10
    prices = [100.0]
    volumes = [1_000_000.0]
    for r in returns:
        prices.append(prices[-1] * (1.0 + r))
        volumes.append(volumes[-1] * math.exp(r))
    bars = build_bars({"000001": prices}, volume_by_symbol={"000001": volumes})
    out = factor_lab.compute(bars, [FactorSpec("volume_return_divergence_20d")])
    assert math.isclose(
        _last_value(out, "000001", "volume_return_divergence_20d"),
        1.0,
        abs_tol=1e-9,
    )


def test_price_rank_20d_hand_calc(build_bars, factor_lab):
    bars = build_bars(
        {
            "000001": list(range(1, 21)),
            "600000": list(range(2, 21)) + [1],
            "300001": [10.0] * 20,
        }
    )
    out = factor_lab.compute(bars, [FactorSpec("price_rank_20d")])
    assert math.isclose(_last_value(out, "000001", "price_rank_20d"), 1.0, rel_tol=1e-12)
    assert math.isclose(_last_value(out, "600000", "price_rank_20d"), 0.0, rel_tol=1e-12)
    assert math.isclose(_last_value(out, "300001", "price_rank_20d"), 0.5, rel_tol=1e-12)


def test_unknown_factor_raises(factor_lab, build_bars):
    bars = build_bars({"000001": [10, 11, 12, 13, 14, 15]})
    try:
        factor_lab.compute(bars, [FactorSpec("does_not_exist")])
    except KeyError:
        return
    raise AssertionError("expected KeyError for unknown factor")
