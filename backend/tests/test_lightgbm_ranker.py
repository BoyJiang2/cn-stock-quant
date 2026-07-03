import math

import pandas as pd

from train_lightgbm_ranker import (
    date_slice,
    feature_columns,
    _normalise_symbol,
    rankic_by_date,
    top_bottom_return,
)


def test_feature_columns_excludes_index_and_all_forward_labels():
    frame = pd.DataFrame(
        {
            "trade_date": ["2025-01-02"],
            "symbol": ["000001"],
            "factor_a": [1.0],
            "factor_b": [2.0],
            "fwd_5d": [0.01],
            "fwd_10d": [0.02],
        }
    )

    assert feature_columns(frame, "fwd_5d") == ["factor_a", "factor_b"]


def test_date_slice_is_inclusive_and_rejects_reverse_range():
    frame = pd.DataFrame(
        {
            "trade_date": ["2025-01-01", "2025-01-02", "2025-01-03"],
            "symbol": ["A", "A", "A"],
            "factor": [1, 2, 3],
            "fwd_5d": [0.1, 0.2, 0.3],
        }
    )

    sliced = date_slice(frame, start="2025-01-02", end="2025-01-03")
    assert sliced["factor"].tolist() == [2, 3]

    try:
        date_slice(frame, start="2025-01-03", end="2025-01-02")
    except ValueError as exc:
        assert "start" in str(exc)
    else:
        raise AssertionError("reverse date range should fail")


def test_rankic_and_top_bottom_return_by_date():
    frame = pd.DataFrame(
        {
            "trade_date": ["2025-01-02"] * 5 + ["2025-01-03"] * 5,
            "symbol": [f"S{i}" for i in range(10)],
            "score": [1, 2, 3, 4, 5, 5, 4, 3, 2, 1],
            "fwd_5d": [1, 2, 3, 4, 5, 5, 4, 3, 2, 1],
        }
    )

    rankic = rankic_by_date(frame, label="fwd_5d")
    spread = top_bottom_return(frame, label="fwd_5d", quantile=0.4)

    assert math.isclose(rankic["rankic_mean"], 1.0)
    assert rankic["n_dates"] == 2
    assert math.isclose(spread["long_short_return"], 3.0)


def test_normalise_symbol_preserves_a_share_width():
    assert _normalise_symbol("1") == "000001"
    assert _normalise_symbol("600000") == "600000"
    assert _normalise_symbol("7.0") == "000007"
