from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as datetime_time
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.database import SessionLocal, init_db
from app.data.akshare_news_provider import AkShareNewsProvider
from app.data.repository import MarketDataRepository


@dataclass
class NewsSyncItem:
    symbol: str
    status: str
    synced: int = 0
    news_rows: int = 0
    risk_rows: int = 0
    first_published_at: str | None = None
    last_published_at: str | None = None
    sources: list[str] = field(default_factory=list)
    message: str = ""


@dataclass
class NewsSyncSummary:
    total: int
    processed: int = 0
    success: int = 0
    empty: int = 0
    failed: int = 0
    dry_run: bool = False
    items: list[NewsSyncItem] = field(default_factory=list)

    @property
    def news_rows(self) -> int:
        return sum(item.news_rows for item in self.items)

    @property
    def risk_rows(self) -> int:
        return sum(item.risk_rows for item in self.items)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch sync stock news and build a coverage report.")
    parser.add_argument("--symbol-source", choices=["manual", "research_pool"], default="manual")
    parser.add_argument("--symbols", default="", help="Comma/space separated symbols or stock names for manual mode.")
    parser.add_argument("--symbols-file", type=Path, help="Optional file with one symbol/name per line.")
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--start-at", type=_parse_datetime)
    parser.add_argument("--end-at", type=_parse_datetime)
    parser.add_argument("--pool-max-symbols", type=int, default=300)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--min-request-interval", type=float, default=0.35)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--json-output", type=Path, default=Path("backend/artifacts/news/news-sync-report.json"))
    parser.add_argument("--markdown-output", type=Path, default=Path("backend/artifacts/news/news-sync-report.md"))
    return parser.parse_args()


def run_news_sync(args: argparse.Namespace) -> dict[str, Any]:
    init_db()
    start_at, end_at = _effective_window(args)
    with SessionLocal() as session:
        repository = MarketDataRepository(session)
        symbols = select_news_symbols(repository, args)
        summary = sync_news_symbols(
            repository=repository,
            provider=AkShareNewsProvider(),
            symbols=symbols,
            start_at=start_at,
            end_at=end_at,
            batch_size=args.batch_size,
            min_request_interval=args.min_request_interval,
            dry_run=args.dry_run,
            stop_on_error=args.stop_on_error,
        )
    return build_news_sync_report(
        summary,
        symbol_source=args.symbol_source,
        start_at=start_at,
        end_at=end_at,
        pool_max_symbols=args.pool_max_symbols,
    )


def select_news_symbols(repository: MarketDataRepository, args: argparse.Namespace) -> list[str]:
    if args.symbol_source == "research_pool":
        start_at, end_at = _effective_window(args)
        start_date = start_at.date() if start_at is not None else date(2024, 1, 1)
        end_date = end_at.date() if end_at is not None else date.today()
        symbols = repository.select_research_symbols(
            start_date,
            end_date,
            limit=args.pool_max_symbols,
        )
        if not symbols:
            symbols = repository.covered_research_symbols(
                start_date,
                end_date,
                limit=args.pool_max_symbols,
            )
        return symbols

    raw_symbols = _split_symbols(args.symbols)
    if args.symbols_file is not None:
        raw_symbols.extend(_split_symbols(args.symbols_file.read_text(encoding="utf-8")))
    if not raw_symbols:
        raise ValueError("manual mode requires --symbols or --symbols-file")
    return repository.resolve_symbols(raw_symbols)


def sync_news_symbols(
    *,
    repository: MarketDataRepository,
    provider: AkShareNewsProvider,
    symbols: list[str],
    start_at: datetime | None,
    end_at: datetime | None,
    batch_size: int = 20,
    min_request_interval: float = 0.35,
    dry_run: bool = False,
    stop_on_error: bool = False,
) -> NewsSyncSummary:
    summary = NewsSyncSummary(total=len(symbols), dry_run=dry_run)
    effective_batch_size = max(1, int(batch_size))
    for offset in range(0, len(symbols), effective_batch_size):
        batch = symbols[offset : offset + effective_batch_size]
        for symbol in batch:
            if dry_run:
                item = NewsSyncItem(symbol=symbol, status="dry_run")
            else:
                item = _sync_one_symbol(
                    repository=repository,
                    provider=provider,
                    symbol=symbol,
                    start_at=start_at,
                    end_at=end_at,
                )
            summary.items.append(item)
            summary.processed += 1
            if item.status == "success":
                summary.success += 1
            elif item.status == "empty":
                summary.empty += 1
            elif item.status == "failed":
                summary.failed += 1
                if stop_on_error:
                    return summary
            if min_request_interval > 0 and not dry_run:
                time.sleep(min_request_interval)
    return summary


def build_news_sync_report(
    summary: NewsSyncSummary,
    *,
    symbol_source: str,
    start_at: datetime | None,
    end_at: datetime | None,
    pool_max_symbols: int,
) -> dict[str, Any]:
    return {
        "metadata": {
            "generated_at": datetime.utcnow().isoformat(),
            "symbol_source": symbol_source,
            "start_at": start_at.isoformat() if start_at else None,
            "end_at": end_at.isoformat() if end_at else None,
            "pool_max_symbols": pool_max_symbols,
            "dry_run": summary.dry_run,
        },
        "summary": {
            "total": summary.total,
            "processed": summary.processed,
            "success": summary.success,
            "empty": summary.empty,
            "failed": summary.failed,
            "news_rows": summary.news_rows,
            "risk_rows": summary.risk_rows,
            "symbols_with_news": sum(1 for item in summary.items if item.news_rows > 0),
            "symbols_with_risk_news": sum(1 for item in summary.items if item.risk_rows > 0),
        },
        "items": [asdict(item) for item in summary.items],
    }


