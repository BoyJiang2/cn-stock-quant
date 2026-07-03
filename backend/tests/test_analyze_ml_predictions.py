import math
from pathlib import Path

import pandas as pd

from analyze_ml_predictions import (
    coverage_summary,
    load_predictions,
    score_bucket_summary,
)


def test_load_predictions_normalises_symbols(tmp_path: Path):
    path = tmp_path / "predictions.csv"
    path.write_text(
        "\n".join(
            [
                "trade_date,symbol,score,fwd_5d",
                "2026-01-05,1,0.1,0.01",
                "2026-01-05,600000,0.2,0.02",
            ]
        ),
        encoding="utf-8",
    )

    frame = load_predictions(path, label="fwd_5d", score_column="score")

    assert frame["symbol"].tolist() == ["000001", "600000"]


def test_score_bucket_summary_uses_daily_score_ranks():
    frame = pd.DataFrame(
        {
            "trade_date": ["2026-01-05"] * 10,
            "symbol": [f"{i:06d}" for i in range(10)],
            "score": list(range(10)),
            "fwd_5d": list(range(10)),
        }
    )

    buckets = score_bucket_summary(frame, label="fwd_5d", n_buckets=5)

    assert len(buckets) == 5
    assert buckets[0]["bucket"] == 1
    assert buckets[-1]["bucket"] == 5
    assert math.isclose(buckets[-1]["mean_return"] - buckets[0]["mean_return"], 8.0)


def test_coverage_summary_reports_date_and_symbol_counts():
    frame = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-05", "2026-01-05", "2026-01-06"]).date,
            "symbol": ["000001", "000002", "000001"],
            "score": [0.1, 0.2, 0.3],
            "fwd_5d": [0.01, 0.02, 0.03],
        }
    )

    summary = coverage_summary(frame)

    assert summary["rows"] == 3
    assert summary["date_count"] == 2
    assert summary["symbol_count"] == 2
    assert summary["min_symbols_per_date"] == 1
    assert summary["max_symbols_per_date"] == 2
