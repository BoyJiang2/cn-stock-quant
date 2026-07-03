import argparse
from datetime import date
from pathlib import Path

from watch_full_market_sync import build_runner_command, should_stop_watching


def test_build_runner_command_includes_retry_flag_when_requested():
    args = argparse.Namespace(
        runner=Path("sync_full_market.py"),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 18),
        batch_size=20,
        interval=0.2,
        max_failures=3,
        state_file=Path("state.json"),
        retry_failed=True,
    )

    command = build_runner_command(args)

    assert command[1] == "sync_full_market.py"
    assert command[-1] == "--retry-failed"
    assert "--state-file" in command
    assert "state.json" in command


def test_watcher_stops_when_state_completed_or_remaining_zero():
    assert should_stop_watching(
        {"exit_reason": "completed", "progress": {"remaining": 0}},
        0,
    ) == (True, "completed")
    assert should_stop_watching(
        {"exit_reason": None, "progress": {"remaining": 0}},
        2,
    ) == (True, "completed")


def test_watcher_restarts_blocked_or_missing_state():
    assert should_stop_watching(
        {"exit_reason": "blocked", "progress": {"remaining": 10}},
        2,
    ) == (False, "blocked")
    assert should_stop_watching(None, 1) == (False, "state-missing")


def test_watcher_stops_on_clean_non_continue_reason():
    assert should_stop_watching(
        {"exit_reason": "idle", "progress": {"remaining": 3}},
        0,
    ) == (True, "idle")
