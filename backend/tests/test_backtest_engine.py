from datetime import date, timedelta

import pandas as pd

from app.backtest.engine import BacktestConfig, DailyBacktestEngine
from app.strategy.base import Strategy, StrategyContext
from app.strategy.examples import MeanReversionStrategy, MomentumRankStrategy, MovingAverageStrategy
from app.strategy.registry import list_strategies


class AlwaysLongStrategy(Strategy):
    def generate_target_weights(self, context: StrategyContext, history: pd.DataFrame) -> dict[str, float]:
        return {symbol: 1.0 for symbol in history["symbol"].unique()}


class FirstSignalLongThenFlatStrategy(Strategy):
    def generate_target_weights(self, context: StrategyContext, history: pd.DataFrame) -> dict[str, float]:
        target_weight = 1.0 if len(history["trade_date"].unique()) == 1 else 0.0
        return {symbol: target_weight for symbol in history["symbol"].unique()}


class RecordingStrategy(Strategy):
    def __init__(self):
        self.signal_dates: list[date] = []

    def generate_target_weights(self, context: StrategyContext, history: pd.DataFrame) -> dict[str, float]:
        self.signal_dates.append(context.current_date)
        return {symbol: 0.0 for symbol in history["symbol"].unique()}


class RotateSymbolStrategy(Strategy):
    def generate_target_weights(self, context: StrategyContext, history: pd.DataFrame) -> dict[str, float]:
        if len(history["trade_date"].unique()) == 1:
            return {"600000": 1.0, "000001": 0.0}
        return {"600000": 0.0, "000001": 1.0}


class OverweightThreeSymbolsStrategy(Strategy):
    def generate_target_weights(self, context: StrategyContext, history: pd.DataFrame) -> dict[str, float]:
        return {"000001": 0.5, "600000": 0.5, "300001": 0.5}


class BenchmarkRecordingStrategy(Strategy):
    def __init__(self):
        self.benchmark_dates: list[list[date]] = []

    def generate_target_weights(self, context: StrategyContext, history: pd.DataFrame) -> dict[str, float]:
        benchmark = context.benchmark_history
        self.benchmark_dates.append(
            benchmark["trade_date"].tolist() if benchmark is not None else []
        )
        return {symbol: 0.0 for symbol in history["symbol"].unique()}


class NewsRecordingStrategy(Strategy):
    def __init__(self):
        self.news_ids_by_date: list[list[str]] = []

    def generate_target_weights(self, context: StrategyContext, history: pd.DataFrame) -> dict[str, float]:
        news = context.news_history
        self.news_ids_by_date.append(
            news["source_id"].tolist() if news is not None else []
        )
        return {symbol: 0.0 for symbol in history["symbol"].unique()}


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


def test_signal_generated_today_executes_next_trading_day():
    start = date(2024, 1, 1)
    bars = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "trade_date": start + timedelta(days=i),
                "open": 10,
                "high": 10,
                "low": 10,
                "close": 10,
                "volume": 100000,
                "amount": 1000000,
            }
            for i in range(3)
        ]
    )

    result = DailyBacktestEngine().run(
        strategy=AlwaysLongStrategy(),
        bars=bars,
        config=BacktestConfig(start_date=start, end_date=start + timedelta(days=2), initial_cash=100000),
    )

    assert result.trades[0]["side"] == "buy"
    assert result.trades[0]["trade_date"] == start + timedelta(days=1)


def test_news_history_is_filtered_by_known_at_without_future_leakage():
    start = date(2024, 1, 1)
    bars = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "trade_date": start + timedelta(days=i),
                "open": 10,
                "high": 10,
                "low": 10,
                "close": 10,
                "volume": 100000,
                "amount": 1000000,
            }
            for i in range(3)
        ]
    )
    news = pd.DataFrame(
        [
            {
                "source_id": "known-day-1",
                "symbol": "000001",
                "published_at": "2024-01-01 10:00:00",
                "fetched_at": "2024-01-01 10:05:00",
            },
            {
                "source_id": "future-day-3",
                "symbol": "000001",
                "published_at": "2024-01-03 10:00:00",
                "fetched_at": "2024-01-03 10:05:00",
            },
        ]
    )
    strategy = NewsRecordingStrategy()

    DailyBacktestEngine().run(
        strategy=strategy,
        bars=bars,
        config=BacktestConfig(
            start_date=start,
            end_date=start + timedelta(days=2),
            initial_cash=100000,
            news_history=news,
        ),
    )

    assert strategy.news_ids_by_date == [
        ["known-day-1"],
        ["known-day-1"],
        ["known-day-1", "future-day-3"],
    ]


