"""Create monthly return/excess diagnostics from a backtest JSON file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, default=Path("monthly-backtest-diagnostics.json"))
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args()


def monthly_report(backtest: dict[str, Any]) -> dict[str, Any]:
    if not backtest.get("runs"):
        raise ValueError("backtest has no runs")
    run = backtest["runs"][0]
    if "equity_curve" not in run:
        raise ValueError("backtest run has no equity_curve; rerun with --include-curves")

    equity = _curve_frame(run["equity_curve"], "strategy_equity")
    benchmark = _curve_frame(run.get("benchmark_curve", []), "benchmark_equity")
    if benchmark.empty:
        merged = equity.copy()
        merged["benchmark_equity"] = pd.NA
    else:
        merged = equity.merge(benchmark, on="trade_date", how="left").ffill()
    merged["month"] = merged["trade_date"].dt.to_period("M").astype(str)

    rows: list[dict[str, Any]] = []
    for month, group in merged.groupby("month", sort=True):
        strategy_return = _period_return(group["strategy_equity"])
        benchmark_return = (
            _period_return(group["benchmark_equity"]) if group["benchmark_equity"].notna().any() else None
        )
        rows.append(
            {
                "month": month,
                "start_date": group["trade_date"].iloc[0].date().isoformat(),
                "end_date": group["trade_date"].iloc[-1].date().isoformat(),
                "strategy_return": strategy_return,
                "benchmark_return": benchmark_return,
                "excess_return": (
                    strategy_return - benchmark_return if benchmark_return is not None else None
                ),
                "min_drawdown": float(group["drawdown"].min()) if "drawdown" in group else None,
            }
        )

    excess_values = [row["excess_return"] for row in rows if row["excess_return"] is not None]
    return {
        "metadata": backtest.get("metadata", {}),
        "parameters": run.get("parameters", {}),
        "metrics": run.get("metrics", {}),
        "monthly": rows,
        "summary": {
            "months": len(rows),
            "positive_excess_months": sum(1 for value in excess_values if value > 0),
            "negative_excess_months": sum(1 for value in excess_values if value < 0),
            "mean_monthly_excess": float(pd.Series(excess_values).mean()) if excess_values else None,
            "worst_monthly_excess": min(excess_values) if excess_values else None,
            "best_monthly_excess": max(excess_values) if excess_values else None,
        },
    }


def _curve_frame(points: list[dict[str, Any]], equity_column: str) -> pd.DataFrame:
    if not points:
        return pd.DataFrame(columns=["trade_date", equity_column])
    frame = pd.DataFrame(points)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame = frame.rename(columns={"equity": equity_column})
    columns = ["trade_date", equity_column]
    if "drawdown" in frame.columns:
        columns.append("drawdown")
    return frame[columns].sort_values("trade_date")


def _period_return(equity: pd.Series) -> float:
    values = pd.to_numeric(equity, errors="coerce").dropna()
    if values.empty:
        return 0.0
    first = float(values.iloc[0])
    last = float(values.iloc[-1])
    return last / first - 1.0 if first > 0 else 0.0


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Monthly Backtest Diagnostics",
        "",
        f"- strategy: `{report['metadata'].get('strategy')}`",
        f"- benchmark: `{report['metadata'].get('benchmark_symbol')}`",
        f"- start: `{report['metadata'].get('start_date')}`",
        f"- end: `{report['metadata'].get('end_date')}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in report["summary"].items():
        lines.append(f"- {key}: {_fmt(value)}")
    lines.extend(
        [
            "",
            "## Monthly",
            "",
            "| Month | Strategy | Benchmark | Excess | Min DD |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in report["monthly"]:
        lines.append(
            "| {month} | {strategy_return} | {benchmark_return} | {excess_return} | {min_drawdown} |".format(
                month=row["month"],
                strategy_return=_fmt(row["strategy_return"]),
                benchmark_return=_fmt(row["benchmark_return"]),
                excess_return=_fmt(row["excess_return"]),
                min_drawdown=_fmt(row["min_drawdown"]),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return str(value)


def main() -> int:
    args = parse_args()
    backtest = json.loads(args.backtest.read_text(encoding="utf-8"))
    report = monthly_report(backtest)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.markdown_output is not None:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(to_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
