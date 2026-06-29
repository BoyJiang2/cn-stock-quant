"""Advanced OHLCV strategies — Module C.

Two strategy implementations:

1. **VolatilityContractionBreakoutStrategy** — VCP-style breakout detection with
   long-term trend filter, multi-window high breakout, ATR contraction, and volume
   expansion confirmation. Cross-sectional percentile scoring across four factors.

2. **TrendFilteredMeanReversionStrategy** — Trend-filtered mean reversion that
   only buys oversold dips in confirmed uptrends (MA120 filter), using 5-day
   drawdown, RSI oversold, and z-score deviation signals. Liquidity-gated.
"""

from typing import Any

import pandas as pd

from app.strategy.base import Strategy, StrategyContext, StrategyParameter


# ═══════════════════════════════════════════════════════════════════════════
# Strategy 1: Volatility Contraction Breakout (VCP)
# ═══════════════════════════════════════════════════════════════════════════


class VolatilityContractionBreakoutStrategy(Strategy):
    """波动收缩突破策略 (Volatility Contraction Breakout / VCP).

    Detects stocks in a long-term uptrend that are breaking above recent highs
    with **contracting volatility** and **expanding volume** — the classic
    "volatility contraction pattern" (VCP) described by Mark Minervini.

    **Gates (all must pass for scoring):**

    1. **Trend** — close > MA(trend_window).  Filters out downtrends.
    2. **Breakout proximity** — close / max_close(breakout_window) >=
       breakout_threshold.  Stock must be near its N-day high.
    3. **Volume expansion** — short-term avg volume / long-term avg volume >=
       volume_expansion_threshold.  Validates institutional participation.
    4. **ATR contraction** — current ATR must rank at or below
       vol_contraction_pct within its recent window (lower percentile =
       more contracted).  Confirms the "contraction" part of VCP.
    5. **Liquidity** — 20-day average amount >= min_avg_amount_20d.
    6. **Minimum price** — close >= min_price.

    **Scoring factors** (cross-sectional percentile, equal-weighted):

    - Trend strength: close / MA(trend_window)
    - Breakout proximity: close / max_close(breakout_window)
    - Volume expansion: vol_short_ma / vol_long_ma
    - Volatility contraction: -(ATR percentile)  (lower percentile → higher)

    **Weighting:** top_n get equal weight, individually capped by
    max_position_weight, sum capped by max_total_weight.  Every symbol
    in *history* appears in the output dict.
    """

    name = "volatility_contraction_breakout"
    display_name = "波动收缩突破策略"
    description = (
        "筛选处于长期均线上方、突破近期高点、波动收缩且量能扩张的品种，"
        "按趋势强度、突破强度、量能扩张、波动收缩四因子横截面百分位综合评分，"
        "等权持有前 top_n 名，受单票与总仓位上限约束。"
    )
    parameters: list[StrategyParameter] = [
        StrategyParameter("trend_window", "趋势均线窗口", "int", 60, min=2, step=1,
                          description="长期趋势过滤均线周期（MA60 默认）"),
        StrategyParameter("breakout_window", "突破观察窗口", "int", 20, min=2, step=1,
                          description="检测高点突破的回看天数（20 日高点）"),
        StrategyParameter("breakout_threshold", "突破接近度阈值", "float", 0.95, min=0.0, max=1.0, step=0.01,
                          description="收盘价 / 窗口内最高收盘价的下限，越接近 1 越严格"),
        StrategyParameter("atr_window", "ATR 计算窗口", "int", 14, min=2, step=1,
                          description="平均真实波幅（ATR）的计算周期"),
        StrategyParameter("vol_contraction_window", "波动收缩观察窗口", "int", 20, min=2, step=1,
                          description="判断 ATR 是否收缩的回看天数"),
        StrategyParameter("vol_contraction_pct", "波动收缩百分位阈值", "float", 0.5, min=0.0, max=1.0, step=0.05,
                          description="当前 ATR 在观察窗口内的百分位上限（越低越严格）"),
        StrategyParameter("volume_short_window", "量能短窗口", "int", 5, min=2, step=1,
                          description="短期均量计算周期"),
        StrategyParameter("volume_long_window", "量能长窗口", "int", 50, min=2, step=1,
                          description="长期均量计算周期"),
        StrategyParameter("volume_expansion_threshold", "量能扩张阈值", "float", 1.0, min=0.0, step=0.05,
                          description="短期均量 / 长期均量的下限，>=1 表示放量"),
        StrategyParameter("min_avg_amount_20d", "20日成交额下限", "float", 50_000_000, min=0.0, step=1_000_000,
                          description="20 日均成交额门槛（元），低于此值跳过"),
        StrategyParameter("min_price", "最低价格", "float", 5.0, min=0.0, step=0.1,
                          description="收盘价低于此值跳过"),
        StrategyParameter("top_n", "持仓数量", "int", 10, min=1, step=1,
                          description="最多持有的品种数量"),
        StrategyParameter("max_position_weight", "单票上限", "float", 0.15, min=0.0, max=1.0, step=0.01,
                          description="单票最大权重"),
        StrategyParameter("max_total_weight", "组合仓位", "float", 0.95, min=0.0, max=1.0, step=0.05,
                          description="总仓位上限"),
    ]

    # ------------------------------------------------------------------
    # Static helpers  (public so tests can import and exercise directly)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_atr(
        high: pd.Series, low: pd.Series, close: pd.Series, window: int,
    ) -> pd.Series:
        """Average True Range over *window* periods.

        True range = max(high − low, |high − prev_close|, |low − prev_close|).
        Returns a Series aligned to the input index; the first ``window − 1``
        entries are NaN.
        """
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return true_range.rolling(window, min_periods=window).mean()

    @staticmethod
    def _percentile(values: list[float], ascending: bool = True) -> list[float]:
        """Map a list of values to [0, 1] percentile ranks.

        When *ascending* is ``True`` (default), the smallest value maps to 0.0
        and the largest to 1.0.  Set ``ascending=False`` to invert.
        """
        m = len(values)
        if m <= 1:
            return [1.0] * m
        ranks = pd.Series(values).rank(ascending=ascending, method="average")
        return [float((r - 1) / (m - 1)) for r in ranks]

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def generate_target_weights(
        self, context: StrategyContext, history: pd.DataFrame,
    ) -> dict[str, float]:
        # ── Extract parameters ────────────────────────────────────────
        trend_window = int(context.params.get("trend_window", 60))
        breakout_window = int(context.params.get("breakout_window", 20))
        breakout_threshold = float(context.params.get("breakout_threshold", 0.95))
        atr_window = int(context.params.get("atr_window", 14))
        vol_contraction_window = int(context.params.get("vol_contraction_window", 20))
        vol_contraction_pct = float(context.params.get("vol_contraction_pct", 0.5))
        volume_short_window = int(context.params.get("volume_short_window", 5))
        volume_long_window = int(context.params.get("volume_long_window", 50))
        volume_expansion_threshold = float(
            context.params.get("volume_expansion_threshold", 1.0)
        )
        min_avg_amount_20d = float(context.params.get("min_avg_amount_20d", 50_000_000))
        min_price = float(context.params.get("min_price", 5.0))
        top_n = max(1, int(context.params.get("top_n", 10)))
        max_position_weight = float(context.params.get("max_position_weight", 0.15))
        max_total_weight = float(context.params.get("max_total_weight", 0.95))

        # ── Parameter validation ──────────────────────────────────────
        for name, val in [
            ("trend_window", trend_window),
            ("breakout_window", breakout_window),
            ("atr_window", atr_window),
            ("vol_contraction_window", vol_contraction_window),
            ("volume_short_window", volume_short_window),
            ("volume_long_window", volume_long_window),
        ]:
            if val <= 1:
                raise ValueError(f"{name} must be > 1, got {val}")

        float_params: list[float] = [
            breakout_threshold, vol_contraction_pct, volume_expansion_threshold,
            min_avg_amount_20d, min_price, max_position_weight, max_total_weight,
        ]
        if any(isinstance(p, float) and pd.isna(p) for p in float_params):
            raise ValueError("Parameters contain NaN values")

        for name, val in [
            ("breakout_threshold", breakout_threshold),
            ("vol_contraction_pct", vol_contraction_pct),
            ("max_position_weight", max_position_weight),
            ("max_total_weight", max_total_weight),
        ]:
            if val < 0.0 or val > 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {val}")

        for name, val in [
            ("volume_expansion_threshold", volume_expansion_threshold),
            ("min_price", min_price),
            ("min_avg_amount_20d", min_avg_amount_20d),
        ]:
            if val < 0.0:
                raise ValueError(f"{name} must be >= 0, got {val}")

        all_symbols = list(history["symbol"].unique())
        if history.empty:
            return {s: 0.0 for s in all_symbols}

        min_data_len = max(
            trend_window, breakout_window, atr_window,
            vol_contraction_window, volume_long_window,
        )

        # ── Candidate screening ───────────────────────────────────────
        candidates: list[dict[str, object]] = []
        for symbol, group in history.groupby("symbol"):
            ordered = group.sort_values("trade_date")
            closes = ordered["close"].astype(float)
            highs = ordered["high"].astype(float)
            lows = ordered["low"].astype(float)
            volumes = ordered["volume"].astype(float)
            n = len(closes)
            if n < min_data_len:
                continue

            # 1) Trend filter: close > MA(trend_window)
            ma_trend = float(closes.tail(trend_window).mean())
            if pd.isna(ma_trend) or closes.iloc[-1] <= ma_trend:
                continue

            # 2) Breakout proximity
            max_close_brk = float(closes.tail(breakout_window).max())
            if max_close_brk <= 0:
                continue
            breakout_ratio = closes.iloc[-1] / max_close_brk
            if pd.isna(breakout_ratio) or breakout_ratio < breakout_threshold:
                continue

            # 3) Volume expansion
            vol_short_ma = float(volumes.tail(volume_short_window).mean())
            vol_long_ma = float(volumes.tail(volume_long_window).mean())
            if vol_long_ma <= 0:
                continue
            volume_ratio = vol_short_ma / vol_long_ma
            if pd.isna(volume_ratio) or volume_ratio < volume_expansion_threshold:
                continue

            # 4) ATR contraction
            atr_series = self._compute_atr(highs, lows, closes, atr_window)
            current_atr = float(atr_series.iloc[-1])
            if pd.isna(current_atr) or current_atr <= 0:
                continue

            # ATR percentile within vol_contraction_window
            # A lower percentile means ATR is low relative to recent history
            # → volatility is contracting (desirable)
            atr_tail = atr_series.tail(vol_contraction_window).dropna()
            if len(atr_tail) < 2:
                continue
            atr_rank = float((atr_tail <= current_atr).mean())
            if pd.isna(atr_rank) or atr_rank > vol_contraction_pct:
                continue

            # 5) Liquidity
            if min_avg_amount_20d > 0:
                if "amount" not in ordered.columns:
                    continue
                avg_amount_20d = float(ordered["amount"].tail(20).mean())
                if pd.isna(avg_amount_20d) or avg_amount_20d < min_avg_amount_20d:
                    continue

            # 6) Minimum price
            if pd.isna(closes.iloc[-1]) or closes.iloc[-1] < min_price:
                continue

            # ── Raw factor values ───────────────────────────────────
            trend_raw = closes.iloc[-1] / ma_trend
            breakout_raw = breakout_ratio
            volume_raw = volume_ratio
            # Lower ATR percentile → more contraction → better in cross-section
            contraction_raw = float(atr_rank)

            candidates.append({
                "symbol": symbol,
                "trend_raw": trend_raw,
                "breakout_raw": breakout_raw,
                "volume_raw": volume_raw,
                "contraction_raw": contraction_raw,
            })

        # ── No candidates → all-zero weights ─────────────────────────
        if not candidates:
            return {s: 0.0 for s in all_symbols}

        # ── Cross-sectional percentile ranking ────────────────────────
        trend_pct = self._percentile(
            [float(c["trend_raw"]) for c in candidates], ascending=True,
        )
        breakout_pct = self._percentile(
            [float(c["breakout_raw"]) for c in candidates], ascending=True,
        )
        volume_pct = self._percentile(
            [float(c["volume_raw"]) for c in candidates], ascending=True,
        )
        # contraction_raw: lower is better → ascending=False maps low → high percentile
        contraction_pct = self._percentile(
            [float(c["contraction_raw"]) for c in candidates], ascending=False,
        )

        # ── Composite score (equal-weighted factors) ──────────────────
        for i, c in enumerate(candidates):
            c["score"] = (
                trend_pct[i] + breakout_pct[i] + volume_pct[i] + contraction_pct[i]
            ) / 4.0  # type: ignore[operator]

        # ── Select top_n ──────────────────────────────────────────────
        candidates.sort(key=lambda c: float(c["score"]), reverse=True)  # type: ignore[arg-type]
        selected = candidates[:top_n]

        # ── Equal weight with caps ────────────────────────────────────
        n_selected = len(selected)
        raw_weight = min(max_position_weight, max_total_weight / n_selected)
        selected_map: dict[str, float] = {
            str(c["symbol"]): raw_weight for c in selected  # type: ignore[arg-type]
        }
        return {s: selected_map.get(s, 0.0) for s in all_symbols}


