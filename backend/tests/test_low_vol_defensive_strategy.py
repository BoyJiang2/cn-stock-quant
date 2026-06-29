"""Deterministic tests for LowVolDefensiveStrategy (低波抗跌趋势策略)."""

from datetime import date, timedelta

import pandas as pd
import pytest

from app.strategy.base import StrategyContext
from app.strategy.examples import BUILTIN_STRATEGIES, LowVolDefensiveStrategy


# ── helpers ──────────────────────────────────────────────────────────────
def _bars(
    start: date,
    symbols_prices: dict[str, list[float]],
    *,
    volume: float = 1_000_000.0,
    amount: float = 100_000_000.0,
) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame for the given symbol→price_series map.

    All price series must have the same length.  Each day open/high/low are
    derived from close so the row is self-consistent.
    """
    length = {s: len(p) for s, p in symbols_prices.items()}
    if len(set(length.values())) != 1:
        raise ValueError("All price series must have the same length")
    n = list(length.values())[0]

    rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        for sym, prices in symbols_prices.items():
            c = float(prices[i])
            rows.append({
                "symbol": sym,
                "trade_date": t,
                "open": c * 0.99,
                "high": c * 1.02,
                "low": c * 0.98,
                "close": c,
                "volume": volume,
                "amount": amount,
            })
    return pd.DataFrame(rows)


def _context(**params) -> StrategyContext:
    """StrategyContext with small windows suitable for synthetic short series."""
    defaults = {
        "trend_window": 5,
        "volatility_window": 5,
        "volume_short_window": 3,
        "volume_long_window": 5,
        "drawdown_window": 5,
        "top_n": 20,
        "max_position_weight": 0.1,
        "max_total_weight": 0.95,
        "min_avg_amount_20d": 50_000_000,
        "min_price": 5.0,
        "min_up_day_ratio": 0.5,
        "max_drawdown": 0.25,
    }
    defaults.update(params)
    return StrategyContext(current_date=date.today(), cash=1_000_000, params=defaults)


# ══════════════════════════════════════════════════════════════════════════
# Filter tests
# ══════════════════════════════════════════════════════════════════════════


def test_filters_pass_and_fail_independently():
    """Each filter should gate independently; only candidates passing all receive weight."""
    start = date(2025, 1, 1)
    n = 10  # bars per symbol

    # ── PASS ── steady uptrend, high amount, no drawdown
    pass_prices = [10.0 + i * 0.5 for i in range(n)]  # 10 → 14.5, every day up

    # ── BELOW_MA ── flat then dips so close <= MA5
    # MA5 of last 5: [15,15,15,15,14] = 14.8, close=14 ≤ 14.8
    below_ma_prices = [15.0] * (n - 1) + [14.0]

    # ── LOW_AMOUNT ── same as PASS but we override amount in bars
    low_amount_prices = pass_prices[:]  # prices pass, amount fails

    # ── LOW_PRICE ── close always < min_price (5)
    low_price_prices = [4.0 + i * 0.1 for i in range(n)]  # 4.0 → 4.9

    # ── LOW_UP_RATIO ── mostly down days: only 1 up in last 5 changes
    # Last 5 closes: [8,7.5,7,6.5,6] → 4 changes all negative → up_ratio=0
    low_up_prices = [10.0, 9.5, 9.0, 8.5, 8.0, 7.5, 7.0, 6.5, 6.0, 5.5]

    # ── HIGH_DRAWDOWN ── sharp drop in last 5 days
    # Last 5 closes: [7,6,5,6,5], expanding peak: [7,7,7,7,7]
    # dd = [0,-0.143,-0.286,-0.143,-0.286], worst=-0.286, abs=0.286 > 0.25
    high_dd_prices = [10.0, 9.0, 8.0, 7.0, 6.0, 7.0, 6.0, 5.0, 6.0, 5.0]

    all_prices = {
        "PASS": pass_prices,
        "BELOW_MA": below_ma_prices,
        "LOW_AMOUNT": low_amount_prices,
        "LOW_PRICE": low_price_prices,
        "LOW_UP": low_up_prices,
        "HIGH_DD": high_dd_prices,
    }

    # Build bars — override amount for LOW_AMOUNT to 1M (below 50M threshold)
    bars_list: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        for sym, prices in all_prices.items():
            c = float(prices[i])
            amt = 1_000_000.0 if sym == "LOW_AMOUNT" else 100_000_000.0
            bars_list.append({
                "symbol": sym,
                "trade_date": t,
                "open": c * 0.99,
                "high": c * 1.02,
                "low": c * 0.98,
                "close": c,
                "volume": 1_000_000,
                "amount": amt,
            })
    bars = pd.DataFrame(bars_list)

    weights = LowVolDefensiveStrategy().generate_target_weights(
        _context(min_avg_amount_20d=50_000_000),
        bars,
    )

    # Only PASS should be selected
    assert weights["PASS"] > 0.0, f"PASS should have weight > 0, got {weights['PASS']}"
    for sym in ["BELOW_MA", "LOW_AMOUNT", "LOW_PRICE", "LOW_UP", "HIGH_DD"]:
        assert weights[sym] == 0.0, f"{sym} should be filtered out, got {weights[sym]}"


def test_no_candidates_returns_all_zero():
    """When no stock passes filters, every symbol gets explicit 0.0 weight."""
    start = date(2025, 1, 1)
    # All prices below min_price=5
    bars = _bars(start, {"S1": [3.0] * 8, "S2": [4.0] * 8})
    weights = LowVolDefensiveStrategy().generate_target_weights(_context(), bars)
    assert weights == {"S1": 0.0, "S2": 0.0}


def test_empty_history_returns_all_zero():
    """Empty history → every unique symbol gets 0.0."""
    bars = pd.DataFrame(columns=["symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"])
    weights = LowVolDefensiveStrategy().generate_target_weights(_context(), bars)
    assert weights == {}


def test_all_symbols_explicitly_in_output():
    """Every symbol present in history must appear in the output dict, even with weight 0."""
    start = date(2025, 1, 1)
    # PASS will be selected, EXTRA won't meet price filter
    bars = _bars(start, {
        "PASS": [10.0 + i * 0.5 for i in range(10)],
        "EXTRA": [3.0] * 10,
    })
    weights = LowVolDefensiveStrategy().generate_target_weights(
        _context(min_price=5.0),
        bars,
    )
    assert set(weights.keys()) == {"PASS", "EXTRA"}
    assert weights["PASS"] > 0.0
    assert weights["EXTRA"] == 0.0


# ══════════════════════════════════════════════════════════════════════════
#  Low-volatility ranking
# ══════════════════════════════════════════════════════════════════════════


def test_low_volatility_stock_ranks_higher():
    """A stock with lower 20-day return volatility should get a higher low_vol score
    and therefore rank above an otherwise-identical high-volatility peer."""
    start = date(2025, 1, 1)
    n = 10

    # Low-vol: steady climb, small consistent returns → low std
    low_vol_prices = [10.0 + i * 0.3 for i in range(n)]  # ≈3 % per step, constant

    # High-vol: lumpy path with clear close > MA5 and larger return std
    high_vol_prices = [10.0, 11.0, 9.0, 12.0, 9.0, 13.0, 10.0, 14.0, 11.0, 15.0]

    bars = _bars(start, {"LV": low_vol_prices, "HV": high_vol_prices})

    weights = LowVolDefensiveStrategy().generate_target_weights(
        _context(top_n=2, max_position_weight=0.5, max_total_weight=1.0),
        bars,
    )

    # Both candidates should be selected (top_n=2), but low-vol should have
    # higher weight because inverse-vol weighting gives it more allocation
    assert weights["LV"] > weights["HV"], (
        f"Low-volatility stock should get higher weight: LV={weights['LV']:.4f}, HV={weights['HV']:.4f}"
    )
    assert weights["LV"] > 0.0
    assert weights["HV"] > 0.0


# ══════════════════════════════════════════════════════════════════════════
#  Inverse-volatility weighting
# ══════════════════════════════════════════════════════════════════════════


def test_inverse_volatility_weight_ratio():
    """When two candidates have known volatilities, weights should be proportional
    to 1/volatility before capping."""
    start = date(2025, 1, 1)
    n = 12  # enough so tail(5) is well-defined

    # Stock A: [100 → 101 → 102 → … → 111] — returns all ~1 %, std ≈ 0
    prices_a = [100.0 + i * 1.0 for i in range(n)]

    # Stock B: [100, 102, 98, 103, 97, 104, 96, 105, 95, 106, 94, 107]
    # Large swings → much higher std
    prices_b = [100.0]
    for i in range(1, n):
        if i % 2 == 1:
            prices_b.append(prices_b[-1] + 2.0)  # up
        else:
            prices_b.append(prices_b[-1] - 4.0)  # down

    bars = _bars(start, {"A": prices_a, "B": prices_b})

    # Use generous caps so the raw inverse-vol ratio is visible
    weights = LowVolDefensiveStrategy().generate_target_weights(
        _context(top_n=2, max_position_weight=1.0, max_total_weight=1.0),
        bars,
    )

    # Stock A's volatility should be much lower → weight_A >> weight_B
    assert weights["A"] > weights["B"] * 2.0, (
        f"Low-vol A should dominate: A={weights['A']:.4f}, B={weights['B']:.4f}"
    )
    # Weights should sum to 1.0 (no cap hit)
    assert abs(weights["A"] + weights["B"] - 1.0) < 1e-9


def test_position_cap_is_respected():
    """max_position_weight must cap each individual weight."""
    start = date(2025, 1, 1)
    n = 10
    bars = _bars(start, {f"S{i}": [10.0 + j * 0.5 + i for j in range(n)] for i in range(3)})

    weights = LowVolDefensiveStrategy().generate_target_weights(
        _context(top_n=3, max_position_weight=0.08, max_total_weight=0.95),
        bars,
    )
    for sym, w in weights.items():
        assert w <= 0.08 + 1e-9, f"{sym} weight {w} exceeds cap 0.08"


def test_total_weight_cap_is_respected():
    """max_total_weight must cap the sum of all weights."""
    start = date(2025, 1, 1)
    n = 10
    bars = _bars(start, {f"S{i}": [10.0 + j * 0.5 + i for j in range(n)] for i in range(5)})

    weights = LowVolDefensiveStrategy().generate_target_weights(
        _context(top_n=5, max_position_weight=0.5, max_total_weight=0.4),
        bars,
    )
    total = sum(weights.values())
    assert total <= 0.4 + 1e-9, f"Total weight {total} exceeds cap 0.4"


# ══════════════════════════════════════════════════════════════════════════
#  Parameter validation
# ══════════════════════════════════════════════════════════════════════════


def test_window_must_be_gt_1():
    """Window parameters ≤ 1 must raise ValueError."""
    strategy = LowVolDefensiveStrategy()
    bars = _bars(date(2025, 1, 1), {"S": [10.0] * 10})
    for bad_param in ["trend_window", "volatility_window", "volume_short_window",
                       "volume_long_window", "drawdown_window"]:
        with pytest.raises(ValueError, match="must be > 1"):
            strategy.generate_target_weights(_context(**{bad_param: 1}), bars)


def test_nan_param_raises():
    """NaN float parameters must raise ValueError."""
    import math
    strategy = LowVolDefensiveStrategy()
    bars = _bars(date(2025, 1, 1), {"S": [10.0] * 10})
    with pytest.raises(ValueError, match="NaN"):
        strategy.generate_target_weights(_context(min_price=float("nan")), bars)
    with pytest.raises(ValueError, match="NaN"):
        strategy.generate_target_weights(_context(max_drawdown=float("nan")), bars)


# ══════════════════════════════════════════════════════════════════════════
#  Metadata
# ══════════════════════════════════════════════════════════════════════════


def test_metadata():
    cls = LowVolDefensiveStrategy
    meta = cls.metadata()
    assert meta["name"] == "low_vol_defensive"
    assert meta["display_name"] == "低波抗跌趋势策略"
    assert "趋势" in meta["description"]
    param_names = {p["name"] for p in meta["parameters"]}
    expected = {
        "trend_window", "volatility_window", "volume_short_window",
        "volume_long_window", "drawdown_window", "top_n",
        "max_position_weight", "max_total_weight", "min_avg_amount_20d",
        "min_price", "min_up_day_ratio", "max_drawdown",
        "relative_window", "relative_weight", "max_downside_beta",
    }
    assert param_names == expected


def test_registered_in_builtin_strategies():
    assert "low_vol_defensive" in BUILTIN_STRATEGIES
    assert BUILTIN_STRATEGIES["low_vol_defensive"] is LowVolDefensiveStrategy


# ══════════════════════════════════════════════════════════════════════════
#  Same-volatility: stronger trend / volume wins
# ══════════════════════════════════════════════════════════════════════════


def test_stronger_trend_and_volume_wins_at_same_volatility():
    """When candidates have similar return volatility, the one with
    stronger volume expansion should rank higher and be selected first."""
    start = date(2025, 1, 1)
    n = 10

    # Same prices for all three → same returns, same volatility, same trend_raw
    prices = [10.0 + i * 0.5 for i in range(n)]  # steady uptrend

    rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        c = float(prices[i])
        for sym, vol in [
            ("STRONG", 2_000_000.0 * (i + 1)),     # expanding volume
            ("FLAT", 1_000_000.0),                  # flat volume
            ("WEAK", 2_000_000.0 * (n - i)),        # contracting volume
        ]:
            rows.append({
                "symbol": sym,
                "trade_date": t,
                "open": c * 0.99,
                "high": c * 1.02,
                "low": c * 0.98,
                "close": c,
                "volume": vol,
                "amount": 100_000_000.0,
            })
    bars = pd.DataFrame(rows)

    weights = LowVolDefensiveStrategy().generate_target_weights(
        _context(top_n=1, max_position_weight=1.0, max_total_weight=1.0),
        bars,
    )

    # Same price path → same trend_raw and low_vol_raw.
    # STRONG has expanding volume (vol_short_ma / vol_long_ma > 1)
    # FLAT has flat volume (volume_raw ≈ 1)
    # WEAK has contracting volume (volume_raw < 1)
    # With top_n=1, only the highest-scoring candidate (STRONG) should be selected.
    assert weights["STRONG"] > 0.0, (
        f"STRONG (expanding volume) should be selected, got {weights['STRONG']:.4f}"
    )
    assert weights["FLAT"] == 0.0, (
        f"FLAT should not be selected when top_n=1, got {weights['FLAT']:.4f}"
    )
    assert weights["WEAK"] == 0.0, (
        f"WEAK should not be selected when top_n=1, got {weights['WEAK']:.4f}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  Missing amount column
# ══════════════════════════════════════════════════════════════════════════


def test_missing_amount_column_skips_when_min_amount_set():
    """When min_avg_amount_20d > 0 but the DataFrame has no 'amount' column,
    the candidate must be skipped — not silently passed."""
    start = date(2025, 1, 1)
    n = 10
    prices = [10.0 + i * 0.5 for i in range(n)]

    rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        rows.append({
            "symbol": "S1",
            "trade_date": t,
            "open": 9.9,
            "high": 10.2,
            "low": 9.8,
            "close": float(prices[i]),
            "volume": 1_000_000.0,
            # NOTE: no "amount" column
        })
    bars = pd.DataFrame(rows)

    weights = LowVolDefensiveStrategy().generate_target_weights(
        _context(min_avg_amount_20d=50_000_000),
        bars,
    )
    # Without amount column, cannot verify liquidity → should be filtered out
    assert weights["S1"] == 0.0, f"Expected S1 to be skipped (no amount column), got {weights['S1']}"


# ══════════════════════════════════════════════════════════════════════════
#  Illegal parameter values
# ══════════════════════════════════════════════════════════════════════════


def test_out_of_range_params_raise_value_error():
    """Parameters outside their valid ranges must raise ValueError."""
    strategy = LowVolDefensiveStrategy()
    bars = _bars(date(2025, 1, 1), {"S": [10.0] * 10})

    # [0, 1] range params
    bad_range_params = [
        ("max_position_weight", 1.5),
        ("max_position_weight", -0.1),
        ("max_total_weight", 2.0),
        ("max_total_weight", -0.5),
        ("min_up_day_ratio", 1.5),
        ("min_up_day_ratio", -0.2),
        ("max_drawdown", 1.5),
        ("max_drawdown", -0.1),
        ("relative_weight", 1.5),
        ("relative_weight", -0.1),
    ]
    for name, val in bad_range_params:
        with pytest.raises(ValueError, match="must be in \\[0, 1\\]"):
            strategy.generate_target_weights(_context(**{name: val}), bars)

    # Non-negative params
    bad_nonneg_params = [
        ("min_price", -1.0),
        ("min_avg_amount_20d", -100_000),
        ("max_downside_beta", -0.5),
    ]
    for name, val in bad_nonneg_params:
        with pytest.raises(ValueError, match="must be >= 0"):
            strategy.generate_target_weights(_context(**{name: val}), bars)


# ══════════════════════════════════════════════════════════════════════════
#  Downside beta — static helper
# ══════════════════════════════════════════════════════════════════════════


def test_compute_downside_beta_basic():
    """On down-benchmark days stock moves half as much → beta ≈ 0.5."""
    import pandas as pd

    idx = pd.date_range("2025-01-02", periods=100, freq="B")
    # Benchmark: varying up/down returns (not all down days identical)
    bm_vals = []
    for i in range(100):
        if i % 3 == 0:
            bm_vals.append(0.005 + 0.001 * (i % 10))      # up (varying)
        elif i % 3 == 1:
            bm_vals.append(-0.008 - 0.002 * (i % 10))     # down (varying)
        else:
            bm_vals.append(-0.015 - 0.003 * (i % 7))      # down (varying)
    bm_ret = pd.Series(bm_vals, index=idx, name="bm")

    # Stock: on down days moves ~half of benchmark; on up days ~0.3x
    stock_vals = [
        bm_ret.iloc[i] * 0.5 if bm_ret.iloc[i] < 0 else bm_ret.iloc[i] * 0.3
        for i in range(100)
    ]
    stock_ret = pd.Series(stock_vals, index=idx, name="stock")

    beta = LowVolDefensiveStrategy._compute_downside_beta(stock_ret, bm_ret)
    assert beta is not None
    assert 0.40 < beta < 0.60, f"Expected beta ≈ 0.5, got {beta:.4f}"


def test_compute_downside_beta_insufficient_samples():
    """Fewer than 5 down-benchmark days → returns None."""
    import pandas as pd

    idx = pd.date_range("2025-01-02", periods=10, freq="B")
    # Only 3 negative benchmark days
    bm_ret = pd.Series(
        [0.01, 0.01, -0.01, 0.02, 0.01, -0.01, 0.01, 0.02, -0.01, 0.01],
        index=idx,
    )
    stock_ret = pd.Series([0.005] * 10, index=idx)

    beta = LowVolDefensiveStrategy._compute_downside_beta(stock_ret, bm_ret)
    assert beta is None


def test_compute_downside_beta_zero_variance():
    """When benchmark returns are all equal on down days → variance=0 → None."""
    import pandas as pd

    idx = pd.date_range("2025-01-02", periods=60, freq="B")
    # Construct 60 values: 30 positive, 30 negative, all negatives equal
    bm_vals = []
    for i in range(60):
        if i % 2 == 0:
            bm_vals.append(1.0)   # positive (not used for downside beta)
        else:
            bm_vals.append(-2.0)  # all negative days have identical -2.0
    bm_ret = pd.Series(bm_vals, index=idx)
    stock_ret = pd.Series([0.5] * 60, index=idx)

    beta = LowVolDefensiveStrategy._compute_downside_beta(stock_ret, bm_ret)
    assert beta is None


def test_compute_downside_beta_misaligned_indices():
    """When indices don't overlap at all → returns None."""
    import pandas as pd

    idx_a = pd.date_range("2025-01-02", periods=30, freq="B")
    idx_b = pd.date_range("2025-03-02", periods=30, freq="B")
    bm_ret = pd.Series([-0.02] * 30, index=idx_a)
    stock_ret = pd.Series([0.01] * 30, index=idx_b)

    beta = LowVolDefensiveStrategy._compute_downside_beta(stock_ret, bm_ret)
    assert beta is None


