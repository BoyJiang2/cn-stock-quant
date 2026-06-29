import pandas as pd

from app.strategy.base import Strategy, StrategyContext, StrategyParameter


class MovingAverageStrategy(Strategy):
    name = "moving_average"
    display_name = "双均线择时策略"
    description = "当快均线高于慢均线时持有，否则空仓。适合做单票或少量股票的趋势择时基线。"
    parameters = [
        StrategyParameter("fast_window", "快线周期", "int", 20, min=2, step=1),
        StrategyParameter("slow_window", "慢线周期", "int", 60, min=5, step=1),
        StrategyParameter("max_position_weight", "最大仓位", "float", 0.95, min=0.0, max=1.0, step=0.05),
    ]

    def generate_target_weights(self, context: StrategyContext, history: pd.DataFrame) -> dict[str, float]:
        fast_window = int(context.params.get("fast_window", 20))
        slow_window = int(context.params.get("slow_window", 60))
        max_position_weight = float(context.params.get("max_position_weight", 0.95))

        weights: dict[str, float] = {}
        if history.empty:
            return weights

        for symbol, group in history.groupby("symbol"):
            closes = group.sort_values("trade_date")["close"]
            if len(closes) < slow_window:
                weights[symbol] = 0.0
                continue
            fast_ma = closes.tail(fast_window).mean()
            slow_ma = closes.tail(slow_window).mean()
            weights[symbol] = max_position_weight if fast_ma > slow_ma else 0.0
        return weights


class MomentumRankStrategy(Strategy):
    name = "momentum_rank"
    display_name = "动量排序策略"
    description = "按过去一段时间涨幅排序，跳过近期过热区间，等权持有流动性达标且动量为正的股票。"
    parameters = [
        StrategyParameter("lookback_window", "回看周期", "int", 60, min=5, step=1),
        StrategyParameter("skip_recent_days", "跳过近期", "int", 5, min=0, step=1),
        StrategyParameter("top_n", "持仓数量", "int", 10, min=1, step=1),
        StrategyParameter("max_position_weight", "单票上限", "float", 0.1, min=0.0, max=1.0, step=0.01),
        StrategyParameter("max_total_weight", "组合仓位", "float", 0.8, min=0.0, max=1.0, step=0.05),
        StrategyParameter("min_avg_amount_20d", "20日成交额下限", "float", 50_000_000, min=0.0, step=1_000_000),
    ]

    def generate_target_weights(self, context: StrategyContext, history: pd.DataFrame) -> dict[str, float]:
        lookback_window = int(context.params.get("lookback_window", 60))
        skip_recent_days = max(0, int(context.params.get("skip_recent_days", 5)))
        top_n = max(1, int(context.params.get("top_n", 10)))
        max_position_weight = float(context.params.get("max_position_weight", 0.1))
        max_total_weight = float(context.params.get("max_total_weight", 0.8))
        min_avg_amount_20d = float(context.params.get("min_avg_amount_20d", 50_000_000))

        scores: list[tuple[str, float]] = []
        for symbol, group in history.groupby("symbol"):
            ordered = group.sort_values("trade_date")
            closes = ordered["close"].astype(float)
            if len(closes) <= lookback_window + skip_recent_days:
                continue

            if min_avg_amount_20d > 0 and "amount" in ordered:
                avg_amount_20d = float(ordered["amount"].tail(20).mean())
                if avg_amount_20d < min_avg_amount_20d:
                    continue

            signal_price = closes.iloc[-skip_recent_days - 1] if skip_recent_days else closes.iloc[-1]
            base_price = closes.iloc[-lookback_window - skip_recent_days - 1]
            if base_price <= 0:
                continue
            momentum = signal_price / base_price - 1.0
            if momentum > 0:
                scores.append((symbol, float(momentum)))

        selected = sorted(scores, key=lambda item: item[1], reverse=True)[:top_n]
        if not selected:
            return {symbol: 0.0 for symbol in history["symbol"].unique()}

        weight = min(max_position_weight, max_total_weight / len(selected))
        selected_symbols = {symbol for symbol, _ in selected}
        return {
            symbol: (weight if symbol in selected_symbols else 0.0)
            for symbol in history["symbol"].unique()
        }


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"
    display_name = "均值回归策略"
    description = "寻找短期明显低于均线的股票，按等权方式持有超跌标的。"
    parameters = [
        StrategyParameter("window", "均线周期", "int", 20, min=5, step=1),
        StrategyParameter("entry_zscore", "入场偏离", "float", 1.5, min=0.1, step=0.1),
        StrategyParameter("max_positions", "持仓数量", "int", 5, min=1, step=1),
        StrategyParameter("max_total_weight", "组合仓位", "float", 0.95, min=0.0, max=1.0, step=0.05),
    ]

    def generate_target_weights(self, context: StrategyContext, history: pd.DataFrame) -> dict[str, float]:
        window = int(context.params.get("window", 20))
        entry_zscore = float(context.params.get("entry_zscore", 1.5))
        max_positions = max(1, int(context.params.get("max_positions", 5)))
        max_total_weight = float(context.params.get("max_total_weight", 0.95))

        candidates: list[tuple[str, float]] = []
        for symbol, group in history.groupby("symbol"):
            closes = group.sort_values("trade_date")["close"].astype(float)
            if len(closes) < window:
                continue
            recent = closes.tail(window)
            std = recent.std()
            if std <= 0:
                continue
            zscore = (recent.iloc[-1] - recent.mean()) / std
            if zscore <= -entry_zscore:
                candidates.append((symbol, float(zscore)))

        selected = sorted(candidates, key=lambda item: item[1])[:max_positions]
        if not selected:
            return {symbol: 0.0 for symbol in history["symbol"].unique()}

        weight = max_total_weight / len(selected)
        selected_symbols = {symbol for symbol, _ in selected}
        return {
            symbol: (weight if symbol in selected_symbols else 0.0)
            for symbol in history["symbol"].unique()
        }