# ═══════════════════════════════════════════════════════════════════════════
# Strategy 2: Trend-Filtered Mean Reversion
# ═══════════════════════════════════════════════════════════════════════════


class TrendFilteredMeanReversionStrategy(Strategy):
    """趋势过滤均值回归策略 (Trend-Filtered Mean Reversion).

    Only buys oversold dips in stocks that are in a **confirmed uptrend**
    (above a long-term moving average).  This avoids "catching a falling knife"
    in downtrends — the trend filter is the primary risk-control mechanism.

    **Gates (all must pass for scoring):**

    1. **Trend** — close > MA(trend_window).  Stock must be in uptrend.
    2. **5-day oversold** — oversold_lookback-day return <= oversold_threshold
       (default: -5 % over 5 days).
    3. **RSI oversold** — RSI(rsi_window) <= rsi_oversold (default 30)
       **OR** **Z-score oversold** — zscore(zscore_window) <= -entry_zscore
       (default -1.5).  At least one of the two technical signals must fire.
    4. **Liquidity** — 20-day average amount >= min_avg_amount_20d.
    5. **Minimum price** — close >= min_price.

    **Scoring factors** (cross-sectional percentile, equal-weighted):

    - Oversold depth: -(oversold_lookback-day return)
    - RSI lowness: -(RSI value)   (lower RSI → higher score)
    - Z-score deviation: -(z-score value)   (more negative → higher score)

    **Weighting:** top_n get equal weight, individually capped by
    max_position_weight, sum capped by max_total_weight.  Every symbol
    in *history* appears in the output dict.
    """

    name = "trend_filtered_mean_reversion"
    display_name = "趋势过滤均值回归策略"
    description = (
        "筛选处于长期均线上方的品种，寻找 5 日超跌且 RSI 或 Z-score 进入超卖区域的机会，"
        "按超跌深度、RSI 低位、Z-score 偏离三因子横截面百分位综合评分，"
        "等权持有前 top_n 名，受单票与总仓位上限约束。"
        "趋势过滤器（MA120 默认）确保只在上升趋势中做均值回归。"
    )
    parameters: list[StrategyParameter] = [
        StrategyParameter("trend_window", "趋势均线窗口", "int", 120, min=2, step=1,
                          description="趋势过滤均线周期（MA120 默认），仅收盘价高于此均线时考虑"),
        StrategyParameter("oversold_lookback", "超跌回看天数", "int", 5, min=1, step=1,
                          description="计算超跌幅度的回看天数（默认 5 日超跌）"),
        StrategyParameter("oversold_threshold", "超跌阈值", "float", -0.05, max=0.0, step=0.01,
                          description="回看期收益率下限，如 -0.05 表示跌幅超过 5%"),
        StrategyParameter("rsi_window", "RSI 计算窗口", "int", 14, min=2, step=1,
                          description="相对强弱指标（RSI）的计算周期"),
        StrategyParameter("rsi_oversold", "RSI 超卖阈值", "float", 30.0, min=0.0, max=100.0, step=1.0,
                          description="RSI 低于此值视为超卖"),
        StrategyParameter("zscore_window", "Z-score 计算窗口", "int", 20, min=5, step=1,
                          description="Z-score 的均值和标准差计算窗口"),
        StrategyParameter("entry_zscore", "Z-score 入场阈值", "float", 1.5, min=0.1, step=0.1,
                          description="Z-score 低于此值的负数时视为超卖，"
                                      "如 1.5 表示低于滚动均值 1.5 个标准差"),
        StrategyParameter("min_avg_amount_20d", "20日成交额下限", "float", 50_000_000, min=0.0, step=1_000_000,
                          description="20 日均成交额门槛（元），低于此值跳过"),
        StrategyParameter("min_price", "最低价格", "float", 5.0, min=0.0, step=0.1,
                          description="收盘价低于此值跳过"),
        StrategyParameter("top_n", "持仓数量", "int", 10, min=1, step=1,
                          description="最多持有的品种数量"),
        StrategyParameter("max_position_weight", "单票上限", "float", 0.1, min=0.0, max=1.0, step=0.01,
                          description="单票最大权重"),
        StrategyParameter("max_total_weight", "组合仓位", "float", 0.95, min=0.0, max=1.0, step=0.05,
                          description="总仓位上限"),
    ]

    # ------------------------------------------------------------------
    # Static helpers  (public so tests can import and exercise directly)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_rsi(closes: pd.Series, window: int) -> pd.Series:
        """Compute Wilder's RSI over *window* periods.

        Returns a Series aligned to the input index; the first *window*
        entries are NaN.

        Edge cases:
        - When avg_loss ≈ 0 → RSI = 100 (all gains, no losses)
        - When avg_gain ≈ 0 → RSI = 0   (all losses, no gains)
        - When both ≈ 0 (flat prices) → RSI = 50 (neutral)
        """
        delta = closes.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)

        avg_gain = gain.rolling(window, min_periods=window).mean()
        avg_loss = loss.rolling(window, min_periods=window).mean()

        # Prevent division by zero: treat avg_loss == 0 as inf RS
        rs = avg_gain / avg_loss.replace(0.0, float("nan"))
        rsi = 100.0 - (100.0 / (1.0 + rs))

        # When avg_loss == 0 (RSI should be 100 — all gains)
        rsi = rsi.fillna(100.0)

        # When avg_gain == 0 (RSI should be 0 — all losses)
        rsi.loc[(avg_gain == 0) & (avg_loss > 0)] = 0.0

        # When both avg_gain and avg_loss are 0 (flat prices) → neutral 50
        rsi.loc[(avg_gain == 0) & (avg_loss == 0)] = 50.0

        return rsi

    @staticmethod
    def _compute_zscore(closes: pd.Series, window: int) -> pd.Series:
        """Compute rolling z-score over *window* periods.

        ``zscore = (close - rolling_mean) / rolling_std``

        Returns a Series aligned to the input index; the first
        ``window − 1`` entries are NaN.  When rolling_std is zero the
        z-score is NaN.
        """
        rolling_mean = closes.rolling(window, min_periods=window).mean()
        rolling_std = closes.rolling(window, min_periods=window).std()
        zscore = (closes - rolling_mean) / rolling_std.replace(0.0, float("nan"))
        return zscore

    @staticmethod
    def _percentile(values: list[float], ascending: bool = True) -> list[float]:
        """Map a list of values to [0, 1] percentile ranks.

        When *ascending* is ``True`` (default), the smallest value maps to 0.0
        and the largest to 1.0.  Set ``ascending=False`` to invert.
        """
        m = len(values)
        if m <= 1:
            return [1.0] * m
        ranks = pd.Series(values).rank(ascending=ascending, method="average")
        return [float((r - 1) / (m - 1)) for r in ranks]

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def generate_target_weights(
        self, context: StrategyContext, history: pd.DataFrame,
    ) -> dict[str, float]:
        # ── Extract parameters ────────────────────────────────────────
        trend_window = int(context.params.get("trend_window", 120))
        oversold_lookback = int(context.params.get("oversold_lookback", 5))
        oversold_threshold = float(context.params.get("oversold_threshold", -0.05))
        rsi_window = int(context.params.get("rsi_window", 14))
        rsi_oversold = float(context.params.get("rsi_oversold", 30.0))
        zscore_window = int(context.params.get("zscore_window", 20))
        entry_zscore = float(context.params.get("entry_zscore", 1.5))
        min_avg_amount_20d = float(context.params.get("min_avg_amount_20d", 50_000_000))
        min_price = float(context.params.get("min_price", 5.0))
        top_n = max(1, int(context.params.get("top_n", 10)))
        max_position_weight = float(context.params.get("max_position_weight", 0.1))
        max_total_weight = float(context.params.get("max_total_weight", 0.95))

        # ── Parameter validation ──────────────────────────────────────
        for name, val in [
            ("trend_window", trend_window),
            ("oversold_lookback", oversold_lookback),
            ("rsi_window", rsi_window),
            ("zscore_window", zscore_window),
        ]:
            if val <= 1:
                raise ValueError(f"{name} must be > 1, got {val}")

        float_params: list[float] = [
            oversold_threshold, rsi_oversold, entry_zscore,
            min_avg_amount_20d, min_price, max_position_weight, max_total_weight,
        ]
        if any(isinstance(p, float) and pd.isna(p) for p in float_params):
            raise ValueError("Parameters contain NaN values")

        for name, val in [
            ("max_position_weight", max_position_weight),
            ("max_total_weight", max_total_weight),
        ]:
            if val < 0.0 or val > 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {val}")

        if rsi_oversold < 0.0 or rsi_oversold > 100.0:
            raise ValueError(f"rsi_oversold must be in [0, 100], got {rsi_oversold}")

        for name, val in [
            ("entry_zscore", entry_zscore),
            ("min_price", min_price),
            ("min_avg_amount_20d", min_avg_amount_20d),
        ]:
            if val < 0.0:
                raise ValueError(f"{name} must be >= 0, got {val}")

        all_symbols = list(history["symbol"].unique())
        if history.empty:
            return {s: 0.0 for s in all_symbols}

        min_data_len = max(
            trend_window, oversold_lookback, rsi_window, zscore_window,
        ) + 1  # +1 for the pct_change / diff

        # ── Candidate screening ───────────────────────────────────────
        candidates: list[dict[str, object]] = []
        for symbol, group in history.groupby("symbol"):
            ordered = group.sort_values("trade_date")
            closes = ordered["close"].astype(float)
            n = len(closes)
            if n < min_data_len:
                continue

            # 1) Trend filter: close > MA(trend_window)
            ma_trend = float(closes.tail(trend_window).mean())
            if pd.isna(ma_trend) or closes.iloc[-1] <= ma_trend:
                continue

            # 2) 5-day oversold
            if n <= oversold_lookback:
                continue
            base_price = closes.iloc[-oversold_lookback - 1]
            if pd.isna(base_price) or base_price <= 0:
                continue
            ret_lookback = float(closes.iloc[-1] / base_price - 1.0)
            if pd.isna(ret_lookback) or ret_lookback > oversold_threshold:
                continue  # not oversold enough (or price went up)

            # 3) RSI and Z-score signals
            rsi_series = self._compute_rsi(closes, rsi_window)
            rsi_latest = float(rsi_series.iloc[-1])
            if pd.isna(rsi_latest):
                continue

            zscore_series = self._compute_zscore(closes, zscore_window)
            zscore_latest = float(zscore_series.iloc[-1])
            if pd.isna(zscore_latest):
                continue

            # Gate: RSI oversold OR zscore oversold
            rsi_pass = rsi_latest <= rsi_oversold
            zscore_pass = zscore_latest <= -entry_zscore
            if not (rsi_pass or zscore_pass):
                continue

            # 4) Liquidity
            if min_avg_amount_20d > 0:
                if "amount" not in ordered.columns:
                    continue
                avg_amount_20d = float(ordered["amount"].tail(20).mean())
                if pd.isna(avg_amount_20d) or avg_amount_20d < min_avg_amount_20d:
                    continue

            # 5) Minimum price
            if pd.isna(closes.iloc[-1]) or closes.iloc[-1] < min_price:
                continue

            # ── Raw factor values ───────────────────────────────────
            # oversold_raw: more positive = more deeply oversold
            oversold_raw = -ret_lookback
            # rsi_raw: lower RSI → higher raw value (for ascending percentile)
            rsi_raw = -rsi_latest
            # zscore_raw: more negative zscore → higher raw value
            zscore_raw = -zscore_latest

            candidates.append({
                "symbol": symbol,
                "oversold_raw": oversold_raw,
                "rsi_raw": rsi_raw,
                "zscore_raw": zscore_raw,
            })

        # ── No candidates → all-zero weights ─────────────────────────
        if not candidates:
            return {s: 0.0 for s in all_symbols}

        # ── Cross-sectional percentile ranking ────────────────────────
        oversold_pct = self._percentile(
            [float(c["oversold_raw"]) for c in candidates], ascending=True,
        )
        rsi_pct = self._percentile(
            [float(c["rsi_raw"]) for c in candidates], ascending=True,
        )
        zscore_pct = self._percentile(
            [float(c["zscore_raw"]) for c in candidates], ascending=True,
        )

        # ── Composite score (equal-weighted, 3 factors) ───────────────
        for i, c in enumerate(candidates):
            c["score"] = (
                oversold_pct[i] + rsi_pct[i] + zscore_pct[i]
            ) / 3.0  # type: ignore[operator]

        # ── Select top_n ──────────────────────────────────────────────
        candidates.sort(key=lambda c: float(c["score"]), reverse=True)  # type: ignore[arg-type]
        selected = candidates[:top_n]

        # ── Equal weight with caps ────────────────────────────────────
        n_selected = len(selected)
        raw_weight = min(max_position_weight, max_total_weight / n_selected)
        selected_map: dict[str, float] = {
            str(c["symbol"]): raw_weight for c in selected  # type: ignore[arg-type]
        }
        return {s: selected_map.get(s, 0.0) for s in all_symbols}


# ═══════════════════════════════════════════════════════════════════════════
# Module-level registry helpers
# ═══════════════════════════════════════════════════════════════════════════

ADVANCED_STRATEGIES: dict[str, type[Strategy]] = {
    VolatilityContractionBreakoutStrategy.name: VolatilityContractionBreakoutStrategy,
    TrendFilteredMeanReversionStrategy.name: TrendFilteredMeanReversionStrategy,
}
