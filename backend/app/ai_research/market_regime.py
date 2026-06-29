"""
Market regime analyzer for A-share stocks and indices.

Determines the current market regime (BULL / BEAR / SIDEWAYS / PANIC /
EUPHORIA) from trailing price history. Every score is bounded to [0, 1] and
the classifier is **deterministic** — no LLM call, no UNKNOWN fallback — so it
can serve as a reliable signal in backtests.

The module also provides :func:`build_llm_market_context` which turns a regime
result into a structured context block suitable for DeepSeek, GLM, FinGPT, or
any OpenAI-compatible chat endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np
import pandas as pd

__all__ = [
    "RegimeResult",
    "MarketRegimeAnalyzer",
    "build_llm_market_context",
]

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class RegimeResult:
    """Output of :meth:`MarketRegimeAnalyzer.analyze`.

    All scores are in [0, 1].  ``reasons`` is a human-readable list of the
    strongest signals that drove the classification, ordered by importance.
    """

    regime: str  # BULL | BEAR | SIDEWAYS | PANIC | EUPHORIA
    confidence: float  # 0–1
    trend_score: float  # 0–1, 0.5 = neutral, >0.5 = bullish
    breadth_score: float  # 0–1, how many MAs agree
    volatility_score: float  # 0–1, higher = more turbulent
    drawdown: float  # 0–1, fraction from 120‑day peak
    reasons: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class MarketRegimeAnalyzer:
    """Classify the market regime from a single symbol's daily bars.

    The analyzer uses three moving averages (20, 60, 120 days), a 20-day
    rolling high-water mark, and return-based realised volatility.  Every
    dimension is normalised into a [0, 1] score so the classification rules
    are transparent and easy to tune.

    Typical usage::

        analyzer = MarketRegimeAnalyzer()
        result = analyzer.analyze(bars_df)
        ctx = build_llm_market_context(result)
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        history: pd.DataFrame,
        market_history: pd.DataFrame | None = None,
    ) -> RegimeResult:
        """Compute regime from *history* (one symbol, sorted by trade_date).

        Parameters
        ----------
        history : pd.DataFrame
            Must contain at least the columns ``close`` and ``trade_date``,
            sorted ascending by date.  A minimum of 20 bars is required;
            fewer bars will produce a low-confidence SIDEWAYS result.

        Returns
        -------
        RegimeResult
        """
        if history.empty:
            return RegimeResult(
                regime="SIDEWAYS",
                confidence=0.0,
                trend_score=0.5,
                breadth_score=0.0,
                volatility_score=0.0,
                drawdown=0.0,
                reasons=["No history data available."],
            )

        closes = self._ensure_series(history)

        if len(closes) < 20:
            return RegimeResult(
                regime="SIDEWAYS",
                confidence=0.0,
                trend_score=0.5,
                breadth_score=0.0,
                volatility_score=0.0,
                drawdown=0.0,
                reasons=[f"Only {len(closes)} bars (< 20); insufficient for regime classification."],
            )

        # --- core metrics ---------------------------------------------------
        ma20 = closes.rolling(20).mean()
        ma60 = closes.rolling(60).mean()
        ma120 = closes.rolling(120).mean()

        # 20-day high for drawdown context (rolling max of close)
        high20 = closes.rolling(20).max()
        high120 = closes.rolling(120).max()

        latest = closes.iloc[-1]
        latest_ma20 = self._last_valid(ma20)
        latest_ma60 = self._last_valid(ma60)
        latest_ma120 = self._last_valid(ma120)
        latest_high120 = self._last_valid(high120)
        latest_high20 = self._last_valid(high20)

        # --- trend score (0 = strong bear, 0.5 = neutral, 1 = strong bull) --
        trend_score = self._compute_trend_score(
            closes, ma20, ma60, ma120, latest, latest_ma20, latest_ma60, latest_ma120
        )

        # --- breadth score --------------------------------------------------
        breadth_score = (
            self._compute_market_breadth_score(market_history)
            if market_history is not None and not market_history.empty
            else self._compute_breadth_score(closes, ma20, ma60, ma120)
        )

        # --- volatility score -----------------------------------------------
        volatility_score = self._compute_volatility_score(closes)

        # --- drawdown -------------------------------------------------------
        drawdown = self._compute_drawdown(latest, latest_high120)

        # --- classify -------------------------------------------------------
        regime, confidence, reasons = self._classify(
            trend_score, breadth_score, volatility_score, drawdown,
            latest, latest_ma20, latest_ma60, latest_ma120,
            latest_high20, latest_high120, closes,
        )

        history_adequacy = min(1.0, len(closes) / 120.0)
        if len(closes) < 120:
            reasons.append(
                f"Only {len(closes)} bars available; confidence reduced until 120 bars."
            )
        return RegimeResult(
            regime=regime,
            confidence=round(confidence * history_adequacy, 4),
            trend_score=round(trend_score, 4),
            breadth_score=round(breadth_score, 4),
            volatility_score=round(volatility_score, 4),
            drawdown=round(drawdown, 4),
            reasons=reasons,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_series(history: pd.DataFrame) -> pd.Series:
        """Extract a sorted close Series from a bar DataFrame."""
        df = history.copy()
        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.sort_values("trade_date")
        return df["close"].reset_index(drop=True)

    @staticmethod
    def _last_valid(series: pd.Series) -> float | None:
        """Last non-NaN value, or None."""
        valid = series.dropna()
        if valid.empty:
            return None
        return float(valid.iloc[-1])

    # ----- trend -----------------------------------------------------------

    def _compute_trend_score(
        self,
        closes: pd.Series,
        ma20: pd.Series,
        ma60: pd.Series,
        ma120: pd.Series,
        latest: float,
        latest_ma20: float | None,
        latest_ma60: float | None,
        latest_ma120: float | None,
    ) -> float:
        """Compute trend score in [0, 1].

        Components (each contributes up to 0.25):
        - Price vs MA20
        - MA20 vs MA60 (golden/death cross signal)
        - MA60 vs MA120
        - Slope of MA20 over last 20 bars
        """
        components: list[float] = []

        # Component 1: price vs MA20 — normalised gap clamped to ±10%
        if latest_ma20 is not None and latest_ma20 > 0:
            gap = (latest - latest_ma20) / latest_ma20
            components.append(self._sigmoid_clamp(gap / 0.05))  # 5% gap → 0.73
        else:
            components.append(0.5)

        # Component 2: MA20 vs MA60 cross
        if latest_ma20 is not None and latest_ma60 is not None and latest_ma60 > 0:
            gap = (latest_ma20 - latest_ma60) / latest_ma60
            components.append(self._sigmoid_clamp(gap / 0.03))
        else:
            components.append(0.5)

        # Component 3: MA60 vs MA120
        if latest_ma60 is not None and latest_ma120 is not None and latest_ma120 > 0:
            gap = (latest_ma60 - latest_ma120) / latest_ma120
            components.append(self._sigmoid_clamp(gap / 0.03))
        else:
            components.append(0.5)

        # Component 4: MA20 slope (rate of change over ~20 bars)
        ma20_valid = ma20.dropna()
        if len(ma20_valid) >= 21:
            ma20_20d_ago = float(ma20_valid.iloc[-21])
            ma20_now = float(ma20_valid.iloc[-1])
            if ma20_20d_ago > 0:
                slope = (ma20_now - ma20_20d_ago) / ma20_20d_ago
                components.append(self._sigmoid_clamp(slope / 0.03))
            else:
                components.append(0.5)
        elif len(ma20_valid) >= 2:
            # Approximate slope with available data
            first = float(ma20_valid.iloc[0])
            last = float(ma20_valid.iloc[-1])
            if first > 0:
                slope = (last - first) / first
                components.append(self._sigmoid_clamp(slope / 0.03))
            else:
                components.append(0.5)
        else:
            components.append(0.5)

        return float(np.clip(np.mean(components), 0.0, 1.0))

    # ----- breadth ----------------------------------------------------------

    def _compute_breadth_score(
        self,
        closes: pd.Series,
        ma20: pd.Series,
        ma60: pd.Series,
        ma120: pd.Series,
    ) -> float:
        """Breadth = fraction of the last 20 bars where MA20 > MA60 > MA120.

        Also incorporates up-day ratio over the last 20 bars as a
        participation signal.
        """
        n = min(20, len(closes))
        recent_closes = closes.iloc[-n:]
        recent_ma20 = ma20.iloc[-n:]
        recent_ma60 = ma60.iloc[-n:]
        recent_ma120 = ma120.iloc[-n:]

        # MA alignment: count bars where MA20 > MA60 > MA120
        aligned_mask = (recent_ma20 > recent_ma60) & (recent_ma60 > recent_ma120)
        alignment_ratio = aligned_mask.sum() / max(1, aligned_mask.count())

        # Up-day ratio: fraction of days with positive return
        rets = recent_closes.pct_change().dropna()
        if len(rets) > 0:
            up_ratio = (rets > 0).sum() / len(rets)
        else:
            up_ratio = 0.5

        # Weighted combination
        return float(np.clip(0.6 * alignment_ratio + 0.4 * up_ratio, 0.0, 1.0))

    @staticmethod
    def _compute_market_breadth_score(market_history: pd.DataFrame) -> float:
        """Cross-sectional breadth from the latest observation of each stock."""
        required = {"symbol", "trade_date", "close"}
        if not required.issubset(market_history.columns):
            return 0.0
        scores: list[float] = []
        for _, group in market_history.groupby("symbol"):
            closes = (
                group.sort_values("trade_date")["close"]
                .astype(float)
                .replace([np.inf, -np.inf], np.nan)
                .dropna()
            )
            if len(closes) < 60:
                continue
            latest = float(closes.iloc[-1])
            ma20 = float(closes.tail(20).mean())
            ma60 = float(closes.tail(60).mean())
            scores.append((float(latest > ma20) + float(latest > ma60)) / 2.0)
        if not scores:
            return 0.0
        return float(np.clip(np.mean(scores), 0.0, 1.0))

    # ----- volatility -------------------------------------------------------

    def _compute_volatility_score(self, closes: pd.Series) -> float:
        """Normalised realised volatility score.

        Uses 20-day annualised volatility, mapped through a scaling function
        so that ~15% annual vol → 0.3, ~30% → 0.5, ~60% → 0.8.  A-Share
        markets routinely see higher vol than developed markets, so the
        thresholds are calibrated accordingly.
        """
        rets = closes.pct_change().dropna()
        if len(rets) < 5:
            return 0.5  # neutral when insufficient data

        window = min(20, len(rets))
        recent_rets = rets.iloc[-window:]
        daily_vol = recent_rets.std()
        if pd.isna(daily_vol) or daily_vol <= 0:
            return 0.0

        annual_vol = daily_vol * np.sqrt(252)

        # Map annual vol to [0, 1] with a logistic-style curve
        # centred around 35% (moderate for A-shares).
        score = 1.0 / (1.0 + np.exp(-(annual_vol - 0.35) / 0.12))
        return float(np.clip(score, 0.0, 1.0))

    # ----- drawdown ---------------------------------------------------------

    def _compute_drawdown(
        self,
        latest: float,
        latest_high120: float | None,
    ) -> float:
        """Current drawdown from 120-day peak, as a fraction [0, 1]."""
        if latest_high120 is None or latest_high120 <= 0:
            return 0.0
        dd = (latest_high120 - latest) / latest_high120
        return float(np.clip(dd, 0.0, 1.0))

    # ----- classification ---------------------------------------------------

    def _classify(
        self,
        trend: float,
        breadth: float,
        volatility: float,
        drawdown: float,
        latest: float,
        ma20: float | None,
        ma60: float | None,
        ma120: float | None,
        high20: float | None,
        high120: float | None,
        closes: pd.Series,
    ) -> tuple[str, float, list[str]]:
        """Map the four scores to a regime with confidence and reasons."""

        reasons: list[str] = []

        # --- Build reason strings -------------------------------------------
        if ma20 is not None and ma60 is not None:
            if ma20 > ma60:
                reasons.append(f"MA20({ma20:.2f}) > MA60({ma60:.2f}) — golden cross zone")
            else:
                reasons.append(f"MA20({ma20:.2f}) < MA60({ma60:.2f}) — death cross zone")

        if high20 is not None:
            pct_from_high20 = (latest - high20) / high20 * 100 if high20 > 0 else 0
            reasons.append(f"距20日高点 {pct_from_high20:+.1f}%")

        if drawdown > 0.05:
            reasons.append(f"{drawdown:.1%} 从120日高点回撤")

        if volatility > 0.6:
            reasons.append(f"高波动率 ({volatility:.2f})")

        # --- Classification rules (ordered — first match wins) --------------

        # PANIC: high vol + large drawdown + bearish trend
        if volatility >= 0.70 and drawdown >= 0.15 and trend <= 0.40:
            confidence = float(np.clip(
                0.6 + 0.4 * ((volatility - 0.7) / 0.3 + (drawdown - 0.15) / 0.5) / 2,
                0.6, 0.95,
            ))
            return ("PANIC", confidence, reasons + ["恐慌信号：高波动+大幅回撤+弱势趋势"])

        # PANIC: extreme drawdown alone
        if drawdown >= 0.35:
            confidence = float(np.clip(0.6 + drawdown, 0.6, 0.95))
            return ("PANIC", confidence, reasons + ["极端回撤触发恐慌判定"])

        # EUPHORIA: strong trend + high volatility + small drawdown
        if trend >= 0.65 and volatility >= 0.55 and drawdown <= 0.10:
            confidence = float(np.clip(0.5 + 0.4 * trend + 0.3 * volatility, 0.55, 0.90))
            return ("EUPHORIA", confidence, reasons + ["亢奋信号：强趋势+高波动+低回撤"])

        # BULL: strong trend + decent breadth + moderate or low vol
        if trend >= 0.55 and breadth >= 0.40 and volatility <= 0.60 and drawdown <= 0.15:
            confidence = float(np.clip(0.4 + 0.4 * trend + 0.2 * breadth, 0.50, 0.90))
            return ("BULL", confidence, reasons + ["牛市信号：趋势向好+广度支撑"])

        # BULL: all MAs aligned upward
        if trend >= 0.50 and breadth >= 0.60:
            confidence = float(np.clip(0.4 + 0.4 * breadth + 0.2 * trend, 0.45, 0.85))
            return ("BULL", confidence, reasons + ["均线多头排列"])

        # BEAR: weak trend + poor breadth + notable drawdown
        if trend <= 0.35 and breadth <= 0.30:
            confidence = float(np.clip(
                0.5 + 0.3 * (0.5 - trend) + 0.2 * (0.5 - breadth) + 0.2 * drawdown,
                0.50, 0.90,
            ))
            return ("BEAR", confidence, reasons + ["熊市信号：弱势趋势+低广度"])

        # BEAR: significant drawdown + bearish trend
        if drawdown >= 0.20 and trend <= 0.40:
            confidence = float(np.clip(0.5 + 0.3 * drawdown + 0.2 * (0.5 - trend), 0.50, 0.85))
            return ("BEAR", confidence, reasons + ["回撤确认熊市"])

        # BEAR: extremely weak trend
        if trend <= 0.25:
            confidence = float(np.clip(0.6 + 0.4 * (0.5 - trend), 0.55, 0.90))
            return ("BEAR", confidence, reasons + ["趋势极度弱势"])

        # SIDEWAYS: default — moderate everything
        # Compute confidence as inverse of how far scores are from neutral
        dist_from_neutral = abs(trend - 0.5) * 2 + abs(breadth - 0.5) + abs(volatility - 0.4)
        confidence = float(np.clip(1.0 - dist_from_neutral / 3, 0.30, 0.75))
        return ("SIDEWAYS", confidence, reasons + ["震荡整理：各指标未形成明确方向"])

    # ----- math utility -----------------------------------------------------

    @staticmethod
    def _sigmoid_clamp(x: float) -> float:
        """Sigmoid centred at 0, scaled to [0, 1]."""
        return float(1.0 / (1.0 + np.exp(-x)))


# ---------------------------------------------------------------------------
# LLM context builder
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPTS = {
    "deepseek": "你是一个专业的A股量化分析师，擅长市场状态研判。",
    "glm": "你是一个专业的A股量化分析师，擅长市场状态研判。",
    "fingpt": "You are a professional quantitative analyst specializing in Chinese A-share market regime analysis.",
    "default": "You are a professional quantitative analyst.",
}


def build_llm_market_context(
    result: RegimeResult,
    *,
    symbol: str = "",
    model_family: str = "default",
    extra_context: dict | None = None,
) -> dict:
    """Build a structured LLM context block from a regime result.

    Returns a dict suitable for injecting into a chat-completion ``messages``
    list as a system or user message.  The caller can append the returned
    ``content`` block to their prompt.

    Parameters
    ----------
    result : RegimeResult
        The output of :meth:`MarketRegimeAnalyzer.analyze`.
    symbol : str
        Optional stock/index symbol for labelling.
    model_family : str
        One of ``"deepseek"``, ``"glm"``, ``"fingpt"``, or ``"default"``.
        Influences the system prompt tone (Chinese vs. English).
    extra_context : dict | None
        Optional extra key-value pairs merged into the context body.

    Returns
    -------
    dict
        ``{"role": "system", "content": "<structured text>"}``
    """
    system_prompt = _LLM_SYSTEM_PROMPTS.get(
        model_family.lower(), _LLM_SYSTEM_PROMPTS["default"]
    )

    # Build a rich natural-language block
    regime_labels = {
        "BULL": "牛市 (Bull Market)",
        "BEAR": "熊市 (Bear Market)",
        "SIDEWAYS": "震荡市 (Sideways / Range-bound)",
        "PANIC": "恐慌市 (Panic / Crisis)",
        "EUPHORIA": "亢奋市 (Euphoria / Overbought)",
    }
    regime_label = regime_labels.get(result.regime, result.regime)

    lines = [
        f"## 市场状态分析{' — ' + symbol if symbol else ''}",
        "",
        f"- **当前市场阶段**: {regime_label}",
        f"- **判定置信度**: {result.confidence:.1%}",
        f"- **趋势评分**: {result.trend_score:.2f} (0=极端弱势, 0.5=中性, 1=极端强势)",
        f"- **广度评分**: {result.breadth_score:.2f} (均线与涨跌比的一致性)",
        f"- **波动率评分**: {result.volatility_score:.2f} (0=极低波动, 1=极高波动)",
        f"- **回撤幅度**: {result.drawdown:.1%} (从120日高点计算)",
        "",
    ]

    if result.reasons:
        lines.append("**判定依据**:")
        for i, reason in enumerate(result.reasons, 1):
            lines.append(f"  {i}. {reason}")
        lines.append("")

    # Model-family-specific tuning hints
    tuning_hints = {
        "deepseek": (
            "请仅解释以上市场状态、关键风险和不确定性。"
            "不要生成买卖订单或绕过量化验证。"
        ),
        "glm": (
            "请仅解释以上市场状态、关键风险和不确定性。"
            "不要生成买卖订单或绕过量化验证。"
        ),
        "fingpt": (
            "Explain the market regime, risk management concerns, and uncertainty only. "
            "Do not generate orders or bypass quantitative validation."
        ),
        "default": (
            "Explain the market regime, risks, and uncertainty only. "
            "Do not generate orders or bypass quantitative validation."
        ),
    }
    lines.append(tuning_hints.get(model_family.lower(), tuning_hints["default"]))

    # Merge extra context
    if extra_context:
        lines.append("")
        lines.append("**补充数据**:")
        for key, value in extra_context.items():
            lines.append(f"  - {key}: {value}")

    return {
        "role": "system",
        "content": "\n".join(lines),
    }
