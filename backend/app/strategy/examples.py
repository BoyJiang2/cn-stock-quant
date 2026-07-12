from datetime import timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from app.strategy.base import Strategy, StrategyContext, StrategyParameter


def _default_ml_scores_path() -> str:
    artifact_dir = Path(__file__).resolve().parents[2] / "artifacts" / "ml"
    preferred = [
        artifact_dir / "lgbm-fwd5-static-2026-predictions.csv",
        artifact_dir / "wf-lgbm-fwd5-2026-v45-embargo15-predictions.csv",
        artifact_dir / "wf-lgbm-fwd5-2026-embargo15-predictions.csv",
    ]
    for path in preferred:
        if path.exists():
            return str(path)
    candidates = sorted(
        (
            path
            for path in artifact_dir.glob("*-predictions.csv")
            if not path.name.startswith("quick-")
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return str(candidates[0]) if candidates else ""


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


class InverseMomentumStrategy(Strategy):
    name = "inverse_momentum"
    display_name = "Inverse Momentum"
    description = (
        "Ranks liquid stocks by weak trailing momentum and holds the most "
        "oversold names with position and total exposure caps."
    )
    parameters = [
        StrategyParameter("lookback_window", "Lookback window", "int", 60, min=5, step=1),
        StrategyParameter("skip_recent_days", "Skip recent days", "int", 5, min=0, step=1),
        StrategyParameter("top_n", "Position count", "int", 30, min=1, step=1),
        StrategyParameter("max_position_weight", "Single-name cap", "float", 0.05, min=0.0, max=1.0, step=0.01),
        StrategyParameter("max_total_weight", "Total exposure", "float", 0.8, min=0.0, max=1.0, step=0.05),
        StrategyParameter("min_avg_amount_20d", "20d amount floor", "float", 50_000_000, min=0.0, step=1_000_000),
        StrategyParameter("min_price", "Minimum price", "float", 5.0, min=0.0, step=0.1),
        StrategyParameter("max_momentum", "Maximum momentum", "float", 0.0, step=0.01),
        StrategyParameter("max_drawdown", "Maximum drawdown", "float", 0.5, min=0.0, max=1.0, step=0.05),
        StrategyParameter("amount_ratio_short_window", "Short amount window", "int", 5, min=1, step=1),
        StrategyParameter("amount_ratio_long_window", "Long amount window", "int", 20, min=2, step=1),
        StrategyParameter("max_amount_ratio", "Crowding ratio cap", "float", 2.5, min=0.0, step=0.1),
        StrategyParameter("hold_rank_multiplier", "Hold rank buffer", "float", 1.0, min=1.0, step=0.1),
        StrategyParameter("benchmark_window", "Benchmark momentum window", "int", 20, min=2, step=1),
        StrategyParameter("max_benchmark_momentum", "Benchmark momentum cap", "float", 1.0, step=0.01),
    ]

    def generate_target_weights(self, context: StrategyContext, history: pd.DataFrame) -> dict[str, float]:
        if history.empty:
            return {}

        lookback_window = int(context.params.get("lookback_window", 60))
        skip_recent_days = max(0, int(context.params.get("skip_recent_days", 5)))
        top_n = max(1, int(context.params.get("top_n", 30)))
        max_position_weight = float(context.params.get("max_position_weight", 0.05))
        max_total_weight = float(context.params.get("max_total_weight", 0.8))
        min_avg_amount_20d = float(context.params.get("min_avg_amount_20d", 50_000_000))
        min_price = float(context.params.get("min_price", 5.0))
        max_momentum = float(context.params.get("max_momentum", 0.0))
        max_drawdown = float(context.params.get("max_drawdown", 0.5))
        amount_short_window = int(context.params.get("amount_ratio_short_window", 5))
        amount_long_window = int(context.params.get("amount_ratio_long_window", 20))
        max_amount_ratio = float(context.params.get("max_amount_ratio", 2.5))
        hold_rank_multiplier = float(context.params.get("hold_rank_multiplier", 1.0))
        benchmark_window = int(context.params.get("benchmark_window", 20))
        max_benchmark_momentum = float(context.params.get("max_benchmark_momentum", 1.0))

        for name, value in [
            ("lookback_window", lookback_window),
            ("amount_ratio_short_window", amount_short_window),
            ("amount_ratio_long_window", amount_long_window),
            ("benchmark_window", benchmark_window),
        ]:
            minimum = 0 if name == "amount_ratio_short_window" else 1
            if value <= minimum:
                raise ValueError(f"{name} must be > {minimum}, got {value}")
        for name, value in [
            ("max_position_weight", max_position_weight),
            ("max_total_weight", max_total_weight),
            ("max_drawdown", max_drawdown),
        ]:
            if value < 0.0 or value > 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {value}")
        for name, value in [
            ("min_avg_amount_20d", min_avg_amount_20d),
            ("min_price", min_price),
            ("max_amount_ratio", max_amount_ratio),
            ("hold_rank_multiplier", hold_rank_multiplier),
        ]:
            if value < 0.0:
                raise ValueError(f"{name} must be >= 0, got {value}")
        if hold_rank_multiplier < 1.0:
            raise ValueError(f"hold_rank_multiplier must be >= 1, got {hold_rank_multiplier}")

        all_symbols = list(history["symbol"].unique())
        if self._benchmark_momentum(context, benchmark_window) > max_benchmark_momentum:
            return {symbol: 0.0 for symbol in all_symbols}

        min_len = max(
            lookback_window + skip_recent_days + 1,
            amount_short_window,
            amount_long_window,
            20 if min_avg_amount_20d > 0.0 else 0,
        )
        candidates: list[tuple[str, float]] = []
        for symbol, group in history.groupby("symbol"):
            ordered = group.sort_values("trade_date")
            if len(ordered) < min_len:
                continue

            closes = ordered["close"].astype(float)
            latest_close = float(closes.iloc[-1])
            if latest_close < min_price:
                continue

            if min_avg_amount_20d > 0.0:
                if "amount" not in ordered.columns:
                    continue
                amounts = ordered["amount"].astype(float)
                avg_amount_20d = float(amounts.tail(20).mean())
                if avg_amount_20d < min_avg_amount_20d:
                    continue
            elif "amount" in ordered.columns:
                amounts = ordered["amount"].astype(float)
            else:
                amounts = None

            if amounts is not None:
                long_amount = float(amounts.tail(amount_long_window).mean())
                if long_amount <= 0.0:
                    continue
                amount_ratio = float(amounts.tail(amount_short_window).mean()) / long_amount
                if max_amount_ratio > 0.0 and amount_ratio > max_amount_ratio:
                    continue

            signal_price = closes.iloc[-skip_recent_days - 1] if skip_recent_days else closes.iloc[-1]
            base_price = closes.iloc[-lookback_window - skip_recent_days - 1]
            if base_price <= 0.0 or signal_price <= 0.0:
                continue
            momentum = float(signal_price / base_price - 1.0)
            if momentum > max_momentum:
                continue

            lookback_closes = closes.tail(lookback_window)
            peak = lookback_closes.expanding().max()
            worst_drawdown = abs(float(((lookback_closes - peak) / peak).min()))
            if worst_drawdown > max_drawdown:
                continue

            candidates.append((str(symbol), momentum))

        ranked = sorted(candidates, key=lambda item: item[1])
        if not ranked:
            return {symbol: 0.0 for symbol in all_symbols}

        rank_by_symbol = {symbol: rank for rank, (symbol, _) in enumerate(ranked, start=1)}
        hold_cutoff = max(top_n, int(top_n * hold_rank_multiplier))
        retained_symbols = {
            symbol
            for symbol, quantity in context.positions.items()
            if quantity > 0 and rank_by_symbol.get(symbol, hold_cutoff + 1) <= hold_cutoff
        }
        selected_symbols = [
            symbol
            for symbol, _ in ranked
            if symbol in retained_symbols
        ][:top_n]
        for symbol, _ in ranked:
            if len(selected_symbols) >= top_n:
                break
            if symbol in selected_symbols:
                continue
            selected_symbols.append(symbol)

        weight = min(max_position_weight, max_total_weight / len(selected_symbols))
        selected_symbol_set = set(selected_symbols)
        return {
            symbol: (weight if symbol in selected_symbol_set else 0.0)
            for symbol in all_symbols
        }

    @staticmethod
    def _benchmark_momentum(context: StrategyContext, window: int) -> float:
        benchmark = context.benchmark_history
        if benchmark is None or benchmark.empty:
            return 0.0
        closes = benchmark.sort_values("trade_date")["close"].astype(float)
        if len(closes) <= window:
            return 0.0
        base = float(closes.iloc[-window - 1])
        latest = float(closes.iloc[-1])
        if base <= 0.0:
            return 0.0
        return latest / base - 1.0


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


class MultiFactorRankStrategy(Strategy):
    name = "multi_factor_rank"
    display_name = "Multi-Factor Rank"
    description = (
        "Combines the strongest current factor evidence: low amount volatility, "
        "low-vol reversal, stable amount, inverse momentum, and left-tail risk. "
        "It is designed as the first bridge from single-factor research toward "
        "LightGBM-style ranked stock selection."
    )
    parameters = [
        StrategyParameter("top_n", "Position count", "int", 30, min=1, step=1),
        StrategyParameter("max_position_weight", "Single-name cap", "float", 0.05, min=0.0, max=1.0, step=0.01),
        StrategyParameter("max_total_weight", "Total exposure", "float", 0.8, min=0.0, max=1.0, step=0.05),
        StrategyParameter("min_avg_amount_20d", "20d amount floor", "float", 50_000_000, min=0.0, step=1_000_000),
        StrategyParameter("min_price", "Minimum price", "float", 5.0, min=0.0, step=0.1),
        StrategyParameter("reversal_window", "Reversal window", "int", 20, min=2, step=1),
        StrategyParameter("momentum_window", "Inverse momentum window", "int", 60, min=5, step=1),
        StrategyParameter("amount_window", "Amount window", "int", 20, min=2, step=1),
        StrategyParameter("tail_window", "Tail-risk window", "int", 20, min=5, step=1),
        StrategyParameter("max_amount_ratio", "Crowding ratio cap", "float", 2.5, min=0.0, step=0.1),
        StrategyParameter("hold_rank_multiplier", "Hold rank buffer", "float", 1.3, min=1.0, step=0.1),
        StrategyParameter("entry_rank_multiplier", "Entry rank buffer", "float", 1.0, min=1.0, step=0.1),
        StrategyParameter("amount_vol_weight", "Amount-vol weight", "float", 0.30, min=0.0, max=1.0, step=0.05),
        StrategyParameter("low_vol_reversal_weight", "Low-vol reversal weight", "float", 0.30, min=0.0, max=1.0, step=0.05),
        StrategyParameter("amount_stability_weight", "Amount stability weight", "float", 0.20, min=0.0, max=1.0, step=0.05),
        StrategyParameter("inverse_momentum_weight", "Inverse momentum weight", "float", 0.15, min=0.0, max=1.0, step=0.05),
        StrategyParameter("tail_risk_weight", "Tail-risk weight", "float", 0.05, min=0.0, max=1.0, step=0.05),
    ]

    def generate_target_weights(
        self, context: StrategyContext, history: pd.DataFrame
    ) -> dict[str, float]:
        if history.empty:
            return {}

        top_n = max(1, int(context.params.get("top_n", 30)))
        max_position_weight = float(context.params.get("max_position_weight", 0.05))
        max_total_weight = float(context.params.get("max_total_weight", 0.8))
        min_avg_amount_20d = float(context.params.get("min_avg_amount_20d", 50_000_000))
        min_price = float(context.params.get("min_price", 5.0))
        reversal_window = int(context.params.get("reversal_window", 20))
        momentum_window = int(context.params.get("momentum_window", 60))
        amount_window = int(context.params.get("amount_window", 20))
        tail_window = int(context.params.get("tail_window", 20))
        max_amount_ratio = float(context.params.get("max_amount_ratio", 2.5))
        hold_rank_multiplier = float(context.params.get("hold_rank_multiplier", 1.3))
        entry_rank_multiplier = float(context.params.get("entry_rank_multiplier", 1.0))
        factor_weights = {
            "amount_vol": float(context.params.get("amount_vol_weight", 0.30)),
            "low_vol_reversal": float(context.params.get("low_vol_reversal_weight", 0.30)),
            "amount_stability": float(context.params.get("amount_stability_weight", 0.20)),
            "inverse_momentum": float(context.params.get("inverse_momentum_weight", 0.15)),
            "tail_risk": float(context.params.get("tail_risk_weight", 0.05)),
        }

        for name, value in [
            ("reversal_window", reversal_window),
            ("momentum_window", momentum_window),
            ("amount_window", amount_window),
            ("tail_window", tail_window),
        ]:
            if value <= 1:
                raise ValueError(f"{name} must be > 1, got {value}")
        for name, value in [
            ("max_position_weight", max_position_weight),
            ("max_total_weight", max_total_weight),
        ]:
            if value < 0.0 or value > 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {value}")
        for name, value in [
            ("min_avg_amount_20d", min_avg_amount_20d),
            ("min_price", min_price),
            ("max_amount_ratio", max_amount_ratio),
            ("hold_rank_multiplier", hold_rank_multiplier),
            ("entry_rank_multiplier", entry_rank_multiplier),
            *[(key, weight) for key, weight in factor_weights.items()],
        ]:
            if value < 0.0:
                raise ValueError(f"{name} must be >= 0, got {value}")
        if hold_rank_multiplier < 1.0:
            raise ValueError(f"hold_rank_multiplier must be >= 1, got {hold_rank_multiplier}")
        if entry_rank_multiplier < 1.0:
            raise ValueError(f"entry_rank_multiplier must be >= 1, got {entry_rank_multiplier}")
        weight_sum = sum(factor_weights.values())
        if weight_sum <= 0.0:
            raise ValueError("at least one factor weight must be positive")

        all_symbols = list(history["symbol"].unique())
        min_len = max(momentum_window + 1, reversal_window + 1, amount_window + 1, tail_window + 1, 20)
        candidates: list[dict[str, float | str]] = []
        for symbol, group in history.groupby("symbol"):
            ordered = group.sort_values("trade_date")
            if len(ordered) < min_len or "amount" not in ordered.columns:
                continue

            closes = ordered["close"].astype(float)
            amounts = ordered["amount"].astype(float)
            latest_close = float(closes.iloc[-1])
            if latest_close < min_price:
                continue

            avg_amount_20d = float(amounts.tail(20).mean())
            if min_avg_amount_20d > 0.0 and avg_amount_20d < min_avg_amount_20d:
                continue

            amount_short = float(amounts.tail(5).mean())
            amount_long = float(amounts.tail(amount_window).mean())
            if amount_long <= 0.0:
                continue
            if max_amount_ratio > 0.0 and amount_short / amount_long > max_amount_ratio:
                continue

            returns = closes.pct_change(fill_method=None).dropna()
            if len(returns) < max(reversal_window, tail_window):
                continue

            reversal_base = float(closes.iloc[-reversal_window - 1])
            momentum_base = float(closes.iloc[-momentum_window - 1])
            if reversal_base <= 0.0 or momentum_base <= 0.0 or latest_close <= 0.0:
                continue
            reversal = reversal_base / latest_close - 1.0
            momentum = latest_close / momentum_base - 1.0

            vol = float(returns.tail(reversal_window).std())
            if vol <= 0.0 or pd.isna(vol):
                continue
            low_vol_reversal = reversal / vol

            amount_tail = amounts.tail(amount_window)
            amount_std = float(amount_tail.std())
            if amount_std <= 0.0 or pd.isna(amount_std):
                continue
            amount_stability = float(amount_tail.mean()) / amount_std

            log_amount_change = np.log(amounts.where(amounts > 0.0)).diff()
            amount_volatility = float(log_amount_change.tail(amount_window).std())
            if amount_volatility < 0.0 or pd.isna(amount_volatility):
                continue

            tail_risk = float(returns.tail(tail_window).quantile(0.05))
            if pd.isna(tail_risk):
                continue

            candidates.append(
                {
                    "symbol": str(symbol),
                    "amount_vol": -amount_volatility,
                    "low_vol_reversal": low_vol_reversal,
                    "amount_stability": amount_stability,
                    "inverse_momentum": -momentum,
                    "tail_risk": tail_risk,
                }
            )

        if not candidates:
            return {symbol: 0.0 for symbol in all_symbols}

        score = [0.0] * len(candidates)
        for factor_name, raw_weight in factor_weights.items():
            if raw_weight <= 0.0:
                continue
            percentiles = StableReversalStrategy._percentiles(
                [float(item[factor_name]) for item in candidates]
            )
            normalised_weight = raw_weight / weight_sum
            for index, percentile in enumerate(percentiles):
                score[index] += normalised_weight * percentile

        for index, item in enumerate(candidates):
            item["score"] = score[index]

        ranked = sorted(candidates, key=lambda item: float(item["score"]), reverse=True)
        rank_by_symbol = {
            str(item["symbol"]): rank
            for rank, item in enumerate(ranked, start=1)
        }
        hold_cutoff = max(top_n, int(top_n * hold_rank_multiplier))
        entry_cutoff = max(top_n, int(top_n * entry_rank_multiplier))
        retained_symbols = {
            symbol
            for symbol, quantity in context.positions.items()
            if quantity > 0 and rank_by_symbol.get(symbol, hold_cutoff + 1) <= hold_cutoff
        }

        selected_symbols = [
            str(item["symbol"])
            for item in ranked
            if str(item["symbol"]) in retained_symbols
        ][:top_n]
        for item in ranked:
            symbol = str(item["symbol"])
            if len(selected_symbols) >= top_n:
                break
            if symbol in selected_symbols:
                continue
            if rank_by_symbol[symbol] > entry_cutoff:
                continue
            selected_symbols.append(symbol)

        weight = min(max_position_weight, max_total_weight / len(selected_symbols))
        selected_symbol_set = set(selected_symbols)
        return {
            symbol: (weight if symbol in selected_symbol_set else 0.0)
            for symbol in all_symbols
        }


class MLScoreRankStrategy(Strategy):
    name = "ml_score_rank"
    display_name = "ML Score Rank"
    description = (
        "Reads an offline prediction CSV with trade_date/symbol/score columns, "
        "ranks stocks by same-date model score, and returns equal target weights."
    )
    parameters = [
        StrategyParameter("scores_path", "Scores CSV path", "str", _default_ml_scores_path()),
        StrategyParameter("score_column", "Score column", "str", "score"),
        StrategyParameter("top_n", "Position count", "int", 30, min=1, step=1),
        StrategyParameter("max_position_weight", "Single-name cap", "float", 0.05, min=0.0, max=1.0, step=0.01),
        StrategyParameter("max_total_weight", "Total exposure", "float", 0.8, min=0.0, max=1.0, step=0.05),
        StrategyParameter("min_score", "Minimum score", "float", -1.0, step=0.001),
        StrategyParameter("min_avg_amount_20d", "20d amount floor", "float", 50_000_000, min=0.0, step=1_000_000),
        StrategyParameter("min_price", "Minimum price", "float", 5.0, min=0.0, step=0.1),
        StrategyParameter("hold_rank_multiplier", "Hold rank buffer", "float", 1.3, min=1.0, step=0.1),
        StrategyParameter("entry_rank_multiplier", "Entry rank buffer", "float", 1.0, min=1.0, step=0.1),
        StrategyParameter("trade_gap_path", "Trade-gap CSV path", "str", ""),
        StrategyParameter("exclude_gap_types", "Excluded gap types", "str", "suspended,provider_gap,limit_halt,unknown"),
        StrategyParameter("negative_news_path", "Negative-news CSV path", "str", ""),
        StrategyParameter("use_db_negative_news", "Use DB negative news", "bool", False),
        StrategyParameter("news_availability", "News availability mode", "str", "observed"),
        StrategyParameter("negative_news_lookback_days", "Negative-news lookback days", "int", 3, min=0, step=1),
        StrategyParameter("negative_news_min_relevance", "Negative-news relevance floor", "float", 0.0, min=0.0, max=1.0, step=0.05),
        StrategyParameter("negative_news_max_sentiment", "Negative sentiment score cap", "float", -0.2, step=0.05),
    ]

    def generate_target_weights(
        self, context: StrategyContext, history: pd.DataFrame
    ) -> dict[str, float]:
        if history.empty:
            return {}

        scores_path = str(context.params.get("scores_path", "")).strip()
        if not scores_path:
            raise ValueError("scores_path is required for ml_score_rank")
        score_column = str(context.params.get("score_column", "score")).strip() or "score"
        top_n = max(1, int(context.params.get("top_n", 30)))
        max_position_weight = float(context.params.get("max_position_weight", 0.05))
        max_total_weight = float(context.params.get("max_total_weight", 0.8))
        min_score = float(context.params.get("min_score", -1.0))
        min_avg_amount_20d = float(context.params.get("min_avg_amount_20d", 50_000_000))
        min_price = float(context.params.get("min_price", 5.0))
        hold_rank_multiplier = float(context.params.get("hold_rank_multiplier", 1.3))
        entry_rank_multiplier = float(context.params.get("entry_rank_multiplier", 1.0))
        trade_gap_path = str(context.params.get("trade_gap_path", "")).strip()
        exclude_gap_types = {
            item.strip().lower()
            for item in str(
                context.params.get(
                    "exclude_gap_types", "suspended,provider_gap,limit_halt,unknown"
                )
            ).split(",")
            if item.strip()
        }
        negative_news_path = str(context.params.get("negative_news_path", "")).strip()
        negative_news_lookback_days = max(
            0, int(context.params.get("negative_news_lookback_days", 3))
        )
        negative_news_min_relevance = float(
            context.params.get("negative_news_min_relevance", 0.0)
        )
        negative_news_max_sentiment = float(
            context.params.get("negative_news_max_sentiment", -0.2)
        )

        for name, value in [
            ("max_position_weight", max_position_weight),
            ("max_total_weight", max_total_weight),
        ]:
            if value < 0.0 or value > 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {value}")
        for name, value in [
            ("min_avg_amount_20d", min_avg_amount_20d),
            ("min_price", min_price),
            ("hold_rank_multiplier", hold_rank_multiplier),
            ("entry_rank_multiplier", entry_rank_multiplier),
        ]:
            if value < 0.0:
                raise ValueError(f"{name} must be >= 0, got {value}")
        if hold_rank_multiplier < 1.0:
            raise ValueError(f"hold_rank_multiplier must be >= 1, got {hold_rank_multiplier}")
        if entry_rank_multiplier < 1.0:
            raise ValueError(f"entry_rank_multiplier must be >= 1, got {entry_rank_multiplier}")

        all_symbols = list(history["symbol"].unique())
        score_frame = _load_ml_scores(scores_path, score_column)
        today_scores = score_frame[score_frame["trade_date"] == context.current_date]
        if today_scores.empty:
            return {symbol: 0.0 for symbol in all_symbols}
        blocked_symbols: set[str] = set()
        if trade_gap_path:
            blocked_symbols.update(
                _blocked_by_trade_gap(trade_gap_path, context.current_date, exclude_gap_types)
            )
        if negative_news_path:
            blocked_symbols.update(
                _blocked_by_negative_news(
                    negative_news_path,
                    context.current_date,
                    lookback_days=negative_news_lookback_days,
                    min_relevance=negative_news_min_relevance,
                    max_sentiment=negative_news_max_sentiment,
                )
            )
        if _truthy(context.params.get("use_db_negative_news", False)):
            blocked_symbols.update(
                _blocked_by_negative_news_frame(
                    context.news_history,
                    context.current_date,
                    lookback_days=negative_news_lookback_days,
                    min_relevance=negative_news_min_relevance,
                    max_sentiment=negative_news_max_sentiment,
                )
            )

        latest = history.sort_values(["symbol", "trade_date"]).groupby("symbol").tail(20)
        eligible_symbols: set[str] = set()
        for symbol, group in latest.groupby("symbol"):
            ordered = group.sort_values("trade_date")
            if ordered.empty:
                continue
            latest_close = float(ordered["close"].iloc[-1])
            if latest_close < min_price:
                continue
            if min_avg_amount_20d > 0.0:
                if "amount" not in ordered.columns or len(ordered) < 20:
                    continue
                if float(ordered["amount"].astype(float).tail(20).mean()) < min_avg_amount_20d:
                    continue
            symbol = str(symbol)
            if symbol in blocked_symbols:
                continue
            eligible_symbols.add(symbol)

        candidates = [
            (str(row.symbol), float(row.score))
            for row in today_scores.itertuples()
            if str(row.symbol) in eligible_symbols and float(row.score) >= min_score
        ]
        ranked = sorted(candidates, key=lambda item: item[1], reverse=True)
        if not ranked:
            return {symbol: 0.0 for symbol in all_symbols}

        rank_by_symbol = {symbol: rank for rank, (symbol, _) in enumerate(ranked, start=1)}
        hold_cutoff = max(top_n, int(top_n * hold_rank_multiplier))
        entry_cutoff = max(top_n, int(top_n * entry_rank_multiplier))
        retained_symbols = {
            symbol
            for symbol, quantity in context.positions.items()
            if quantity > 0 and rank_by_symbol.get(symbol, hold_cutoff + 1) <= hold_cutoff
        }

        selected_symbols = [
            symbol for symbol, _ in ranked if symbol in retained_symbols
        ][:top_n]
        for symbol, _ in ranked:
            if len(selected_symbols) >= top_n:
                break
            if symbol in selected_symbols:
                continue
            if rank_by_symbol[symbol] > entry_cutoff:
                continue
            selected_symbols.append(symbol)

        weight = min(max_position_weight, max_total_weight / len(selected_symbols))
        selected_symbol_set = set(selected_symbols)
        return {
            symbol: (weight if symbol in selected_symbol_set else 0.0)
            for symbol in all_symbols
        }


@lru_cache(maxsize=8)
def _load_ml_scores(path: str, score_column: str) -> pd.DataFrame:
    scores_path = Path(path)
    if not scores_path.exists():
        raise ValueError(f"scores_path does not exist: {path}")
    scores = pd.read_csv(scores_path, dtype={"symbol": "string"})
    required = {"trade_date", "symbol", score_column}
    missing = required - set(scores.columns)
    if missing:
        raise ValueError(f"scores file is missing columns: {', '.join(sorted(missing))}")
    loaded = scores[["trade_date", "symbol", score_column]].rename(
        columns={score_column: "score"}
    )
    loaded["trade_date"] = pd.to_datetime(loaded["trade_date"]).dt.date
    loaded["symbol"] = loaded["symbol"].map(_normalise_score_symbol)
    loaded["score"] = pd.to_numeric(loaded["score"], errors="coerce")
    loaded = loaded.dropna(subset=["trade_date", "symbol", "score"])
    loaded = loaded.sort_values(["trade_date", "score"], ascending=[True, False])
    return loaded.drop_duplicates(["trade_date", "symbol"], keep="first")


def _normalise_score_symbol(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    symbol = str(value).strip()
    if symbol.endswith(".0"):
        symbol = symbol[:-2]
    return symbol.zfill(6) if symbol.isdigit() and len(symbol) <= 6 else symbol


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


@lru_cache(maxsize=8)
def _load_trade_gaps(path: str) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"symbol": "string"})
    required = {"symbol", "trade_date", "gap_type"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"trade gap file is missing columns: {', '.join(sorted(missing))}")
    out = frame[["symbol", "trade_date", "gap_type"]].copy()
    out["symbol"] = out["symbol"].map(_normalise_score_symbol)
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.date
    out["gap_type"] = out["gap_type"].astype(str).str.strip().str.lower()
    return out.dropna(subset=["symbol", "trade_date", "gap_type"])


def _blocked_by_trade_gap(
    path: str, current_date, exclude_gap_types: set[str]
) -> set[str]:
    if not exclude_gap_types:
        return set()
    gaps = _load_trade_gaps(path)
    today = gaps[gaps["trade_date"] == current_date]
    if today.empty:
        return set()
    return set(today[today["gap_type"].isin(exclude_gap_types)]["symbol"].astype(str))


@lru_cache(maxsize=8)
def _load_negative_news(path: str) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"symbol": "string"})
    required = {"symbol", "published_at", "fetched_at", "sentiment_label", "sentiment_score"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"negative news file is missing columns: {', '.join(sorted(missing))}")
    out = frame.copy()
    out["symbol"] = out["symbol"].map(_normalise_score_symbol)
    out["published_at"] = pd.to_datetime(out["published_at"], errors="coerce")
    out["fetched_at"] = pd.to_datetime(out["fetched_at"], errors="coerce")
    out["known_at"] = out[["published_at", "fetched_at"]].max(axis=1)
    out["sentiment_label"] = out["sentiment_label"].astype(str).str.strip().str.lower()
    out["sentiment_score"] = pd.to_numeric(out["sentiment_score"], errors="coerce")
    if "relevance_score" not in out.columns:
        out["relevance_score"] = 1.0
    out["relevance_score"] = pd.to_numeric(out["relevance_score"], errors="coerce").fillna(0.0)
    return out.dropna(subset=["symbol", "known_at", "sentiment_score"])


