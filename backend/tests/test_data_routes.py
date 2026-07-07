"""HTTP tests for the data API diagnostics and symbol-status endpoints.

The real ``akshare``/network layer is never touched. Each test owns an
in-memory SQLite database injected via FastAPI's ``dependency_overrides``.
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import data as data_module
from app.core.database import get_session
from app.main import create_app
from app.models.entities import Base, DailyBar, IndexDailyBar, Stock


def _make_session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _client_with(session_factory: sessionmaker) -> TestClient:
    app = create_app()

    def override_get_session() -> Session:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)


def _seed_stock(session_factory: sessionmaker, symbol: str, name: str, exchange: str = "SZ") -> None:
    session = session_factory()
    try:
        session.merge(Stock(symbol=symbol, name=name, exchange=exchange, status="active"))
        session.commit()
    finally:
        session.close()


def _seed_bar(
    session_factory: sessionmaker,
    symbol: str,
    trade_date: date,
    *,
    open_: float = 10.0,
    high: float = 10.5,
    low: float = 9.8,
    close: float = 10.2,
    volume: float = 1000.0,
    amount: float = 10200.0,
) -> None:
    session = session_factory()
    try:
        session.add(
            DailyBar(
                symbol=symbol,
                trade_date=trade_date,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                amount=amount,
                adj="qfq",
            )
        )
        session.commit()
    finally:
        session.close()


def _seed_index_bar(
    session_factory: sessionmaker,
    symbol: str,
    trade_date: date,
) -> None:
    session = session_factory()
    try:
        session.add(
            IndexDailyBar(
                symbol=symbol,
                trade_date=trade_date,
                open=10.0,
                high=10.5,
                low=9.8,
                close=10.2,
                volume=1000.0,
                amount=10200.0,
            )
        )
        session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# GET /api/data/diagnostics
# ---------------------------------------------------------------------------


def test_diagnostics_empty_database_reports_zeros_and_null_dates(monkeypatch):
    monkeypatch.setattr(data_module, "settings", SimpleNamespace(database_url="sqlite:///:memory:"))
    client = _client_with(_make_session_factory())

    response = client.get("/api/data/diagnostics")

    assert response.status_code == 200
    body = response.json()
    assert body["stock_count"] == 0
    assert body["bar_count"] == 0
    assert body["symbols_with_bars"] == 0
    assert body["start_date"] is None
    assert body["end_date"] is None
    assert body["database"] == "sqlite:///:memory:"


def test_diagnostics_with_data_returns_coverage_and_relative_database_display(monkeypatch):
    monkeypatch.setattr(data_module, "settings", SimpleNamespace(database_url="sqlite:///./diag.db"))
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    _seed_stock(session_factory, "600000", "SPDB", "SH")
    _seed_bar(session_factory, "000001", date(2024, 1, 2))
    client = _client_with(session_factory)

    response = client.get("/api/data/diagnostics")

    assert response.status_code == 200
    body = response.json()
    assert body["stock_count"] == 2
    assert body["bar_count"] == 1
    assert body["symbols_with_bars"] == 1
    assert body["start_date"] == "2024-01-02"
    assert body["end_date"] == "2024-01-02"
    database = body["database"]
    assert isinstance(database, str)
    # The display keeps the configured relative path and never resolves to an
    # absolute filesystem path that would leak the server layout.
    assert database == "sqlite:///./diag.db"
    from pathlib import Path

    assert database != str(Path("./diag.db").resolve())
    assert not Path(database.replace("sqlite:///", "", 1)).is_absolute()


def test_diagnostics_non_sqlite_url_masks_credentials(monkeypatch):
    monkeypatch.setattr(data_module, "settings", SimpleNamespace(database_url="postgresql://user:pw@host/db"))
    client = _client_with(_make_session_factory())

    response = client.get("/api/data/diagnostics")

    assert response.status_code == 200
    database = response.json()["database"]
    assert database == "postgresql://host/db"
    assert "user" not in database
    assert "pw" not in database


def test_diagnostics_masks_absolute_sqlite_directory(monkeypatch):
    monkeypatch.setattr(
        data_module,
        "settings",
        SimpleNamespace(database_url=r"sqlite:///D:\private\project\data\quant.db"),
    )
    client = _client_with(_make_session_factory())

    response = client.get("/api/data/diagnostics")

    assert response.status_code == 200
    assert response.json()["database"] == "sqlite:///quant.db"


# ---------------------------------------------------------------------------
# GET /api/data/symbol-status
# ---------------------------------------------------------------------------


def test_symbol_status_existing_stock_with_bars_reports_coverage():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    _seed_bar(session_factory, "000001", date(2024, 1, 2))
    _seed_bar(session_factory, "000001", date(2024, 1, 3))
    client = _client_with(session_factory)

    response = client.get("/api/data/symbol-status", params={"symbol": "000001"})

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "symbol": "000001",
        "stock_exists": True,
        "name": "Ping An Bank",
        "exchange": "SZ",
        "has_daily_bars": True,
        "start_date": "2024-01-02",
        "end_date": "2024-01-03",
        "bar_count": 2,
    }


def test_symbol_status_existing_stock_without_bars_distinguishes_from_missing():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    client = _client_with(session_factory)

    response = client.get("/api/data/symbol-status", params={"symbol": "000001"})

    assert response.status_code == 200
    body = response.json()
    assert body["stock_exists"] is True
    assert body["has_daily_bars"] is False
    assert body["name"] == "Ping An Bank"
    assert body["exchange"] == "SZ"
    assert body["start_date"] is None
    assert body["end_date"] is None
    assert body["bar_count"] == 0


def test_symbol_status_unknown_stock_reports_security_missing():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    client = _client_with(session_factory)

    response = client.get("/api/data/symbol-status", params={"symbol": "600000"})

    assert response.status_code == 200
    body = response.json()
    assert body["stock_exists"] is False
    assert body["has_daily_bars"] is False
    assert body["name"] is None
    assert body["exchange"] is None
    assert body["start_date"] is None
    assert body["end_date"] is None
    assert body["bar_count"] == 0


def test_symbol_status_normalizes_prefixed_symbol():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    _seed_bar(session_factory, "000001", date(2024, 1, 2))
    client = _client_with(session_factory)

    response = client.get("/api/data/symbol-status", params={"symbol": "SZ000001"})

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "000001"
    assert body["stock_exists"] is True
    assert body["has_daily_bars"] is True


def test_symbol_status_accepts_stock_name():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "002156", "通富微电", "SZ")
    _seed_bar(session_factory, "002156", date(2024, 1, 2))
    client = _client_with(session_factory)

    response = client.get("/api/data/symbol-status", params={"symbol": "通富微电"})

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "002156"
    assert body["name"] == "通富微电"
    assert body["has_daily_bars"] is True


def test_symbol_status_invalid_symbol_returns_400():
    client = _client_with(_make_session_factory())

    response = client.get("/api/data/symbol-status", params={"symbol": "abc"})

    assert response.status_code == 400


def test_symbol_status_missing_param_returns_422():
    client = _client_with(_make_session_factory())

    response = client.get("/api/data/symbol-status")

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/data/sync/news and GET /api/data/news
# ---------------------------------------------------------------------------


def test_sync_news_accepts_stock_name_and_list_news_filters_by_name(monkeypatch):
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "002156", "通富微电", "SZ")

    class FakeNewsProvider:
        def stock_news(self, symbol, *, start_at=None, end_at=None):
            assert symbol == "002156"
            return pd.DataFrame(
                [
                    {
                        "source": "eastmoney_stock_news",
                        "source_id": "em-002156-1",
                        "symbol": symbol,
                        "title": "通富微电获得机构买入",
                        "body": "body",
                        "url": "https://example.com/news/1",
                        "event_type": "stock_news",
                        "sentiment_label": "",
                        "sentiment_score": None,
                        "relevance_score": 1.0,
                        "published_at": datetime(2026, 6, 4, 6, 53),
                        "fetched_at": datetime(2026, 7, 3, 9, 0),
                        "raw": {"id": 1},
                    }
                ]
            )

    monkeypatch.setattr(data_module, "AkShareNewsProvider", FakeNewsProvider)
    client = _client_with(session_factory)

    sync_response = client.post(
        "/api/data/sync/news",
        json={
            "symbol": "通富微电",
            "start_at": "2026-06-01T00:00:00",
            "end_at": "2026-07-03T23:59:59",
        },
    )
    list_response = client.get("/api/data/news", params={"symbol": "通富微电"})

    assert sync_response.status_code == 200
    assert sync_response.json() == {
        "symbol": "002156",
        "synced": 1,
        "status": "success",
        "message": "",
    }
    assert list_response.status_code == 200
    items = list_response.json()
    assert len(items) == 1
    assert items[0]["symbol"] == "002156"
    assert items[0]["title"] == "通富微电获得机构买入"


def test_sync_news_cleans_mojibake_provider_payload(monkeypatch):
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "002156", "通富微电", "SZ")

    class FakeNewsProvider:
        def stock_news(self, symbol, *, start_at=None, end_at=None):
            return pd.DataFrame(
                [
                    {
                        "source": "eastmoney_stock_news",
                        "source_id": "em-002156-dirty",
                        "symbol": symbol,
                        "title": "ç»çåºæ¿æ¦å¿µä¸è·9.93%ï¼20è¡ä¸»åèµéåæµåºè¶äº¿å",
                        "body": "é¿çµç§æ -10.00",
                        "url": "https://example.com/news/dirty",
                        "event_type": "risk_news",
                        "sentiment_label": "risk",
                        "sentiment_score": -0.4,
                        "relevance_score": 1.0,
                        "published_at": datetime(2026, 7, 2, 17, 29),
                        "fetched_at": datetime(2026, 7, 3, 9, 0),
                        "raw": {"æ°é»æ é¢": "ç»çåºæ¿æ¦å¿µä¸è·"},
                    }
                ]
            )

    monkeypatch.setattr(data_module, "AkShareNewsProvider", FakeNewsProvider)
    client = _client_with(session_factory)

    sync_response = client.post("/api/data/sync/news", json={"symbol": "002156"})
    list_response = client.get("/api/data/news", params={"symbol": "002156"})

    assert sync_response.status_code == 200
    assert list_response.status_code == 200
    item = list_response.json()[0]
    assert item["title"] == "玻璃基板概念下跌9.93%，20股主力资金净流出超亿元"
    assert item["body"] == "长电科技 -10.00"
    assert "新闻标题" in item["raw"]


# ---------------------------------------------------------------------------
# Shared fakes for the daily sync endpoints
# ---------------------------------------------------------------------------


_BAR_COLUMNS = ["symbol", "trade_date", "open", "high", "low", "close", "volume", "amount", "adj"]


def _bars_frame(symbol: str, trade_dates: list[date]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "volume": 1000.0,
                "amount": 10200.0,
                "adj": "qfq",
            }
            for trade_date in trade_dates
        ]
    )


def _empty_bars_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_BAR_COLUMNS)


class _FakeProvider:
    """Stand-in for :class:`AkShareProvider` with scripted bar/error responses."""

    def __init__(self, bars_by_symbol=None, errors=None, empty_symbols=None):
        self.bars_by_symbol = bars_by_symbol or {}
        self.errors = errors or {}
        self.empty_symbols = set(empty_symbols or ())
        self.calls: list[tuple] = []
        self.index_calls: list[tuple] = []

    def daily_bars(self, symbol, start_date, end_date, adjust="qfq"):
        self.calls.append((symbol, start_date, end_date, adjust))
        if symbol in self.errors:
            raise self.errors[symbol]
        if symbol in self.empty_symbols:
            return _empty_bars_frame()
        return self.bars_by_symbol.get(symbol, _empty_bars_frame())

    def index_daily_bars(self, symbol, start_date, end_date):
        self.index_calls.append((symbol, start_date, end_date))
        if symbol in self.errors:
            raise self.errors[symbol]
        if symbol in self.empty_symbols:
            return _empty_bars_frame()
        return self.bars_by_symbol.get(symbol, _empty_bars_frame())

    def stock_list(self):
        return pd.DataFrame(columns=["symbol", "name", "exchange", "status"])

    def trading_calendar(self):
        return pd.DataFrame(
            {
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3)],
                "is_open": [True, True],
            }
        )


def _install_provider(monkeypatch, provider: _FakeProvider) -> None:
    monkeypatch.setattr(data_module, "AkShareProvider", lambda: provider)


def _failed_sync_job_targets(client: TestClient) -> list[str]:
    jobs = client.get("/api/data/sync/jobs", params={"limit": 50}).json()
    return [job["target"] for job in jobs if job["status"] == "failed"]


def test_sync_calendar_persists_open_days(monkeypatch):
    provider = _FakeProvider()
    _install_provider(monkeypatch, provider)
    session_factory = _make_session_factory()
    client = _client_with(session_factory)

    response = client.post("/api/data/sync/calendar")

    assert response.status_code == 200
    assert response.json() == {
        "synced": 2,
        "start_date": "2024-01-02",
        "end_date": "2024-01-03",
    }
    quality = client.get(
        "/api/data/quality",
        params={
            "start_date": "2024-01-01",
            "end_date": "2024-01-03",
            "limit": 10,
        },
    )
    assert quality.status_code == 200
    assert quality.json()["expected_trading_days"] == 2


def test_full_market_next_includes_beijing_exchange(monkeypatch):
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "430047", "BJ Stock", "BJ")
    provider = _FakeProvider(
        bars_by_symbol={"430047": _bars_frame("430047", [date(2024, 1, 2)])}
    )
    _install_provider(monkeypatch, provider)
    client = _client_with(session_factory)

    response = client.post(
        "/api/data/sync/full-market/next",
        json={
            "start_date": "2024-01-02",
            "end_date": "2024-01-03",
            "batch_size": 20,
            "min_request_interval": 0,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] == 1
    assert body["items"][0]["symbol"] == "430047"


# ---------------------------------------------------------------------------
# GET /api/data/daily
# ---------------------------------------------------------------------------


def test_list_daily_returns_bars_for_valid_date_range():
    session_factory = _make_session_factory()
    _seed_bar(session_factory, "000001", date(2024, 1, 2))
    _seed_bar(session_factory, "000001", date(2024, 1, 3))
    client = _client_with(session_factory)

    response = client.get(
        "/api/data/daily",
        params={"symbol": "000001", "start_date": "2024-01-01", "end_date": "2024-01-31"},
    )

    assert response.status_code == 200
    bars = response.json()
    assert len(bars) == 2
    assert {bar["trade_date"] for bar in bars} == {"2024-01-02", "2024-01-03"}


def test_list_daily_accepts_stock_name():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "002156", "通富微电", "SZ")
    _seed_bar(session_factory, "002156", date(2024, 1, 2))
    client = _client_with(session_factory)

    response = client.get(
        "/api/data/daily",
        params={"symbol": "通富微电", "start_date": "2024-01-01", "end_date": "2024-01-31"},
    )

    assert response.status_code == 200
    bars = response.json()
    assert len(bars) == 1
    assert bars[0]["symbol"] == "002156"


def test_list_daily_start_after_end_returns_400():
    client = _client_with(_make_session_factory())

    response = client.get(
        "/api/data/daily",
        params={"symbol": "000001", "start_date": "2024-02-01", "end_date": "2024-01-01"},
    )

    assert response.status_code == 400
    assert "start_date" in response.json()["detail"]


def test_list_daily_malformed_date_returns_422():
    client = _client_with(_make_session_factory())

    response = client.get(
        "/api/data/daily",
        params={"symbol": "000001", "start_date": "not-a-date", "end_date": "2024-01-31"},
    )

    assert response.status_code == 422


def test_list_daily_missing_date_param_returns_422():
    client = _client_with(_make_session_factory())

    response = client.get("/api/data/daily", params={"symbol": "000001", "start_date": "2024-01-01"})

    assert response.status_code == 422


def test_list_daily_invalid_symbol_returns_400():
    client = _client_with(_make_session_factory())

    response = client.get(
        "/api/data/daily",
        params={"symbol": "abc", "start_date": "2024-01-01", "end_date": "2024-01-31"},
    )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/data/sync/daily
# ---------------------------------------------------------------------------


def test_sync_daily_success_returns_symbol_synced_status(monkeypatch):
    provider = _FakeProvider(bars_by_symbol={"000001": _bars_frame("000001", [date(2024, 1, 2), date(2024, 1, 3)])})
    _install_provider(monkeypatch, provider)
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/data/sync/daily",
        json={"symbol": "000001", "start_date": "2024-01-01", "end_date": "2024-01-31", "adjust": "qfq"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "000001"
    assert body["synced"] == 2
    assert body["status"] == "success"
    assert "message" in body


def test_sync_daily_empty_returns_response_not_404(monkeypatch):
    provider = _FakeProvider(empty_symbols={"000001"})
    _install_provider(monkeypatch, provider)
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/data/sync/daily",
        json={"symbol": "000001", "start_date": "2024-01-01", "end_date": "2024-01-31", "adjust": "qfq"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "000001"
    assert body["synced"] == 0
    assert body["status"] == "empty"
    assert body["message"]


def test_sync_daily_cached_returns_response_with_status(monkeypatch):
    session_factory = _make_session_factory()
    _seed_bar(session_factory, "000001", date(2024, 1, 2))
    provider = _FakeProvider(errors={"000001": RuntimeError("akshare down")})
    _install_provider(monkeypatch, provider)
    client = _client_with(session_factory)

    response = client.post(
        "/api/data/sync/daily",
        json={"symbol": "000001", "start_date": "2024-01-01", "end_date": "2024-01-31", "adjust": "qfq"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "000001"
    assert body["synced"] == 1
    assert body["status"] == "cached"


def test_sync_daily_invalid_symbol_returns_400(monkeypatch):
    _install_provider(monkeypatch, _FakeProvider())
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/data/sync/daily",
        json={"symbol": "abc", "start_date": "2024-01-01", "end_date": "2024-01-31", "adjust": "qfq"},
    )

    assert response.status_code == 400


def test_sync_daily_start_after_end_is_not_required_by_route(monkeypatch):
    # The POST model uses Pydantic date types; the route itself does not add a
    # start<=end guard (only GET /daily does). This test documents that the
    # sync request still validates date parsing (malformed -> 422).
    _install_provider(monkeypatch, _FakeProvider())
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/data/sync/daily",
        json={"symbol": "000001", "start_date": "2024-01-01", "end_date": "not-a-date", "adjust": "qfq"},
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/data/sync/daily/batch
# ---------------------------------------------------------------------------


def test_sync_daily_batch_success_and_empty_items(monkeypatch):
    provider = _FakeProvider(
        bars_by_symbol={"000001": _bars_frame("000001", [date(2024, 1, 2)])},
        empty_symbols={"000002"},
    )
    _install_provider(monkeypatch, provider)
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/data/sync/daily/batch",
        json={"symbols": ["000001", "000002"], "start_date": "2024-01-01", "end_date": "2024-01-31"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["success"] == 1
    assert body["failed"] == 0
    by_symbol = {item["symbol"]: item for item in body["items"]}
    assert by_symbol["000001"]["status"] == "success"
    assert by_symbol["000001"]["synced"] == 1
    assert by_symbol["000002"]["status"] == "empty"


def test_sync_daily_batch_failure_logs_normalized_symbol(monkeypatch):
    provider = _FakeProvider(errors={"000001": RuntimeError("akshare down")})
    _install_provider(monkeypatch, provider)
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/data/sync/daily/batch",
        json={"symbols": ["SZ000001"], "start_date": "2024-01-01", "end_date": "2024-01-31"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["failed"] == 1
    item = body["items"][0]
    # Failure target is the normalized symbol, not the raw prefixed input.
    assert item["symbol"] == "000001"
    assert item["status"] == "failed"
    assert "SZ000001" not in item["message"]
    assert _failed_sync_job_targets(client) == ["000001"]
    # Provider was called with the normalized symbol.
    assert provider.calls[0][0] == "000001"


def test_sync_daily_batch_invalid_input_logs_raw_symbol(monkeypatch):
    provider = _FakeProvider()
    _install_provider(monkeypatch, provider)
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/data/sync/daily/batch",
        json={"symbols": ["abc"], "start_date": "2024-01-01", "end_date": "2024-01-31"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["failed"] == 1
    item = body["items"][0]
    # Invalid input cannot be normalized, so the raw (stripped) value is logged.
    assert item["symbol"] == "abc"
    assert item["status"] == "failed"
    assert _failed_sync_job_targets(client) == ["abc"]
    # Provider was never called because normalization failed first.
    assert provider.calls == []


# ---------------------------------------------------------------------------
# GET /api/data/sync/research/progress
# ---------------------------------------------------------------------------


def test_research_progress_empty_database_reports_full_coverage():
    client = _client_with(_make_session_factory())

    response = client.get(
        "/api/data/sync/research/progress",
        params={"start_date": "2024-01-02", "end_date": "2024-01-03"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["covered"] == 0
    assert body["remaining"] == 0
    # No stocks => nothing to cover; repository reports 100% by convention.
    assert body["percent"] == 100.0


def test_research_progress_reports_covered_and_remaining_counts():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    _seed_stock(session_factory, "600000", "SPDB", "SH")
    _seed_bar(session_factory, "000001", date(2024, 1, 2))
    _seed_bar(session_factory, "000001", date(2024, 1, 3))
    client = _client_with(session_factory)

    response = client.get(
        "/api/data/sync/research/progress",
        params={"start_date": "2024-01-02", "end_date": "2024-01-03"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["covered"] == 1
    assert body["remaining"] == 1
    assert body["percent"] == 50.0


def test_research_progress_start_after_end_returns_400():
    client = _client_with(_make_session_factory())

    response = client.get(
        "/api/data/sync/research/progress",
        params={"start_date": "2024-01-03", "end_date": "2024-01-02"},
    )

    assert response.status_code == 400
    assert "start_date" in response.json()["detail"]


def test_research_progress_missing_param_returns_422():
    client = _client_with(_make_session_factory())

    response = client.get(
        "/api/data/sync/research/progress",
        params={"start_date": "2024-01-02"},
    )

    assert response.status_code == 422


def test_research_progress_excludes_st_and_delisted_by_default():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    _seed_stock(session_factory, "000002", "ST Scam", "SZ")
    _seed_stock(session_factory, "000003", "Foo退", "SH")
    client = _client_with(session_factory)

    response = client.get(
        "/api/data/sync/research/progress",
        params={"start_date": "2024-01-02", "end_date": "2024-01-03"},
    )

    assert response.status_code == 200
    body = response.json()
    # ST and 退 names are excluded by default; only the clean SZ stock counts.
    assert body["total"] == 1
    assert body["remaining"] == 1


# ---------------------------------------------------------------------------
# POST /api/data/sync/research/next
# ---------------------------------------------------------------------------


def test_sync_research_next_syncs_candidates_and_updates_progress(monkeypatch):
    provider = _FakeProvider(
        bars_by_symbol={
            "000001": _bars_frame("000001", [date(2024, 1, 2), date(2024, 1, 3)]),
            "600000": _bars_frame("600000", [date(2024, 1, 2), date(2024, 1, 3)]),
        }
    )
    _install_provider(monkeypatch, provider)
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    _seed_stock(session_factory, "600000", "SPDB", "SH")
    client = _client_with(session_factory)

    response = client.post(
        "/api/data/sync/research/next",
        json={"start_date": "2024-01-02", "end_date": "2024-01-03", "batch_size": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["success"] == 2
    assert body["failed"] == 0
    assert {item["symbol"] for item in body["items"]} == {"000001", "600000"}
    progress = body["progress"]
    assert progress["total"] == 2
    assert progress["covered"] == 2
    assert progress["remaining"] == 0
    assert progress["percent"] == 100.0
    # Provider was called with the repository-selected candidate symbols.
    assert {call[0] for call in provider.calls} == {"000001", "600000"}


def test_sync_research_next_when_all_covered_returns_empty_items(monkeypatch):
    _install_provider(monkeypatch, _FakeProvider())
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    _seed_bar(session_factory, "000001", date(2024, 1, 2))
    _seed_bar(session_factory, "000001", date(2024, 1, 3))
    client = _client_with(session_factory)

    response = client.post(
        "/api/data/sync/research/next",
        json={"start_date": "2024-01-02", "end_date": "2024-01-03", "batch_size": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert body["success"] == 0
    assert body["progress"]["remaining"] == 0
    assert body["progress"]["covered"] == 1


def test_sync_research_next_uses_default_batch_size_when_omitted(monkeypatch):
    provider = _FakeProvider(
        bars_by_symbol={"000001": _bars_frame("000001", [date(2024, 1, 2), date(2024, 1, 3)])}
    )
    _install_provider(monkeypatch, provider)
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    client = _client_with(session_factory)

    response = client.post(
        "/api/data/sync/research/next",
        json={"start_date": "2024-01-02", "end_date": "2024-01-03"},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_sync_research_next_batch_size_below_min_returns_422(monkeypatch):
    _install_provider(monkeypatch, _FakeProvider())
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/data/sync/research/next",
        json={"start_date": "2024-01-02", "end_date": "2024-01-03", "batch_size": 0},
    )

    assert response.status_code == 422


def test_sync_research_next_batch_size_above_max_returns_422(monkeypatch):
    _install_provider(monkeypatch, _FakeProvider())
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/data/sync/research/next",
        json={"start_date": "2024-01-02", "end_date": "2024-01-03", "batch_size": 51},
    )

    assert response.status_code == 422


def test_sync_research_next_failure_recorded_as_failed_item(monkeypatch):
    provider = _FakeProvider(errors={"000001": RuntimeError("akshare down")})
    _install_provider(monkeypatch, provider)
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    client = _client_with(session_factory)

    response = client.post(
        "/api/data/sync/research/next",
        json={"start_date": "2024-01-02", "end_date": "2024-01-03", "batch_size": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["failed"] == 1
    assert body["success"] == 0
    item = body["items"][0]
    assert item["symbol"] == "000001"
    assert item["status"] == "failed"
    # Still incomplete after the failure.
    assert body["progress"]["remaining"] == 1
    assert _failed_sync_job_targets(client) == ["000001"]


def test_sync_research_next_cached_reuses_local_bars_on_provider_failure(monkeypatch):
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    # Partial local coverage: a bar exists but does not reach end_date, so the
    # symbol stays a sync candidate and the cached fallback can kick in.
    _seed_bar(session_factory, "000001", date(2024, 1, 2))
    provider = _FakeProvider(errors={"000001": RuntimeError("akshare down")})
    _install_provider(monkeypatch, provider)
    client = _client_with(session_factory)

    response = client.post(
        "/api/data/sync/research/next",
        json={"start_date": "2024-01-02", "end_date": "2024-01-03", "batch_size": 10},
    )

    assert response.status_code == 200
    body = response.json()
    item = body["items"][0]
    assert item["status"] == "cached"
    assert item["synced"] == 1
    # cached reuses existing bars without advancing coverage, so it must NOT
    # count as a research success (otherwise a failing provider behind partial
    # cache would leave progress unchanged and loop the frontend forever).
    assert body["success"] == 0
    assert body["failed"] == 0
    # Coverage still incomplete because the cache does not span end_date.
    assert body["progress"]["remaining"] == 1


def test_sync_research_next_partial_cache_keeps_progress_and_success_zero(monkeypatch):
    # Regression: when the candidate provider fails but partial local cache
    # exists, the research response must report success=0 and leave progress
    # remaining unchanged, so the frontend stops instead of auto-running an
    # infinite loop on a persistently failing provider.
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    # Partial local coverage: a bar exists but does not reach end_date, so the
    # symbol stays a sync candidate and the cached fallback can kick in.
    _seed_bar(session_factory, "000001", date(2024, 1, 2))
    provider = _FakeProvider(errors={"000001": RuntimeError("akshare down")})
    _install_provider(monkeypatch, provider)
    client = _client_with(session_factory)

    before = client.get(
        "/api/data/sync/research/progress",
        params={"start_date": "2024-01-02", "end_date": "2024-01-03"},
    ).json()

    response = client.post(
        "/api/data/sync/research/next",
        json={"start_date": "2024-01-02", "end_date": "2024-01-03", "batch_size": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["success"] == 0
    assert body["failed"] == 0
    assert body["items"][0]["status"] == "cached"
    # Progress remaining is unchanged: the cache did not extend coverage.
    assert body["progress"]["remaining"] == before["remaining"]
    assert body["progress"]["remaining"] == 1


# ---------------------------------------------------------------------------
# GET /api/data/sync/research/progress
# ---------------------------------------------------------------------------


def test_research_sync_progress_empty_database_reports_full_coverage():
    client = _client_with(_make_session_factory())

    response = client.get(
        "/api/data/sync/research/progress",
        params={"start_date": "2024-01-01", "end_date": "2024-12-31"},
    )

    assert response.status_code == 200
    # With no stocks the pool is vacuously fully covered: percent 100, no remaining.
    assert response.json() == {"total": 0, "covered": 0, "remaining": 0, "percent": 100.0}


def test_research_sync_progress_start_after_end_returns_400():
    client = _client_with(_make_session_factory())

    response = client.get(
        "/api/data/sync/research/progress",
        params={"start_date": "2024-12-31", "end_date": "2024-01-01"},
    )

    assert response.status_code == 400
    assert "start_date" in response.json()["detail"]


def test_research_sync_progress_reports_covered_and_remaining_for_mixed_pool():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    _seed_stock(session_factory, "600000", "SPDB", "SH")
    # 000001 fully covers the requested range; 600000 has no bars at all.
    _seed_bar(session_factory, "000001", date(2024, 1, 1))
    _seed_bar(session_factory, "000001", date(2024, 12, 31))
    client = _client_with(session_factory)

    response = client.get(
        "/api/data/sync/research/progress",
        params={"start_date": "2024-01-01", "end_date": "2024-12-31"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["covered"] == 1
    assert body["remaining"] == 1
    assert body["percent"] == 50.0


# ---------------------------------------------------------------------------
# POST /api/data/sync/research/next
# ---------------------------------------------------------------------------


def test_sync_research_next_start_after_end_returns_400(monkeypatch):
    _install_provider(monkeypatch, _FakeProvider())
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/data/sync/research/next",
        json={"start_date": "2024-12-31", "end_date": "2024-01-01", "batch_size": 10},
    )

    assert response.status_code == 400
    assert "start_date" in response.json()["detail"]


def test_sync_research_next_empty_pool_returns_empty_items_and_progress(monkeypatch):
    _install_provider(monkeypatch, _FakeProvider())
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/data/sync/research/next",
        json={"start_date": "2024-01-01", "end_date": "2024-12-31", "batch_size": 20},
    )

    assert response.status_code == 200
    body = response.json()
    # No research-pool stocks -> nothing to sync, but progress is still returned.
    assert body["total"] == 0
    assert body["success"] == 0
    assert body["failed"] == 0
    assert body["items"] == []
    assert body["progress"]["total"] == 0
    assert body["progress"]["remaining"] == 0


def test_sync_research_next_syncs_incomplete_stock_and_updates_progress(monkeypatch):
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    # No bars seeded -> 000001 is incomplete and picked up as the next batch.
    provider = _FakeProvider(
        bars_by_symbol={"000001": _bars_frame("000001", [date(2024, 1, 1), date(2024, 12, 31)])}
    )
    _install_provider(monkeypatch, provider)
    client = _client_with(session_factory)

    response = client.post(
        "/api/data/sync/research/next",
        json={"start_date": "2024-01-01", "end_date": "2024-12-31", "batch_size": 20},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["success"] == 1
    assert body["failed"] == 0
    item = body["items"][0]
    assert item["symbol"] == "000001"
    assert item["status"] == "success"
    assert item["synced"] == 2
    # After the sync the pool is fully covered: remaining drops to 0.
    assert body["progress"]["total"] == 1
    assert body["progress"]["covered"] == 1
    assert body["progress"]["remaining"] == 0
    # Provider received the normalized symbol within the requested range.
    assert provider.calls[0][0] == "000001"


# ---------------------------------------------------------------------------
# POST /api/data/sync/index
# ---------------------------------------------------------------------------


def test_sync_index_success_returns_symbol_synced_status(monkeypatch):
    provider = _FakeProvider(
        bars_by_symbol={"000300": _bars_frame("000300", [date(2024, 1, 2), date(2024, 1, 3)])}
    )
    _install_provider(monkeypatch, provider)
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/data/sync/index",
        json={"symbol": "000300", "start_date": "2024-01-01", "end_date": "2024-01-31"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "000300"
    assert body["synced"] == 2
    assert body["status"] == "success"
    assert "message" in body
    # Provider was called with the whitelisted symbol and the requested range.
    assert provider.index_calls[0][0] == "000300"
    assert provider.index_calls[0][1] == date(2024, 1, 1)
    assert provider.index_calls[0][2] == date(2024, 1, 31)


def test_sync_index_non_whitelisted_symbol_returns_400(monkeypatch):
    provider = _FakeProvider()
    _install_provider(monkeypatch, provider)
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/data/sync/index",
        json={"symbol": "000001", "start_date": "2024-01-01", "end_date": "2024-01-31"},
    )

    assert response.status_code == 400
    assert "000001" in response.json()["detail"]
    # The provider is never called for a non-whitelisted symbol.
    assert provider.index_calls == []


def test_sync_index_empty_returns_response_not_error(monkeypatch):
    provider = _FakeProvider(empty_symbols={"000905"})
    _install_provider(monkeypatch, provider)
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/data/sync/index",
        json={"symbol": "000905", "start_date": "2024-01-01", "end_date": "2024-01-31"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "000905"
    assert body["synced"] == 0
    assert body["status"] == "empty"
    assert body["message"]


def test_sync_index_cached_reuses_local_bars_on_provider_failure(monkeypatch):
    session_factory = _make_session_factory()
    _seed_index_bar(session_factory, "000852", date(2024, 1, 2))
    provider = _FakeProvider(errors={"000852": RuntimeError("akshare down")})
    _install_provider(monkeypatch, provider)
    client = _client_with(session_factory)

    response = client.post(
        "/api/data/sync/index",
        json={"symbol": "000852", "start_date": "2024-01-01", "end_date": "2024-01-31"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "000852"
    assert body["synced"] == 1
    assert body["status"] == "cached"


def test_sync_index_does_not_overwrite_same_code_stock_bars(monkeypatch):
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000905", "Xiamen Port", "SZ")
    _seed_bar(session_factory, "000905", date(2024, 1, 2), close=7.5)
    provider = _FakeProvider(
        bars_by_symbol={"000905": _bars_frame("000905", [date(2024, 1, 2)])}
    )
    _install_provider(monkeypatch, provider)
    client = _client_with(session_factory)

    response = client.post(
        "/api/data/sync/index",
        json={"symbol": "000905", "start_date": "2024-01-01", "end_date": "2024-01-31"},
    )

    assert response.status_code == 200
    session = session_factory()
    try:
        stock_bar = session.query(DailyBar).filter_by(symbol="000905").one()
        index_bar = session.query(IndexDailyBar).filter_by(symbol="000905").one()
        assert stock_bar.close == 7.5
        assert index_bar.close == 10.2
    finally:
        session.close()
