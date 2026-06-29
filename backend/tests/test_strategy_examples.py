from datetime import date, timedelta

import pandas as pd

from app.strategy.base import StrategyContext
from app.strategy.examples import MomentumRankStrategy


def test_momentum_rank_skips_recent_days_and_caps_position_weight():
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
                "amount": 100000000,
            }
            for i in range(8)
            for symbol, price in {
                "000001": 10 + i,
                "600000": 10 + i * 0.4,
                "300001": 20 - i,
            }.items()
        ]
    )

    weights = MomentumRankStrategy().generate_target_weights(
        StrategyContext(
            current_date=start + timedelta(days=7),
            cash=100000,
            params={
                "lookback_window": 3,
                "skip_recent_days": 2,
                "top_n": 2,
                "max_position_weight": 0.2,
                "max_total_weight": 0.8,
                "min_avg_amount_20d": 0,
            },
        ),
        bars,
    )

    assert weights["000001"] == 0.2
    assert weights["600000"] == 0.2
    assert weights["300001"] == 0.0


def test_momentum_rank_filters_low_liquidity_symbols():
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
                "amount": amount,
            }
            for i in range(8)
            for symbol, price, amount in [
                ("000001", 10 + i, 100000000),
                ("600000", 10 + i * 2, 1000000),
            ]
        ]
    )

    weights = MomentumRankStrategy().generate_target_weights(
        StrategyContext(
            current_date=start + timedelta(days=7),
            cash=100000,
            params={
                "lookback_window": 3,
                "skip_recent_days": 0,
                "top_n": 2,
                "max_position_weight": 0.5,
                "max_total_weight": 0.8,
                "min_avg_amount_20d": 50000000,
            },
        ),
        bars,
    )

    assert weights["000001"] == 0.5
    assert weights["600000"] == 0.0
