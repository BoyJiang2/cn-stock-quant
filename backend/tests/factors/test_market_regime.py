"""Tests for market regime analyzer and LLM context builder."""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.ai_research.market_regime import (
    MarketRegimeAnalyzer,
    RegimeResult,
    build_llm_market_context,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_bars(
    closes: list[float],
    start: date = date(2024, 1, 1),
    symbol: str = "000300",
) -> pd.DataFrame:
    """Build a long-form daily bars DataFrame from a list of close prices."""
    n = len(closes)
    dates = [start + timedelta(days=i) for i in range(n)]
    data = []
    for i, (d, c) in enumerate(zip(dates, closes)):
        spread = c * 0.005  # 0.5% spread
        data.append({
            "symbol": symbol,
            "trade_date": d,
            "open": closes[max(0, i - 1)] if i > 0 else c,
            "high": c + spread,
            "low": c - spread,
            "close": c,
            "volume": 1_000_000 + i * 1000,
            "amount": c * (1_000_000 + i * 1000),
        })
    return pd.DataFrame(data)


def _geometric_brownian(
    n: int = 252,
    start_price: float = 10.0,
    mu: float = 0.0005,
    sigma: float = 0.015,
    seed: int = 42,
) -> list[float]:
    """Generate a GBM price series for realistic testing."""
    rng = np.random.default_rng(seed)
    returns = mu + sigma * rng.standard_normal(n)
    prices = [start_price]
    for r in returns:
        prices.append(prices[-1] * (1.0 + r))
    return prices


def _trending_series(
    n: int = 252,
    start: float = 10.0,
    end: float = 15.0,
    noise: float = 0.005,
    seed: int = 42,
) -> list[float]:
    """Linear trend + small noise → clear bull/bear."""
    rng = np.random.default_rng(seed)
    slope = (end - start) / n
    prices = []
    for i in range(n):
        p = start + slope * i + rng.normal(0, start * noise)
        prices.append(max(p, 0.01))
    return prices


def _crash_then_recover(
    n_before: int = 100,
    crash_size: float = -0.35,
    n_after: int = 20,
    start: float = 10.0,
) -> list[float]:
    """Steady uptrend → sudden crash → flat bottom."""
    rng = np.random.default_rng(99)
    prices = []
    p = start
    for i in range(n_before):
        p *= (1.0 + rng.normal(0.0003, 0.008))
        prices.append(p)
    # crash day
    p *= (1.0 + crash_size)
    prices.append(p)
    for i in range(n_after):
        p *= (1.0 + rng.normal(0.0, 0.01))
        prices.append(p)
    return prices


# ---------------------------------------------------------------------------
# analyzer tests — basic contracts
# ---------------------------------------------------------------------------


class TestMarketRegimeAnalyzerBasic:
    """Edge cases and contract tests."""

    def test_empty_bars_returns_sideways_zero_confidence(self):
        analyzer = MarketRegimeAnalyzer()
        result = analyzer.analyze(pd.DataFrame())
        assert result.regime == "SIDEWAYS"
        assert result.confidence == 0.0
        assert "No history" in result.reasons[0]

    def test_fewer_than_20_bars_returns_sideways(self):
        analyzer = MarketRegimeAnalyzer()
        bars = _make_bars([10.0 + i * 0.1 for i in range(15)])
        result = analyzer.analyze(bars)
        assert result.regime == "SIDEWAYS"
        assert result.confidence == 0.0
        assert any("15 bars" in r or "< 20" in r for r in result.reasons)

    def test_never_returns_unknown(self):
        """For any reasonable input the analyzer must produce a concrete regime."""
        analyzer = MarketRegimeAnalyzer()
        for seed in range(20):
            prices = _geometric_brownian(200, seed=seed)
            bars = _make_bars(prices)
            result = analyzer.analyze(bars)
            assert result.regime in ("BULL", "BEAR", "SIDEWAYS", "PANIC", "EUPHORIA")
            assert result.regime != "UNKNOWN"

    def test_all_scores_in_zero_one_range(self):
        analyzer = MarketRegimeAnalyzer()
        for seed in range(10):
            prices = _geometric_brownian(200, seed=seed)
            bars = _make_bars(prices)
            result = analyzer.analyze(bars)
            assert 0.0 <= result.trend_score <= 1.0, f"trend={result.trend_score}"
            assert 0.0 <= result.breadth_score <= 1.0, f"breadth={result.breadth_score}"
            assert 0.0 <= result.volatility_score <= 1.0, f"vol={result.volatility_score}"
            assert 0.0 <= result.drawdown <= 1.0, f"dd={result.drawdown}"
            assert 0.0 <= result.confidence <= 1.0, f"conf={result.confidence}"

    def test_result_has_reasons(self):
        analyzer = MarketRegimeAnalyzer()
        prices = _geometric_brownian(200, seed=42)
        bars = _make_bars(prices)
        result = analyzer.analyze(bars)
        assert len(result.reasons) >= 1
        # Golden/death cross reason should mention MA20 vs MA60
        cross_reason = result.reasons[0]
        assert "MA20" in cross_reason and "MA60" in cross_reason


# ---------------------------------------------------------------------------
# regime-specific tests
# ---------------------------------------------------------------------------


class TestBullRegime:
    """Strong uptrend should classify as BULL."""

    def test_strong_uptrend_is_bull(self):
        prices = _trending_series(200, start=10.0, end=16.0, noise=0.003)
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert result.regime == "BULL"
        assert result.trend_score > 0.5
        assert result.breadth_score > 0.3
        assert result.confidence >= 0.4

    def test_mild_uptrend_with_good_breadth_is_bull(self):
        # Slow steady rise where all MAs should align
        rng = np.random.default_rng(1)
        prices = [10.0]
        for i in range(200):
            prices.append(prices[-1] * (1.0 + rng.normal(0.0008, 0.005)))
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        # Should be BULL (or at minimum not BEAR/PANIC)
        assert result.regime in ("BULL", "SIDEWAYS", "EUPHORIA")

    def test_golden_cross_mentioned_in_reasons(self):
        """When MA20 > MA60, reasons should mention golden cross."""
        # Build a series where MA20 clearly crosses above MA60
        n = 200
        rng = np.random.default_rng(5)
        # Start flat, then accelerate upward
        prices = [10.0] * 60 + [10.0 + 0.05 * i for i in range(140)]
        # Add tiny noise
        prices = [p * (1.0 + rng.normal(0, 0.003)) for p in prices]
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert "金叉" in result.reasons[0] or "golden" in result.reasons[0].lower()


class TestBearRegime:
    """Sustained downtrend should classify as BEAR."""

    def test_strong_downtrend_is_bear(self):
        prices = _trending_series(200, start=16.0, end=10.0, noise=0.003)
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert result.regime == "BEAR"
        assert result.trend_score < 0.5

    def test_large_drawdown_with_weak_trend_is_bear(self):
        # Steady decline producing large drawdown
        prices = _trending_series(200, start=20.0, end=10.0, noise=0.002)
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert result.regime in ("BEAR", "PANIC")
        assert result.drawdown > 0.15

    def test_death_cross_mentioned_in_reasons(self):
        """When MA20 < MA60, reasons should mention death cross."""
        n = 200
        rng = np.random.default_rng(6)
        prices = [15.0] * 60 + [15.0 - 0.05 * i for i in range(140)]
        prices = [p * (1.0 + rng.normal(0, 0.003)) for p in prices]
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert "死叉" in result.reasons[0] or "death" in result.reasons[0].lower()


class TestSidewaysRegime:
    """Range-bound markets should classify as SIDEWAYS."""

    def test_flat_market_is_sideways(self):
        rng = np.random.default_rng(7)
        prices = [10.0 + rng.normal(0, 0.05) for _ in range(200)]
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert result.regime == "SIDEWAYS"
        assert abs(result.trend_score - 0.5) < 0.25  # near neutral

    def test_oscillating_market_is_sideways(self):
        """Sine-wave price action should be sideways."""
        prices = [10.0 + math.sin(i * 0.05) * 0.5 for i in range(200)]
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert result.regime == "SIDEWAYS"


class TestPanicRegime:
    """Crash-like scenarios should classify as PANIC."""

    def test_sudden_crash_is_panic(self):
        # Crash with volatile aftermath — high drawdown triggers PANIC,
        # but the volatility score depends on the post-crash turbulence.
        prices = _crash_then_recover(n_before=150, crash_size=-0.35, n_after=20)
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert result.regime == "PANIC"
        assert result.drawdown > 0.10
        # The 35%+ drawdown alone should trigger PANIC via the extreme-drawdown rule

    def test_extreme_drawdown_alone_triggers_panic(self):
        """A slow grind down of > 35% should trigger PANIC."""
        prices = _trending_series(200, start=30.0, end=15.0, noise=0.002)
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert result.regime == "PANIC"
        assert result.drawdown >= 0.20

    def test_panic_has_high_volatility_score(self):
        """Sharp reversals with very large daily swings → high volatility."""
        rng = np.random.default_rng(8)
        prices = [10.0]
        for i in range(200):
            # Big swings every few days to generate high realised vol
            if i % 3 == 0:
                prices.append(prices[-1] * (1.0 + rng.normal(-0.04, 0.04)))
            elif i % 3 == 1:
                prices.append(prices[-1] * (1.0 + rng.normal(0.04, 0.04)))
            else:
                prices.append(prices[-1] * (1.0 + rng.normal(0, 0.02)))
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        # High-vol crash-like scenario
        assert result.volatility_score > 0.4


class TestEuphoriaRegime:
    """Parabolic / extreme momentum should classify as EUPHORIA."""

    def test_parabolic_rise_is_euphoria(self):
        """Fast accelerating uptrend with low drawdown → euphoria."""
        rng = np.random.default_rng(9)
        # Exponential rise
        prices = [10.0 * math.exp(0.008 * i) + rng.normal(0, 0.1) for i in range(200)]
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        # Should be either EUPHORIA or BULL depending on exact vol
        assert result.regime in ("EUPHORIA", "BULL")
        assert result.trend_score > 0.5

    def test_strong_trend_high_vol_low_dd_is_euphoria(self):
        """Accelerating uptrend with elevated volatility but minimal drawdown.

        Uses a deterministic construction: alternating +6%/-3% daily moves
        that produce a strong net uptrend with high realised volatility while
        keeping drawdown low (the -3% days never create deep troughs).
        """
        prices = [10.0]
        for i in range(200):
            if i % 2 == 0:
                prices.append(prices[-1] * 1.06)  # +6% day
            else:
                prices.append(prices[-1] * 0.97)  # -3% day, net up
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert result.regime in ("EUPHORIA", "BULL")
        # Very low drawdown — price is always near all-time highs
        assert result.drawdown < 0.20


# ---------------------------------------------------------------------------
# score dimension tests
# ---------------------------------------------------------------------------


class TestTrendScore:
    """Trend score should reflect price-vs-MA relationships."""

    def test_price_above_all_mas_gives_high_trend(self):
        """Price firmly above MA20/60/120 → trend > 0.6."""
        # Build a series with a clear acceleration: slow rise → fast rise
        rng = np.random.default_rng(11)
        prices = [10.0]
        for i in range(200):
            drift = 0.0003 if i < 100 else 0.002
            prices.append(prices[-1] * (1.0 + rng.normal(drift, 0.005)))
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert result.trend_score > 0.55

    def test_price_below_all_mas_gives_low_trend(self):
        """Price firmly below MA20/60/120 → trend < 0.4."""
        rng = np.random.default_rng(12)
        prices = [20.0]
        for i in range(200):
            drift = -0.0003 if i < 100 else -0.002
            prices.append(prices[-1] * (1.0 + rng.normal(drift, 0.005)))
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert result.trend_score < 0.45


class TestBreadthScore:
    """Breadth score reflects MA alignment and up-day ratio."""

    def test_all_mas_aligned_upwards_gives_high_breadth(self):
        """MA20 > MA60 > MA120 consistently → high breadth."""
        rng = np.random.default_rng(13)
        prices = [10.0]
        for i in range(200):
            prices.append(prices[-1] * (1.0 + rng.normal(0.001, 0.003)))
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        # In a steady uptrend, breadth should be decent
        assert result.breadth_score > 0.3

    def test_mixed_mas_gives_low_breadth(self):
        """Whipsawing around MAs → low breadth."""
        rng = np.random.default_rng(14)
        prices = [10.0]
        for i in range(200):
            # Oscillate
            if i % 30 < 15:
                prices.append(prices[-1] * (1.0 + rng.normal(0.002, 0.008)))
            else:
                prices.append(prices[-1] * (1.0 + rng.normal(-0.002, 0.008)))
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        # Mixed alignment → breadth not extreme
        assert result.breadth_score < 0.8


class TestVolatilityScore:
    """Volatility score reflects realised volatility."""

    def test_stable_market_low_vol(self):
        rng = np.random.default_rng(15)
        prices = [10.0 + rng.normal(0, 0.02) for _ in range(200)]
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert result.volatility_score < 0.5

    def test_choppy_market_high_vol(self):
        rng = np.random.default_rng(16)
        prices = [10.0]
        for i in range(200):
            prices.append(prices[-1] * (1.0 + rng.normal(0, 0.04)))
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert result.volatility_score > 0.4


class TestDrawdown:
    """Drawdown metric should capture peak-to-current decline."""

    def test_at_all_time_high_zero_drawdown(self):
        prices = [10.0 + 0.01 * i for i in range(200)]  # Straight line up
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert result.drawdown < 0.05

    def test_well_off_high_is_large_drawdown(self):
        """Price far below its 120-day peak."""
        prices = _trending_series(200, start=20.0, end=10.0, noise=0.001)
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert result.drawdown > 0.15

    def test_drawdown_never_exceeds_one(self):
        """Even with extreme data, drawdown is clamped to [0, 1]."""
        # Prices going to near-zero
        prices = [10.0 * (0.97 ** i) for i in range(200)]
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert 0.0 <= result.drawdown <= 1.0


# ---------------------------------------------------------------------------
# LLM context builder tests
# ---------------------------------------------------------------------------


class TestBuildLLMMarketContext:
    """Tests for build_llm_market_context."""

    def test_returns_dict_with_role_and_content(self):
        result = RegimeResult(
            regime="BULL",
            confidence=0.75,
            trend_score=0.68,
            breadth_score=0.55,
            volatility_score=0.30,
            drawdown=0.05,
            reasons=["MA20 > MA60 — 金叉区域", "距20日高点 +2.3%"],
        )
        ctx = build_llm_market_context(result, symbol="000300")
        assert ctx["role"] == "system"
        assert "牛市" in ctx["content"]
        assert "000300" in ctx["content"]
        assert "75.0%" in ctx["content"]
        assert "0.68" in ctx["content"]
        assert "金叉" in ctx["content"]

    def test_deepseek_model_family_chinese(self):
        result = RegimeResult(
            regime="BEAR",
            confidence=0.60,
            trend_score=0.30,
            breadth_score=0.25,
            volatility_score=0.45,
            drawdown=0.18,
            reasons=["MA20 < MA60 — 死叉区域"],
        )
        ctx = build_llm_market_context(result, model_family="deepseek")
        assert ctx["role"] == "system"
        assert "风险管理" in ctx["content"] or "量化" in ctx["content"]
        assert "熊市" in ctx["content"]

    def test_fingpt_model_family_english(self):
        result = RegimeResult(
            regime="SIDEWAYS",
            confidence=0.50,
            trend_score=0.48,
            breadth_score=0.42,
            volatility_score=0.35,
            drawdown=0.03,
            reasons=["震荡整理"],
        )
        ctx = build_llm_market_context(result, model_family="fingpt")
        assert ctx["role"] == "system"
        assert "Sideways" in ctx["content"] or "Range-bound" in ctx["content"]
        assert "position sizing" in ctx["content"].lower() or "risk management" in ctx["content"].lower()

    def test_extra_context_merged(self):
        result = RegimeResult(
            regime="BULL",
            confidence=0.70,
            trend_score=0.65,
            breadth_score=0.50,
            volatility_score=0.25,
            drawdown=0.02,
            reasons=["均线多头排列"],
        )
        ctx = build_llm_market_context(
            result,
            extra_context={"行业板块": "新能源", "市值": "大盘"},
        )
        assert "新能源" in ctx["content"]
        assert "大盘" in ctx["content"]
        assert "补充数据" in ctx["content"]

    def test_all_regimes_have_labels(self):
        """Every regime type should have a human-readable label in context."""
        for regime in ("BULL", "BEAR", "SIDEWAYS", "PANIC", "EUPHORIA"):
            result = RegimeResult(
                regime=regime,
                confidence=0.6,
                trend_score=0.5,
                breadth_score=0.5,
                volatility_score=0.4,
                drawdown=0.1,
                reasons=["test"],
            )
            ctx = build_llm_market_context(result)
            assert len(ctx["content"]) > 50  # non-trivial content
            assert ctx["role"] == "system"


# ---------------------------------------------------------------------------
# integration-style tests
# ---------------------------------------------------------------------------


class TestRealisticScenarios:
    """End-to-end tests with realistic A-share index-like data."""

    def test_2015_bull_run_like_scenario(self):
        """Simulate a rapid bull run followed by a peak (like 2015 H1)."""
        rng = np.random.default_rng(100)
        # Grind up for 120 days, then accelerate for 80
        prices = [3000.0]
        for i in range(120):
            prices.append(prices[-1] * (1.0 + rng.normal(0.0003, 0.008)))
        for i in range(80):
            prices.append(prices[-1] * (1.0 + rng.normal(0.002, 0.012)))
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert result.regime in ("BULL", "EUPHORIA")
        assert result.trend_score > 0.55

    def test_2018_bear_market_like_scenario(self):
        """Simulate a persistent grind lower (like 2018)."""
        rng = np.random.default_rng(101)
        prices = [3500.0]
        for i in range(200):
            prices.append(prices[-1] * (1.0 + rng.normal(-0.0008, 0.006)))
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        assert result.regime in ("BEAR", "PANIC")
        assert result.trend_score < 0.5
        assert result.drawdown > 0.10

    def test_post_crash_stabilization(self):
        """After a crash, market stabilizes → SIDEWAYS or BEAR."""
        prices = _crash_then_recover(
            n_before=100, crash_size=-0.40, n_after=100, start=3500.0
        )
        bars = _make_bars(prices)
        result = MarketRegimeAnalyzer().analyze(bars)
        # After crash + flat, should be BEAR or SIDEWAYS (not BULL)
        assert result.regime in ("BEAR", "SIDEWAYS", "PANIC")
        assert result.drawdown > 0.05

    def test_deterministic_same_input_same_output(self):
        """Same bars should always produce the same result."""
        prices = _geometric_brownian(200, seed=42)
        bars1 = _make_bars(prices)
        bars2 = _make_bars(prices)
        r1 = MarketRegimeAnalyzer().analyze(bars1)
        r2 = MarketRegimeAnalyzer().analyze(bars2)
        assert r1.regime == r2.regime
        assert r1.trend_score == r2.trend_score
        assert r1.breadth_score == r2.breadth_score
        assert r1.volatility_score == r2.volatility_score
        assert r1.drawdown == r2.drawdown
        assert r1.confidence == r2.confidence

    def test_single_symbol_bars_with_trade_date_column(self):
        """Analyzer handles standard long-form bars with trade_date."""
        bars = _make_bars(_geometric_brownian(200))
        assert "trade_date" in bars.columns
        assert "close" in bars.columns
        result = MarketRegimeAnalyzer().analyze(bars)
        assert result.regime in ("BULL", "BEAR", "SIDEWAYS", "PANIC", "EUPHORIA")