def _blocked_by_negative_news(
    path: str,
    current_date,
    *,
    lookback_days: int,
    min_relevance: float,
    max_sentiment: float,
) -> set[str]:
    news = _load_negative_news(path)
    end = pd.Timestamp(current_date) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    start = pd.Timestamp(current_date) - pd.Timedelta(days=lookback_days)
    filtered = news[
        (news["known_at"] >= start)
        & (news["known_at"] <= end)
        & (news["relevance_score"] >= min_relevance)
        & (
            (news["sentiment_label"].isin({"negative", "risk", "bearish", "bad"}))
            | (news["sentiment_score"] <= max_sentiment)
        )
    ]
    return set(filtered["symbol"].astype(str))


def _blocked_by_negative_news_frame(
    frame: pd.DataFrame | None,
    current_date,
    *,
    lookback_days: int,
    min_relevance: float,
    max_sentiment: float,
) -> set[str]:
    if frame is None or frame.empty:
        return set()
    news = frame.copy()
    if "known_at" not in news.columns:
        if not {"published_at", "fetched_at"}.issubset(news.columns):
            return set()
        news["published_at"] = pd.to_datetime(news["published_at"], errors="coerce")
        news["fetched_at"] = pd.to_datetime(news["fetched_at"], errors="coerce")
        news["known_at"] = news[["published_at", "fetched_at"]].max(axis=1)
    else:
        news["known_at"] = pd.to_datetime(news["known_at"], errors="coerce")
    if "relevance_score" not in news.columns:
        news["relevance_score"] = 1.0
    news["symbol"] = news["symbol"].map(_normalise_score_symbol)
    if "sentiment_label" not in news.columns:
        news["sentiment_label"] = ""
    if "sentiment_score" not in news.columns:
        news["sentiment_score"] = np.nan
    if "event_type" not in news.columns:
        news["event_type"] = ""
    news["sentiment_label"] = news["sentiment_label"].astype(str).str.strip().str.lower()
    news["sentiment_score"] = pd.to_numeric(news["sentiment_score"], errors="coerce")
    news["relevance_score"] = pd.to_numeric(news["relevance_score"], errors="coerce").fillna(0.0)
    news["event_type"] = news["event_type"].astype(str).str.strip().str.lower()
    news = news.dropna(subset=["symbol", "known_at"])
    end = pd.Timestamp(current_date) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    start = pd.Timestamp(current_date) - pd.Timedelta(days=lookback_days)
    filtered = news[
        (news["known_at"] >= start)
        & (news["known_at"] <= end)
        & (news["relevance_score"] >= min_relevance)
        & (
            (news["event_type"].isin({"negative_news", "risk_news"}))
            | (news["sentiment_label"].isin({"negative", "risk", "bearish", "bad"}))
            | (news["sentiment_score"] <= max_sentiment)
        )
    ]
    return set(filtered["symbol"].astype(str))


BUILTIN_STRATEGIES: dict[str, type[Strategy]] = {
    MovingAverageStrategy.name: MovingAverageStrategy,
    MomentumRankStrategy.name: MomentumRankStrategy,
    InverseMomentumStrategy.name: InverseMomentumStrategy,
    MeanReversionStrategy.name: MeanReversionStrategy,
    LowVolDefensiveStrategy.name: LowVolDefensiveStrategy,
    StableReversalStrategy.name: StableReversalStrategy,
    MultiFactorRankStrategy.name: MultiFactorRankStrategy,
    MLScoreRankStrategy.name: MLScoreRankStrategy,
}
