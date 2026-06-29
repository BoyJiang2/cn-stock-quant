"""CLI runner for reproducible strategy backtests and small parameter grids."""

from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path
from typing import Any

from app.backtest.engine import BacktestConfig, DailyBacktestEngine
from app.core.database import SessionLocal, init_db
from app.data.repository import MarketDataRepository
from app.strategy.registry import get_strategy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", default="stable_reversal")
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--symbol-source", choices=["research_pool", "manual"], default="research_pool")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--pool-max-symbols", type=int, default=6000)
    parser.add_argument("--benchmark-symbol", default="000300")
    parser.add_argument("--initial-cash", type=float, default=1_000_000)
    parser.add_argument("--commission-rate", type=float, default=0.0003)
    parser.add_argument("--stamp-tax-rate", type=float, default=0.001)
    parser.add_argument("--slippage-rate", type=float, default=0.0005)
    parser.add_argument("--rebalance-interval", type=int, default=1)
    parser.add_argument("--risk-max-symbol-weight", type=float, default=1.0)
    parser.add_argument("--risk-max-total-weight", type=float, default=1.0)
    parser.add_argument("--risk-max-positions", type=int)
    parser.add_argument("--param", action="append", default=[], help="Strategy parameter as name=value.")
    parser.add_argument(
        "--grid-json",
        type=Path,
        help="Optional JSON file containing a list of strategy parameter dicts.",
    )
    parser.add_argument("--output", type=Path, default=Path("strategy-backtest-result.json"))
    return parser.parse_args()


def _parse_value(value: str) -> Any:
    lowered = value.strip().lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_params(items: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Invalid --param {item!r}; expected name=value.")
        name, value = item.split("=", 1)
        name = name.strip()
        if not name:
            raise ValueError(f"Invalid --param {item!r}; parameter name is empty.")
        params[name] = _parse_value(value)
    return params


def _grid_params(args: argparse.Namespace) -> list[dict[str, Any]]:
    base = _parse_params(args.param)
    if args.grid_json is None:
        return [base]

    loaded = json.loads(args.grid_json.read_text(encoding="utf-8"))
    if not isinstance(loaded, list):
        raise ValueError("--grid-json must contain a JSON list of parameter objects.")
    grid: list[dict[str, Any]] = []
    for index, item in enumerate(loaded):
        if not isinstance(item, dict):
            raise ValueError(f"Grid item {index} is not an object.")
        grid.append({**base, **item})
    return grid or [base]


def _trade_stats(trades: list[dict], initial_cash: float) -> dict[str, Any]:
    buy_amount = sum(float(trade["amount"]) for trade in trades if trade["side"] == "buy")
    sell_amount = sum(float(trade["amount"]) for trade in trades if trade["side"] == "sell")
    commissions = sum(float(trade.get("commission", 0.0)) for trade in trades)
    stamp_tax = sum(float(trade.get("stamp_tax", 0.0)) for trade in trades)
    return {
        "trade_count": len(trades),
        "buy_amount": round(buy_amount, 2),
        "sell_amount": round(sell_amount, 2),
        "turnover_on_initial_cash": round((buy_amount + sell_amount) / initial_cash, 6)
        if initial_cash > 0
        else 0.0,
        "commission": round(commissions, 2),
        "stamp_tax": round(stamp_tax, 2),
    }


def run_backtests(args: argparse.Namespace) -> dict[str, Any]:
    if args.start_date > args.end_date:
        raise ValueError("start_date must be <= end_date")
    if args.pool_max_symbols < 1:
        raise ValueError("pool_max_symbols must be >= 1")

    started_at = time.time()
    init_db()
    with SessionLocal() as session:
        repository = MarketDataRepository(session)
        if args.symbol_source == "manual":
            symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
        else:
            symbols = repository.select_research_symbols(
                args.start_date,
                args.end_date,
                limit=args.pool_max_symbols,
            )
            if not symbols:
                symbols = repository.covered_research_symbols(
                    args.start_date,
                    args.end_date,
                    limit=args.pool_max_symbols,
                )
        if not symbols:
            raise ValueError("No symbols selected for strategy backtest.")

        bars_started_at = time.time()
        bars = repository.daily_bars(symbols, args.start_date, args.end_date)
        bars_seconds = time.time() - bars_started_at
        if bars.empty:
            raise ValueError("No local daily bars for selected symbols and date range.")

        benchmark_bars = None
        if args.benchmark_symbol:
            benchmark_bars = repository.index_daily_bars(
                args.benchmark_symbol,
                args.start_date,
                args.end_date,
            )
            if benchmark_bars.empty:
                benchmark_bars = None

    strategy = get_strategy(args.strategy)
    runs = []
    for index, params in enumerate(_grid_params(args), start=1):
        run_started_at = time.time()
        result = DailyBacktestEngine().run(
            strategy=strategy,
            bars=bars,
            benchmark_bars=benchmark_bars,
            config=BacktestConfig(
                start_date=args.start_date,
                end_date=args.end_date,
                initial_cash=args.initial_cash,
                commission_rate=args.commission_rate,
                stamp_tax_rate=args.stamp_tax_rate,
                slippage_rate=args.slippage_rate,
                rebalance_interval=args.rebalance_interval,
                risk_max_symbol_weight=args.risk_max_symbol_weight,
                risk_max_total_weight=args.risk_max_total_weight,
                risk_max_positions=args.risk_max_positions,
                params=params,
            ),
        )
        runs.append(
            {
                "run_index": index,
                "parameters": params,
                "metrics": result.metrics,
                "trade_stats": _trade_stats(result.trades, args.initial_cash),
                "timing_seconds": round(time.time() - run_started_at, 3),
            }
        )

    runs.sort(
        key=lambda item: (
            float(item["metrics"].get("excess_return", item["metrics"]["total_return"])),
            float(item["metrics"]["sharpe"]),
            float(item["metrics"]["max_drawdown"]),
        ),
        reverse=True,
    )
    return {
        "metadata": {
            "strategy": args.strategy,
            "start_date": args.start_date.isoformat(),
            "end_date": args.end_date.isoformat(),
            "symbol_source": args.symbol_source,
            "selected_symbol_count": len(symbols),
            "bar_rows": len(bars),
            "benchmark_symbol": args.benchmark_symbol if benchmark_bars is not None else None,
            "commission_rate": args.commission_rate,
            "stamp_tax_rate": args.stamp_tax_rate,
            "slippage_rate": args.slippage_rate,
            "rebalance_interval": args.rebalance_interval,
            "point_in_time": False,
            "degraded": True,
            "degraded_reasons": [
                "research_pool is selected from today's active-stock coverage",
                "historical listing, delisting, and ST states are not fully applied",
                "qfq OHLCV history may be revised after future corporate actions",
            ],
            "timing_seconds": {
                "bars_load": round(bars_seconds, 3),
                "total": round(time.time() - started_at, 3),
            },
        },
        "runs": runs,
    }


def main() -> int:
    args = parse_args()
    result = run_backtests(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["metadata"], ensure_ascii=False), flush=True)
    if result["runs"]:
        print(json.dumps(result["runs"][0], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
