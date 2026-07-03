"""Build a versioned factor/label dataset for the first ML ranking pass."""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.database import SessionLocal, init_db
from app.data.repository import MarketDataRepository
from app.factors import (
    BUILTIN_FACTOR_NAMES,
    FACTOR_DIRECTIONS,
    FactorLab,
    FactorSpec,
    forward_returns,
    preprocess,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--horizon", action="append", type=int, dest="horizons")
    parser.add_argument("--pool-max-symbols", type=int, default=6000)
    parser.add_argument("--factor", action="append", dest="factors")
    parser.add_argument("--output", type=Path, default=Path("ml-factor-dataset.csv"))
    parser.add_argument("--metadata-output", type=Path)
    return parser.parse_args()


def _finite(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def build_dataset(
    bars: pd.DataFrame,
    *,
    start_date: date,
    end_date: date,
    factors: list[str],
    horizons: list[int],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if start_date > end_date:
        raise ValueError("start_date must be <= end_date")
    if not factors:
        raise ValueError("factors must not be empty")
    if not horizons or any(horizon < 1 for horizon in horizons):
        raise ValueError("horizons must be positive")

    factor_panel = FactorLab().compute(bars, [FactorSpec(name) for name in factors])
    adjusted = pd.DataFrame(index=factor_panel.index)
    for name in factors:
        adjusted[name] = factor_panel[name] * FACTOR_DIRECTIONS[name]
    features = preprocess(adjusted)["standardized"]

    labels = forward_returns(bars, horizons=horizons)
    frame = features.join(labels, how="inner")
    trade_dates = frame.index.get_level_values("trade_date")
    frame = frame[(trade_dates >= start_date) & (trade_dates <= end_date)]
    frame = frame.dropna(subset=factors + [f"fwd_{horizon}d" for horizon in horizons])
    frame = frame.reset_index().sort_values(["trade_date", "symbol"]).reset_index(drop=True)
    frame["symbol"] = frame["symbol"].map(_normalise_symbol)

    metadata = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "factor_count": len(factors),
        "factors": factors,
        "horizons": horizons,
        "rows": len(frame),
        "symbol_count": int(frame["symbol"].nunique()) if not frame.empty else 0,
        "date_count": int(frame["trade_date"].nunique()) if not frame.empty else 0,
        "feature_transform": "per-date MAD winsorized robust z-score after direction adjustment",
        "label_timing": "T+1 entry forward return from close(t+1) to close(t+1+h)",
        "point_in_time": False,
        "degraded": True,
        "degraded_reasons": [
            "research_pool is selected from today's active-stock coverage",
            "dataset currently uses non-PIT universes",
            "qfq OHLCV history may be revised after future corporate actions",
        ],
    }
    return frame, metadata


def _normalise_symbol(value: object) -> str:
    symbol = str(value).strip()
    if symbol.endswith(".0"):
        symbol = symbol[:-2]
    return symbol.zfill(6) if symbol.isdigit() and len(symbol) <= 6 else symbol


def run(args: argparse.Namespace) -> dict[str, Any]:
    requested = args.factors or list(BUILTIN_FACTOR_NAMES)
    unknown = sorted(set(requested) - set(BUILTIN_FACTOR_NAMES))
    if unknown:
        raise ValueError(f"Unknown factors: {', '.join(unknown)}")

    horizons = sorted(set(args.horizons or [5, 10]))
    warmup_start = args.start_date - timedelta(days=260)
    label_end = args.end_date + timedelta(days=max(horizons) * 3 + 10)
    started_at = time.time()

    init_db()
    with SessionLocal() as session:
        repository = MarketDataRepository(session)
        symbols = repository.covered_research_symbols(
            args.start_date,
            args.end_date,
            limit=args.pool_max_symbols,
        )
        if not symbols:
            raise ValueError("No eligible symbols for ML dataset")
        bars_started_at = time.time()
        bars = repository.daily_bars(symbols, warmup_start, label_end)
        bars_seconds = time.time() - bars_started_at

    if bars.empty:
        raise ValueError("No local daily bars for requested ML dataset")

    build_started_at = time.time()
    dataset, metadata = build_dataset(
        bars,
        start_date=args.start_date,
        end_date=args.end_date,
        factors=requested,
        horizons=horizons,
    )
    metadata.update(
        {
            "selected_symbol_count": len(symbols),
            "bar_rows": len(bars),
            "output": str(args.output),
            "timing_seconds": {
                "bars_load": round(bars_seconds, 3),
                "dataset_build": round(time.time() - build_started_at, 3),
                "total": round(time.time() - started_at, 3),
            },
        }
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(args.output, index=False, encoding="utf-8")
    metadata_output = args.metadata_output or args.output.with_suffix(args.output.suffix + ".metadata.json")
    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    metadata_output.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def main() -> int:
    metadata = run(parse_args())
    print(json.dumps(metadata, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