def to_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    metadata = report["metadata"]
    lines = [
        "# News Sync Coverage Report",
        "",
        f"- generated_at: `{metadata['generated_at']}`",
        f"- symbol_source: `{metadata['symbol_source']}`",
        f"- window: `{metadata['start_at']}` to `{metadata['end_at']}`",
        f"- dry_run: `{metadata['dry_run']}`",
        "",
        "## Summary",
        "",
        f"- total: `{summary['total']}`",
        f"- processed: `{summary['processed']}`",
        f"- success: `{summary['success']}`",
        f"- empty: `{summary['empty']}`",
        f"- failed: `{summary['failed']}`",
        f"- news_rows: `{summary['news_rows']}`",
        f"- risk_rows: `{summary['risk_rows']}`",
        f"- symbols_with_news: `{summary['symbols_with_news']}`",
        f"- symbols_with_risk_news: `{summary['symbols_with_risk_news']}`",
        "",
        "## Items",
        "",
        "| Symbol | Status | Synced | News | Risk | First | Last | Sources | Message |",
        "| --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |",
    ]
    for item in report["items"]:
        lines.append(
            "| {symbol} | {status} | {synced} | {news_rows} | {risk_rows} | {first} | {last} | {sources} | {message} |".format(
                symbol=item["symbol"],
                status=item["status"],
                synced=item["synced"],
                news_rows=item["news_rows"],
                risk_rows=item["risk_rows"],
                first=item["first_published_at"] or "",
                last=item["last_published_at"] or "",
                sources=", ".join(item["sources"]),
                message=str(item["message"]).replace("|", "/")[:120],
            )
        )
    lines.append("")
    return "\n".join(lines)


def _sync_one_symbol(
    *,
    repository: MarketDataRepository,
    provider: AkShareNewsProvider,
    symbol: str,
    start_at: datetime | None,
    end_at: datetime | None,
) -> NewsSyncItem:
    try:
        items = provider.stock_news(symbol, start_at=start_at, end_at=end_at)
    except Exception as exc:
        repository.create_sync_job(
            "news",
            symbol,
            "failed",
            message=str(exc),
            start_date=_datetime_to_date(start_at),
            end_date=_datetime_to_date(end_at),
        )
        return NewsSyncItem(symbol=symbol, status="failed", message=str(exc))

    if items.empty:
        repository.create_sync_job(
            "news",
            symbol,
            "empty",
            start_date=_datetime_to_date(start_at),
            end_date=_datetime_to_date(end_at),
        )
        return NewsSyncItem(symbol=symbol, status="empty", message="no news")

    synced = repository.upsert_news_items(items)
    repository.create_sync_job(
        "news",
        symbol,
        "success",
        records=synced,
        start_date=_datetime_to_date(start_at),
        end_date=_datetime_to_date(end_at),
    )
    coverage = _coverage_item(symbol, items)
    coverage.synced = synced
    return coverage


def _coverage_item(symbol: str, frame: pd.DataFrame) -> NewsSyncItem:
    if frame.empty:
        return NewsSyncItem(symbol=symbol, status="empty", message="no news")
    dates = pd.to_datetime(frame["published_at"], errors="coerce").dropna()
    sources = sorted(frame.get("source", pd.Series(dtype=str)).dropna().astype(str).unique().tolist())
    risk_mask = _risk_mask(frame)
    return NewsSyncItem(
        symbol=symbol,
        status="success",
        news_rows=int(len(frame)),
        risk_rows=int(risk_mask.sum()),
        first_published_at=dates.min().isoformat() if not dates.empty else None,
        last_published_at=dates.max().isoformat() if not dates.empty else None,
        sources=sources,
    )


def _risk_mask(frame: pd.DataFrame) -> pd.Series:
    event = frame.get("event_type", pd.Series("", index=frame.index)).astype(str).str.lower()
    label = frame.get("sentiment_label", pd.Series("", index=frame.index)).astype(str).str.lower()
    score = pd.to_numeric(frame.get("sentiment_score", pd.Series(pd.NA, index=frame.index)), errors="coerce")
    return event.isin({"negative_news", "risk_news"}) | label.isin({"negative", "risk"}) | (score <= -0.2)


def _effective_window(args: argparse.Namespace) -> tuple[datetime | None, datetime | None]:
    start_at = args.start_at
    end_at = args.end_at
    if args.start_date is not None:
        start_at = datetime.combine(args.start_date, datetime_time.min)
    if args.end_date is not None:
        end_at = datetime.combine(args.end_date, datetime_time.max)
    if start_at is not None and end_at is not None and start_at > end_at:
        raise ValueError("start must be <= end")
    return start_at, end_at


def _split_symbols(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，;；\s]+", value) if item.strip()]


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.combine(date.fromisoformat(value), datetime_time.min)


def _datetime_to_date(value: datetime | None) -> date | None:
    return value.date() if value is not None else None


def main() -> int:
    args = parse_args()
    report = run_news_sync(args)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown_output is not None:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(to_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
