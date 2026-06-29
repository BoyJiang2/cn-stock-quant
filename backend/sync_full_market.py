"""Resumable full A-share daily-bar sync runner."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.database import SessionLocal, init_db
from app.data.akshare_provider import AkShareProvider
from app.data.full_market import (
    FullMarketSyncBatchSummary,
    FullMarketSyncConfig,
    FullMarketSyncCoordinator,
)
from app.data.repository import MarketDataRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.35)
    parser.add_argument("--max-failures", type=int, default=3)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path("full-market-sync.state.json"),
        help="JSON heartbeat file updated after every batch.",
    )
    return parser.parse_args()


def _summary_items(summary: FullMarketSyncBatchSummary) -> list[dict[str, Any]]:
    return [
        {
            "symbol": item.symbol,
            "status": item.status,
            "synced": item.synced,
            "message": item.message,
        }
        for item in summary.items
    ]


def full_market_exit_reason(
    summary: FullMarketSyncBatchSummary,
    progress: dict[str, Any],
) -> str:
    """Classify the next action for one emitted sync-runner report."""
    if int(progress.get("remaining", 0)) == 0 or summary.completed:
        return "completed"
    if summary.blocked:
        return "blocked"
    if summary.processed == 0:
        return "idle"
    return "continue"


def build_full_market_report(
    summary: FullMarketSyncBatchSummary,
    progress: dict[str, Any],
    *,
    start_date: date,
    end_date: date,
    config: FullMarketSyncConfig,
) -> dict[str, Any]:
    """Return one JSON-serializable background-runner status report."""
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "batch_size": config.batch_size,
        "exchanges": list(config.exchanges),
        "max_failures": config.max_failures,
        "min_request_interval": config.min_request_interval,
        "adjust": config.adjust,
        "retry_failed": config.retry_failed,
        "exit_reason": full_market_exit_reason(summary, progress),
        "processed": summary.processed,
        "success": summary.success,
        "empty": summary.empty,
        "failed": summary.failed,
        "skipped": summary.skipped,
        "completed": summary.completed,
        "blocked": summary.blocked,
        "items": _summary_items(summary),
        "progress": progress,
    }


def _state_payload(
    *,
    start_date: date,
    end_date: date,
    summary: Any | None,
    progress: dict,
    exit_reason: str | None,
) -> dict:
    payload = {
        "pid": os.getpid(),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "progress": progress,
        "exit_reason": exit_reason,
    }
    if summary is not None:
        payload["last_batch"] = {
            "processed": summary.processed,
            "success": summary.success,
            "empty": summary.empty,
            "failed": summary.failed,
            "skipped": summary.skipped,
            "completed": summary.completed,
            "blocked": summary.blocked,
            "items": _summary_items(summary),
        }
    return payload


def _write_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    init_db()
    state_file = args.state_file
    while True:
        config = FullMarketSyncConfig(
            batch_size=args.batch_size,
            exchanges=("SH", "SZ", "BJ"),
            max_failures=args.max_failures,
            min_request_interval=args.interval,
            exclude_risk_names=False,
            retry_failed=args.retry_failed,
        )
        with SessionLocal() as session:
            repository = MarketDataRepository(session)
            coordinator = FullMarketSyncCoordinator(
                repository,
                AkShareProvider(),
                config,
            )
            summary = coordinator.run_batch(args.start_date, args.end_date)
            progress = repository.full_market_sync_progress(
                args.start_date,
                args.end_date,
            )
        line = build_full_market_report(
            summary,
            progress,
            start_date=args.start_date,
            end_date=args.end_date,
            config=config,
        )
        print(json.dumps(line, ensure_ascii=False), flush=True)
        if line["exit_reason"] == "completed":
            _write_state(
                state_file,
                _state_payload(
                    start_date=args.start_date,
                    end_date=args.end_date,
                    summary=summary,
                    progress=progress,
                    exit_reason="completed",
                ),
            )
            return 0
        if line["exit_reason"] in {"blocked", "idle"}:
            _write_state(
                state_file,
                _state_payload(
                    start_date=args.start_date,
                    end_date=args.end_date,
                    summary=summary,
                    progress=progress,
                    exit_reason=line["exit_reason"],
                ),
            )
            return 2
        _write_state(
            state_file,
            _state_payload(
                start_date=args.start_date,
                end_date=args.end_date,
                summary=summary,
                progress=progress,
                exit_reason=None,
            ),
        )


if __name__ == "__main__":
    raise SystemExit(main())
