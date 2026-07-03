"""Run reproducible 2026 factor and strategy research batches.

This script is intentionally a thin orchestrator around the existing research
CLIs. It keeps the long-running commands and result locations standardized so
future agents can resume the same P0/P1 validation instead of hand-building
slightly different commands.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

from app.factors import BUILTIN_FACTOR_NAMES


DEFAULT_START = date(2026, 1, 1)
DEFAULT_END = date(2026, 6, 18)

NEW_FACTOR_BATCH = [
    "money_flow_proxy_20d",
    "amount_volatility_20d",
    "low_vol_reversal_20d",
    "breakout_strength_20d",
    "drawdown_recovery_20d",
    "close_position_20d",
    "price_efficiency_20d",
    "intraday_momentum_20d",
    "overnight_gap_20d",
    "tail_risk_20d",
]

BENCHMARKS = ["000300", "000905", "000852"]

STRATEGY_RUNS = [
    {
        "name": "multi_factor_rank_low_turnover",
        "strategy": "multi_factor_rank",
        "rebalance_interval": 10,
        "params": {
            "top_n": 20,
            "momentum_window": 20,
            "reversal_window": 10,
            "hold_rank_multiplier": 1.5,
            "entry_rank_multiplier": 1.2,
        },
    },
    {
        "name": "inverse_momentum_60d_top30",
        "strategy": "inverse_momentum",
        "rebalance_interval": 10,
        "params": {
            "lookback_window": 60,
            "top_n": 30,
            "hold_rank_multiplier": 1.2,
        },
    },
]

COST_CASES = {
    "default": {"commission": 0.0003, "slippage": 0.0005},
    "retail": {"commission": 0.0005, "slippage": 0.0010},
    "stress": {"commission": 0.0005, "slippage": 0.0020},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--end-date", type=date.fromisoformat, default=DEFAULT_END)
    parser.add_argument(
        "--stage",
        choices=["all", "factors", "strategies"],
        default="all",
        help="Which P0/P1 batch to run.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Use a small stock pool for smoke validation before full-market runs.",
    )
    parser.add_argument("--pool-max-symbols", type=int, default=6000)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("research_runs") / "2026",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used for child CLI processes.",
    )
    return parser.parse_args()


def _run(command: list[str]) -> None:
    print(" ".join(command), flush=True)
    subprocess.run(command, check=True)


def _factor_command(
    args: argparse.Namespace,
    name: str,
    factors: list[str],
    output: Path,
) -> list[str]:
    pool_max = 300 if args.quick else args.pool_max_symbols
    command = [
        args.python,
        "run_factor_experiment.py",
        "--start-date",
        args.start_date.isoformat(),
        "--end-date",
        args.end_date.isoformat(),
        "--pool-max-symbols",
        str(pool_max),
        "--output",
        str(output),
    ]
    for factor in factors:
        command.extend(["--factor", factor])
    return command


def run_factor_batches(args: argparse.Namespace) -> list[Path]:
    factor_dir = args.output_dir / ("quick" if args.quick else "full") / "factors"
    factor_dir.mkdir(parents=True, exist_ok=True)
    batches = [
        ("all_builtin", list(BUILTIN_FACTOR_NAMES)),
        ("new_factor_batch", NEW_FACTOR_BATCH),
    ]
    outputs: list[Path] = []
    for name, factors in batches:
        output = factor_dir / f"{name}.json"
        _run(_factor_command(args, name, factors, output))
        outputs.append(output)
    return outputs


def _strategy_command(
    args: argparse.Namespace,
    run: dict[str, Any],
    benchmark: str,
    cost_name: str,
    cost: dict[str, float],
    output: Path,
) -> list[str]:
    pool_max = 300 if args.quick else args.pool_max_symbols
    command = [
        args.python,
        "run_strategy_backtest.py",
        "--strategy",
        run["strategy"],
        "--start-date",
        args.start_date.isoformat(),
        "--end-date",
        args.end_date.isoformat(),
        "--pool-max-symbols",
        str(pool_max),
        "--benchmark-symbol",
        benchmark,
        "--rebalance-interval",
        str(run["rebalance_interval"]),
        "--commission-rate",
        str(cost["commission"]),
        "--slippage-rate",
        str(cost["slippage"]),
        "--output",
        str(output),
    ]
    for key, value in run["params"].items():
        command.extend(["--param", f"{key}={value}"])
    return command


def run_strategy_batches(args: argparse.Namespace) -> list[Path]:
    strategy_dir = args.output_dir / ("quick" if args.quick else "full") / "strategies"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for run in STRATEGY_RUNS:
        for benchmark in BENCHMARKS:
            for cost_name, cost in COST_CASES.items():
                output = strategy_dir / f"{run['name']}_{benchmark}_{cost_name}.json"
                _run(_strategy_command(args, run, benchmark, cost_name, cost, output))
                outputs.append(output)
    return outputs


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_summary(paths: list[Path], summary_path: Path) -> None:
    lines = [
        "# 2026 Research Pipeline Summary",
        "",
        "Generated by `backend/run_2026_research_pipeline.py`.",
        "",
        "## Factor Outputs",
        "",
    ]
    factor_paths = [path for path in paths if "\\factors\\" in str(path) or "/factors/" in str(path)]
    for path in factor_paths:
        data = _load_json(path)
        meta = data["metadata"]
        top = data["summaries"][:10]
        lines.extend(
            [
                f"### {path.name}",
                "",
                f"- symbols: {meta['selected_symbol_count']}",
                f"- factor_count: {meta['factor_count']}",
                f"- factor_panel_rows: {meta['factor_panel_rows']}",
                "",
                "| Factor | RankIC | ICIR | Long-short | Turnover |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for item in top:
            lines.append(
                "| {name} | {rankic_mean} | {rankic_ir} | {long_short_return} | {long_short_turnover} |".format(
                    name=item["name"],
                    rankic_mean=_fmt(item["rankic_mean"]),
                    rankic_ir=_fmt(item["rankic_ir"]),
                    long_short_return=_fmt(item["long_short_return"]),
                    long_short_turnover=_fmt(item["long_short_turnover"]),
                )
            )
        lines.append("")

    strategy_paths = [path for path in paths if "\\strategies\\" in str(path) or "/strategies/" in str(path)]
    lines.extend(["## Strategy Outputs", ""])
    if strategy_paths:
        lines.extend(
            [
                "| File | Benchmark | Return | Benchmark | Excess | Max DD | Sharpe | Turnover |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
    for path in strategy_paths:
        data = _load_json(path)
        meta = data["metadata"]
        run = data["runs"][0]
        metrics = run["metrics"]
        trades = run["trade_stats"]
        lines.append(
            "| {file} | {bench} | {total} | {benchmark} | {excess} | {dd} | {sharpe} | {turnover} |".format(
                file=path.name,
                bench=meta["benchmark_symbol"],
                total=_fmt(metrics.get("total_return")),
                benchmark=_fmt(metrics.get("benchmark_return")),
                excess=_fmt(metrics.get("excess_return")),
                dd=_fmt(metrics.get("max_drawdown")),
                sharpe=_fmt(metrics.get("sharpe")),
                turnover=_fmt(trades.get("turnover_on_initial_cash")),
            )
        )
    lines.append("")
    summary_path.write_text("\n".join(lines), encoding="utf-8")


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return str(value)


def main() -> int:
    args = parse_args()
    if args.start_date > args.end_date:
        raise ValueError("start-date must be <= end-date")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    outputs: list[Path] = []
    if args.stage in {"all", "factors"}:
        outputs.extend(run_factor_batches(args))
    if args.stage in {"all", "strategies"}:
        outputs.extend(run_strategy_batches(args))

    summary_name = f"summary_{args.stage}_{'quick' if args.quick else 'full'}.md"
    write_summary(outputs, args.output_dir / summary_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
