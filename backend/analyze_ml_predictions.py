"""Diagnose offline ML prediction files.

The trainer tells us whether a model has statistical rank signal. This script
adds the missing investment diagnostics: score buckets, date coverage, and
feature importance. It is intentionally offline and writes small JSON/Markdown
reports; large prediction CSVs remain local artifacts.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from train_lightgbm_ranker import _normalise_symbol, rankic_by_date


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--label", default="fwd_5d")
    parser.add_argument("--score-column", default="score")
    parser.add_argument("--split")
    parser.add_argument("--n-buckets", type=int, default=5)
    parser.add_argument("--model-file", type=Path)
    parser.add_argument("--json-output", type=Path, default=Path("ml-prediction-diagnostics.json"))
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args()


def load_predictions(path: Path, *, label: str, score_column: str) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"symbol": "string"})
    required = {"trade_date", "symbol", score_column, label}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"predictions file is missing columns: {', '.join(sorted(missing))}")
    frame = frame.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    frame["symbol"] = frame["symbol"].map(_normalise_symbol)
    frame[score_column] = pd.to_numeric(frame[score_column], errors="coerce")
    frame[label] = pd.to_numeric(frame[label], errors="coerce")
    return frame.dropna(subset=["trade_date", "symbol", score_column, label])


def coverage_summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "rows": 0,
            "date_count": 0,
            "symbol_count": 0,
            "start_date": None,
            "end_date": None,
            "min_symbols_per_date": 0,
            "median_symbols_per_date": 0,
            "max_symbols_per_date": 0,
        }
    counts = frame.groupby("trade_date")["symbol"].nunique()
    return {
        "rows": int(len(frame)),
        "date_count": int(frame["trade_date"].nunique()),
        "symbol_count": int(frame["symbol"].nunique()),
        "start_date": str(min(frame["trade_date"])),
        "end_date": str(max(frame["trade_date"])),
        "min_symbols_per_date": int(counts.min()),
        "median_symbols_per_date": float(counts.median()),
        "max_symbols_per_date": int(counts.max()),
    }


def score_bucket_summary(
    frame: pd.DataFrame,
    *,
    label: str,
    score_column: str = "score",
    n_buckets: int = 5,
) -> list[dict[str, Any]]:
    if n_buckets < 2:
        raise ValueError("n_buckets must be >= 2")
    if frame.empty:
        return []

    bucketed_parts: list[pd.DataFrame] = []
    for _, group in frame.groupby("trade_date", sort=True):
        group = group.dropna(subset=[score_column, label]).copy()
        if len(group) < n_buckets:
            continue
        ranks = group[score_column].rank(method="first", pct=True)
        group["bucket"] = (ranks.mul(n_buckets).apply(math.ceil)).clip(1, n_buckets).astype(int)
        bucketed_parts.append(group)
    if not bucketed_parts:
        return []

    bucketed = pd.concat(bucketed_parts, ignore_index=True)
    rows: list[dict[str, Any]] = []
    for bucket, group in bucketed.groupby("bucket", sort=True):
        by_date = group.groupby("trade_date").agg(
            mean_return=(label, "mean"),
            mean_score=(score_column, "mean"),
            count=("symbol", "count"),
        )
        rows.append(
            {
                "bucket": int(bucket),
                "rows": int(len(group)),
                "date_count": int(group["trade_date"].nunique()),
                "avg_symbols_per_date": float(by_date["count"].mean()),
                "mean_score": float(by_date["mean_score"].mean()),
                "mean_return": float(by_date["mean_return"].mean()),
            }
        )
    return rows


def feature_importance(model_file: Path | None) -> list[dict[str, Any]]:
    if model_file is None:
        return []
    try:
        import lightgbm as lgb  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("LightGBM is required to read model feature importance") from exc
    booster = lgb.Booster(model_file=str(model_file))
    names = booster.feature_name()
    split = booster.feature_importance(importance_type="split")
    gain = booster.feature_importance(importance_type="gain")
    rows = [
        {"feature": name, "split": int(split_value), "gain": float(gain_value)}
        for name, split_value, gain_value in zip(names, split, gain)
    ]
    return sorted(rows, key=lambda item: (item["gain"], item["split"]), reverse=True)


def run(args: argparse.Namespace) -> dict[str, Any]:
    frame = load_predictions(args.predictions, label=args.label, score_column=args.score_column)
    if args.split:
        if "split" not in frame.columns:
            raise ValueError("--split was provided but predictions file has no split column")
        frame = frame[frame["split"] == args.split].copy()
    if frame.empty:
        raise ValueError("no prediction rows after filtering")

    rankic = rankic_by_date(frame, score_column=args.score_column, label=args.label)
    buckets = score_bucket_summary(
        frame,
        label=args.label,
        score_column=args.score_column,
        n_buckets=args.n_buckets,
    )
    diagnostics = {
        "predictions": str(args.predictions),
        "label": args.label,
        "score_column": args.score_column,
        "split": args.split,
        "coverage": coverage_summary(frame),
        "rankic": rankic,
        "score_buckets": buckets,
        "long_short_bucket_return": (
            buckets[-1]["mean_return"] - buckets[0]["mean_return"] if len(buckets) >= 2 else None
        ),
        "feature_importance": feature_importance(args.model_file),
    }

    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.markdown_output is not None:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(to_markdown(diagnostics), encoding="utf-8")
    return diagnostics


def to_markdown(diagnostics: dict[str, Any]) -> str:
    lines = [
        "# ML Prediction Diagnostics",
        "",
        f"- predictions: `{diagnostics['predictions']}`",
        f"- label: `{diagnostics['label']}`",
        f"- split: `{diagnostics['split']}`",
        "",
        "## Coverage",
        "",
    ]
    for key, value in diagnostics["coverage"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## RankIC",
            "",
        ]
    )
    for key, value in diagnostics["rankic"].items():
        lines.append(f"- {key}: {_fmt(value)}")
    lines.extend(
        [
            "",
            "## Score Buckets",
            "",
            "| Bucket | Rows | Dates | Avg names/date | Mean score | Mean return |",
            "| ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in diagnostics["score_buckets"]:
        lines.append(
            "| {bucket} | {rows} | {date_count} | {avg_symbols_per_date} | {mean_score} | {mean_return} |".format(
                bucket=row["bucket"],
                rows=row["rows"],
                date_count=row["date_count"],
                avg_symbols_per_date=_fmt(row["avg_symbols_per_date"]),
                mean_score=_fmt(row["mean_score"]),
                mean_return=_fmt(row["mean_return"]),
            )
        )
    lines.extend(
        [
            "",
            f"- long_short_bucket_return: {_fmt(diagnostics['long_short_bucket_return'])}",
            "",
            "## Feature Importance",
            "",
            "| Feature | Split | Gain |",
            "| --- | ---: | ---: |",
        ]
    )
    for row in diagnostics["feature_importance"][:30]:
        lines.append(f"| {row['feature']} | {row['split']} | {_fmt(row['gain'])} |")
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
    diagnostics = run(parse_args())
    print(json.dumps({k: diagnostics[k] for k in ["coverage", "rankic", "long_short_bucket_return"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
