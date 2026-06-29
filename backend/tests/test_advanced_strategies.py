"""Tests for advanced strategies (Module C).

Covers:
- VolatilityContractionBreakoutStrategy (VCP)
- TrendFilteredMeanReversionStrategy (MR)
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from app.strategy.advanced import (
    ADVANCED_STRATEGIES,
    TrendFilteredMeanReversionStrategy,
    VolatilityContractionBreakoutStrategy,
)
from app.strategy.base import StrategyContext


# ═══════════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════════


def _bars(
    start: date,
    symbol_prices: dict[str, list[float]],
    *,
    volume: float = 1_000_000.0,
    amount: float = 100_000_000.0,
    high_ratio: float = 1.02,
    low_ratio: float = 0.98,
) -> pd.DataFrame:
    """Build an OHLCV DataFrame for symbol→price_series.

    All price series must have the same length.
    """
    lengths = {s: len(p) for s, p in symbol_prices.items()}
    if len(set(lengths.values())) != 1:
        raise ValueError("All price series must have the same length")
    n = list(lengths.values())[0]

    rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        for sym, prices in symbol_prices.items():
            c = float(prices[i])
            rows.append({
                "symbol": sym,
                "trade_date": t,
                "open": c * 0.99,
                "high": c * high_ratio,
                "low": c * low_ratio,
                "close": c,
                "volume": volume,
                "amount": amount,
            })
    return pd.DataFrame(rows)


def _vcp_ctx(**params) -> StrategyContext:
    """Generous VCP context — all gates wide open by default.

    Individual tests tighten the specific parameter under test.
    """
    defaults: dict = {
        "trend_window": 3,
        "breakout_window": 5,
        "breakout_threshold": 0.8,
        "atr_window": 5,
        "vol_contraction_window": 5,
        "vol_contraction_pct": 1.0,  # all ATR levels pass
        "volume_short_window": 2,
        "volume_long_window": 5,
        "volume_expansion_threshold": 0.0,  # any volume ratio passes
        "min_avg_amount_20d": 0,
        "min_price": 0.0,
        "top_n": 10,
        "max_position_weight": 1.0,
        "max_total_weight": 1.0,
    }
    defaults.update(params)
    return StrategyContext(current_date=date.today(), cash=1_000_000, params=defaults)


def _mr_ctx(**params) -> StrategyContext:
    """Generous MR context — all gates wide open by default.

    Individual tests tighten the specific parameter under test.
    """
    defaults: dict = {
        "trend_window": 3,
        "oversold_lookback": 5,
        "oversold_threshold": +1.0,  # any return passes
        "rsi_window": 5,
        "rsi_oversold": 100.0,  # any RSI passes
        "zscore_window": 5,
        "entry_zscore": 100.0,  # any zscore passes
        "min_avg_amount_20d": 0,
        "min_price": 0.0,
        "top_n": 10,
        "max_position_weight": 1.0,
        "max_total_weight": 1.0,
    }
    defaults.update(params)
    return StrategyContext(current_date=date.today(), cash=1_000_000, params=defaults)


# ═══════════════════════════════════════════════════════════════════════════
# Helper: build a price series that passes all generous VCP gates
# ═══════════════════════════════════════════════════════════════════════════

def _vcp_pass_prices(n: int = 15) -> list[float]:
    """Gentle uptrend — passes all generous VCP gates."""
    return [10.0 + i * 0.3 for i in range(n)]


# ═══════════════════════════════════════════════════════════════════════════
# Helper: build a price series that passes all generous MR gates
# Must satisfy: close > MA3, N-day return < +1.0, RSI <= 100, zscore <= -100
# ═══════════════════════════════════════════════════════════════════════════

def _mr_pass_prices(n: int = 20) -> list[float]:
    """Gentle uptrend — passes all generous MR gates."""
    return [10.0 + i * 0.3 for i in range(n)]


def _mr_dip_recover_prices(  # noqa: D417
    n_before: int = 15,
    start: float = 10.0,
    step: float = 0.4,
    dip_size: float = 3.0,
    recovery_step: float = 0.15,
    recovery_bars: int = 4,
) -> list[float]:
    """Build a "drop then partial recovery" series.

    - *n_before* bars of steady uptrend from *start* by *step*
    - Then a 2-bar sharp drop totalling *dip_size*
    - Then *recovery_bars* of gentle recovery by *recovery_step* each

    Result: close is above MA3 (recovering), but below its level
    *n_before* bars ago (oversold).  RSI is low from the drop;
    z-score is moderately negative.
    """
    prices = [start + i * step for i in range(n_before)]
    peak = prices[-1]
    # Sharp 2-bar drop
    prices.append(peak - dip_size * 0.55)
    prices.append(prices[-1] - dip_size * 0.45)
    # Gentle recovery
    for _ in range(recovery_bars):
        prices.append(prices[-1] + recovery_step)
    return prices


# ═══════════════════════════════════════════════════════════════════════════
# VCP — Empty / no-candidate / all-symbols
# ═══════════════════════════════════════════════════════════════════════════


def test_vcp_empty_history_returns_all_zero():
    bars = pd.DataFrame(
        columns=["symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"],
    )
    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(), bars,
    )
    assert weights == {}


def test_vcp_no_candidates_returns_all_zero():
    start = date(2025, 1, 1)
    # All below min_price
    bars = _bars(start, {"S1": [3.0] * 8, "S2": [4.0] * 8})
    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(min_price=5.0), bars,
    )
    assert weights == {"S1": 0.0, "S2": 0.0}


def test_vcp_all_symbols_in_output():
    start = date(2025, 1, 1)
    n = 15
    bars = _bars(start, {
        "PASS": _vcp_pass_prices(n),
        "FAIL": [3.0] * n,
    }, volume=2_000_000)

    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(min_price=5.0), bars,
    )
    assert set(weights.keys()) == {"PASS", "FAIL"}
    assert weights["PASS"] > 0.0, f"PASS expected > 0, got {weights['PASS']}"
    assert weights["FAIL"] == 0.0, f"FAIL expected 0, got {weights['FAIL']}"


# ═══════════════════════════════════════════════════════════════════════════
# VCP — Individual gate tests
# ═══════════════════════════════════════════════════════════════════════════


def test_vcp_trend_filter_blocks_below_ma():
    """Stock with close <= MA(trend_window) must be filtered out."""
    start = date(2025, 1, 1)
    n = 8
    # UP: [10, 10.5, 11, 11.5, 12, 12.5, 13, 13.5], MA3 of tail = (12.5+13+13.5)/3=13, close=13.5>13
    up = [10.0 + i * 0.5 for i in range(n)]
    # DOWN: [13.5, 13, 12.5, 12, 11.5, 11, 10.5, 10], MA3 of tail = (11+10.5+10)/3=10.5, close=10<=10.5
    down = [13.5 - i * 0.5 for i in range(n)]

    bars = _bars(start, {"UP": up, "DOWN": down}, volume=2_000_000)

    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(trend_window=3), bars,
    )
    assert weights["UP"] > 0.0, f"UP should pass trend, got {weights['UP']}"
    assert weights["DOWN"] == 0.0, f"DOWN should fail trend, got {weights['DOWN']}"


def test_vcp_breakout_filter_blocks_distant_from_high():
    """Stock far below its breakout_window high must be filtered out."""
    start = date(2025, 1, 1)
    n = 10
    # NEAR: close is max → ratio=1.0
    near = [10.0 + i * 0.2 for i in range(n)]   # 10 → 11.8, max=11.8
    # FAR: sharp drop at end → close=8.0, max≈14.5, ratio=0.55
    far = [10.0 + i * 0.5 for i in range(n - 1)] + [8.0]

    bars = _bars(start, {"NEAR": near, "FAR": far}, volume=2_000_000)

    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(breakout_window=5, breakout_threshold=0.9), bars,
    )
    assert weights["NEAR"] > 0.0, f"NEAR should pass, got {weights['NEAR']}"
    assert weights["FAR"] == 0.0, f"FAR should fail breakout, got {weights['FAR']}"


def test_vcp_volume_expansion_filter():
    """Stock with contracting volume must be filtered out when threshold >= 1."""
    start = date(2025, 1, 1)
    n = 10
    prices = _vcp_pass_prices(n)

    rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        c = prices[i]
        # EXPAND: volume grows
        rows.append({
            "symbol": "EXPAND", "trade_date": t,
            "open": c * 0.99, "high": c * 1.02, "low": c * 0.98, "close": c,
            "volume": 500_000.0 * (i + 1), "amount": 100_000_000,
        })
        # CONTRACT: volume shrinks
        rows.append({
            "symbol": "CONTRACT", "trade_date": t,
            "open": c * 0.99, "high": c * 1.02, "low": c * 0.98, "close": c,
            "volume": 5_000_000.0 / (i + 1), "amount": 100_000_000,
        })
    bars = pd.DataFrame(rows)

    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(volume_short_window=3, volume_long_window=5,
                 volume_expansion_threshold=1.0), bars,
    )
    # EXPAND: short_ma > long_ma → ratio > 1 → passes
    # CONTRACT: short_ma < long_ma → ratio < 1 → fails
    assert weights["EXPAND"] > 0.0, f"EXPAND should pass, got {weights['EXPAND']}"
    assert weights["CONTRACT"] == 0.0, f"CONTRACT should fail volume, got {weights['CONTRACT']}"


def test_vcp_atr_contraction_filter():
    """Stock with high ATR must be filtered when vol_contraction_pct is strict.

    STEADY: starts with larger price swings, then settles → ATR *decreases* over
    time → current ATR is in a low percentile → passes strict pct gate.
    VOLATILE: consistent large swings → ATR stays high → fails.
    """
    start = date(2025, 1, 1)
    n = 15

    # STEADY: dampening volatility — early bars are jumpy, later bars are calm.
    # ATR shrinks → current ATR is the lowest → atr_rank ≈ 0.1 (bottom decile).
    s_vals = [10.0]
    for i in range(1, n):
        if i <= 5:
            # Early: ±0.8 swings → larger true range
            delta = 0.8 if i % 2 == 0 else -0.7
        elif i <= 9:
            # Mid: ±0.3 swings
            delta = 0.3 if i % 2 == 0 else -0.25
        else:
            # Late: tiny moves
            delta = 0.05 if i % 2 == 0 else -0.03
        s_vals.append(s_vals[-1] + delta)

    # VOLATILE: consistently large alternating swings → high ATR throughout.
    v_vals = [10.0]
    for i in range(1, n):
        if i % 2 == 0:
            v_vals.append(v_vals[-1] * 1.06)
        else:
            v_vals.append(v_vals[-1] * 0.94)

    rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        sc = s_vals[i]
        rows.append({
            "symbol": "STEADY", "trade_date": t,
            "open": sc * 0.99, "high": sc * 1.01, "low": sc * 0.99, "close": sc,
            "volume": 2_000_000, "amount": 100_000_000,
        })
        vc = v_vals[i]
        # VOLATILE: wide intraday range (16 % high–low) to amplify ATR
        rows.append({
            "symbol": "VOLATILE", "trade_date": t,
            "open": vc * 0.95, "high": vc * 1.08, "low": vc * 0.92, "close": vc,
            "volume": 2_000_000, "amount": 100_000_000,
        })
    bars = pd.DataFrame(rows)

    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(atr_window=5, vol_contraction_window=10, vol_contraction_pct=0.3), bars,
    )
    # STEADY: ATR shrinking → current atr_rank is low → passes pct=0.3
    # VOLATILE: ATR consistently high → atr_rank is moderate/high → fails
    assert weights["STEADY"] > 0.0, f"STEADY should pass ATR contraction, got {weights['STEADY']}"
    assert weights["VOLATILE"] == 0.0, f"VOLATILE should fail ATR contraction, got {weights['VOLATILE']}"


def test_vcp_liquidity_filter():
    start = date(2025, 1, 1)
    n = 10
    prices = _vcp_pass_prices(n)
    rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        c = prices[i]
        rows.append({
            "symbol": "RICH", "trade_date": t,
            "open": c * 0.99, "high": c * 1.02, "low": c * 0.98, "close": c,
            "volume": 2_000_000, "amount": 200_000_000,
        })
        rows.append({
            "symbol": "POOR", "trade_date": t,
            "open": c * 0.99, "high": c * 1.02, "low": c * 0.98, "close": c,
            "volume": 2_000_000, "amount": 1_000_000,
        })
    bars = pd.DataFrame(rows)

    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(min_avg_amount_20d=50_000_000), bars,
    )
    assert weights["RICH"] > 0.0, f"RICH should pass, got {weights['RICH']}"
    assert weights["POOR"] == 0.0, f"POOR should fail, got {weights['POOR']}"


def test_vcp_min_price_filter():
    start = date(2025, 1, 1)
    n = 10
    bars = _bars(start, {
        "HIGH": [20.0 + i for i in range(n)],
        "LOW": [3.0] * n,
    }, volume=2_000_000)
    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(min_price=5.0), bars,
    )
    assert weights["HIGH"] > 0.0
    assert weights["LOW"] == 0.0


def test_vcp_missing_amount_column_skips_when_min_amount_set():
    start = date(2025, 1, 1)
    n = 10
    prices = _vcp_pass_prices(n)
    rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        rows.append({
            "symbol": "S1", "trade_date": t,
            "open": 9.9, "high": 10.2, "low": 9.8, "close": prices[i],
            "volume": 2_000_000,
        })
    bars = pd.DataFrame(rows)
    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(min_avg_amount_20d=50_000_000), bars,
    )
    assert weights["S1"] == 0.0


def test_vcp_insufficient_data_skipped():
    start = date(2025, 1, 1)
    n = 12
    bars_data = _bars(start, {"LONG": _vcp_pass_prices(n)}, volume=2_000_000)
    short_rows: list[dict] = []
    for i in range(3):
        short_rows.append({
            "symbol": "SHORT", "trade_date": start + timedelta(days=i),
            "open": 9.9, "high": 10.2, "low": 9.8, "close": 10.0 + i * 0.3,
            "volume": 2_000_000, "amount": 100_000_000,
        })
    bars = pd.concat([bars_data, pd.DataFrame(short_rows)], ignore_index=True)
    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(trend_window=5), bars,
    )
    # min_data_len = max(5,5,5,5,5)=5, SHORT has 3 → skipped
    assert weights["LONG"] > 0.0
    assert weights["SHORT"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# VCP — Scoring & ranking
# ═══════════════════════════════════════════════════════════════════════════


def test_vcp_top_n_selection():
    start = date(2025, 1, 1)
    n = 10
    symbols = {f"S{i}": [10.0 + j * 0.3 + i * 0.01 for j in range(n)] for i in range(5)}
    bars = _bars(start, symbols, volume=2_000_000)

    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(top_n=2), bars,
    )
    positive = sum(1 for w in weights.values() if w > 0)
    assert positive == 2, f"Expected 2 positive weights, got {positive}"
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_vcp_single_candidate_gets_full_allocation():
    start = date(2025, 1, 1)
    n = 10
    bars = _bars(start, {"ONLY": _vcp_pass_prices(n)}, volume=2_000_000)
    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(top_n=10, max_total_weight=0.8), bars,
    )
    assert abs(weights["ONLY"] - 0.8) < 1e-9, f"Expected 0.8, got {weights['ONLY']}"


def test_vcp_position_cap_respected():
    start = date(2025, 1, 1)
    n = 10
    symbols = {f"S{i}": [10.0 + j * 0.3 + i * 0.1 for j in range(n)] for i in range(5)}
    bars = _bars(start, symbols, volume=2_000_000)
    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(top_n=5, max_position_weight=0.08, max_total_weight=0.95), bars,
    )
    for sym, w in weights.items():
        if w > 0:
            assert w <= 0.08 + 1e-9, f"{sym} weight {w} exceeds cap 0.08"


def test_vcp_total_weight_cap_respected():
    start = date(2025, 1, 1)
    n = 10
    symbols = {f"S{i}": [10.0 + j * 0.3 + i for j in range(n)] for i in range(5)}
    bars = _bars(start, symbols, volume=2_000_000)
    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(top_n=5, max_total_weight=0.4), bars,
    )
    total = sum(weights.values())
    assert total <= 0.4 + 1e-9, f"Total weight {total} exceeds cap 0.4"


def test_vcp_stronger_breakout_and_volume_ranks_higher():
    """BEST (expanding volume, at high) outranks WORST (contracting volume, dipped)."""
    start = date(2025, 1, 1)
    n = 15
    base = [10.0 + i * 0.3 for i in range(n)]
    rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        c = base[i]
        rows.append({
            "symbol": "BEST", "trade_date": t,
            "open": c * 0.99, "high": c * 1.02, "low": c * 0.98, "close": c,
            "volume": 2_000_000 * (i + 1), "amount": 100_000_000,
        })
        dip_c = c if i < n - 3 else c * 0.90
        rows.append({
            "symbol": "WORST", "trade_date": t,
            "open": dip_c * 0.99, "high": dip_c * 1.02, "low": dip_c * 0.98, "close": dip_c,
            "volume": 2_000_000 * max(1, n - i), "amount": 100_000_000,
        })
    bars = pd.DataFrame(rows)
    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(top_n=2, breakout_window=10, breakout_threshold=0.85,
                 volume_expansion_threshold=0.5, volume_short_window=3,
                 volume_long_window=10), bars,
    )
    assert weights["BEST"] > 0.0, f"BEST should be selected, got {weights['BEST']}"


# ═══════════════════════════════════════════════════════════════════════════
# VCP — Parameter validation
# ═══════════════════════════════════════════════════════════════════════════


def test_vcp_window_params_must_be_gt_1():
    strategy = VolatilityContractionBreakoutStrategy()
    bars = _bars(date(2025, 1, 1), {"S": [10.0] * 10}, volume=2_000_000)
    for param in ["trend_window", "breakout_window", "atr_window",
                  "vol_contraction_window", "volume_short_window", "volume_long_window"]:
        with pytest.raises(ValueError, match="must be > 1"):
            strategy.generate_target_weights(_vcp_ctx(**{param: 1}), bars)


def test_vcp_nan_param_raises():
    strategy = VolatilityContractionBreakoutStrategy()
    bars = _bars(date(2025, 1, 1), {"S": [10.0] * 10}, volume=2_000_000)
    with pytest.raises(ValueError, match="NaN"):
        strategy.generate_target_weights(_vcp_ctx(min_price=float("nan")), bars)
    with pytest.raises(ValueError, match="NaN"):
        strategy.generate_target_weights(_vcp_ctx(breakout_threshold=float("nan")), bars)


def test_vcp_out_of_range_params_raise_value_error():
    strategy = VolatilityContractionBreakoutStrategy()
    bars = _bars(date(2025, 1, 1), {"S": [10.0] * 10}, volume=2_000_000)

    for name, val in [
        ("breakout_threshold", 1.5), ("breakout_threshold", -0.1),
        ("vol_contraction_pct", 1.5), ("vol_contraction_pct", -0.1),
        ("max_position_weight", 1.5), ("max_position_weight", -0.1),
        ("max_total_weight", 2.0), ("max_total_weight", -0.5),
    ]:
        with pytest.raises(ValueError):
            strategy.generate_target_weights(_vcp_ctx(**{name: val}), bars)

    for name, val in [
        ("volume_expansion_threshold", -1.0),
        ("min_price", -5.0),
        ("min_avg_amount_20d", -100),
    ]:
        with pytest.raises(ValueError):
            strategy.generate_target_weights(_vcp_ctx(**{name: val}), bars)


# ═══════════════════════════════════════════════════════════════════════════
# VCP — Metadata
# ═══════════════════════════════════════════════════════════════════════════


def test_vcp_metadata():
    meta = VolatilityContractionBreakoutStrategy.metadata()
    assert meta["name"] == "volatility_contraction_breakout"
    assert meta["display_name"] == "波动收缩突破策略"
    param_names = {p["name"] for p in meta["parameters"]}
    expected = {
        "trend_window", "breakout_window", "breakout_threshold",
        "atr_window", "vol_contraction_window", "vol_contraction_pct",
        "volume_short_window", "volume_long_window", "volume_expansion_threshold",
        "min_avg_amount_20d", "min_price", "top_n",
        "max_position_weight", "max_total_weight",
    }
    assert param_names == expected


def test_vcp_registered_in_advanced_strategies():
    assert "volatility_contraction_breakout" in ADVANCED_STRATEGIES
    assert ADVANCED_STRATEGIES["volatility_contraction_breakout"] is VolatilityContractionBreakoutStrategy


# ═══════════════════════════════════════════════════════════════════════════
# VCP — Static helpers
# ═══════════════════════════════════════════════════════════════════════════


def test_compute_atr_basic():
    closes = pd.Series([10.0, 10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5])
    highs = closes * 1.02
    lows = closes * 0.98
    atr = VolatilityContractionBreakoutStrategy._compute_atr(highs, lows, closes, 5)
    valid = atr.dropna()
    assert len(valid) > 0
    assert (valid > 0).all()


def test_compute_atr_length():
    closes = pd.Series([10.0] * 20)
    highs = closes * 1.02
    lows = closes * 0.98
    atr = VolatilityContractionBreakoutStrategy._compute_atr(highs, lows, closes, 14)
    assert len(atr) == 20
    assert atr.iloc[:13].isna().all()
    assert atr.iloc[13:].notna().all()


# ═══════════════════════════════════════════════════════════════════════════
# MR — Empty / no-candidate / all-symbols
# ═══════════════════════════════════════════════════════════════════════════


def test_mr_empty_history_returns_all_zero():
    bars = pd.DataFrame(
        columns=["symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"],
    )
    weights = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(), bars,
    )
    assert weights == {}


def test_mr_no_candidates_returns_all_zero():
    start = date(2025, 1, 1)
    bars = _bars(start, {"S1": [3.0] * 15, "S2": [2.0] * 15})
    weights = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(min_price=5.0), bars,
    )
    assert weights == {"S1": 0.0, "S2": 0.0}


def test_mr_all_symbols_in_output():
    """Every symbol appears in output, even those filtered out."""
    start = date(2025, 1, 1)
    n = 20
    # PASS: gentle uptrend (clearly above MA3)
    pass_prices = [10.0 + i * 0.5 for i in range(n)]  # 10 → 19.5
    fail_prices = [3.0] * n

    bars = _bars(start, {"PASS": pass_prices, "FAIL": fail_prices}, volume=1_000_000)
    weights = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(min_price=5.0), bars,
    )
    assert set(weights.keys()) == {"PASS", "FAIL"}
    assert weights["FAIL"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# MR — Individual gate tests
# ═══════════════════════════════════════════════════════════════════════════


def test_mr_trend_filter_blocks_below_ma():
    """Stock with close <= MA3 must be filtered out."""
    start = date(2025, 1, 1)
    n = 8
    # UP: [10, 10.5, 11, 11.5, 12, 12.5, 13, 13.5], MA3=(12.5+13+13.5)/3=13, close=13.5>13 ✓
    # DOWN: [13.5, 13, 12.5, ..., 10], MA3=(11+10.5+10)/3=10.5, close=10<=10.5 ✗
    up = [10.0 + i * 0.5 for i in range(n)]
    down = [13.5 - i * 0.5 for i in range(n)]
    bars = _bars(start, {"UP": up, "DOWN": down}, volume=1_000_000)
    weights = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(trend_window=3), bars,
    )
    assert weights["UP"] > 0.0, f"UP should pass trend, got {weights['UP']}"
    assert weights["DOWN"] == 0.0, f"DOWN should fail trend, got {weights['DOWN']}"


def test_mr_oversold_filter_blocks_non_oversold():
    """Stock that hasn't dropped enough must be filtered out.

    DIP uses the "drop then recover" pattern: steep 2-bar drop followed by
    gentle 4-bar recovery.  The recovery lifts close above MA3 (passing trend)
    while the lookback return vs. 5 bars ago is still deeply negative (passing
    oversold).  STEADY keeps rising — it passes trend/RSI/zscore but not
    oversold.
    """
    start = date(2025, 1, 1)
    dip = _mr_dip_recover_prices(n_before=14, dip_size=4.0, recovery_step=0.2, recovery_bars=4)
    steady = [10.0 + i * 0.5 for i in range(len(dip))]

    bars = _bars(start, {"DIP": dip, "STEADY": steady}, volume=1_000_000)
    weights = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(trend_window=3, oversold_lookback=5, oversold_threshold=-0.05,
                rsi_oversold=90, entry_zscore=5.0),
        bars,
    )
    assert weights["DIP"] > 0.0, f"DIP should pass oversold, got {weights['DIP']}"
    assert weights["STEADY"] == 0.0, f"STEADY should fail oversold, got {weights['STEADY']}"


def test_mr_rsi_oversold_gate():
    """Stock must pass RSI<=threshold OR zscore<=-threshold to be selected.

    DIP: drop-then-recover → RSI is low from the drop → passes RSI gate.
    RALLY: pure uptrend → RSI≈100 and zscore positive → fails both gates.
    """
    start = date(2025, 1, 1)
    dip = _mr_dip_recover_prices(n_before=15, dip_size=3.5, recovery_step=0.2, recovery_bars=4)
    rally = [10.0 + i * 0.5 for i in range(len(dip))]

    bars = _bars(start, {"DIP": dip, "RALLY": rally}, volume=1_000_000)
    weights = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(trend_window=3, oversold_lookback=5, oversold_threshold=+1.0,
                rsi_window=5, rsi_oversold=40.0, zscore_window=5, entry_zscore=0.2),
        bars,
    )
    # DIP: RSI is low (< 40) AND zscore negative → passes OR gate
    # RALLY: RSI ≈ 100 AND zscore positive → fails both
    assert weights["DIP"] > 0.0, f"DIP should pass RSI/zscore, got {weights['DIP']}"
    assert weights["RALLY"] == 0.0, f"RALLY should fail both, got {weights['RALLY']}"


def test_mr_zscore_oversold_gate():
    """zscore <= -entry_zscore OR RSI <= rsi_oversold must pass.

    DIP_Z: drop-then-recover → zscore moderately negative (the high pre-drop
    bars pull the 5-bar mean above the recovering close) → passes zscore gate
    with lenient entry_zscore=0.5.  RSI gate is set extremely strict (5) so
    only zscore can pass.

    STEADY_Z: pure uptrend → zscore positive → fails both gates.
    """
    start = date(2025, 1, 1)
    # Use recovery_bars=2 so the 5-bar zscore window still contains pre-dip
    # high bars → pulls the mean above the recovering close → zscore < 0.
    dip_z = _mr_dip_recover_prices(n_before=15, dip_size=5.0, recovery_step=0.15, recovery_bars=2)
    steady_z = [10.0 + i * 0.3 for i in range(len(dip_z))]

    bars = _bars(start, {"DIP_Z": dip_z, "STEADY_Z": steady_z}, volume=1_000_000)
    weights = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(trend_window=3, oversold_lookback=5, oversold_threshold=+1.0,
                rsi_oversold=5.0,  # extremely strict RSI — effectively requires zscore
                zscore_window=5, entry_zscore=0.5),
        bars,
    )
    # DIP_Z: zscore negative enough (< -0.5) → passes zscore gate
    # STEADY_Z: zscore positive → fails zscore AND fails RSI=5
    assert weights["DIP_Z"] > 0.0, f"DIP_Z should pass zscore, got {weights['DIP_Z']}"
    assert weights["STEADY_Z"] == 0.0, f"STEADY_Z should fail, got {weights['STEADY_Z']}"


def test_mr_liquidity_filter():
    start = date(2025, 1, 1)
    n = 20
    prices = [10.0 + i * 0.5 for i in range(n)]
    rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        c = prices[i]
        rows.append({
            "symbol": "RICH", "trade_date": t,
            "open": c * 0.99, "high": c * 1.02, "low": c * 0.98, "close": c,
            "volume": 1_000_000, "amount": 200_000_000,
        })
        rows.append({
            "symbol": "POOR", "trade_date": t,
            "open": c * 0.99, "high": c * 1.02, "low": c * 0.98, "close": c,
            "volume": 1_000_000, "amount": 1_000_000,
        })
    bars = pd.DataFrame(rows)
    weights = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(min_avg_amount_20d=50_000_000), bars,
    )
    assert weights["RICH"] > 0.0, f"RICH should pass, got {weights['RICH']}"
    assert weights["POOR"] == 0.0, f"POOR should fail, got {weights['POOR']}"


def test_mr_min_price_filter():
    start = date(2025, 1, 1)
    n = 20
    bars = _bars(start, {
        "HIGH": [20.0 + i * 0.3 for i in range(n)],
        "LOW": [3.0] * n,
    }, volume=1_000_000)
    weights = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(min_price=5.0), bars,
    )
    assert weights["HIGH"] > 0.0
    assert weights["LOW"] == 0.0


def test_mr_missing_amount_column_skips_when_min_amount_set():
    start = date(2025, 1, 1)
    n = 20
    prices = [10.0 + i * 0.5 for i in range(n)]
    rows: list[dict] = []
    for i in range(n):
        rows.append({
            "symbol": "S1", "trade_date": start + timedelta(days=i),
            "open": 9.9, "high": 10.2, "low": 9.8, "close": prices[i],
            "volume": 1_000_000,
        })
    bars = pd.DataFrame(rows)
    weights = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(min_avg_amount_20d=50_000_000), bars,
    )
    assert weights["S1"] == 0.0


def test_mr_insufficient_data_skipped():
    start = date(2025, 1, 1)
    n = 20
    long_prices = [10.0 + i * 0.5 for i in range(n)]
    bars_data = _bars(start, {"LONG": long_prices}, volume=1_000_000)
    short_rows: list[dict] = []
    for i in range(4):
        short_rows.append({
            "symbol": "SHORT", "trade_date": start + timedelta(days=i),
            "open": 9.9, "high": 10.2, "low": 9.8, "close": 10.0 + i * 0.3,
            "volume": 1_000_000, "amount": 100_000_000,
        })
    bars = pd.concat([bars_data, pd.DataFrame(short_rows)], ignore_index=True)
    weights = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(trend_window=5), bars,
    )
    # min_data_len = max(5,5,5,5)+1 = 6; SHORT has 4 → skipped
    assert weights["LONG"] > 0.0
    assert weights["SHORT"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# MR — Scoring & ranking
# ═══════════════════════════════════════════════════════════════════════════


def test_mr_top_n_selection():
    start = date(2025, 1, 1)
    n = 20
    # All 5 stocks: gentle uptrend with a small dip (passes generous gates)
    symbols = {}
    for i in range(5):
        prices = [10.0 + j * 0.5 + i * 0.02 for j in range(n - 3)]
        prices += [prices[-1] + 0.1, prices[-1] + 0.05, prices[-1] + 0.08]
        symbols[f"S{i}"] = prices
    bars = _bars(start, symbols, volume=1_000_000)
    weights = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(top_n=2), bars,
    )
    positive = sum(1 for w in weights.values() if w > 0)
    assert positive == 2, f"Expected 2 positive weights, got {positive}"


def test_mr_single_candidate_gets_full_allocation():
    start = date(2025, 1, 1)
    n = 20
    prices = [10.0 + i * 0.5 for i in range(n)]
    bars = _bars(start, {"ONLY": prices}, volume=1_000_000)
    weights = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(top_n=10, max_total_weight=0.75), bars,
    )
    assert abs(weights["ONLY"] - 0.75) < 1e-9, f"Expected 0.75, got {weights['ONLY']}"


def test_mr_position_cap_respected():
    start = date(2025, 1, 1)
    n = 20
    symbols = {}
    for i in range(5):
        prices = [10.0 + j * 0.3 + i * 0.05 for j in range(n)]
        symbols[f"S{i}"] = prices
    bars = _bars(start, symbols, volume=1_000_000)
    weights = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(top_n=5, max_position_weight=0.1, max_total_weight=0.95), bars,
    )
    for sym, w in weights.items():
        if w > 0:
            assert w <= 0.1 + 1e-9, f"{sym} weight {w} exceeds cap 0.1"


def test_mr_total_weight_cap_respected():
    start = date(2025, 1, 1)
    n = 20
    symbols = {}
    for i in range(5):
        prices = [10.0 + j * 0.3 + i for j in range(n)]
        symbols[f"S{i}"] = prices
    bars = _bars(start, symbols, volume=1_000_000)
    weights = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(top_n=5, max_total_weight=0.35), bars,
    )
    total = sum(weights.values())
    assert total <= 0.35 + 1e-9, f"Total {total} exceeds cap 0.35"


def test_mr_deeper_oversold_ranks_higher():
    """DEEPER dip should outrank SHALLOWER dip.

    Both use the drop-then-recover pattern, but DEEP has a larger drop.
    The deeper dip produces a lower RSI, more negative zscore, and more
    negative lookback return → higher percentile score → higher rank.
    """
    start = date(2025, 1, 1)
    deep = _mr_dip_recover_prices(n_before=15, dip_size=6.0, recovery_step=0.15, recovery_bars=2)
    shallow = _mr_dip_recover_prices(n_before=15, dip_size=1.5, recovery_step=0.15, recovery_bars=2)

    bars = _bars(start, {"DEEP": deep, "SHALLOW": shallow}, volume=1_000_000)
    # top_n=1: only the highest-scored candidate gets weight
    weights = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(top_n=1, trend_window=3, oversold_lookback=5, oversold_threshold=+1.0,
                rsi_oversold=90, entry_zscore=5.0),
        bars,
    )
    # DEEP has more negative returns, lower RSI, more negative zscore
    # → higher composite percentile → ranked #1 → gets the weight
    assert weights["DEEP"] > 0.0, f"DEEP should be selected, got {weights['DEEP']}"
    assert weights["SHALLOW"] == 0.0, f"SHALLOW should not be selected, got {weights['SHALLOW']}"


# ═══════════════════════════════════════════════════════════════════════════
# MR — Static helpers
# ═══════════════════════════════════════════════════════════════════════════


def test_compute_rsi_all_gains():
    closes = pd.Series([10.0 + i * 1.0 for i in range(20)])
    rsi = TrendFilteredMeanReversionStrategy._compute_rsi(closes, 14)
    assert rsi.iloc[-1] == 100.0, f"Expected RSI=100, got {rsi.iloc[-1]}"


def test_compute_rsi_all_losses():
    closes = pd.Series([20.0 - i * 1.0 for i in range(20)])
    rsi = TrendFilteredMeanReversionStrategy._compute_rsi(closes, 14)
    assert rsi.iloc[-1] == 0.0, f"Expected RSI=0, got {rsi.iloc[-1]}"


def test_compute_rsi_flat():
    closes = pd.Series([10.0] * 20)
    rsi = TrendFilteredMeanReversionStrategy._compute_rsi(closes, 14)
    assert rsi.iloc[-1] == 50.0, f"Expected RSI=50, got {rsi.iloc[-1]}"


def test_compute_rsi_typical():
    closes = pd.Series([10.0, 10.5, 10.2, 10.8, 10.3, 11.0, 10.5, 11.2,
                        10.8, 11.5, 11.0, 11.8, 11.3, 12.0, 11.5])
    rsi = TrendFilteredMeanReversionStrategy._compute_rsi(closes, 5)
    assert 0.0 <= rsi.iloc[-1] <= 100.0
    assert not pd.isna(rsi.iloc[-1])


def test_compute_zscore_basic():
    closes = pd.Series([10.0] * 20)
    zscore = TrendFilteredMeanReversionStrategy._compute_zscore(closes, 5)
    assert pd.isna(zscore.iloc[-1]), f"Zero-std zscore should be NaN, got {zscore.iloc[-1]}"


def test_compute_zscore_uptrend():
    closes = pd.Series([10.0 + i * 0.5 for i in range(20)])
    zscore = TrendFilteredMeanReversionStrategy._compute_zscore(closes, 5)
    assert not pd.isna(zscore.iloc[-1])
    assert zscore.iloc[-1] > 0, f"Uptrend zscore positive, got {zscore.iloc[-1]}"


def test_compute_zscore_downtrend():
    closes = pd.Series([15.0 - i * 0.2 for i in range(15)] + [12.5, 11.0, 9.5, 8.0, 6.5])
    zscore = TrendFilteredMeanReversionStrategy._compute_zscore(closes, 5)
    assert not pd.isna(zscore.iloc[-1])
    assert zscore.iloc[-1] < 0, f"Dip zscore negative, got {zscore.iloc[-1]}"


# ═══════════════════════════════════════════════════════════════════════════
# MR — Parameter validation
# ═══════════════════════════════════════════════════════════════════════════


def test_mr_window_params_must_be_gt_1():
    strategy = TrendFilteredMeanReversionStrategy()
    bars = _bars(date(2025, 1, 1), {"S": [10.0] * 15})
    for param in ["trend_window", "oversold_lookback", "rsi_window", "zscore_window"]:
        with pytest.raises(ValueError, match="must be > 1"):
            strategy.generate_target_weights(_mr_ctx(**{param: 1}), bars)


def test_mr_nan_param_raises():
    strategy = TrendFilteredMeanReversionStrategy()
    bars = _bars(date(2025, 1, 1), {"S": [10.0] * 15})
    with pytest.raises(ValueError, match="NaN"):
        strategy.generate_target_weights(_mr_ctx(min_price=float("nan")), bars)
    with pytest.raises(ValueError, match="NaN"):
        strategy.generate_target_weights(_mr_ctx(rsi_oversold=float("nan")), bars)


def test_mr_out_of_range_params_raise_value_error():
    strategy = TrendFilteredMeanReversionStrategy()
    bars = _bars(date(2025, 1, 1), {"S": [10.0] * 15})

    for name, val in [
        ("max_position_weight", 1.5), ("max_position_weight", -0.1),
        ("max_total_weight", 2.0), ("max_total_weight", -0.5),
    ]:
        with pytest.raises(ValueError):
            strategy.generate_target_weights(_mr_ctx(**{name: val}), bars)

    for val in [150.0, -10.0]:
        with pytest.raises(ValueError, match="must be in \\[0, 100\\]"):
            strategy.generate_target_weights(_mr_ctx(rsi_oversold=val), bars)

    for name, val in [
        ("entry_zscore", -1.0), ("min_price", -5.0), ("min_avg_amount_20d", -100),
    ]:
        with pytest.raises(ValueError):
            strategy.generate_target_weights(_mr_ctx(**{name: val}), bars)


# ═══════════════════════════════════════════════════════════════════════════
# MR — Metadata
# ═══════════════════════════════════════════════════════════════════════════


def test_mr_metadata():
    meta = TrendFilteredMeanReversionStrategy.metadata()
    assert meta["name"] == "trend_filtered_mean_reversion"
    assert meta["display_name"] == "趋势过滤均值回归策略"
    param_names = {p["name"] for p in meta["parameters"]}
    expected = {
        "trend_window", "oversold_lookback", "oversold_threshold",
        "rsi_window", "rsi_oversold", "zscore_window", "entry_zscore",
        "min_avg_amount_20d", "min_price", "top_n",
        "max_position_weight", "max_total_weight",
    }
    assert param_names == expected


def test_mr_registered_in_advanced_strategies():
    assert "trend_filtered_mean_reversion" in ADVANCED_STRATEGIES
    assert ADVANCED_STRATEGIES["trend_filtered_mean_reversion"] is TrendFilteredMeanReversionStrategy


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases — both strategies
# ═══════════════════════════════════════════════════════════════════════════


def test_zero_volume_and_amount_handled():
    start = date(2025, 1, 1)
    n = 10
    rows: list[dict] = []
    for i in range(n):
        rows.append({
            "symbol": "S1", "trade_date": start + timedelta(days=i),
            "open": 9.9, "high": 10.2, "low": 9.8, "close": 10.0 + i * 0.5,
            "volume": 0.0, "amount": 0.0,
        })
    bars = pd.DataFrame(rows)
    # VCP: zero vol → vol_long_ma=0 → skipped (does not crash)
    weights_vcp = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(volume_expansion_threshold=0.0), bars,
    )
    assert "S1" in weights_vcp
    assert weights_vcp["S1"] == 0.0  # skipped due to vol_long_ma <= 0


def test_negative_prices_not_used():
    start = date(2025, 1, 1)
    n = 10
    bad_prices = [10.0 - i * 1.5 for i in range(n)]
    bars = _bars(start, {"BAD": bad_prices}, volume=1_000_000)

    # Should not crash
    weights_vcp = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(min_price=0.0), bars,
    )
    assert "BAD" in weights_vcp

    weights_mr = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(min_price=0.0), bars,
    )
    assert "BAD" in weights_mr


def test_single_bar_per_symbol():
    start = date(2025, 1, 1)
    bars = _bars(start, {"S1": [10.0], "S2": [12.0]})
    weights_vcp = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(), bars,
    )
    assert weights_vcp == {"S1": 0.0, "S2": 0.0}
    weights_mr = TrendFilteredMeanReversionStrategy().generate_target_weights(
        _mr_ctx(), bars,
    )
    assert weights_mr == {"S1": 0.0, "S2": 0.0}


def test_identical_candidates_equal_weights():
    """When multiple candidates have identical price paths, equal weight each."""
    start = date(2025, 1, 1)
    n = 15
    # Truly identical price paths
    prices = _vcp_pass_prices(n)
    symbols = {"S0": prices, "S1": list(prices), "S2": list(prices)}
    bars = _bars(start, symbols, volume=2_000_000, amount=100_000_000)

    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(top_n=3, max_total_weight=0.9), bars,
    )
    positive = {s: round(w, 10) for s, w in weights.items() if w > 0}
    unique_w = set(positive.values())
    assert len(unique_w) == 1, f"Identical scores → same weight, got {positive}"
    assert abs(sum(weights.values()) - 0.9) < 1e-9


def test_market_data_with_nan_in_non_essential():
    """NaN in non-essential columns (like 'open') should not crash."""
    start = date(2025, 1, 1)
    n = 10
    rows: list[dict] = []
    for i in range(n):
        rows.append({
            "symbol": "S1", "trade_date": start + timedelta(days=i),
            "open": float("nan"), "high": 10.2, "low": 9.8,
            "close": 10.0 + i * 0.3, "volume": 1_000_000, "amount": 100_000_000,
        })
    bars = pd.DataFrame(rows)
    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(), bars,
    )
    assert "S1" in weights


def test_high_equal_low_equal_close_same_bar():
    """When high==low==close and prices change day to day, ATR > 0 from prev_close."""
    start = date(2025, 1, 1)
    n = 15
    rows: list[dict] = []
    for i in range(n):
        c = 10.0 + i * 0.3
        rows.append({
            "symbol": "S1", "trade_date": start + timedelta(days=i),
            "open": c, "high": c, "low": c, "close": c,
            "volume": 1_000_000, "amount": 100_000_000,
        })
    bars = pd.DataFrame(rows)
    # Even with high=low=close, true range uses |high - prev_close| which is ~0.3
    # So ATR > 0 → candidate can be selected
    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(), bars,
    )
    assert weights["S1"] > 0.0, f"Should be selected (ATR > 0 from prev_close gap), got {weights['S1']}"


def test_symbol_count_smaller_than_top_n():
    """When fewer candidates than top_n, all are selected."""
    start = date(2025, 1, 1)
    n = 10
    symbols = {f"S{i}": _vcp_pass_prices(n) for i in range(3)}
    bars = _bars(start, symbols, volume=2_000_000, amount=100_000_000)
    weights = VolatilityContractionBreakoutStrategy().generate_target_weights(
        _vcp_ctx(top_n=10, max_position_weight=0.3, max_total_weight=0.8), bars,
    )
    for sym in ["S0", "S1", "S2"]:
        assert weights[sym] > 0.0, f"{sym} should have weight"
        assert weights[sym] <= 0.3 + 1e-9