class LowVolDefensiveStrategy(Strategy):
    name = "low_vol_defensive"
    display_name = "低波抗跌趋势策略"
    description = (
        "筛选处于中期均线上方、低波动且回撤可控的品种，"
        "通过趋势强度、低波动、量能扩张、相对抗跌"
        "多因子横截面百分位评分，按20日收益波动率倒数归一化加权，"
        "受单票与总仓位上限约束，适合防御型组合配置。"
        "当提供基准指数历史时启用下行贝塔因子，"
        "衡量个股在基准下跌日的跟跌程度。"
    )
    parameters = [
        StrategyParameter("trend_window", "趋势窗口", "int", 60, min=2, step=1),
        StrategyParameter("volatility_window", "波动率窗口", "int", 20, min=2, step=1),
        StrategyParameter("volume_short_window", "量能短窗口", "int", 5, min=2, step=1),
        StrategyParameter("volume_long_window", "量能长窗口", "int", 20, min=2, step=1),
        StrategyParameter("drawdown_window", "回撤窗口", "int", 60, min=2, step=1),
        StrategyParameter("top_n", "持仓数量", "int", 20, min=1, step=1),
        StrategyParameter("max_position_weight", "单票上限", "float", 0.1, min=0.0, max=1.0, step=0.01),
        StrategyParameter("max_total_weight", "组合仓位", "float", 0.95, min=0.0, max=1.0, step=0.05),
        StrategyParameter("min_avg_amount_20d", "20日成交额下限", "float", 50_000_000, min=0.0, step=1_000_000),
        StrategyParameter("min_price", "最低价格", "float", 5.0, min=0.0, step=0.1),
        StrategyParameter("min_up_day_ratio", "最低上涨天数占比", "float", 0.5, min=0.0, max=1.0, step=0.05),
        StrategyParameter("max_drawdown", "最大允许回撤", "float", 0.25, min=0.0, max=1.0, step=0.01),
        StrategyParameter("relative_window", "相对抗跌窗口", "int", 60, min=5, step=1),
        StrategyParameter("relative_weight", "相对抗跌权重", "float", 0.25, min=0.0, max=1.0, step=0.05),
        StrategyParameter("max_downside_beta", "最大下行贝塔", "float", 2.0, min=0.0, step=0.1),
    ]

    # ------------------------------------------------------------------
    # Internal helpers (static so they are easy to test in isolation)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_downside_beta(
        stock_returns: pd.Series,
        benchmark_returns: pd.Series,
        relative_window: int = 60,
    ) -> float | None:
        """Compute downside beta of *stock_returns* against *benchmark_returns*.

        Aligns the two series on their index, restricts to the most recent
        *relative_window* observations, then only keeps dates where the
        benchmark return is **negative** for the beta estimation.

        Returns ``None`` when there are fewer than 5 qualifying samples or
        the benchmark variance on those dates is zero.
        """
        # Align on index (trade_date) — drop any date missing from either side
        aligned = pd.concat(
            [stock_returns, benchmark_returns], axis=1, keys=["stock", "bm"]
        ).dropna()
        if aligned.empty:
            return None

        # Restrict to the most recent relative_window observations
        if len(aligned) > relative_window:
            aligned = aligned.tail(relative_window)

        # Only keep days where benchmark went down
        down = aligned[aligned["bm"] < 0]
        if len(down) < 5:
            return None

        bm_var = float(down["bm"].var())
        if bm_var < 1e-12:
            return None

        cov = float(down["stock"].cov(down["bm"]))
        return cov / bm_var

    @staticmethod
    def _daily_returns(closes: pd.Series) -> pd.Series:
        """Compute period-over-period returns from a sorted price series.

        Returns a Series indexed by the original index (excluding the first
        row).  Drops NaN so the caller never has to handle missing values.
        """
        return closes.pct_change().dropna()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def generate_target_weights(
        self, context: StrategyContext, history: pd.DataFrame
    ) -> dict[str, float]:
        # ── 提取参数 ──────────────────────────────────────────
        trend_window = int(context.params.get("trend_window", 60))
        volatility_window = int(context.params.get("volatility_window", 20))
        volume_short_window = int(context.params.get("volume_short_window", 5))
        volume_long_window = int(context.params.get("volume_long_window", 20))
        drawdown_window = int(context.params.get("drawdown_window", 60))
        top_n = max(1, int(context.params.get("top_n", 20)))
        max_position_weight = float(context.params.get("max_position_weight", 0.1))
        max_total_weight = float(context.params.get("max_total_weight", 0.95))
        min_avg_amount_20d = float(context.params.get("min_avg_amount_20d", 50_000_000))
        min_price = float(context.params.get("min_price", 5.0))
        min_up_day_ratio = float(context.params.get("min_up_day_ratio", 0.5))
        max_drawdown = float(context.params.get("max_drawdown", 0.25))
        relative_window = int(context.params.get("relative_window", 60))
        relative_weight = float(context.params.get("relative_weight", 0.25))
        max_downside_beta = float(context.params.get("max_downside_beta", 2.0))

        # ── 参数校验 ──────────────────────────────────────────
        for name, val in [
            ("trend_window", trend_window),
            ("volatility_window", volatility_window),
            ("volume_short_window", volume_short_window),
            ("volume_long_window", volume_long_window),
            ("drawdown_window", drawdown_window),
            ("relative_window", relative_window),
        ]:
            if val <= 1:
                raise ValueError(f"{name} must be > 1, got {val}")

        float_params = [
            max_position_weight,
            max_total_weight,
            min_avg_amount_20d,
            min_price,
            min_up_day_ratio,
            max_drawdown,
            relative_weight,
            max_downside_beta,
        ]
        if any(isinstance(p, float) and pd.isna(p) for p in float_params):
            raise ValueError("Parameters contain NaN values")

        # 校验 [0, 1] 范围参数
        for name, val in [
            ("max_position_weight", max_position_weight),
            ("max_total_weight", max_total_weight),
            ("min_up_day_ratio", min_up_day_ratio),
            ("max_drawdown", max_drawdown),
            ("relative_weight", relative_weight),
        ]:
            if val < 0.0 or val > 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {val}")

        # 校验非负参数
        for name, val in [
            ("min_price", min_price),
            ("min_avg_amount_20d", min_avg_amount_20d),
            ("max_downside_beta", max_downside_beta),
        ]:
            if val < 0.0:
                raise ValueError(f"{name} must be >= 0, got {val}")

        all_symbols = list(history["symbol"].unique())
        if history.empty:
            return {s: 0.0 for s in all_symbols}

        min_data_len = max(
            trend_window, drawdown_window, volatility_window,
            volume_long_window, volume_short_window,
        )

        # ── 预处理基准日收益（仅当提供了 benchmark_history） ──
        benchmark_has_data = (
            context.benchmark_history is not None
            and not context.benchmark_history.empty
        )
        bm_return_series: pd.Series | None = None
        if benchmark_has_data:
            bm_sorted = context.benchmark_history.sort_values("trade_date")  # type: ignore[union-attr]
            bm_return_series = self._daily_returns(
                bm_sorted["close"].astype(float)
            )
            # Re-index so alignment uses trade_date values directly
            bm_return_series.index = bm_sorted["trade_date"].iloc[1:]

        # ── 候选股筛选与原始因子计算 ──────────────────────────
        candidates: list[dict[str, object]] = []
        for symbol, group in history.groupby("symbol"):
            ordered = group.sort_values("trade_date")
            closes = ordered["close"].astype(float)
            n = len(closes)
            if n < min_data_len:
                continue

            # 1) close > MA(trend_window)
            ma_trend = float(closes.tail(trend_window).mean())
            if closes.iloc[-1] <= ma_trend:
                continue

            # 2) 20 日均成交额下限
            if min_avg_amount_20d > 0:
                if "amount" not in ordered.columns:
                    continue  # 缺少成交额列，无法验证，跳过
                avg_amount_20d = float(ordered["amount"].tail(20).mean())
                if avg_amount_20d < min_avg_amount_20d:
                    continue

            # 3) 价格阈值
            if closes.iloc[-1] < min_price:
                continue

            # 4) 20 日上涨天数占比（close > previous close）
            tail_vol = closes.tail(volatility_window)
            returns_vol: pd.Series = tail_vol.pct_change().dropna()  # type: ignore[assignment]
            if len(returns_vol) < 2:
                continue
            up_ratio = float((returns_vol > 0).mean())
            if up_ratio < min_up_day_ratio:
                continue

            # 5) drawdown_window 最大回撤 ≤ max_drawdown
            drawdown_closes = closes.tail(drawdown_window)
            peak = drawdown_closes.expanding().max()
            dd_series = (drawdown_closes - peak) / peak
            worst_dd = float(dd_series.min())
            if abs(worst_dd) > max_drawdown:
                continue

            # ── 原始因子值 ────────────────────────────────────
            # 趋势因子：最新价 / 趋势均线
            trend_raw = closes.iloc[-1] / ma_trend

            # 低波因子：取负日收益标准差（值越大表示波动越低）
            low_vol_raw = -float(returns_vol.std())

            # 量能扩张因子：短期均量 / 长期均量
            volumes = ordered["volume"].astype(float)
            vol_short_ma = float(volumes.tail(volume_short_window).mean())
            vol_long_ma = float(volumes.tail(volume_long_window).mean())
            volume_raw = vol_short_ma / vol_long_ma if vol_long_ma > 0 else 1.0

            # 20 日收益波动率（供后续加权使用）
            vol_20d = float(returns_vol.std())
            if pd.isna(vol_20d) or abs(vol_20d) == float("inf"):
                continue  # 波动率非有限，跳过

            # ── 相对抗跌因子（下行贝塔） ──────────────────────
            relative_raw: float | None = None
            if bm_return_series is not None:
                # Build stock daily return series indexed by trade_date
                stock_ret = self._daily_returns(closes)
                stock_ret_indexed = stock_ret.copy()
                stock_ret_indexed.index = ordered["trade_date"].iloc[1:]

                beta = self._compute_downside_beta(
                    stock_ret_indexed, bm_return_series, relative_window,
                )
                if beta is not None and beta > max_downside_beta:
                    # Downside beta too high → eliminate candidate entirely
                    continue
                if beta is not None:
                    # Lower beta → more defensive → higher raw score
                    relative_raw = -beta
                # beta is None: neutral fallback — cannot compute, keep
                # relative_raw = None so candidate is not eliminated but
                # gets 0 percentile on the relative factor.

            candidates.append({
                "symbol": symbol,
                "trend_raw": trend_raw,
                "low_vol_raw": low_vol_raw,
                "volume_raw": volume_raw,
                "relative_raw": relative_raw,
                "volatility": max(vol_20d, 1e-8),
            })

        # ── 无候选：全部返回 0 ─────────────────────────────────
        if not candidates:
            return {s: 0.0 for s in all_symbols}

        # ── 判断是否有有效的相对抗跌因子 ───────────────────────
        has_relative = any(c.get("relative_raw") is not None for c in candidates)

        # ── 横截面百分位排名 ───────────────────────────────────
        trend_vals = [float(c["trend_raw"]) for c in candidates]  # type: ignore[arg-type]
        low_vol_vals = [float(c["low_vol_raw"]) for c in candidates]  # type: ignore[arg-type]
        volume_vals = [float(c["volume_raw"]) for c in candidates]  # type: ignore[arg-type]

        def _percentile(values: list[float]) -> list[float]:
            """将一组值映射到 [0, 1] 百分位。原始值越大，百分位越高。"""
            m = len(values)
            if m <= 1:
                return [1.0] * m
            ranks = pd.Series(values).rank(ascending=True, method="average")
            return [float((r - 1) / (m - 1)) for r in ranks]

        trend_pct = _percentile(trend_vals)     # 趋势越强 → 值越大 → 百分位越高
        low_vol_pct = _percentile(low_vol_vals)  # -vol 越大（波动越低） → 百分位越高
        volume_pct = _percentile(volume_vals)    # 量能扩张越强 → 百分位越高

        # 相对抗跌百分位（仅对有效值排名，缺失值取 0 百分位）
        if has_relative:
            rel_vals = [
                float(c["relative_raw"]) if c.get("relative_raw") is not None else float("-inf")
                for c in candidates
            ]
            # Map -inf to the bottom so they don't affect valid-item ranking
            finite_vals = [v for v in rel_vals if v != float("-inf")]
            if finite_vals:
                # Compute percentile only over finite (valid) values
                rel_pct_full: list[float] = []
                if len(finite_vals) >= 2:
                    finite_ranks = pd.Series(finite_vals).rank(ascending=True, method="average")
                    finite_pct = {
                        v: float((r - 1) / (len(finite_vals) - 1))
                        for v, r in zip(finite_vals, finite_ranks)
                    }
                else:
                    finite_pct = {finite_vals[0]: 1.0}
                for v in rel_vals:
                    if v == float("-inf"):
                        rel_pct_full.append(0.0)
                    else:
                        rel_pct_full.append(finite_pct[v])
            else:
                rel_pct_full = [0.0] * len(candidates)
        else:
            rel_pct_full = [0.0] * len(candidates)

        # ── 动态权重分配 ──────────────────────────────────────
        if has_relative and relative_weight > 0:
            w_trend = max(0.0, 1.0 - 0.25 - 0.15 - relative_weight)
            w_low_vol = 0.25
            w_volume = 0.15
            w_relative = relative_weight
            # Normalise so they sum to exactly 1.0
            total_w = w_trend + w_low_vol + w_volume + w_relative
        else:
            w_trend = 0.45
            w_low_vol = 0.35
            w_volume = 0.20
            w_relative = 0.0
            total_w = 1.0

        for i, c in enumerate(candidates):
            c["score"] = (
                w_trend * trend_pct[i]
                + w_low_vol * low_vol_pct[i]
                + w_volume * volume_pct[i]
                + w_relative * rel_pct_full[i]
            ) / total_w  # type: ignore[operator]

        # ── 按综合得分选 top_n ────────────────────────────────
        candidates.sort(key=lambda c: float(c["score"]), reverse=True)  # type: ignore[arg-type]
        selected = candidates[:top_n]

        # ── 逆波动率倒数加权 ───────────────────────────────────
        eps = 1e-8
        inv_vols = [1.0 / (float(c["volatility"]) + eps) for c in selected]  # type: ignore[arg-type]
        total_inv = sum(inv_vols)
        raw_weights = [iv / total_inv for iv in inv_vols]

        # 单票上限裁剪
        capped = [min(w, max_position_weight) for w in raw_weights]
        total_capped = sum(capped)
        if total_capped > max_total_weight:
            capped = [w * max_total_weight / total_capped for w in capped]

        # ── 构建输出（所有 symbol 显式出现） ──────────────────
        selected_map = {c["symbol"]: capped[i] for i, c in enumerate(selected)}  # type: ignore[arg-type]
        return {s: selected_map.get(s, 0.0) for s in all_symbols}


