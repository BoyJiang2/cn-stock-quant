from datetime import date, timedelta

import pandas as pd

from app.backtest.engine import BacktestConfig
from app.strategy.base import Strategy, StrategyContext
from app.validation.walk_forward import ValidationWindow, rolling_oos_windows, run_cost_stress, run_walk_forward


class AlwaysLong(Strategy):
    def generate_target_weights(self, context: StrategyContext, history: pd.DataFrame) -> dict[str, float]:
        return {"000001": 1.0}


class StatefulStrategy(AlwaysLong):
    def __init__(self):
        self.calls = 0

    def generate_target_weights(self, context: StrategyContext, history: pd.DataFrame) -> dict[str, float]:
        self.calls += 1
        return super().generate_target_weights(context, history)


def make_bars(days: int = 12) -> pd.DataFrame:
    start = date(2024, 1, 1)
    return pd.DataFrame(
        [
            {
                "symbol": "000001",
                "trade_date": start + timedelta(days=index),
                "open": 10.0 + index,
                "high": 10.0 + index,
                "low": 10.0 + index,
                "close": 10.0 + index,
                "volume": 100000.0,
                "amount": 1000000.0,
            }
            for index in range(days)
        ]
    )


def test_run_walk_forward_keeps_windows_separate():
    bars = make_bars()
    result = run_walk_forward(
        AlwaysLong(),
        bars,
        BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 12),
            initial_cash=100000,
            commission_rate=0,
            stamp_tax_rate=0,
            slippage_rate=0,
        ),
        [
            ValidationWindow("first", date(2024, 1, 1), date(2024, 1, 6)),
            ValidationWindow("second", date(2024, 1, 7), date(2024, 1, 12)),
        ],
    )

    assert [item["name"] for item in result] == ["first", "second"]
    assert all(item["trade_count"] == 1 for item in result)


def test_run_cost_stress_reports_each_multiplier():
    results = run_cost_stress(
        AlwaysLong(),
        make_bars(),
        BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 12),
            initial_cash=100000,
            commission_rate=0.0003,
            stamp_tax_rate=0.001,
            slippage_rate=0.0005,
        ),
    )

    assert [item["cost_multiplier"] for item in results] == [1.0, 2.0, 3.0]
    assert results[0]["metrics"]["final_equity"] >= results[-1]["metrics"]["final_equity"]


def test_validation_does_not_mutate_stateful_strategy():
    strategy = StatefulStrategy()
    run_walk_forward(
        strategy,
        make_bars(),
        BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 1, 12), initial_cash=100000),
        [ValidationWindow("all", date(2024, 1, 1), date(2024, 1, 12))],
    )
    assert strategy.calls == 0


def test_rolling_oos_windows_use_only_prior_history_as_warmup():
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(12)]

    windows = rolling_oos_windows(
        dates,
        warmup_trading_days=3,
        oos_window_trading_days=3,
    )

    assert [(window.warmup_start_date, window.start_date, window.end_date) for window in windows] == [
        (date(2024, 1, 1), date(2024, 1, 4), date(2024, 1, 6)),
        (date(2024, 1, 4), date(2024, 1, 7), date(2024, 1, 9)),
        (date(2024, 1, 7), date(2024, 1, 10), date(2024, 1, 12)),
    ]
