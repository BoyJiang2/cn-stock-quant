from datetime import date, timedelta

import pandas as pd

from app.backtest.engine import BacktestConfig, DailyBacktestEngine
from app.strategy.examples import MovingAverageStrategy


def test_moving_average_backtest_runs():
    start = date(2024, 1, 1)
    bars = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "trade_date": start + timedelta(days=i),
                "open": 10 + i * 0.01,
                "high": 10 + i * 0.01,
                "low": 10 + i * 0.01,
                "close": 10 + i * 0.01,
                "volume": 100000,
                "amount": 1000000,
            }
            for i in range(80)
        ]
    )
    result = DailyBacktestEngine().run(
        strategy=MovingAverageStrategy(),
        bars=bars,
        config=BacktestConfig(
            start_date=start,
            end_date=start + timedelta(days=79),
            initial_cash=100000,
            params={"fast_window": 5, "slow_window": 20, "max_position_weight": 0.9},
        ),
    )
    assert result.metrics["final_equity"] > 0
    assert len(result.equity_curve) == 80

