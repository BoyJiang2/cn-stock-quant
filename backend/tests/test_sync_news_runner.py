from argparse import Namespace
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.data.repository import MarketDataRepository
from app.models.entities import Base, DailyBar, Stock, SyncJob
from sync_news import build_news_sync_report, select_news_symbols, sync_news_symbols, to_markdown


def make_repository() -> MarketDataRepository:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return MarketDataRepository(Session(engine))


class FakeNewsProvider:
    def __init__(self, failures: set[str] | None = None):
        self.failures = failures or set()
        self.calls: list[str] = []

    def stock_news(self, symbol, *, start_at=None, end_at=None):
        self.calls.append(symbol)
        if symbol in self.failures:
            raise RuntimeError("provider timeout")
        if symbol == "600000":
            return pd.DataFrame(columns=["source", "source_id", "symbol", "title", "published_at", "fetched_at"])
        return pd.DataFrame(
            [
                {
                    "source": "eastmoney_stock_news",
                    "source_id": f"{symbol}-risk-1",
                    "symbol": symbol,
                    "title": "公司收到监管函",
                    "body": "风险提示",
                    "url": "https://example.com/risk",
                    "event_type": "risk_news",
                    "sentiment_label": "risk",
                    "sentiment_score": -0.4,
                    "relevance_score": 1.0,
                    "published_at": datetime(2026, 7, 1, 9, 30),
                    "fetched_at": datetime(2026, 7, 1, 10, 0),
                    "raw": {"id": 1},
                },
                {
                    "source": "eastmoney_stock_news",
                    "source_id": f"{symbol}-plain-1",
                    "symbol": symbol,
                    "title": "公司发布新产品",
                    "body": "",
                    "url": "https://example.com/plain",
                    "event_type": "stock_news",
                    "sentiment_label": "",
                    "sentiment_score": None,
                    "relevance_score": 1.0,
                    "published_at": datetime(2026, 7, 2, 9, 30),
                    "fetched_at": datetime(2026, 7, 2, 10, 0),
                    "raw": {"id": 2},
                },
            ]
        )


def seed_stock(repository: MarketDataRepository, symbol: str, name: str = "Name", exchange: str = "SZ") -> None:
    repository.upsert_stocks(pd.DataFrame([{"symbol": symbol, "name": name, "exchange": exchange, "status": "active"}]))


def seed_daily_bars(repository: MarketDataRepository, symbol: str, start: date, days: int) -> None:
    rows = []
    for offset in range(days):
        trade_date = start + timedelta(days=offset)
        rows.append(
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.1,
                "volume": 1000,
                "amount": 10000,
                "adj": "qfq",
            }
        )
    repository.replace_daily_bars(symbol, start, start + timedelta(days=days - 1), pd.DataFrame(rows))


def test_sync_news_symbols_persists_news_and_reports_coverage():
    repository = make_repository()
    for symbol in ("000001", "600000", "000002"):
        seed_stock(repository, symbol)
    provider = FakeNewsProvider(failures={"000002"})

    summary = sync_news_symbols(
        repository=repository,
        provider=provider,
        symbols=["000001", "600000", "000002"],
        start_at=datetime(2026, 7, 1),
        end_at=datetime(2026, 7, 3),
        batch_size=2,
        min_request_interval=0,
    )
    report = build_news_sync_report(
        summary,
        symbol_source="manual",
        start_at=datetime(2026, 7, 1),
        end_at=datetime(2026, 7, 3),
        pool_max_symbols=300,
    )

    assert summary.processed == 3
    assert summary.success == 1
    assert summary.empty == 1
    assert summary.failed == 1
    assert report["summary"]["news_rows"] == 2
    assert report["summary"]["risk_rows"] == 1
    assert report["summary"]["symbols_with_news"] == 1
    assert repository.news_items(symbol="000001").shape[0] == 2
    jobs = repository.session.query(SyncJob).order_by(SyncJob.id).all()
    assert [job.status for job in jobs] == ["success", "empty", "failed"]


def test_news_sync_markdown_includes_symbol_rows():
    summary = sync_news_symbols(
        repository=make_repository(),
        provider=FakeNewsProvider(),
        symbols=[],
        start_at=None,
        end_at=None,
        min_request_interval=0,
    )
    report = build_news_sync_report(
        summary,
        symbol_source="manual",
        start_at=None,
        end_at=None,
        pool_max_symbols=300,
    )

    markdown = to_markdown(report)

    assert "# News Sync Coverage Report" in markdown
    assert "news_rows" in markdown


def test_select_news_symbols_research_pool_and_dry_run_do_not_call_provider():
    repository = make_repository()
    seed_stock(repository, "000001", exchange="SZ")
    seed_stock(repository, "600000", exchange="SH")
    seed_daily_bars(repository, "000001", date(2026, 1, 1), 20)
    seed_daily_bars(repository, "600000", date(2026, 1, 1), 20)
    args = Namespace(
        symbol_source="research_pool",
        symbols="",
        symbols_file=None,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 20),
        start_at=None,
        end_at=None,
        pool_max_symbols=1,
    )
    provider = FakeNewsProvider()

    symbols = select_news_symbols(repository, args)
    summary = sync_news_symbols(
        repository=repository,
        provider=provider,
        symbols=symbols,
        start_at=datetime(2026, 1, 1),
        end_at=datetime(2026, 1, 20),
        dry_run=True,
        min_request_interval=0,
    )

    assert symbols == ["000001"]
    assert provider.calls == []
    assert summary.items[0].status == "dry_run"
