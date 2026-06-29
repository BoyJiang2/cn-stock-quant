"""Tests for RiskAdjustedMomentumStrategy (风险调整动量轮动策略).

Covers: candidate gates (trend / Sharpe / liquidity / price), ranking by
Sharpe, weighting caps, parameter validation, metadata, and the static
``_compute_sharpe`` helper.
"""

from datetime import date, timedelta

import math

import pandas as pd
import pytest

from app.strategy.advanced import (
    ADVANCED_STRATEGIES,
    RiskAdjustedMomentumStrategy,
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
    """Build an OHLCV DataFrame for symbol→price_series. All series same length."""
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


def _ctx(**params) -> StrategyContext:
    """Generous context — all gates wide open by default.

    Individual tests tighten the specific parameter under test.
    """
    defaults: dict = {
        "trend_window": 3,
        "sharpe_window": 5,
        "min_sharpe": 0.0,
        "min_avg_amount_20d": 0,
        "min_price": 0.0,
        "top_n": 10,
        "max_position_weight": 1.0,
        "max_total_weight": 1.0,
    }
    defaults.update(params)
    return StrategyContext(current_date=date.today(), cash=1_000_000, params=defaults)


def _uptrend_with_wiggle(n: int, slope: float, wiggle: float, start: float = 10.0) -> list[float]:
    """Deterministic uptrend: linear slope + alternating ±wiggle.

    ``wiggle`` small → low return dispersion → high Sharpe.
    ``wiggle`` large → high return dispersion → low Sharpe.
    Both remain clear uptrends (close > MA3) when slope > wiggle/2.
    """
    return [start + i * slope + (wiggle if i % 2 == 0 else -wiggle) for i in range(n)]


# ═══════════════════════════════════════════════════════════════════════════
# Empty / no-candidate / all-symbols
# ═══════════════════════════════════════════════════════════════════════════


def test_empty_history_returns_all_zero():
    bars = pd.DataFrame(
        columns=["symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"],
    )
    weights = RiskAdjustedMomentumStrategy().generate_target_weights(_ctx(), bars)
    assert weights == {}


def test_no_candidates_returns_all_zero():
    start = date(2025, 1, 1)
    # All below min_price
    bars = _bars(start, {"S1": [3.0] * 10, "S2": [4.0] * 10})
    weights = RiskAdjustedMomentumStrategy().generate_target_weights(
        _ctx(min_price=5.0), bars,
    )
    assert weights == {"S1": 0.0, "S2": 0.0}


def test_all_symbols_in_output():
    start = date(2025, 1, 1)
    n = 10
    bars = _bars(start, {
        "PASS": _uptrend_with_wiggle(n, slope=0.5, wiggle=0.05),
        "FAIL": [3.0] * n,
    }, volume=2_000_000)
    weights = RiskAdjustedMomentumStrategy().generate_target_weights(
        _ctx(min_price=5.0), bars,
    )
    assert set(weights.keys()) == {"PASS", "FAIL"}
    assert weights["PASS"] > 0.0
    assert weights["FAIL"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Individual gate tests
# ═══════════════════════════════════════════════════════════════════════════


def test_trend_filter_blocks_below_ma():
    """Stock with close <= MA(trend_window) must be filtered out."""
    start = date(2025, 1, 1)
    n = 8
    up = [10.0 + i * 0.5 for i in range(n)]      # MA3 tail = 13, close=13.5 > 13
    down = [13.5 - i * 0.5 for i in range(n)]    # MA3 tail = 10.5, close=10 <= 10.5
    bars = _bars(start, {"UP": up, "DOWN": down}, volume=2_000_000)
    weights = RiskAdjustedMomentumStrategy().generate_target_weights(
        _ctx(trend_window=3, min_sharpe=-100.0), bars,
    )
    assert weights["UP"] > 0.0
    assert weights["DOWN"] == 0.0


def test_min_sharpe_gate_blocks_low_sharpe():
    """A choppy stock with Sharpe below the threshold is filtered out.

    STEADY: tiny wiggle → high Sharpe → passes a strict min_sharpe.
    CHOPPY: large wiggle around the same slope → low Sharpe → filtered.
    """
    start = date(2025, 1, 1)
    n = 21
    steady = _uptrend_with_wiggle(n, slope=0.3, wiggle=0.02)
    choppy = _uptrend_with_wiggle(n, slope=0.3, wiggle=0.5)
    bars = _bars(start, {"STEADY": steady, "CHOPPY": choppy}, volume=2_000_000)

    # First confirm both pass a generous gate
    w_loose = RiskAdjustedMomentumStrategy().generate_target_weights(
        _ctx(trend_window=3, sharpe_window=5, min_sharpe=-100.0), bars,
    )
    assert w_loose["STEADY"] > 0.0
    assert w_loose["CHOPPY"] > 0.0

    # Now require a high Sharpe — CHOPPY must drop out
    w_strict = RiskAdjustedMomentumStrategy().generate_target_weights(
        _ctx(trend_window=3, sharpe_window=5, min_sharpe=1.0), bars,
    )
    assert w_strict["STEADY"] > 0.0, f"STEADY should pass strict gate, got {w_strict['STEADY']}"
    assert w_strict["CHOPPY"] == 0.0, f"CHOPPY should fail strict gate, got {w_strict['CHOPPY']}"


def test_negative_sharpe_stock_blocked_by_default_min_sharpe():
    """A downtrending stock has negative mean return → negative Sharpe →
    filtered by the default min_sharpe=0."""
    start = date(2025, 1, 1)
    n = 21
    down = [20.0 - i * 0.3 for i in range(n)]  # clear downtrend
    bars = _bars(start, {"DOWN": down}, volume=2_000_000)
    weights = RiskAdjustedMomentumStrategy().generate_target_weights(
        _ctx(trend_window=3, min_price=0.0, min_avg_amount_20d=0), bars,
    )
    # Downtrend fails both trend filter and min_sharpe=0
    assert weights["DOWN"] == 0.0


def test_liquidity_filter():
    start = date(2025, 1, 1)
    n = 21
    prices = _uptrend_with_wiggle(n, slope=0.3, wiggle=0.05)
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
    weights = RiskAdjustedMomentumStrategy().generate_target_weights(
        _ctx(min_avg_amount_20d=50_000_000), bars,
    )
    assert weights["RICH"] > 0.0
    assert weights["POOR"] == 0.0


def test_min_price_filter():
    start = date(2025, 1, 1)
    n = 21
    bars = _bars(start, {
        "HIGH": _uptrend_with_wiggle(n, slope=0.5, wiggle=0.05, start=20.0),
        "LOW": [3.0] * n,
    }, volume=2_000_000)
    weights = RiskAdjustedMomentumStrategy().generate_target_weights(
        _ctx(min_price=5.0), bars,
    )
    assert weights["HIGH"] > 0.0
    assert weights["LOW"] == 0.0


def test_missing_amount_column_skips_when_min_amount_set():
    start = date(2025, 1, 1)
    n = 21
    prices = _uptrend_with_wiggle(n, slope=0.3, wiggle=0.05)
    rows: list[dict] = []
    for i in range(n):
        rows.append({
            "symbol": "S1", "trade_date": start + timedelta(days=i),
            "open": 9.9, "high": 10.2, "low": 9.8, "close": prices[i],
            "volume": 2_000_000,
        })
    bars = pd.DataFrame(rows)
    weights = RiskAdjustedMomentumStrategy().generate_target_weights(
        _ctx(min_avg_amount_20d=50_000_000), bars,
    )
    assert weights["S1"] == 0.0


def test_insufficient_data_skipped():
    start = date(2025, 1, 1)
    n = 21
    bars_data = _bars(start, {"LONG": _uptrend_with_wiggle(n, slope=0.3, wiggle=0.05)}, volume=2_000_000)
    short_rows: list[dict] = []
    for i in range(3):
        short_rows.append({
            "symbol": "SHORT", "trade_date": start + timedelta(days=i),
            "open": 9.9, "high": 10.2, "low": 9.8, "close": 10.0 + i * 0.3,
            "volume": 2_000_000, "amount": 100_000_000,
        })
    bars = pd.concat([bars_data, pd.DataFrame(short_rows)], ignore_index=True)
    weights = RiskAdjustedMomentumStrategy().generate_target_weights(
        _ctx(trend_window=5, sharpe_window=5), bars,
    )
    # min_data_len = max(5,5)+1 = 6; SHORT has 3 → skipped
    assert weights["LONG"] > 0.0
    assert weights["SHORT"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Scoring & ranking
# ═══════════════════════════════════════════════════════════════════════════


def test_higher_sharpe_ranks_higher():
    """STEADY (small wiggle → high Sharpe) outranks CHOPPY (large wiggle → low Sharpe)."""
    start = date(2025, 1, 1)
    n = 21
    steady = _uptrend_with_wiggle(n, slope=0.3, wiggle=0.02)
    choppy = _uptrend_with_wiggle(n, slope=0.3, wiggle=0.5)
    bars = _bars(start, {"STEADY": steady, "CHOPPY": choppy}, volume=2_000_000)

    weights = RiskAdjustedMomentumStrategy().generate_target_weights(
        _ctx(trend_window=3, sharpe_window=5, min_sharpe=0.0, top_n=1), bars,
    )
    assert weights["STEADY"] > 0.0, f"STEADY should be selected, got {weights['STEADY']}"
    assert weights["CHOPPY"] == 0.0, f"CHOPPY should not be selected, got {weights['CHOPPY']}"


def test_top_n_selection():
    start = date(2025, 1, 1)
    n = 21
    symbols = {
        f"S{i}": _uptrend_with_wiggle(n, slope=0.3 + i * 0.01, wiggle=0.05 + i * 0.02)
        for i in range(5)
    }
    bars = _bars(start, symbols, volume=2_000_000)
    weights = RiskAdjustedMomentumStrategy().generate_target_weights(
        _ctx(top_n=2), bars,
    )
    positive = sum(1 for w in weights.values() if w > 0)
    assert positive == 2, f"Expected 2 positive weights, got {positive}"


def test_single_candidate_gets_full_allocation():
    start = date(2025, 1, 1)
    n = 21
    bars = _bars(start, {"ONLY": _uptrend_with_wiggle(n, slope=0.3, wiggle=0.05)}, volume=2_000_000)
    weights = RiskAdjustedMomentumStrategy().generate_target_weights(
        _ctx(top_n=10, max_total_weight=0.8), bars,
    )
    assert abs(weights["ONLY"] - 0.8) < 1e-9, f"Expected 0.8, got {weights['ONLY']}"


def test_position_cap_respected():
    start = date(2025, 1, 1)
    n = 21
    symbols = {
        f"S{i}": _uptrend_with_wiggle(n, slope=0.3 + i * 0.1, wiggle=0.05)
        for i in range(5)
    }
    bars = _bars(start, symbols, volume=2_000_000)
    weights = RiskAdjustedMomentumStrategy().generate_target_weights(
        _ctx(top_n=5, max_position_weight=0.08, max_total_weight=0.95), bars,
    )
    for sym, w in weights.items():
        if w > 0:
            assert w <= 0.08 + 1e-9, f"{sym} weight {w} exceeds cap 0.08"


def test_total_weight_cap_respected():
    start = date(2025, 1, 1)
    n = 21
    symbols = {
        f"S{i}": _uptrend_with_wiggle(n, slope=0.3 + i * 0.5, wiggle=0.05)
        for i in range(5)
    }
    bars = _bars(start, symbols, volume=2_000_000)
    weights = RiskAdjustedMomentumStrategy().generate_target_weights(
        _ctx(top_n=5, max_total_weight=0.4), bars,
    )
    total = sum(weights.values())
    assert total <= 0.4 + 1e-9, f"Total weight {total} exceeds cap 0.4"


def test_symbol_count_smaller_than_top_n():
    """When fewer candidates than top_n, all are selected."""
    start = date(2025, 1, 1)
    n = 21
    symbols = {f"S{i}": _uptrend_with_wiggle(n, slope=0.3, wiggle=0.05) for i in range(3)}
    bars = _bars(start, symbols, volume=2_000_000, amount=100_000_000)
    weights = RiskAdjustedMomentumStrategy().generate_target_weights(
        _ctx(top_n=10, max_position_weight=0.3, max_total_weight=0.8), bars,
    )
    for sym in ["S0", "S1", "S2"]:
        assert weights[sym] > 0.0, f"{sym} should have weight"
        assert weights[sym] <= 0.3 + 1e-9


# ═══════════════════════════════════════════════════════════════════════════
# Static helper — _compute_sharpe
# ═══════════════════════════════════════════════════════════════════════════


def test_compute_sharpe_constant_prices_returns_nan():
    closes = pd.Series([10.0] * 25)
    val = RiskAdjustedMomentumStrategy._compute_sharpe(closes, 20)
    assert math.isnan(val), f"constant prices → undefined Sharpe, got {val}"


def test_compute_sharpe_insufficient_data_returns_nan():
    closes = pd.Series([10.0, 10.5, 11.0])  # only 2 returns, need 20
    val = RiskAdjustedMomentumStrategy._compute_sharpe(closes, 20)
    assert math.isnan(val)


def test_compute_sharpe_hand_calc():
    # 20 returns alternating +0.03 / -0.01 → mean=0.01, std=sqrt(0.008/19)
    returns = [0.03, -0.01] * 10
    px = [100.0]
    for r in returns:
        px.append(px[-1] * (1 + r))
    closes = pd.Series(px)
    val = RiskAdjustedMomentumStrategy._compute_sharpe(closes, 20)
    expected = 0.01 / math.sqrt(0.008 / 19.0)
    assert math.isclose(val, expected, rel_tol=1e-9), f"expected {expected}, got {val}"


def test_compute_sharpe_zero_mean_returns_zero():
    returns = [0.02 if i % 2 == 0 else -0.02 for i in range(20)]
    px = [100.0]
    for r in returns:
        px.append(px[-1] * (1 + r))
    closes = pd.Series(px)
    val = RiskAdjustedMomentumStrategy._compute_sharpe(closes, 20)
    assert math.isclose(val, 0.0, abs_tol=1e-12)


# ═══════════════════════════════════════════════════════════════════════════
# Parameter validation
# ═══════════════════════════════════════════════════════════════════════════


def test_window_params_must_be_gt_1():
    strategy = RiskAdjustedMomentumStrategy()
    bars = _bars(date(2025, 1, 1), {"S": [10.0] * 10}, volume=2_000_000)
    for param in ["trend_window", "sharpe_window"]:
        with pytest.raises(ValueError, match="must be > 1"):
            strategy.generate_target_weights(_ctx(**{param: 1}), bars)


def test_nan_param_raises():
    strategy = RiskAdjustedMomentumStrategy()
    bars = _bars(date(2025, 1, 1), {"S": [10.0] * 10}, volume=2_000_000)
    with pytest.raises(ValueError, match="NaN"):
        strategy.generate_target_weights(_ctx(min_price=float("nan")), bars)
    with pytest.raises(ValueError, match="NaN"):
        strategy.generate_target_weights(_ctx(min_sharpe=float("nan")), bars)


def test_out_of_range_params_raise_value_error():
    strategy = RiskAdjustedMomentumStrategy()
    bars = _bars(date(2025, 1, 1), {"S": [10.0] * 10}, volume=2_000_000)

    for name, val in [
        ("max_position_weight", 1.5), ("max_position_weight", -0.1),
        ("max_total_weight", 2.0), ("max_total_weight", -0.5),
    ]:
        with pytest.raises(ValueError, match="must be in \\[0, 1\\]"):
            strategy.generate_target_weights(_ctx(**{name: val}), bars)

    for name, val in [
        ("min_price", -5.0),
        ("min_avg_amount_20d", -100),
    ]:
        with pytest.raises(ValueError, match="must be >= 0"):
            strategy.generate_target_weights(_ctx(**{name: val}), bars)


# ═══════════════════════════════════════════════════════════════════════════
# Metadata & registration
# ═══════════════════════════════════════════════════════════════════════════


def test_metadata():
    meta = RiskAdjustedMomentumStrategy.metadata()
    assert meta["name"] == "risk_adjusted_momentum"
    assert meta["display_name"] == "Risk-Adjusted Momentum"
    param_names = {p["name"] for p in meta["parameters"]}
    expected = {
        "trend_window", "sharpe_window", "min_sharpe",
        "min_avg_amount_20d", "min_price", "top_n",
        "max_position_weight", "max_total_weight",
    }
    assert param_names == expected


def test_registered_in_advanced_strategies():
    assert "risk_adjusted_momentum" in ADVANCED_STRATEGIES
    assert ADVANCED_STRATEGIES["risk_adjusted_momentum"] is RiskAdjustedMomentumStrategy


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════


def test_zero_volume_and_amount_handled():
    start = date(2025, 1, 1)
    n = 21
    rows: list[dict] = []
    for i in range(n):
        rows.append({
            "symbol": "S1", "trade_date": start + timedelta(days=i),
            "open": 9.9, "high": 10.2, "low": 9.8,
            "close": 10.0 + i * 0.3, "volume": 0.0, "amount": 0.0,
        })
    bars = pd.DataFrame(rows)
    # Should not crash; with min_avg_amount_20d=0 it still runs, Sharpe is finite
    weights = RiskAdjustedMomentumStrategy().generate_target_weights(
        _ctx(min_avg_amount_20d=0), bars,
    )
    assert "S1" in weights


def test_single_bar_per_symbol():
    start = date(2025, 1, 1)
    bars = _bars(start, {"S1": [10.0], "S2": [12.0]})
    weights = RiskAdjustedMomentumStrategy().generate_target_weights(_ctx(), bars)
    assert weights == {"S1": 0.0, "S2": 0.0}