# ══════════════════════════════════════════════════════════════════════════
#  Benchmark integration — no-benchmark fallback
# ══════════════════════════════════════════════════════════════════════════


def test_no_benchmark_falls_back_to_original_weights():
    """When benchmark_history is None, strategy uses original 0.45/0.35/0.20
    weights and produces valid output."""
    start = date(2025, 1, 1)
    n = 10
    bars = _bars(start, {
        "S1": [10.0 + i * 0.5 for i in range(n)],
        "S2": [10.0 + i * 0.3 for i in range(n)],
    })

    ctx = _context(top_n=2, max_position_weight=1.0, max_total_weight=1.0)
    ctx.benchmark_history = None  # explicitly no benchmark

    weights = LowVolDefensiveStrategy().generate_target_weights(ctx, bars)
    assert weights["S1"] > 0.0
    assert weights["S2"] > 0.0
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_empty_benchmark_falls_back_to_original_weights():
    """When benchmark_history is an empty DataFrame, strategy falls back."""
    start = date(2025, 1, 1)
    n = 10
    bars = _bars(start, {
        "S1": [10.0 + i * 0.5 for i in range(n)],
    })

    ctx = _context(top_n=1, max_position_weight=1.0, max_total_weight=1.0)
    ctx.benchmark_history = pd.DataFrame(
        columns=["symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"]
    )

    weights = LowVolDefensiveStrategy().generate_target_weights(ctx, bars)
    assert weights["S1"] > 0.0


