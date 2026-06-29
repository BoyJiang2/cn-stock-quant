from dataclasses import dataclass, replace
from datetime import date
from copy import deepcopy

import pandas as pd

from app.backtest.engine import BacktestConfig, BacktestResult, DailyBacktestEngine
from app.strategy.base import Strategy


@dataclass(frozen=True)
class ValidationWindow:
    name: str
    start_date: date
    end_date: date


def run_walk_forward(
    strategy: Strategy,
    bars: pd.DataFrame,
    base_config: BacktestConfig,
    windows: list[ValidationWindow],
    benchmark_bars: pd.DataFrame | None = None,
) -> list[dict]:
    results: list[dict] = []
    for window in windows:
        if window.start_date > window.end_date:
            raise ValueError(f"invalid validation window {window.name}: start_date is after end_date")
        config = replace(
            base_config,
            start_date=window.start_date,
            end_date=window.end_date,
        )
        result = DailyBacktestEngine().run(
            strategy=deepcopy(strategy),
            bars=bars,
            config=config,
            benchmark_bars=benchmark_bars,
        )
        results.append(
            {
                "name": window.name,
                "start_date": window.start_date,
                "end_date": window.end_date,
                "metrics": result.metrics,
                "trade_count": len(result.trades),
            }
        )
    return results


def run_cost_stress(
    strategy: Strategy,
    bars: pd.DataFrame,
    base_config: BacktestConfig,
    multipliers: tuple[float, ...] = (1.0, 2.0, 3.0),
    benchmark_bars: pd.DataFrame | None = None,
) -> list[dict]:
    results: list[dict] = []
    for multiplier in multipliers:
        if multiplier <= 0:
            raise ValueError("cost multipliers must be positive")
        config = replace(
            base_config,
            commission_rate=base_config.commission_rate * multiplier,
            stamp_tax_rate=base_config.stamp_tax_rate * multiplier,
            slippage_rate=base_config.slippage_rate * multiplier,
        )
        result: BacktestResult = DailyBacktestEngine().run(
            strategy=deepcopy(strategy),
            bars=bars,
            config=config,
            benchmark_bars=benchmark_bars,
        )
        results.append(
            {
                "cost_multiplier": multiplier,
                "metrics": result.metrics,
                "trade_count": len(result.trades),
            }
        )
    return results
