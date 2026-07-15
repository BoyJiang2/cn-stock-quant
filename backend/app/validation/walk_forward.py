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
    warmup_start_date: date | None = None


def rolling_oos_windows(
    trading_dates: list[date],
    *,
    warmup_trading_days: int,
    oos_window_trading_days: int,
) -> list[ValidationWindow]:
    """Build non-overlapping OOS windows with trailing history-only warm-up."""
    if warmup_trading_days < 1 or oos_window_trading_days < 1:
        raise ValueError("warmup_trading_days and oos_window_trading_days must be positive")
    dates = sorted(set(trading_dates))
    first_oos_index = warmup_trading_days
    if len(dates) < first_oos_index + oos_window_trading_days:
        return []

    windows: list[ValidationWindow] = []
    index = first_oos_index
    while index + oos_window_trading_days <= len(dates):
        windows.append(
            ValidationWindow(
                name=f"oos_{len(windows) + 1:02d}",
                start_date=dates[index],
                end_date=dates[index + oos_window_trading_days - 1],
                warmup_start_date=dates[index - warmup_trading_days],
            )
        )
        index += oos_window_trading_days
    return windows


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
            start_date=window.warmup_start_date or window.start_date,
            end_date=window.end_date,
            evaluation_start_date=window.start_date,
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
