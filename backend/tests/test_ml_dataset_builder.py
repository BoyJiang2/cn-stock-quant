from datetime import date, timedelta

import pandas as pd

from build_ml_dataset import build_dataset


def test_build_ml_dataset_emits_features_and_t1_labels():
    start = date(2024, 1, 1)
    rows = []
    for i in range(90):
        for symbol, close, amount in [
            ("000001", 10.0 + i * 0.1, 100_000_000 + i * 100_000),
            ("600000", 20.0 - i * 0.05, 120_000_000 + (i % 5) * 200_000),
            ("300001", 15.0 + (i % 10) * 0.2, 130_000_000 + (i % 7) * 300_000),
        ]:
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": start + timedelta(days=i),
                    "open": close * 0.99,
                    "high": close * 1.01,
                    "low": close * 0.98,
                    "close": close,
                    "volume": 100_000,
                    "amount": amount,
                }
            )
    bars = pd.DataFrame(rows)

    dataset, metadata = build_dataset(
        bars,
        start_date=start + timedelta(days=65),
        end_date=start + timedelta(days=80),
        factors=["momentum_20d", "amount_volatility_20d"],
        horizons=[5],
    )

    assert not dataset.empty
    assert list(dataset.columns) == [
        "trade_date",
        "symbol",
        "momentum_20d",
        "amount_volatility_20d",
        "fwd_5d",
    ]
    assert metadata["factor_count"] == 2
    assert metadata["rows"] == len(dataset)
    assert metadata["label_timing"].startswith("T+1 entry")
    assert dataset["trade_date"].min() >= start + timedelta(days=65)
    assert dataset["trade_date"].max() <= start + timedelta(days=80)
    assert dataset["symbol"].str.len().eq(6).all()