def test_buy_execution_respects_minimum_commission_cash_constraint():
    start = date(2024, 1, 1)
    bars = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "trade_date": start + timedelta(days=i),
                "open": 10,
                "high": 10,
                "low": 10,
                "close": 10,
                "volume": 100000,
                "amount": 1000000,
            }
            for i in range(2)
        ]
    )

    result = DailyBacktestEngine().run(
        strategy=AlwaysLongStrategy(),
        bars=bars,
        config=BacktestConfig(
            start_date=start,
            end_date=start + timedelta(days=1),
            initial_cash=1004.0,
            commission_rate=0.0003,
            stamp_tax_rate=0,
            slippage_rate=0,
        ),
    )

    assert result.trades == []
    assert result.equity_curve[-1]["cash"] >= 0.0


def test_zero_commission_rate_disables_minimum_commission():
    start = date(2024, 1, 1)
    bars = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "trade_date": start + timedelta(days=i),
                "open": 10,
                "high": 10,
                "low": 10,
                "close": 10,
                "volume": 100000,
                "amount": 1000000,
            }
            for i in range(2)
        ]
    )

    result = DailyBacktestEngine().run(
        strategy=AlwaysLongStrategy(),
        bars=bars,
        config=BacktestConfig(
            start_date=start,
            end_date=start + timedelta(days=1),
            initial_cash=1000.0,
            commission_rate=0,
            stamp_tax_rate=0,
            slippage_rate=0,
        ),
    )

    assert result.trades[0]["quantity"] == 100
    assert result.trades[0]["commission"] == 0.0
    assert result.equity_curve[-1]["cash"] == 0.0


def test_rebalance_interval_controls_signal_generation_dates():
    start = date(2024, 1, 1)
    bars = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "trade_date": start + timedelta(days=i),
                "open": 10,
                "high": 10,
                "low": 10,
                "close": 10,
                "volume": 100000,
                "amount": 1000000,
            }
            for i in range(5)
        ]
    )
    strategy = RecordingStrategy()

    DailyBacktestEngine().run(
        strategy=strategy,
        bars=bars,
        config=BacktestConfig(
            start_date=start,
            end_date=start + timedelta(days=4),
            initial_cash=100000,
            rebalance_interval=2,
        ),
    )

    assert strategy.signal_dates == [start, start + timedelta(days=2), start + timedelta(days=4)]


def test_limit_up_day_blocks_buy_execution():
    start = date(2024, 1, 1)
    bars = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "trade_date": start,
                "open": 10,
                "high": 10,
                "low": 10,
                "close": 10,
                "volume": 100000,
                "amount": 1000000,
            },
            {
                "symbol": "000001",
                "trade_date": start + timedelta(days=1),
                "open": 11,
                "high": 11,
                "low": 11,
                "close": 11,
                "volume": 100000,
                "amount": 1100000,
            },
        ]
    )

    result = DailyBacktestEngine().run(
        strategy=AlwaysLongStrategy(),
        bars=bars,
        config=BacktestConfig(start_date=start, end_date=start + timedelta(days=1), initial_cash=100000),
    )

    assert result.trades == []


def test_limit_down_day_blocks_sell_execution():
    start = date(2024, 1, 1)
    bars = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "trade_date": start,
                "open": 10,
                "high": 10,
                "low": 10,
                "close": 10,
                "volume": 100000,
                "amount": 1000000,
            },
            {
                "symbol": "000001",
                "trade_date": start + timedelta(days=1),
                "open": 10.2,
                "high": 10.2,
                "low": 10.2,
                "close": 10.2,
                "volume": 100000,
                "amount": 1020000,
            },
            {
                "symbol": "000001",
                "trade_date": start + timedelta(days=2),
                "open": 9.18,
                "high": 9.18,
                "low": 9.18,
                "close": 9.18,
                "volume": 100000,
                "amount": 918000,
            },
        ]
    )

    result = DailyBacktestEngine().run(
        strategy=FirstSignalLongThenFlatStrategy(),
        bars=bars,
        config=BacktestConfig(start_date=start, end_date=start + timedelta(days=2), initial_cash=100000),
    )

    assert [trade["side"] for trade in result.trades] == ["buy"]


def test_rebalance_sells_before_buys_to_release_cash():
    start = date(2024, 1, 1)
    bars = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "trade_date": start + timedelta(days=i),
                "open": 10,
                "high": 10,
                "low": 10,
                "close": 10,
                "volume": 100000,
                "amount": 1000000,
            }
            for i in range(3)
            for symbol in ("000001", "600000")
        ]
    )

    result = DailyBacktestEngine().run(
        strategy=RotateSymbolStrategy(),
        bars=bars,
        config=BacktestConfig(
            start_date=start,
            end_date=start + timedelta(days=2),
            initial_cash=100000,
            commission_rate=0,
            stamp_tax_rate=0,
            slippage_rate=0,
        ),
    )

    day_two_trades = [trade for trade in result.trades if trade["trade_date"] == start + timedelta(days=2)]
    assert [trade["side"] for trade in day_two_trades] == ["sell", "buy"]
    assert day_two_trades[0]["symbol"] == "600000"
    assert day_two_trades[1]["symbol"] == "000001"


