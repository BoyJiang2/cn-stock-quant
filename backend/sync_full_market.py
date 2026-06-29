"""Resumable full A-share daily-bar sync runner."""

from __future__ import annotations

import argparse
import json
from datetime import date

from app.core.database import SessionLocal, init_db
from app.data.akshare_provider import AkShareProvider
from app.data.full_market import FullMarketSyncConfig, FullMarketSyncCoordinator
from app.data.repository import MarketDataRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.35)
    parser.add_argument("--max-failures", type=int, default=3)
    parser.add_argument("--retry-failed", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    init_db()
    while True:
        with SessionLocal() as session:
            repository = MarketDataRepository(session)
            coordinator = FullMarketSyncCoordinator(
                repository,
                AkShareProvider(),
                FullMarketSyncConfig(
                    batch_size=args.batch_size,
                    exchanges=("SH", "SZ", "BJ"),
                    max_failures=args.max_failures,
                    min_request_interval=args.interval,
                    exclude_risk_names=False,
                    retry_failed=args.retry_failed,
                ),
            )
            summary = coordinator.run_batch(args.start_date, args.end_date)
            progress = repository.full_market_sync_progress(
                args.start_date,
                args.end_date,
            )
        print(
            json.dumps(
                {
                    "processed": summary.processed,
                    "success": summary.success,
                    "empty": summary.empty,
                    "failed": summary.failed,
                    "skipped": summary.skipped,
                    "completed": summary.completed,
                    "blocked": summary.blocked,
                    "progress": progress,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if progress["remaining"] == 0 or summary.completed:
            return 0
        if summary.blocked or summary.processed == 0:
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
