import pytest

from walk_forward_lightgbm import walk_forward_windows


def test_walk_forward_windows_roll_monthly_and_clip_requested_dates():
    windows = walk_forward_windows(
        test_start="2026-01-05",
        test_end="2026-03-10",
        train_months=12,
        valid_months=2,
        test_months=1,
        embargo_days=15,
    )

    assert [window["test_start"] for window in windows] == [
        "2026-01-05",
        "2026-02-01",
        "2026-03-01",
    ]
    assert [window["test_end"] for window in windows] == [
        "2026-01-31",
        "2026-02-28",
        "2026-03-10",
    ]
    assert windows[0]["train_start"] == "2024-11-01"
    assert windows[0]["train_end"] == "2025-10-16"
    assert windows[0]["valid_start"] == "2025-11-01"
    assert windows[0]["valid_end"] == "2025-12-16"


def test_walk_forward_windows_reject_invalid_ranges():
    with pytest.raises(ValueError):
        walk_forward_windows(
            test_start="2026-02-01",
            test_end="2026-01-01",
            train_months=12,
            valid_months=2,
            test_months=1,
        )

    with pytest.raises(ValueError):
        walk_forward_windows(
            test_start="2026-01-01",
            test_end="2026-01-31",
            train_months=12,
            valid_months=2,
            test_months=1,
            embargo_days=-1,
        )
