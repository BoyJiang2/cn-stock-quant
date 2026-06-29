"""CLI runner for reproducible large-universe factor experiments."""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.core.database import SessionLocal, init_db
from app.data.repository import MarketDataRepository
from app.factors import (
    BUILTIN_FACTOR_NAMES,
    FACTOR_DIRECTIONS,
    FactorLab,
    FactorSpec,
    evaluate,
    forward_returns,
    preprocess,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--n-groups", type=int, default=5)
    parser.add_argument("--pool-max-symbols", type=int, default=6000)
    parser.add_argument("--factor", action="append", dest="factors")
    parser.add_argument("--output", type=Path, default=Path("factor-experiment-result.json"))
    return parser.parse_args()


def _finite(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def run_experiment(args: argparse.Namespace) -> dict[str, Any]:
    if args.start_date > args.end_date:
        raise ValueError("start_date must be <= end_date")
    if args.horizon < 1:
        raise ValueError("horizon must be >= 1")
    if args.n_groups < 2:
        raise ValueError("n_groups must be >= 2")

    requested = args.factors or list(BUILTIN_FACTOR_NAMES)
    unknown = sorted(set(requested) - set(BUILTIN_FACTOR_NAMES))
    if unknown:
        raise ValueError(f"Unknown factors: {', '.join(unknown)}")

    init_db()
    warmup_start = args.start_date - timedelta(days=260)
    label_end = args.end_date + timedelta(days=args.horizon * 3 + 10)
    started_at = time.time()

    with SessionLocal() as session:
        repository = MarketDataRepository(session)
        symbols = repository.covered_research_symbols(
            args.start_date,
            args.end_date,
            limit=args.pool_max_symbols,
        )
        if not symbols:
            raise ValueError("No eligible symbols for factor experiment")
        bars_started_at = time.time()
        bars = repository.daily_bars(symbols, warmup_start, label_end)
        bars_seconds = time.time() - bars_started_at

    if bars.empty:
        raise ValueError("No local daily bars for requested experiment")

    factor_started_at = time.time()
    factor_panel = FactorLab().compute(bars, [FactorSpec(name) for name in requested])
    factor_seconds = time.time() - factor_started_at

    labels = forward_returns(bars, horizons=(args.horizon,))[f"fwd_{args.horizon}d"]
    signal_dates = factor_panel.index.get_level_values("trade_date")
    factor_panel = factor_panel[
        (signal_dates >= args.start_date) & (signal_dates <= args.end_date)
    ]
    label_dates = labels.index.get_level_values("trade_date")
    labels = labels[(label_dates >= args.start_date) & (label_dates <= args.end_date)]

    summaries: list[dict[str, Any]] = []
    for name in requested:
        adjusted = preprocess(factor_panel[[name]])["standardized"][name] * FACTOR_DIRECTIONS[name]
        report = evaluate(adjusted, labels, n_groups=args.n_groups)
        summaries.append(
            {
                "name": name,
                "direction": FACTOR_DIRECTIONS[name],
                "ic_mean": _finite(report["ic_mean"]),
                "ic_ir": _finite(report["ic_ir"]),
                "rankic_mean": _finite(report["rankic_mean"]),
                "rankic_ir": _finite(report["rankic_ir"]),
                "long_short_return": _finite(report["long_short_return"]),
                "long_short_turnover": _finite(report["long_short_turnover"]),
                "n_dates": int(report["n_dates"]),
                "group_returns": {
                    str(group): _finite(value)
                    for group, value in report["group_returns"].items()
                },
            }
        )
    summaries.sort(
        key=lambda item: item["rankic_mean"]
        if item["rankic_mean"] is not None
        else float("-inf"),
        reverse=True,
    )

    return {
        "metadata": {
            "start_date": args.start_date.isoformat(),
            "end_date": args.end_date.isoformat(),
            "warmup_start": warmup_start.isoformat(),
            "label_end": label_end.isoformat(),
            "horizon": args.horizon,
            "n_groups": args.n_groups,
            "selected_symbol_count": len(symbols),
            "bar_rows": len(bars),
            "factor_count": len(requested),
            "factor_panel_rows": len(factor_panel),
            "point_in_time": False,
            "degraded": True,
            "degraded_reasons": [
                "research_pool is selected from today's active-stock coverage",
                "factor experiments currently use non-PIT universes",
                "qfq OHLCV history may be revised after future corporate actions",
            ],
            "timing_seconds": {
                "bars_load": round(bars_seconds, 3),
                "factor_compute": round(factor_seconds, 3),
                "total": round(time.time() - started_at, 3),
            },
        },
        "summaries": summaries,
    }


def main() -> int:
    args = parse_args()
    result = run_experiment(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["metadata"], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