class StableReversalStrategy(Strategy):
    name = "stable_reversal"
    display_name = "Stable Reversal"
    description = (
        "Ranks liquid stocks by stable traded amount and short-term reversal, "
        "then equal-weights the best candidates under position and total caps."
    )
    parameters = [
        StrategyParameter("reversal_window", "Reversal window", "int", 5, min=1, step=1),
        StrategyParameter("stability_window", "Amount stability window", "int", 20, min=2, step=1),
        StrategyParameter("volatility_window", "Volatility window", "int", 20, min=2, step=1),
        StrategyParameter("amount_ratio_short_window", "Short amount window", "int", 5, min=1, step=1),
        StrategyParameter("amount_ratio_long_window", "Long amount window", "int", 20, min=2, step=1),
        StrategyParameter("top_n", "Position count", "int", 20, min=1, step=1),
        StrategyParameter("max_position_weight", "Single-name cap", "float", 0.05, min=0.0, max=1.0, step=0.01),
        StrategyParameter("max_total_weight", "Total exposure", "float", 0.8, min=0.0, max=1.0, step=0.05),
        StrategyParameter("min_avg_amount_20d", "20d amount floor", "float", 50_000_000, min=0.0, step=1_000_000),
        StrategyParameter("min_price", "Minimum price", "float", 5.0, min=0.0, step=0.1),
        StrategyParameter("min_reversal", "Minimum reversal", "float", 0.0, step=0.01),
        StrategyParameter("max_amount_ratio", "Crowding ratio cap", "float", 2.5, min=0.0, step=0.1),
        StrategyParameter("low_vol_weight", "Low-vol score weight", "float", 0.2, min=0.0, max=1.0, step=0.05),
        StrategyParameter("hold_rank_multiplier", "Hold rank buffer", "float", 1.0, min=1.0, step=0.1),
        StrategyParameter("entry_rank_multiplier", "Entry rank buffer", "float", 1.0, min=1.0, step=0.1),
    ]

    @staticmethod
    def _percentiles(values: list[float]) -> list[float]:
        if len(values) <= 1:
            return [1.0] * len(values)
        ranks = pd.Series(values).rank(ascending=True, method="average")
        return [float((rank - 1.0) / (len(values) - 1.0)) for rank in ranks]

    def generate_target_weights(
        self, context: StrategyContext, history: pd.DataFrame
    ) -> dict[str, float]:
        if history.empty:
            return {}

        reversal_window = int(context.params.get("reversal_window", 5))
        stability_window = int(context.params.get("stability_window", 20))
        volatility_window = int(context.params.get("volatility_window", 20))
        amount_short_window = int(context.params.get("amount_ratio_short_window", 5))
        amount_long_window = int(context.params.get("amount_ratio_long_window", 20))
        top_n = max(1, int(context.params.get("top_n", 20)))
        max_position_weight = float(context.params.get("max_position_weight", 0.05))
        max_total_weight = float(context.params.get("max_total_weight", 0.8))
        min_avg_amount_20d = float(context.params.get("min_avg_amount_20d", 50_000_000))
        min_price = float(context.params.get("min_price", 5.0))
        min_reversal = float(context.params.get("min_reversal", 0.0))
        max_amount_ratio = float(context.params.get("max_amount_ratio", 2.5))
        low_vol_weight = float(context.params.get("low_vol_weight", 0.2))
        hold_rank_multiplier = float(context.params.get("hold_rank_multiplier", 1.0))
        entry_rank_multiplier = float(context.params.get("entry_rank_multiplier", 1.0))

        for name, value in [
            ("reversal_window", reversal_window),
            ("stability_window", stability_window),
            ("volatility_window", volatility_window),
            ("amount_ratio_short_window", amount_short_window),
            ("amount_ratio_long_window", amount_long_window),
        ]:
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value}")

        for name, value in [
            ("max_position_weight", max_position_weight),
            ("max_total_weight", max_total_weight),
            ("low_vol_weight", low_vol_weight),
        ]:
            if value < 0.0 or value > 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {value}")

        for name, value in [
            ("min_avg_amount_20d", min_avg_amount_20d),
            ("min_price", min_price),
            ("max_amount_ratio", max_amount_ratio),
            ("hold_rank_multiplier", hold_rank_multiplier),
            ("entry_rank_multiplier", entry_rank_multiplier),
        ]:
            if value < 0.0:
                raise ValueError(f"{name} must be >= 0, got {value}")
        for name, value in [
            ("hold_rank_multiplier", hold_rank_multiplier),
            ("entry_rank_multiplier", entry_rank_multiplier),
        ]:
            if value < 1.0:
                raise ValueError(f"{name} must be >= 1, got {value}")

        all_symbols = list(history["symbol"].unique())
        min_len = max(
            reversal_window + 1,
            stability_window,
            volatility_window + 1,
            amount_short_window,
            amount_long_window,
            20,
        )
        candidates: list[dict[str, float | str]] = []
        for symbol, group in history.groupby("symbol"):
            ordered = group.sort_values("trade_date")
            if len(ordered) < min_len:
                continue
            if "amount" not in ordered.columns:
                continue

            closes = ordered["close"].astype(float)
            amounts = ordered["amount"].astype(float)
            latest_close = float(closes.iloc[-1])
            if latest_close < min_price:
                continue

            avg_amount_20d = float(amounts.tail(20).mean())
            if min_avg_amount_20d > 0.0 and avg_amount_20d < min_avg_amount_20d:
                continue

            amount_ratio = (
                float(amounts.tail(amount_short_window).mean())
                / float(amounts.tail(amount_long_window).mean())
            )
            if max_amount_ratio > 0.0 and amount_ratio > max_amount_ratio:
                continue

            base_price = float(closes.iloc[-reversal_window - 1])
            if base_price <= 0.0 or latest_close <= 0.0:
                continue
            reversal = base_price / latest_close - 1.0
            if reversal < min_reversal:
                continue

            amount_tail = amounts.tail(stability_window)
            amount_std = float(amount_tail.std())
            if amount_std <= 0.0 or pd.isna(amount_std):
                continue
            amount_stability = float(amount_tail.mean()) / amount_std

            returns = closes.tail(volatility_window + 1).pct_change().dropna()
            volatility = float(returns.std())
            if volatility <= 0.0 or pd.isna(volatility):
                continue

            candidates.append(
                {
                    "symbol": str(symbol),
                    "amount_stability": amount_stability,
                    "reversal": float(reversal),
                    "low_vol": -volatility,
                }
            )

        if not candidates:
            return {symbol: 0.0 for symbol in all_symbols}

        stability_pct = self._percentiles(
            [float(item["amount_stability"]) for item in candidates]
        )
        reversal_pct = self._percentiles([float(item["reversal"]) for item in candidates])
        low_vol_pct = self._percentiles([float(item["low_vol"]) for item in candidates])

        factor_weight = max(0.0, 1.0 - low_vol_weight)
        for index, item in enumerate(candidates):
            item["score"] = (
                factor_weight * (0.55 * stability_pct[index] + 0.45 * reversal_pct[index])
                + low_vol_weight * low_vol_pct[index]
            )

        selected = sorted(
            candidates,
            key=lambda item: (float(item["score"]), float(item["reversal"])),
            reverse=True,
        )
        rank_by_symbol = {
            str(item["symbol"]): rank
            for rank, item in enumerate(selected, start=1)
        }
        hold_cutoff = max(top_n, int(top_n * hold_rank_multiplier))
        entry_cutoff = max(top_n, int(top_n * entry_rank_multiplier))
        retained_symbols = {
            symbol
            for symbol, quantity in context.positions.items()
            if quantity > 0 and rank_by_symbol.get(symbol, hold_cutoff + 1) <= hold_cutoff
        }
        selected_symbols: list[str] = [
            str(item["symbol"])
            for item in selected
            if str(item["symbol"]) in retained_symbols
        ][:top_n]
        for item in selected:
            symbol = str(item["symbol"])
            if len(selected_symbols) >= top_n:
                break
            if symbol in retained_symbols or symbol in selected_symbols:
                continue
            if rank_by_symbol[symbol] > entry_cutoff:
                continue
            selected_symbols.append(symbol)

        if not selected_symbols:
            return {symbol: 0.0 for symbol in all_symbols}
        weight = min(max_position_weight, max_total_weight / len(selected_symbols))
        selected_symbol_set = set(selected_symbols)
        return {
            symbol: (weight if symbol in selected_symbol_set else 0.0)
            for symbol in all_symbols
        }


BUILTIN_STRATEGIES: dict[str, type[Strategy]] = {
    MovingAverageStrategy.name: MovingAverageStrategy,
    MomentumRankStrategy.name: MomentumRankStrategy,
    MeanReversionStrategy.name: MeanReversionStrategy,
    LowVolDefensiveStrategy.name: LowVolDefensiveStrategy,
    StableReversalStrategy.name: StableReversalStrategy,
}
