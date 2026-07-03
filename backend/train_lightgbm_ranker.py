"""Train the first LightGBM-style factor ranking model.

This script intentionally stays outside the FastAPI runtime. It consumes the
CSV produced by ``build_ml_dataset.py`` and writes:

* prediction scores for validation/test rows;
* a JSON metrics report;
* an optional LightGBM model artifact.

The project can still run without LightGBM installed; only this CLI requires
the optional dependency.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd


INDEX_COLUMNS = {"trade_date", "symbol"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--label", default="fwd_5d")
    parser.add_argument("--train-start", required=True)
    parser.add_argument("--train-end", required=True)
    parser.add_argument("--valid-start", required=True)
    parser.add_argument("--valid-end", required=True)
    parser.add_argument("--test-start")
    parser.add_argument("--test-end")
    parser.add_argument("--num-boost-round", type=int, default=500)
    parser.add_argument("--early-stopping-rounds", type=int, default=50)
    parser.add_argument("--learning-rate", type=float, default=0.03)
    parser.add_argument("--num-leaves", type=int, default=31)
    parser.add_argument("--min-data-in-leaf", type=int, default=50)
    parser.add_argument("--predictions-output", type=Path, default=Path("lightgbm-predictions.csv"))
    parser.add_argument("--metrics-output", type=Path, default=Path("lightgbm-metrics.json"))
    parser.add_argument("--model-output", type=Path)
    return parser.parse_args()


def feature_columns(frame: pd.DataFrame, label: str) -> list[str]:
    if label not in frame.columns:
        raise ValueError(f"dataset missing label column: {label}")
    excluded = INDEX_COLUMNS | {label} | {
        column for column in frame.columns if column.startswith("fwd_")
    }
    features = [column for column in frame.columns if column not in excluded]
    if not features:
        raise ValueError("dataset has no feature columns")
    return features


def date_slice(
    frame: pd.DataFrame,
    *,
    start: str,
    end: str,
) -> pd.DataFrame:
    if "trade_date" not in frame.columns:
        raise ValueError("dataset missing trade_date column")
    dates = pd.to_datetime(frame["trade_date"])
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if start_ts > end_ts:
        raise ValueError("slice start must be <= end")
    return frame[(dates >= start_ts) & (dates <= end_ts)].copy()


def rankic_by_date(
    frame: pd.DataFrame,
    *,
    score_column: str = "score",
    label: str,
) -> dict[str, Any]:
    if frame.empty:
        return {"rankic_mean": None, "rankic_std": None, "rankic_ir": None, "n_dates": 0}
    values: list[float] = []
    for _, group in frame.groupby("trade_date", sort=True):
        if group[score_column].nunique(dropna=True) < 2 or group[label].nunique(dropna=True) < 2:
            continue
        corr = group[score_column].corr(group[label], method="spearman")
        if corr is not None and math.isfinite(float(corr)):
            values.append(float(corr))
    if not values:
        return {"rankic_mean": None, "rankic_std": None, "rankic_ir": None, "n_dates": 0}
    series = pd.Series(values, dtype=float)
    mean = float(series.mean())
    std = float(series.std(ddof=1)) if len(series) > 1 else 0.0
    return {
        "rankic_mean": mean,
        "rankic_std": std,
        "rankic_ir": (mean / std) if std > 0 else None,
        "n_dates": len(values),
    }


def top_bottom_return(
    frame: pd.DataFrame,
    *,
    score_column: str = "score",
    label: str,
    quantile: float = 0.2,
) -> dict[str, Any]:
    if frame.empty:
        return {"top_return": None, "bottom_return": None, "long_short_return": None}
    top_returns: list[float] = []
    bottom_returns: list[float] = []
    for _, group in frame.groupby("trade_date", sort=True):
        group = group.dropna(subset=[score_column, label]).sort_values(score_column)
        if len(group) < 5:
            continue
        n = max(1, int(len(group) * quantile))
        bottom_returns.append(float(group.head(n)[label].mean()))
        top_returns.append(float(group.tail(n)[label].mean()))
    if not top_returns or not bottom_returns:
        return {"top_return": None, "bottom_return": None, "long_short_return": None}
    top = float(pd.Series(top_returns).mean())
    bottom = float(pd.Series(bottom_returns).mean())
    return {
        "top_return": top,
        "bottom_return": bottom,
        "long_short_return": top - bottom,
    }


def _require_lightgbm():
    try:
        import lightgbm as lgb  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "LightGBM is not installed. Install backend requirements or run "
            "`pip install lightgbm` in the active environment before training."
        ) from exc
    return lgb


def _predict_frame(model, frame: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    out = frame[["trade_date", "symbol"]].copy()
    out["symbol"] = out["symbol"].map(_normalise_symbol)
    out["score"] = model.predict(frame[features])
    return out


def _normalise_symbol(value: object) -> str:
    symbol = str(value).strip()
    if symbol.endswith(".0"):
        symbol = symbol[:-2]
    return symbol.zfill(6) if symbol.isdigit() and len(symbol) <= 6 else symbol


def run(args: argparse.Namespace) -> dict[str, Any]:
    lgb = _require_lightgbm()
    frame = pd.read_csv(args.dataset, dtype={"symbol": "string"})
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    frame["symbol"] = frame["symbol"].map(_normalise_symbol)
    features = feature_columns(frame, args.label)

    train = date_slice(frame, start=args.train_start, end=args.train_end)
    valid = date_slice(frame, start=args.valid_start, end=args.valid_end)
    test = (
        date_slice(frame, start=args.test_start, end=args.test_end)
        if args.test_start and args.test_end
        else pd.DataFrame(columns=frame.columns)
    )
    for split_name, split in [("train", train), ("valid", valid)]:
        if split.empty:
            raise ValueError(f"{split_name} split is empty")

    train_set = lgb.Dataset(train[features], label=train[args.label], feature_name=features)
    valid_set = lgb.Dataset(valid[features], label=valid[args.label], reference=train_set)
    params = {
        "objective": "regression",
        "metric": "l2",
        "learning_rate": args.learning_rate,
        "num_leaves": args.num_leaves,
        "min_data_in_leaf": args.min_data_in_leaf,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 1,
        "verbosity": -1,
        "seed": 42,
    }
    model = lgb.train(
        params,
        train_set,
        num_boost_round=args.num_boost_round,
        valid_sets=[valid_set],
        valid_names=["valid"],
        callbacks=[
            lgb.early_stopping(args.early_stopping_rounds, verbose=False),
            lgb.log_evaluation(period=0),
        ],
    )

    scored_parts: list[pd.DataFrame] = []
    metrics: dict[str, Any] = {
        "dataset": str(args.dataset),
        "label": args.label,
        "features": features,
        "best_iteration": int(model.best_iteration or args.num_boost_round),
        "splits": {
            "train": {"start": args.train_start, "end": args.train_end, "rows": len(train)},
            "valid": {"start": args.valid_start, "end": args.valid_end, "rows": len(valid)},
            "test": {"start": args.test_start, "end": args.test_end, "rows": len(test)},
        },
    }
    for split_name, split in [("valid", valid), ("test", test)]:
        if split.empty:
            continue
        pred = _predict_frame(model, split, features)
        pred["split"] = split_name
        pred[args.label] = split[args.label].to_numpy()
        scored_parts.append(pred)
        metrics[split_name] = {
            **rankic_by_date(pred, label=args.label),
            **top_bottom_return(pred, label=args.label),
        }

    predictions = pd.concat(scored_parts, axis=0, ignore_index=True) if scored_parts else pd.DataFrame()
    args.predictions_output.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(args.predictions_output, index=False, encoding="utf-8")
    args.metrics_output.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_output.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.model_output is not None:
        args.model_output.parent.mkdir(parents=True, exist_ok=True)
        model.save_model(str(args.model_output))
    return metrics


def main() -> int:
    metrics = run(parse_args())
    print(json.dumps(metrics, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
