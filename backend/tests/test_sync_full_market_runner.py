from datetime import date

from app.data.full_market import (
    FullMarketSyncBatchSummary,
    FullMarketSyncConfig,
    FullMarketSyncItem,
)
from sync_full_market import build_full_market_report, full_market_exit_reason


START = date(2024, 1, 2)
END = date(2024, 1, 31)


def test_full_market_runner_report_includes_item_level_diagnostics():
    summary = FullMarketSyncBatchSummary(
        total=2,
        processed=2,
        success=1,
        empty=0,
        failed=1,
        skipped=0,
        completed=False,
        blocked=False,
        items=[
            FullMarketSyncItem("000001", "success", synced=22),
            FullMarketSyncItem("000002", "failed", message="provider timeout"),
        ],
    )
    progress = {"total": 3, "covered": 1, "remaining": 2, "percent": 33.33}
    config = FullMarketSyncConfig(
        batch_size=2,
        max_failures=3,
        min_request_interval=0.35,
        retry_failed=True,
    )

    report = build_full_market_report(
        summary,
        progress,
        start_date=START,
        end_date=END,
        config=config,
    )

    assert report["exit_reason"] == "continue"
    assert report["start_date"] == "2024-01-02"
    assert report["end_date"] == "2024-01-31"
    assert report["retry_failed"] is True
    assert report["items"] == [
        {"symbol": "000001", "status": "success", "synced": 22, "message": ""},
        {
            "symbol": "000002",
            "status": "failed",
            "synced": 0,
            "message": "provider timeout",
        },
    ]


def test_full_market_runner_exit_reason_distinguishes_blocked_idle_and_completed():
    blocked = FullMarketSyncBatchSummary(
        total=1,
        processed=0,
        success=0,
        empty=0,
        failed=0,
        skipped=1,
        completed=False,
        blocked=True,
        items=[FullMarketSyncItem("000001", "skipped", message="circuit breaker open")],
    )
    idle = FullMarketSyncBatchSummary(
        total=0,
        processed=0,
        success=0,
        empty=0,
        failed=0,
        skipped=0,
        completed=False,
        blocked=False,
    )
    completed = FullMarketSyncBatchSummary(
        total=1,
        processed=1,
        success=1,
        empty=0,
        failed=0,
        skipped=0,
        completed=False,
        blocked=False,
        items=[FullMarketSyncItem("000001", "success", synced=22)],
    )

    assert (
        full_market_exit_reason(
            blocked,
            {"total": 1, "covered": 0, "remaining": 1, "percent": 0.0},
        )
        == "blocked"
    )
    assert (
        full_market_exit_reason(
            idle,
            {"total": 1, "covered": 0, "remaining": 1, "percent": 0.0},
        )
        == "idle"
    )
    assert (
        full_market_exit_reason(
            completed,
            {"total": 1, "covered": 1, "remaining": 0, "percent": 100.0},
        )
        == "completed"
    )