# ══════════════════════════════════════════════════════════════════════════
#  Downside beta — ranking (anti-downside)
# ══════════════════════════════════════════════════════════════════════════


def test_lower_downside_beta_ranks_higher():
    """A stock with lower downside beta (more defensive) should rank above
    an otherwise-identical peer with higher beta when benchmark is provided."""
    start = date(2025, 1, 1)
    n = 60  # enough bars for relative_window=60

    # Same price path for both → same trend/vol/volume factors
    prices = [10.0 + i * 0.3 + (i % 7) * 0.1 for i in range(n)]

    # Build stock bars
    stock_rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        for sym in ["DEFENSIVE", "AGGRESSIVE"]:
            c = float(prices[i])
            stock_rows.append({
                "symbol": sym,
                "trade_date": t,
                "open": c * 0.99,
                "high": c * 1.02,
                "low": c * 0.98,
                "close": c,
                "volume": 1_000_000.0,
                "amount": 100_000_000.0,
            })
    bars = pd.DataFrame(stock_rows)

    # Build benchmark: alternating up/down with some variety
    bm_prices = [3000.0]
    for i in range(1, n):
        if i % 3 == 0:
            bm_prices.append(bm_prices[-1] * 0.98)  # down ~2%
        elif i % 3 == 1:
            bm_prices.append(bm_prices[-1] * 1.01)  # up ~1%
        else:
            bm_prices.append(bm_prices[-1] * 1.005)  # up ~0.5%

    # For DEFENSIVE: modify prices so it moves less on benchmark down days
    # For AGGRESSIVE: modify prices so it moves more on benchmark down days
    defensive_prices = list(prices)
    aggressive_prices = list(prices)
    for i in range(1, n):
        if bm_prices[i] < bm_prices[i - 1]:
            # Benchmark down day → DEFENSIVE drops less, AGGRESSIVE drops more
            base = prices[i]
            # Recalculate from previous close
            prev = prices[i - 1]
            defensive_prices[i] = prev * (1.0 - 0.005)  # -0.5% (defensive)
            aggressive_prices[i] = prev * (1.0 - 0.025)  # -2.5% (aggressive)

    # Rebuild bars with differentiated prices
    stock_rows2: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        price_map = {"DEFENSIVE": defensive_prices[i], "AGGRESSIVE": aggressive_prices[i]}
        for sym, c in price_map.items():
            stock_rows2.append({
                "symbol": sym,
                "trade_date": t,
                "open": c * 0.99,
                "high": c * 1.02,
                "low": c * 0.98,
                "close": c,
                "volume": 1_000_000.0,
                "amount": 100_000_000.0,
            })
    bars = pd.DataFrame(stock_rows2)

    bm_rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        c = bm_prices[i]
        bm_rows.append({
            "symbol": "000300",
            "trade_date": t,
            "open": c * 0.999,
            "high": c * 1.005,
            "low": c * 0.995,
            "close": c,
            "volume": 1e9,
            "amount": 1e11,
        })
    benchmark = pd.DataFrame(bm_rows)

    ctx = _context(
        top_n=2, max_position_weight=1.0, max_total_weight=1.0,
        relative_window=60, max_downside_beta=5.0,
    )
    ctx.benchmark_history = benchmark

    weights = LowVolDefensiveStrategy().generate_target_weights(ctx, bars)

    # DEFENSIVE should get higher weight than AGGRESSIVE
    assert weights["DEFENSIVE"] > weights["AGGRESSIVE"], (
        f"DEFENSIVE (lower downside beta) should outrank AGGRESSIVE: "
        f"DEF={weights['DEFENSIVE']:.4f}, AGG={weights['AGGRESSIVE']:.4f}"
    )
    assert weights["DEFENSIVE"] > 0.0
    assert weights["AGGRESSIVE"] > 0.0


