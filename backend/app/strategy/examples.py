import pandas as pd

from app.strategy.base import Strategy, StrategyContext


class MovingAverageStrategy(Strategy):
    name = "moving_average"
    display_name = "双均线择时策略"

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


BUILTIN_STRATEGIES: dict[str, type[Strategy]] = {
    MovingAverageStrategy.name: MovingAverageStrategy,
}

