"""Generate walk-forward LightGBM predictions from an ML factor dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from train_lightgbm_ranker import (
    _normalise_symbol,
    _predict_frame,
    _require_lightgbm,
    date_slice,
    feature_columns,
    rankic_by_date,
    top_bottom_return,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--label", default="fwd_5d")
    parser.add_argument("--test-start", required=True)
    parser.add_argument("--test-end", required=True)
    parser.add_argument("--train-months", type=int, default=12)
    parser.add_argument("--valid-months", type=int, default=2)
    parser.add_argument("--test-months", type=int, default=1)
    parser.add_argument(
        "--embargo-days",
        type=int,
        default=15,
        help="Calendar-day gap between train/valid/test windows to avoid label overlap.",
    )
    parser.add_argument("--num-boost-round", type=int, default=300)
    parser.add_argument("--early-stopping-rounds", type=int, default=30)
    parser.add_argument("--learning-rate", type=float, default=0.03)
    parser.add_argument("--num-leaves", type=int, default=31)
    parser.add_argument("--min-data-in-leaf", type=int, default=50)
    parser.add_argument("--predictions-output", type=Path, default=Path("walk-forward-predictions.csv"))
    parser.add_argument("--metrics-output", type=Path, default=Path("walk-forward-metrics.json"))
    parser.add_argument("--model-dir", type=Path)
    return parser.parse_args()


def walk_forward_windows(
    *,
    test_start: str,
    test_end: str,
    train_months: int,
    valid_months: int,
    test_months: int,
    embargo_days: int = 15,
) -> list[dict[str, str]]:
    if train_months < 1 or valid_months < 1 or test_months < 1:
        raise ValueError("train_months, valid_months, and test_months must be positive")
    if embargo_days < 0:
        raise ValueError("embargo_days must be >= 0")
    requested_start = pd.Timestamp(test_start)
    requested_end = pd.Timestamp(test_end)
    if requested_start > requested_end:
        raise ValueError("test_start must be <= test_end")

    cursor = requested_start.replace(day=1)
    windows: list[dict[str, str]] = []
    while cursor <= requested_end:
        window_start = max(cursor, requested_start)
        window_end = min(cursor + pd.DateOffset(months=test_months) - pd.Timedelta(days=1), requested_end)
        valid_start = cursor - pd.DateOffset(months=valid_months)
        valid_end = cursor - pd.Timedelta(days=embargo_days + 1)
        train_start = valid_start - pd.DateOffset(months=train_months)
        train_end = valid_start - pd.Timedelta(days=embargo_days + 1)
        windows.append(
            {
                "train_start": _date_str(train_start),
                "train_end": _date_str(train_end),
                "valid_start": _date_str(valid_start),
                "valid_end": _date_str(valid_end),
                "test_start": _date_str(window_start),
                "test_end": _date_str(window_end),
            }
        )
        cursor = cursor + pd.DateOffset(months=test_months)
    return windows


def run(args: argparse.Namespace) -> dict[str, Any]:
    lgb = _require_lightgbm()
    frame = pd.read_csv(args.dataset, dtype={"symbol": "string"})
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    frame["symbol"] = frame["symbol"].map(_normalise_symbol)
    features = feature_columns(frame, args.label)

    windows = walk_forward_windows(
        test_start=args.test_start,
        test_end=args.test_end,
        train_months=args.train_months,
        valid_months=args.valid_months,
        test_months=args.test_months,
        embargo_days=args.embargo_days,
    )
    predictions: list[pd.DataFrame] = []
    window_metrics: list[dict[str, Any]] = []

    for index, window in enumerate(windows, start=1):
        train = date_slice(frame, start=window["train_start"], end=window["train_end"])
        valid = date_slice(frame, start=window["valid_start"], end=window["valid_end"])
        test = date_slice(frame, start=window["test_start"], end=window["test_end"])
        if train.empty or valid.empty or test.empty:
            window_metrics.append(
                {
                    "window_index": index,
                    **window,
                    "skipped": True,
                    "reason": _skip_reason(train, valid, test),
                    "rows": {"train": len(train), "valid": len(valid), "test": len(test)},
                }
            )
            continue

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
            "seed": 42 + index,
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
        pred = _predict_frame(model, test, features)
        pred["split"] = "walk_forward_test"
        pred["model_run_id"] = f"wf_{index:03d}_{window['test_start']}_{window['test_end']}"
        pred[args.label] = test[args.label].to_numpy()
        predictions.append(pred)
        window_metrics.append(
            {
                "window_index": index,
                **window,
                "skipped": False,
                "best_iteration": int(model.best_iteration or args.num_boost_round),
                "rows": {"train": len(train), "valid": len(valid), "test": len(test)},
                "test": {
                    **rankic_by_date(pred, label=args.label),
                    **top_bottom_return(pred, label=args.label),
                },
            }
        )
        if args.model_dir is not None:
            args.model_dir.mkdir(parents=True, exist_ok=True)
            model.save_model(str(args.model_dir / f"{pred['model_run_id'].iloc[0]}.txt"))

    combined = pd.concat(predictions, axis=0, ignore_index=True) if predictions else pd.DataFrame()
    args.predictions_output.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(args.predictions_output, index=False, encoding="utf-8")
    metrics = {
        "dataset": str(args.dataset),
        "label": args.label,
        "features": features,
        "test_start": args.test_start,
        "test_end": args.test_end,
        "train_months": args.train_months,
        "valid_months": args.valid_months,
        "test_months": args.test_months,
        "embargo_days": args.embargo_days,
        "window_count": len(windows),
        "completed_window_count": len(predictions),
        "prediction_rows": len(combined),
        "overall": (
            {
                **rankic_by_date(combined, label=args.label),
                **top_bottom_return(combined, label=args.label),
            }
            if not combined.empty
            else {}
        ),
        "windows": window_metrics,
    }
    args.metrics_output.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_output.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metrics


def _skip_reason(train: pd.DataFrame, valid: pd.DataFrame, test: pd.DataFrame) -> str:
    missing = []
    if train.empty:
        missing.append("train")
    if valid.empty:
        missing.append("valid")
    if test.empty:
        missing.append("test")
    return f"empty {'/'.join(missing)} split"


def _date_str(value: pd.Timestamp) -> str:
    return value.date().isoformat()


def main() -> int:
    metrics = run(parse_args())
    print(
        json.dumps(
            {
                "window_count": metrics["window_count"],
                "completed_window_count": metrics["completed_window_count"],
                "prediction_rows": metrics["prediction_rows"],
                "overall": metrics["overall"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