# ══════════════════════════════════════════════════════════════════════════
#  Downside beta — gating (beta > threshold)
# ══════════════════════════════════════════════════════════════════════════


def test_high_beta_stock_eliminated_entirely():
    """When a stock's downside beta exceeds max_downside_beta, the candidate
    is eliminated entirely (weight = 0), not just excluded from the relative factor."""
    import numpy as np

    rng = np.random.RandomState(123)
    start = date(2025, 1, 1)
    n = 60

    prices = [10.0 + i * 0.3 + (i % 5) * 0.15 for i in range(n)]

    # Build benchmark with varying down-day returns (so beta has non-zero denominator)
    bm_prices = [3000.0]
    for i in range(1, n):
        if i % 3 == 0:
            # Down day: return in [-0.05, -0.02] (2%–5% drop)
            ret = -0.035 + rng.uniform(-0.015, 0.015)
            bm_prices.append(bm_prices[-1] * (1.0 + ret))
        else:
            bm_prices.append(bm_prices[-1] * (1.0 + rng.uniform(-0.005, 0.015)))

    # HIGH_BETA: stock returns ≈ 4× benchmark returns on down days
    high_beta_prices = [prices[0]]
    for i in range(1, n):
        bm_ret_i = (bm_prices[i] - bm_prices[i - 1]) / bm_prices[i - 1]
        if bm_ret_i < 0:
            # Down day: amplify the move 4×
            high_beta_prices.append(high_beta_prices[-1] * (1.0 + bm_ret_i * 4.0))
        else:
            high_beta_prices.append(high_beta_prices[-1] * (1.0 + bm_ret_i * 0.7))

    stock_rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        for sym, p in [("NORMAL", prices[i]), ("HIGH_BETA", high_beta_prices[i])]:
            stock_rows.append({
                "symbol": sym,
                "trade_date": t,
                "open": p * 0.99,
                "high": p * 1.02,
                "low": p * 0.98,
                "close": p,
                "volume": 1_000_000.0,
                "amount": 100_000_000.0,
            })
    bars = pd.DataFrame(stock_rows)

    bm_rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        c = bm_prices[i]
        bm_rows.append({
            "symbol": "000300",
            "trade_date": t,
            "open": c * 0.999,
            "high": c * 1.005,
            "low": c * 0.995,
            "close": c,
            "volume": 1e9,
            "amount": 1e11,
        })
    benchmark = pd.DataFrame(bm_rows)

    # Set max_downside_beta low so HIGH_BETA is gated out entirely
    ctx = _context(
        top_n=2, max_position_weight=1.0, max_total_weight=1.0,
        max_downside_beta=2.5,
    )
    ctx.benchmark_history = benchmark

    weights = LowVolDefensiveStrategy().generate_target_weights(ctx, bars)

    # NORMAL should get full weight (top_n=2 but only 1 candidate passes beta gate)
    assert weights["NORMAL"] > 0.0, (
        f"NORMAL should have positive weight, got {weights['NORMAL']:.4f}"
    )
    # HIGH_BETA must be eliminated entirely — weight exactly 0
    assert weights["HIGH_BETA"] == 0.0, (
        f"HIGH_BETA should be eliminated (beta > max_downside_beta), "
        f"got weight={weights['HIGH_BETA']:.4f}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  Date alignment edge cases
# ══════════════════════════════════════════════════════════════════════════


def test_date_alignment_handles_partial_overlap():
    """When stock and benchmark dates partially overlap, only common dates
    are used for downside beta computation. Strategy still produces output."""
    start = date(2025, 1, 1)
    n_stock = 60
    prices = [10.0 + i * 0.3 for i in range(n_stock)]

    stock_rows: list[dict] = []
    for i in range(n_stock):
        t = start + timedelta(days=i)
        stock_rows.append({
            "symbol": "S1",
            "trade_date": t,
            "open": prices[i] * 0.99,
            "high": prices[i] * 1.02,
            "low": prices[i] * 0.98,
            "close": prices[i],
            "volume": 1_000_000.0,
            "amount": 100_000_000.0,
        })
    bars = pd.DataFrame(stock_rows)

    # Benchmark covers a different (but overlapping) date range
    bm_start = start + timedelta(days=10)
    n_bm = 50
    bm_prices = [3000.0]
    for i in range(1, n_bm):
        if i % 3 == 0:
            bm_prices.append(bm_prices[-1] * 0.98)
        else:
            bm_prices.append(bm_prices[-1] * 1.01)

    bm_rows: list[dict] = []
    for i in range(n_bm):
        t = bm_start + timedelta(days=i)
        bm_rows.append({
            "symbol": "000300",
            "trade_date": t,
            "open": bm_prices[i] * 0.999,
            "high": bm_prices[i] * 1.005,
            "low": bm_prices[i] * 0.995,
            "close": bm_prices[i],
            "volume": 1e9,
            "amount": 1e11,
        })
    benchmark = pd.DataFrame(bm_rows)

    ctx = _context(top_n=1, max_position_weight=1.0, max_total_weight=1.0)
    ctx.benchmark_history = benchmark

    weights = LowVolDefensiveStrategy().generate_target_weights(ctx, bars)
    assert weights["S1"] > 0.0


def test_date_alignment_no_overlap_produces_no_relative_signal():
    """When stock and benchmark dates have zero overlap, relative factor
    is effectively disabled but strategy still produces output."""
    start = date(2025, 1, 1)
    n = 60
    prices = [10.0 + i * 0.3 for i in range(n)]

    stock_rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        stock_rows.append({
            "symbol": "S1",
            "trade_date": t,
            "open": prices[i] * 0.99,
            "high": prices[i] * 1.02,
            "low": prices[i] * 0.98,
            "close": prices[i],
            "volume": 1_000_000.0,
            "amount": 100_000_000.0,
        })
    bars = pd.DataFrame(stock_rows)

    # Benchmark uses completely non-overlapping dates
    bm_start = date(2026, 1, 1)
    bm_rows: list[dict] = []
    for i in range(30):
        t = bm_start + timedelta(days=i)
        bm_rows.append({
            "symbol": "000300",
            "trade_date": t,
            "open": 3000.0,
            "high": 3010.0,
            "low": 2990.0,
            "close": 3005.0,
            "volume": 1e9,
            "amount": 1e11,
        })
    benchmark = pd.DataFrame(bm_rows)

    ctx = _context(top_n=1, max_position_weight=1.0, max_total_weight=1.0)
    ctx.benchmark_history = benchmark

    weights = LowVolDefensiveStrategy().generate_target_weights(ctx, bars)
    # Should still work — falls back to original weights
    assert weights["S1"] > 0.0


# ══════════════════════════════════════════════════════════════════════════
#  Metadata update — new parameters
# ══════════════════════════════════════════════════════════════════════════


def test_metadata_includes_new_relative_params():
    cls = LowVolDefensiveStrategy
    meta = cls.metadata()
    param_names = {p["name"] for p in meta["parameters"]}
    assert "relative_window" in param_names
    assert "relative_weight" in param_names
    assert "max_downside_beta" in param_names


# ══════════════════════════════════════════════════════════════════════════
#  Relative window — observation limiting
# ══════════════════════════════════════════════════════════════════════════


def test_compute_downside_beta_respects_relative_window():
    """relative_window must limit beta estimation to only the most recent
    N aligned return observations, ignoring older data."""
    import numpy as np
    import pandas as pd

    rng = np.random.RandomState(42)
    idx = pd.date_range("2025-01-02", periods=120, freq="B")
    relative_window = 50

    # Build benchmark returns with meaningful variance on down days
    bm_vals = []
    stock_vals = []
    for i in range(120):
        if i % 3 == 0:
            bm = 0.01 + rng.uniform(-0.005, 0.005)  # up
        elif i % 3 == 1:
            bm = -0.015 + rng.uniform(-0.008, 0.008)  # down
        else:
            bm = -0.025 + rng.uniform(-0.010, 0.010)  # down
        bm_vals.append(bm)

        if i < 70:
            # Old regime: stock ≈ 4.0 × bm on down days (high beta)
            stock_vals.append(
                bm * 4.0 + rng.uniform(-0.002, 0.002) if bm < 0
                else bm * 0.5 + rng.uniform(-0.002, 0.002)
            )
        else:
            # Recent regime: stock ≈ 0.15 × bm on down days (low beta)
            stock_vals.append(
                bm * 0.15 + rng.uniform(-0.002, 0.002) if bm < 0
                else bm * 0.5 + rng.uniform(-0.002, 0.002)
            )

    bm_ret = pd.Series(bm_vals, index=idx, name="bm")
    stock_ret = pd.Series(stock_vals, index=idx, name="stock")

    # Without window: beta dominated by old high-beta regime (≈4.0)
    beta_no_window = LowVolDefensiveStrategy._compute_downside_beta(
        stock_ret, bm_ret, relative_window=120,
    )
    assert beta_no_window is not None
    assert beta_no_window > 1.5, (
        f"Without window limit, old high-beta data should dominate, "
        f"got beta={beta_no_window:.4f}"
    )

    # With relative_window=50: only recent low-beta regime (≈0.15)
    beta_windowed = LowVolDefensiveStrategy._compute_downside_beta(
        stock_ret, bm_ret, relative_window=relative_window,
    )
    assert beta_windowed is not None
    assert beta_windowed < 0.5, (
        f"With relative_window={relative_window}, only recent low-beta "
        f"data should be used, got beta={beta_windowed:.4f}"
    )


def test_old_benchmark_data_ignored_in_strategy():
    """Integration: when stock and benchmark have long history but
    relative_window is small, only recent data affects relative factor.
    Strategy still produces correct weights."""
    start = date(2025, 1, 1)
    n = 80  # more bars than default relative_window=60

    # Steady uptrend prices — passes all filters
    prices = [10.0 + i * 0.3 for i in range(n)]

    # Build benchmark: regime change halfway through
    # First 40 obs: benchmark highly volatile (affects old beta)
    # Last 40 obs: benchmark calm, stock defensive → low recent beta
    bm_prices = [3000.0]
    for i in range(1, n):
        if i < 40:
            # Old regime: wild swings
            if i % 2 == 0:
                bm_prices.append(bm_prices[-1] * 1.03)
            else:
                bm_prices.append(bm_prices[-1] * 0.95)
        else:
            # Recent regime: mild movements
            if i % 3 == 0:
                bm_prices.append(bm_prices[-1] * 0.99)
            else:
                bm_prices.append(bm_prices[-1] * 1.01)

    stock_rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        stock_rows.append({
            "symbol": "S1",
            "trade_date": t,
            "open": prices[i] * 0.99,
            "high": prices[i] * 1.02,
            "low": prices[i] * 0.98,
            "close": prices[i],
            "volume": 1_000_000.0,
            "amount": 100_000_000.0,
        })
    bars = pd.DataFrame(stock_rows)

    bm_rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        c = bm_prices[i]
        bm_rows.append({
            "symbol": "000300",
            "trade_date": t,
            "open": c * 0.999,
            "high": c * 1.005,
            "low": c * 0.995,
            "close": c,
            "volume": 1e9,
            "amount": 1e11,
        })
    benchmark = pd.DataFrame(bm_rows)

    # relative_window=30: only last 30 obs used for beta
    ctx = _context(
        top_n=1, max_position_weight=1.0, max_total_weight=1.0,
        relative_window=30, max_downside_beta=5.0,
    )
    ctx.benchmark_history = benchmark

    weights = LowVolDefensiveStrategy().generate_target_weights(ctx, bars)
    # Strategy must produce valid output regardless of old data
    assert weights["S1"] > 0.0, (
        f"Strategy should select S1 with relative_window=30, got {weights['S1']:.4f}"
    )


def test_high_beta_eliminated_weight_is_zero():
    """When downside beta exceeds max_downside_beta, the candidate is
    eliminated — its weight must be exactly 0.0, not just lower."""
    import numpy as np

    rng = np.random.RandomState(456)
    start = date(2025, 1, 1)
    n = 60

    # All candidates share the same base price path (before beta adjustments)
    base_prices = [10.0 + i * 0.3 + (i % 5) * 0.1 for i in range(n)]

    # Benchmark with varying down-day returns
    bm_prices = [3000.0]
    for i in range(1, n):
        if i % 3 == 0:
            factor = 0.96 + rng.uniform(-0.02, 0.01)  # down 2%–5%
            bm_prices.append(bm_prices[-1] * (1.0 + factor))
        else:
            bm_prices.append(bm_prices[-1] * (1.0 + rng.uniform(-0.005, 0.015)))

    # GOOD: stock ≈ 0.5 × benchmark on down days → low beta
    good_prices = [base_prices[0]]
    for i in range(1, n):
        bm_ret_i = (bm_prices[i] - bm_prices[i - 1]) / bm_prices[i - 1]
        if bm_ret_i < 0:
            good_prices.append(good_prices[-1] * (1.0 + bm_ret_i * 0.5))
        else:
            good_prices.append(good_prices[-1] * (1.0 + bm_ret_i * 0.7))

    # BAD: stock ≈ 5 × benchmark on down days → beta ≈ 5 (> max_downside_beta=2)
    bad_prices = [base_prices[0]]
    for i in range(1, n):
        bm_ret_i = (bm_prices[i] - bm_prices[i - 1]) / bm_prices[i - 1]
        if bm_ret_i < 0:
            bad_prices.append(bad_prices[-1] * (1.0 + bm_ret_i * 5.0))
        else:
            bad_prices.append(bad_prices[-1] * (1.0 + bm_ret_i * 0.7))

    stock_rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        for sym, p in [("GOOD", good_prices[i]), ("BAD", bad_prices[i])]:
            stock_rows.append({
                "symbol": sym,
                "trade_date": t,
                "open": p * 0.99,
                "high": p * 1.02,
                "low": p * 0.98,
                "close": p,
                "volume": 1_000_000.0,
                "amount": 200_000_000.0,
            })
    bars = pd.DataFrame(stock_rows)

    bm_rows: list[dict] = []
    for i in range(n):
        t = start + timedelta(days=i)
        c = bm_prices[i]
        bm_rows.append({
            "symbol": "000300",
            "trade_date": t,
            "open": c * 0.999,
            "high": c * 1.005,
            "low": c * 0.995,
            "close": c,
            "volume": 1e9,
            "amount": 1e11,
        })
    benchmark = pd.DataFrame(bm_rows)

    ctx = _context(
        top_n=2, max_position_weight=1.0, max_total_weight=1.0,
        max_downside_beta=2.0, relative_window=60,
    )
    ctx.benchmark_history = benchmark

    weights = LowVolDefensiveStrategy().generate_target_weights(ctx, bars)

    # GOOD must have positive weight
    assert weights["GOOD"] > 0.0, (
        f"GOOD should be selected, got {weights['GOOD']:.4f}"
    )
    # BAD must be eliminated entirely
    assert weights["BAD"] == 0.0, (
        f"BAD (beta > max_downside_beta) must have weight 0, "
        f"got {weights['BAD']:.4f}"
    )
    # Only GOOD selected → its weight should be the full allocation
    assert abs(weights["GOOD"] - 1.0) < 1e-9, (
        f"GOOD should get full allocation, got {weights['GOOD']:.4f}"
    )
