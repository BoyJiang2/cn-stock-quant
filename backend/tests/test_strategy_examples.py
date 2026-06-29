from datetime import date, timedelta

import pandas as pd

from app.strategy.base import StrategyContext
from app.strategy.examples import BUILTIN_STRATEGIES, MomentumRankStrategy, StableReversalStrategy


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


def test_stable_reversal_selects_stable_oversold_liquid_symbols():
    start = date(2024, 1, 1)
    rows = []
    for i in range(25):
        for symbol, close, amount in [
            ("BEST", 10.0 if i < 20 else 9.0 - (i - 20) * 0.1, 100_000_000 + (i % 2) * 1_000_000),
            ("ONE_FACTOR", 10.0 if i < 20 else 9.2 - (i - 20) * 0.1, 100_000_000 + (i % 7) * 15_000_000),
            ("WEAK", 10.0 + i * 0.1, 100_000_000 + (i % 3) * 1_000_000),
            ("ILLIQUID", 10.0 if i < 20 else 8.8, 1_000_000),
        ]:
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": start + timedelta(days=i),
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 100_000,
                    "amount": amount,
                }
            )
    bars = pd.DataFrame(rows)

    weights = StableReversalStrategy().generate_target_weights(
        StrategyContext(
            current_date=start + timedelta(days=24),
            cash=100_000,
            params={
                "top_n": 2,
                "max_position_weight": 0.2,
                "max_total_weight": 0.3,
                "min_avg_amount_20d": 50_000_000,
                "min_price": 1.0,
                "min_reversal": 0.0,
                "low_vol_weight": 0.0,
            },
        ),
        bars,
    )

    assert weights["BEST"] == 0.15
    assert weights["ONE_FACTOR"] == 0.15
    assert weights["WEAK"] == 0.0
    assert weights["ILLIQUID"] == 0.0
    assert abs(sum(weights.values()) - 0.3) < 1e-9


def test_stable_reversal_filters_crowded_amount_spikes():
    start = date(2024, 1, 1)
    bars = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "trade_date": start + timedelta(days=i),
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 100_000,
                "amount": amount,
            }
            for i in range(25)
            for symbol, close, amount in [
                ("STEADY", 10.0 if i < 20 else 9.0, 100_000_000 + (i % 2) * 1_000_000),
                ("SPIKE", 10.0 if i < 20 else 8.8, 100_000_000 if i < 20 else 400_000_000),
            ]
        ]
    )

    weights = StableReversalStrategy().generate_target_weights(
        StrategyContext(
            current_date=start + timedelta(days=24),
            cash=100_000,
            params={
                "top_n": 2,
                "max_position_weight": 0.5,
                "max_total_weight": 0.8,
                "min_avg_amount_20d": 0,
                "min_price": 1.0,
                "max_amount_ratio": 1.5,
            },
        ),
        bars,
    )

    assert weights["STEADY"] == 0.5
    assert weights["SPIKE"] == 0.0


def test_stable_reversal_hold_rank_buffer_keeps_existing_position():
    start = date(2024, 1, 1)
    rows = []
    for i in range(25):
        for symbol, close, amount in [
            ("BEST", 10.0 if i < 20 else 9.0 - (i - 20) * 0.1, 100_000_000 + (i % 2) * 1_000_000),
            ("HELD", 10.0 if i < 20 else 9.2 - (i - 20) * 0.1, 100_000_000 + (i % 7) * 15_000_000),
            ("WEAK", 10.0 + i * 0.1, 100_000_000 + (i % 3) * 1_000_000),
        ]:
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": start + timedelta(days=i),
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 100_000,
                    "amount": amount,
                }
            )
    bars = pd.DataFrame(rows)

    no_buffer = StableReversalStrategy().generate_target_weights(
        StrategyContext(
            current_date=start + timedelta(days=24),
            cash=100_000,
            positions={"HELD": 100},
            params={
                "top_n": 1,
                "max_position_weight": 0.5,
                "max_total_weight": 0.3,
                "min_avg_amount_20d": 0,
                "min_price": 1.0,
                "low_vol_weight": 0.0,
            },
        ),
        bars,
    )
    buffered = StableReversalStrategy().generate_target_weights(
        StrategyContext(
            current_date=start + timedelta(days=24),
            cash=100_000,
            positions={"HELD": 100},
            params={
                "top_n": 1,
                "max_position_weight": 0.5,
                "max_total_weight": 0.3,
                "min_avg_amount_20d": 0,
                "min_price": 1.0,
                "low_vol_weight": 0.0,
                "hold_rank_multiplier": 2.0,
            },
        ),
        bars,
    )

    assert no_buffer["BEST"] == 0.3
    assert no_buffer["HELD"] == 0.0
    assert buffered["BEST"] == 0.0
    assert buffered["HELD"] == 0.3


def test_stable_reversal_hold_rank_buffer_drops_position_outside_buffer():
    start = date(2024, 1, 1)
    rows = []
    for i in range(25):
        for symbol, close, amount in [
            ("BEST", 10.0 if i < 20 else 9.0 - (i - 20) * 0.1, 100_000_000 + (i % 2) * 1_000_000),
            ("SECOND", 10.0 if i < 20 else 9.2 - (i - 20) * 0.1, 100_000_000 + (i % 7) * 15_000_000),
            ("HELD", 10.0 if i < 20 else 9.5 - (i - 20) * 0.1, 100_000_000 + (i % 9) * 20_000_000),
        ]:
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": start + timedelta(days=i),
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 100_000,
                    "amount": amount,
                }
            )
    bars = pd.DataFrame(rows)

    weights = StableReversalStrategy().generate_target_weights(
        StrategyContext(
            current_date=start + timedelta(days=24),
            cash=100_000,
            positions={"HELD": 100},
            params={
                "top_n": 1,
                "max_position_weight": 0.5,
                "max_total_weight": 0.3,
                "min_avg_amount_20d": 0,
                "min_price": 1.0,
                "low_vol_weight": 0.0,
                "hold_rank_multiplier": 2.0,
            },
        ),
        bars,
    )

    assert weights["BEST"] == 0.3
    assert weights["HELD"] == 0.0


def test_stable_reversal_rejects_invalid_rank_buffers():
    start = date(2024, 1, 1)
    bars = pd.DataFrame(
        [
            {
                "symbol": "ONLY",
                "trade_date": start + timedelta(days=i),
                "open": 10.0,
                "high": 10.1,
                "low": 9.9,
                "close": 10.0,
                "volume": 100_000,
                "amount": 100_000_000 + (i % 2) * 1_000_000,
            }
            for i in range(25)
        ]
    )

    for param in ("hold_rank_multiplier", "entry_rank_multiplier"):
        try:
            StableReversalStrategy().generate_target_weights(
                StrategyContext(
                    current_date=start + timedelta(days=24),
                    cash=100_000,
                    params={param: 0.9},
                ),
                bars,
            )
        except ValueError as exc:
            assert param in str(exc)
            continue
        raise AssertionError(f"{param} below 1 should be rejected")


def test_stable_reversal_registered_as_builtin_strategy():
    assert BUILTIN_STRATEGIES["stable_reversal"] is StableReversalStrategy
    metadata = StableReversalStrategy.metadata()
    assert metadata["name"] == "stable_reversal"
    assert {param["name"] for param in metadata["parameters"]} >= {
        "reversal_window",
        "stability_window",
        "top_n",
        "max_total_weight",
        "hold_rank_multiplier",
    }