def test_risk_engine_caps_targets_before_execution():
    start = date(2024, 1, 1)
    bars = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "trade_date": start + timedelta(days=i),
                "open": 10,
                "high": 10,
                "low": 10,
                "close": 10,
                "volume": 100000,
                "amount": 1000000,
            }
            for i in range(2)
            for symbol in ("000001", "600000", "300001")
        ]
    )

    result = DailyBacktestEngine().run(
        strategy=OverweightThreeSymbolsStrategy(),
        bars=bars,
        config=BacktestConfig(
            start_date=start,
            end_date=start + timedelta(days=1),
            initial_cash=100000,
            commission_rate=0,
            stamp_tax_rate=0,
            slippage_rate=0,
            risk_max_symbol_weight=0.3,
            risk_max_total_weight=0.5,
            risk_max_positions=2,
        ),
    )

    assert len(result.trades) == 2
    assert {trade["symbol"] for trade in result.trades} == {"000001", "600000"}
    assert sum(trade["amount"] for trade in result.trades) == 50000


def test_benchmark_curve_and_excess_return_are_reported():
    start = date(2024, 1, 1)
    bars = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "trade_date": start + timedelta(days=i),
                "open": 10,
                "high": 10,
                "low": 10,
                "close": 10,
                "volume": 100000,
                "amount": 1000000,
            }
            for i in range(3)
        ]
    )
    benchmark_bars = pd.DataFrame(
        [
            {
                "symbol": "000300",
                "trade_date": start + timedelta(days=i),
                "open": 100 + i,
                "high": 100 + i,
                "low": 100 + i,
                "close": 100 + i,
                "volume": 100000,
                "amount": 1000000,
            }
            for i in range(3)
        ]
    )

    result = DailyBacktestEngine().run(
        strategy=RecordingStrategy(),
        bars=bars,
        benchmark_bars=benchmark_bars,
        config=BacktestConfig(
            start_date=start,
            end_date=start + timedelta(days=2),
            initial_cash=100000,
            commission_rate=0,
            stamp_tax_rate=0,
            slippage_rate=0,
        ),
    )

    assert result.benchmark_curve[-1]["return"] == 0.02
    assert result.metrics["benchmark_return"] == 0.02
    assert result.metrics["excess_return"] == -0.02


def test_strategy_context_receives_only_benchmark_history_available_by_signal_date():
    start = date(2024, 1, 1)
    bars = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "trade_date": start + timedelta(days=i),
                "open": 10,
                "high": 10,
                "low": 10,
                "close": 10,
                "volume": 100000,
                "amount": 1000000,
            }
            for i in range(3)
        ]
    )
    benchmark = bars.assign(symbol="000300", close=[100, 101, 102])
    strategy = BenchmarkRecordingStrategy()

    DailyBacktestEngine().run(
        strategy=strategy,
        bars=bars,
        benchmark_bars=benchmark,
        config=BacktestConfig(start_date=start, end_date=start + timedelta(days=2), initial_cash=100000),
    )

    assert strategy.benchmark_dates == [
        [start],
        [start, start + timedelta(days=1)],
        [start, start + timedelta(days=1), start + timedelta(days=2)],
    ]


def test_strategy_registry_exposes_metadata():
    strategies = {item["name"]: item for item in list_strategies()}

    assert "moving_average" in strategies
    assert "momentum_rank" in strategies
    assert "mean_reversion" in strategies
    assert strategies["moving_average"]["parameters"][0]["name"] == "fast_window"


def test_momentum_rank_selects_top_positive_symbols():
    start = date(2024, 1, 1)
    bars = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "trade_date": start + timedelta(days=i),
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 100000,
                "amount": 1000000,
            }
            for i in range(6)
            for symbol, price in {
                "000001": 10 + i,
                "600000": 10 + i * 0.2,
                "300001": 10 - i * 0.1,
            }.items()
        ]
    )

    weights = MomentumRankStrategy().generate_target_weights(
        StrategyContext(
            current_date=start + timedelta(days=5),
            cash=100000,
            params={
                "lookback_window": 3,
                "skip_recent_days": 0,
                "top_n": 1,
                "max_position_weight": 0.8,
                "max_total_weight": 0.8,
                "min_avg_amount_20d": 0,
            },
        ),
        bars,
    )

    assert weights["000001"] == 0.8
    assert weights["600000"] == 0.0
    assert weights["300001"] == 0.0


def test_mean_reversion_selects_oversold_symbol():
    start = date(2024, 1, 1)
    prices = [10, 10, 10, 10, 8]
    bars = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "trade_date": start + timedelta(days=i),
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 100000,
                "amount": 1000000,
            }
            for i, price in enumerate(prices)
        ]
    )

    weights = MeanReversionStrategy().generate_target_weights(
        StrategyContext(
            current_date=start + timedelta(days=4),
            cash=100000,
            params={"window": 5, "entry_zscore": 1.0, "max_positions": 1, "max_total_weight": 0.7},
        ),
        bars,
    )

    assert weights["000001"] == 0.7
