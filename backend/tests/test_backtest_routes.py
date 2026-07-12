"""HTTP tests for the backtest API routes, covering the manual and
research-pool symbol sources.

The real ``akshare``/network layer is never touched. Each test owns an
in-memory SQLite database injected via FastAPI's ``dependency_overrides``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_session
from app.main import create_app
from app.models.entities import Base, DailyBar, IndexDailyBar, NewsItem, Stock
from app.models.pit import SecurityStatus
from app.schemas.backtest import BacktestRequest


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


def _seed_daily_bars(
    session_factory: sessionmaker,
    symbol: str,
    start_date: date,
    end_date: date,
    *,
    close: float = 10.0,
) -> None:
    session = session_factory()
    try:
        current = start_date
        while current <= end_date:
            session.add(
                DailyBar(
                    symbol=symbol,
                    trade_date=current,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=1000.0,
                    amount=10000.0,
                    adj="qfq",
                )
            )
            current += timedelta(days=1)
        session.commit()
    finally:
        session.close()


def _seed_index_bars(
    session_factory: sessionmaker,
    symbol: str,
    start_date: date,
    end_date: date,
    *,
    close: float = 3000.0,
) -> None:
    session = session_factory()
    try:
        current = start_date
        while current <= end_date:
            session.add(
                IndexDailyBar(
                    symbol=symbol,
                    trade_date=current,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=1000.0,
                    amount=10000.0,
                )
            )
            current += timedelta(days=1)
        session.commit()
    finally:
        session.close()


def _seed_news_item(
    session_factory: sessionmaker,
    symbol: str,
    *,
    published_at: datetime,
    fetched_at: datetime,
    event_type: str = "negative_news",
    sentiment_label: str = "negative",
    sentiment_score: float = -0.8,
    relevance_score: float = 1.0,
) -> None:
    session = session_factory()
    try:
        session.add(
            NewsItem(
                source="test_news",
                source_id=f"{symbol}-{published_at.isoformat()}",
                symbol=symbol,
                title="negative test news",
                body="body",
                url="",
                event_type=event_type,
                sentiment_label=sentiment_label,
                sentiment_score=sentiment_score,
                relevance_score=relevance_score,
                published_at=published_at,
                fetched_at=fetched_at,
                raw="{}",
            )
        )
        session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# POST /api/backtests/run -- manual symbol source
# ---------------------------------------------------------------------------


def test_run_backtest_manual_returns_symbol_source_and_selected_symbols():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    _seed_daily_bars(session_factory, "000001", date(2024, 1, 1), date(2024, 2, 15))
    client = _client_with(session_factory)

    response = client.post(
        "/api/backtests/run",
        json={
            "strategy_name": "moving_average",
            "symbol_source": "manual",
            "symbols": ["SZ000001"],
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-02-15",
            "fast_window": 5,
            "slow_window": 20,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["symbol_source"] == "manual"
    # Manual route normalizes the prefixed input to the bare 6-digit symbol.
    assert body["selected_symbols"] == ["000001"]
    assert body["run_id"] is not None
    assert body["metrics"]["final_equity"] > 0
    assert len(body["equity_curve"]) > 0


def test_run_backtest_manual_accepts_stock_name():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "002156", "通富微电", "SZ")
    _seed_daily_bars(session_factory, "002156", date(2024, 1, 1), date(2024, 2, 15))
    client = _client_with(session_factory)

    response = client.post(
        "/api/backtests/run",
        json={
            "strategy_name": "moving_average",
            "symbol_source": "manual",
            "symbols": ["通富微电"],
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-02-15",
            "fast_window": 5,
            "slow_window": 20,
        },
    )

    assert response.status_code == 200
    assert response.json()["selected_symbols"] == ["002156"]


def test_run_backtest_strategy_parameter_error_returns_400():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "002156", "通富微电", "SZ")
    _seed_daily_bars(session_factory, "002156", date(2024, 1, 1), date(2024, 2, 15))
    client = _client_with(session_factory)

    response = client.post(
        "/api/backtests/run",
        json={
            "strategy_name": "ml_score_rank",
            "symbol_source": "manual",
            "symbols": ["通富微电"],
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-02-15",
            "parameters": {"scores_path": ""},
        },
    )

    assert response.status_code == 400
    assert "scores_path is required" in response.json()["detail"]


def test_run_backtest_ml_score_rank_uses_db_negative_news(tmp_path):
    session_factory = _make_session_factory()
    for symbol in ("000001", "000002"):
        _seed_stock(session_factory, symbol, f"Stock {symbol}", "SZ")
        _seed_daily_bars(session_factory, symbol, date(2024, 1, 1), date(2024, 1, 25))
    _seed_news_item(
        session_factory,
        "000001",
        published_at=datetime(2024, 1, 4, 15, 0),
        fetched_at=datetime(2024, 1, 4, 15, 10),
    )
    scores_path = tmp_path / "scores.csv"
    scores_path.write_text(
        "\n".join(
            ["trade_date,symbol,score"]
            + [
                f"2024-01-{day:02d},000001,0.90\n2024-01-{day:02d},000002,0.70"
                for day in range(1, 26)
            ]
        ),
        encoding="utf-8",
    )
    client = _client_with(session_factory)

    response = client.post(
        "/api/backtests/run",
        json={
            "strategy_name": "ml_score_rank",
            "symbol_source": "manual",
            "symbols": ["000001", "000002"],
            "benchmark_symbol": None,
            "start_date": "2024-01-05",
            "end_date": "2024-01-25",
            "parameters": {
                "scores_path": str(scores_path),
                "top_n": 1,
                "max_position_weight": 0.5,
                "max_total_weight": 0.5,
                "min_avg_amount_20d": 0,
                "min_price": 1,
                "use_db_negative_news": True,
                "negative_news_lookback_days": 30,
            },
        },
    )

    assert response.status_code == 200
    trades = response.json()["trades"]
    assert not any(trade["symbol"] == "000001" and trade["side"] == "buy" for trade in trades)
    assert any(trade["symbol"] == "000002" and trade["side"] == "buy" for trade in trades)


def test_run_backtest_ml_score_rank_news_availability_published_at_mode(tmp_path):
    session_factory = _make_session_factory()
    for symbol in ("000001", "000002"):
        _seed_stock(session_factory, symbol, f"Stock {symbol}", "SZ")
        _seed_daily_bars(session_factory, symbol, date(2024, 1, 1), date(2024, 1, 25))
    _seed_news_item(
        session_factory,
        "000001",
        published_at=datetime(2024, 1, 4, 15, 0),
        fetched_at=datetime(2026, 7, 12, 15, 10),
    )
    scores_path = tmp_path / "scores.csv"
    scores_path.write_text(
        "\n".join(
            ["trade_date,symbol,score"]
            + [
                f"2024-01-{day:02d},000001,0.90\n2024-01-{day:02d},000002,0.70"
                for day in range(1, 26)
            ]
        ),
        encoding="utf-8",
    )
    client = _client_with(session_factory)

    def run(mode: str):
        return client.post(
            "/api/backtests/run",
            json={
                "strategy_name": "ml_score_rank",
                "symbol_source": "manual",
                "symbols": ["000001", "000002"],
                "benchmark_symbol": None,
                "start_date": "2024-01-05",
                "end_date": "2024-01-25",
                "parameters": {
                    "scores_path": str(scores_path),
                    "top_n": 1,
                    "max_position_weight": 0.5,
                    "max_total_weight": 0.5,
                    "min_avg_amount_20d": 0,
                    "min_price": 1,
                    "use_db_negative_news": True,
                    "news_availability": mode,
                    "negative_news_lookback_days": 30,
                },
            },
        )

    observed = run("observed")
    published = run("published_at")

    assert observed.status_code == 200
    assert published.status_code == 200
    assert any(trade["symbol"] == "000001" and trade["side"] == "buy" for trade in observed.json()["trades"])
    assert not any(trade["symbol"] == "000001" and trade["side"] == "buy" for trade in published.json()["trades"])
    assert any(trade["symbol"] == "000002" and trade["side"] == "buy" for trade in published.json()["trades"])


def test_run_backtest_ml_score_rank_ignores_db_news_when_disabled(tmp_path):
    session_factory = _make_session_factory()
    for symbol in ("000001", "000002"):
        _seed_stock(session_factory, symbol, f"Stock {symbol}", "SZ")
        _seed_daily_bars(session_factory, symbol, date(2024, 1, 1), date(2024, 1, 25))
    _seed_news_item(
        session_factory,
        "000001",
        published_at=datetime(2024, 1, 4, 15, 0),
        fetched_at=datetime(2024, 1, 4, 15, 10),
    )
    scores_path = tmp_path / "scores.csv"
    scores_path.write_text(
        "\n".join(
            ["trade_date,symbol,score"]
            + [
                f"2024-01-{day:02d},000001,0.90\n2024-01-{day:02d},000002,0.70"
                for day in range(1, 26)
            ]
        ),
        encoding="utf-8",
    )
    client = _client_with(session_factory)

    response = client.post(
        "/api/backtests/run",
        json={
            "strategy_name": "ml_score_rank",
            "symbol_source": "manual",
            "symbols": ["000001", "000002"],
            "benchmark_symbol": None,
            "start_date": "2024-01-05",
            "end_date": "2024-01-25",
            "parameters": {
                "scores_path": str(scores_path),
                "top_n": 1,
                "max_position_weight": 0.5,
                "max_total_weight": 0.5,
                "min_avg_amount_20d": 0,
                "min_price": 1,
                "use_db_negative_news": False,
                "negative_news_lookback_days": 3,
            },
        },
    )

    assert response.status_code == 200
    trades = response.json()["trades"]
    assert any(trade["symbol"] == "000001" and trade["side"] == "buy" for trade in trades)


def test_run_backtest_manual_defaults_symbol_source_to_manual():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    _seed_daily_bars(session_factory, "000001", date(2024, 1, 1), date(2024, 2, 15))
    client = _client_with(session_factory)

    response = client.post(
        "/api/backtests/run",
        json={
            "symbols": ["000001"],
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-02-15",
            "fast_window": 5,
            "slow_window": 20,
        },
    )

    assert response.status_code == 200
    assert response.json()["symbol_source"] == "manual"


def test_run_backtest_auto_syncs_missing_benchmark(monkeypatch):
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    _seed_daily_bars(session_factory, "000001", date(2024, 1, 1), date(2024, 2, 15))
    client = _client_with(session_factory)

    class FakeProvider:
        def index_daily_bars(self, symbol, start_date, end_date):
            dates = pd.date_range(start_date, end_date, freq="D")
            return pd.DataFrame(
                {
                    "symbol": symbol,
                    "trade_date": dates.date,
                    "open": 3000.0,
                    "high": 3010.0,
                    "low": 2990.0,
                    "close": 3005.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                    "adj": "none",
                }
            )

    monkeypatch.setattr("app.api.routes.backtest.AkShareProvider", FakeProvider)
    response = client.post(
        "/api/backtests/run",
        json={
            "strategy_name": "moving_average",
            "symbol_source": "manual",
            "symbols": ["000001"],
            "benchmark_symbol": "000300",
            "start_date": "2024-01-01",
            "end_date": "2024-02-15",
            "fast_window": 5,
            "slow_window": 20,
        },
    )

    assert response.status_code == 200
    assert response.json()["benchmark_curve"]
    session = session_factory()
    try:
        assert session.query(IndexDailyBar).filter_by(symbol="000300").count() > 0
    finally:
        session.close()


def test_run_backtest_auto_syncs_stale_benchmark(monkeypatch):
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    _seed_daily_bars(session_factory, "000001", date(2024, 1, 1), date(2024, 2, 15))
    _seed_index_bars(session_factory, "000300", date(2024, 1, 1), date(2024, 1, 31))
    client = _client_with(session_factory)

    class FakeProvider:
        def index_daily_bars(self, symbol, start_date, end_date):
            dates = pd.date_range(start_date, end_date, freq="D")
            return pd.DataFrame(
                {
                    "symbol": symbol,
                    "trade_date": dates.date,
                    "open": 3000.0,
                    "high": 3010.0,
                    "low": 2990.0,
                    "close": 3005.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                    "adj": "none",
                }
            )

    monkeypatch.setattr("app.api.routes.backtest.AkShareProvider", FakeProvider)
    response = client.post(
        "/api/backtests/run",
        json={
            "strategy_name": "moving_average",
            "symbol_source": "manual",
            "symbols": ["000001"],
            "benchmark_symbol": "000300",
            "start_date": "2024-01-01",
            "end_date": "2024-02-15",
            "fast_window": 5,
            "slow_window": 20,
        },
    )

    assert response.status_code == 200
    assert response.json()["benchmark_curve"][-1]["trade_date"] == "2024-02-15"
    session = session_factory()
    try:
        last_date = session.query(IndexDailyBar.trade_date).filter_by(symbol="000300").order_by(IndexDailyBar.trade_date.desc()).first()[0]
        assert last_date == date(2024, 2, 15)
    finally:
        session.close()


def test_run_backtest_reports_benchmark_auto_sync_failure(monkeypatch):
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    _seed_daily_bars(session_factory, "000001", date(2024, 1, 1), date(2024, 2, 15))
    client = _client_with(session_factory)

    class FailingProvider:
        def index_daily_bars(self, symbol, start_date, end_date):
            raise RuntimeError("all index sources unavailable")

    monkeypatch.setattr("app.api.routes.backtest.AkShareProvider", FailingProvider)
    response = client.post(
        "/api/backtests/run",
        json={
            "strategy_name": "moving_average",
            "symbol_source": "manual",
            "symbols": ["000001"],
            "benchmark_symbol": "000300",
            "start_date": "2024-01-01",
            "end_date": "2024-02-15",
            "fast_window": 5,
            "slow_window": 20,
        },
    )

    assert response.status_code == 502
    assert "Benchmark 000300 auto-sync failed" in response.json()["detail"]


def test_run_backtest_reads_benchmark_from_index_namespace():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    _seed_daily_bars(session_factory, "000001", date(2024, 1, 1), date(2024, 2, 15))
    _seed_index_bars(session_factory, "000300", date(2024, 1, 1), date(2024, 2, 15))
    client = _client_with(session_factory)

    response = client.post(
        "/api/backtests/run",
        json={
            "strategy_name": "moving_average",
            "symbols": ["000001"],
            "benchmark_symbol": "000300",
            "start_date": "2024-01-01",
            "end_date": "2024-02-15",
            "fast_window": 5,
            "slow_window": 20,
        },
    )

    assert response.status_code == 200
    assert response.json()["benchmark_curve"]


# ---------------------------------------------------------------------------
# POST /api/backtests/run -- research_pool symbol source
# ---------------------------------------------------------------------------


def test_run_backtest_research_pool_selects_covered_symbols():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    _seed_stock(session_factory, "600000", "SPDB", "SH")
    # Full coverage of the requested range for both symbols.
    _seed_daily_bars(session_factory, "000001", date(2024, 1, 1), date(2024, 1, 31))
    _seed_daily_bars(session_factory, "600000", date(2024, 1, 1), date(2024, 1, 31))
    client = _client_with(session_factory)

    response = client.post(
        "/api/backtests/run",
        json={
            "strategy_name": "moving_average",
            "symbol_source": "research_pool",
            "pool_max_symbols": 100,
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "fast_window": 5,
            "slow_window": 20,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["symbol_source"] == "research_pool"
    # Pool is ordered by symbol; both fully-covered stocks are selected.
    assert body["selected_symbols"] == ["000001", "600000"]
    assert body["run_id"] is not None
    assert body["metrics"]["final_equity"] > 0


def test_run_backtest_point_in_time_includes_historically_listed_stock():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Survivor", "SZ")
    _seed_stock(session_factory, "000002", "Later Delisted", "SZ")
    _seed_daily_bars(session_factory, "000001", date(2020, 1, 1), date(2020, 2, 15))
    _seed_daily_bars(session_factory, "000002", date(2020, 1, 1), date(2020, 2, 15))
    session = session_factory()
    try:
        session.get(Stock, "000002").status = "delisted"
        session.add_all(
            [
                SecurityStatus(
                    symbol="000001",
                    status="listed",
                    valid_from=date(1991, 1, 1),
                    valid_to=None,
                    announced_at=date(1991, 1, 1),
                    source="test",
                    confidence="high",
                ),
                SecurityStatus(
                    symbol="000002",
                    status="listed",
                    valid_from=date(1995, 1, 1),
                    valid_to=date(2022, 1, 1),
                    announced_at=date(1995, 1, 1),
                    source="test",
                    confidence="high",
                ),
                SecurityStatus(
                    symbol="000002",
                    status="delisted",
                    valid_from=date(2022, 1, 1),
                    valid_to=None,
                    announced_at=date(2022, 1, 1),
                    source="test",
                    confidence="high",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()
    client = _client_with(session_factory)

    response = client.post(
        "/api/backtests/run",
        json={
            "strategy_name": "moving_average",
            "symbol_source": "research_pool",
            "point_in_time": True,
            "benchmark_symbol": None,
            "start_date": "2020-01-01",
            "end_date": "2020-02-15",
            "fast_window": 5,
            "slow_window": 20,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["selected_symbols"] == ["000001", "000002"]
    assert body["universe_metadata"]["mode"] == "pit_fixed"
    assert body["universe_metadata"]["pool_key"]


def test_run_backtest_rejects_future_universe_as_of_date():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Test", "SZ")
    _seed_daily_bars(session_factory, "000001", date(2024, 1, 1), date(2024, 2, 15))
    client = _client_with(session_factory)

    response = client.post(
        "/api/backtests/run",
        json={
            "symbol_source": "research_pool",
            "point_in_time": True,
            "universe_as_of_date": "2024-01-10",
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-02-15",
        },
    )

    assert response.status_code == 400
    assert "cannot be later" in response.json()["detail"]


def test_run_backtest_research_pool_respects_max_symbols_limit():
    session_factory = _make_session_factory()
    for symbol, exchange in (("000001", "SZ"), ("000002", "SZ"), ("000003", "SZ")):
        _seed_stock(session_factory, symbol, f"Bank {symbol}", exchange)
        _seed_daily_bars(session_factory, symbol, date(2024, 1, 1), date(2024, 1, 31))
    client = _client_with(session_factory)

    response = client.post(
        "/api/backtests/run",
        json={
            "strategy_name": "moving_average",
            "symbol_source": "research_pool",
            "pool_max_symbols": 2,
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "fast_window": 5,
            "slow_window": 20,
        },
    )

    assert response.status_code == 200
    # Only the first two symbols (ordered by symbol) are selected.
    assert response.json()["selected_symbols"] == ["000001", "000002"]


def test_run_backtest_research_pool_includes_partial_coverage_with_enough_bars():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Full", "SZ")
    _seed_stock(session_factory, "000002", "Partial", "SZ")
    _seed_daily_bars(session_factory, "000001", date(2024, 1, 1), date(2024, 1, 31))
    # 000002 covers 17 calendar days (Jan 15–31).  That is enough bars to be
    # useful even though it doesn't span the full range — the trading-day
    # based selection should include it.
    _seed_daily_bars(session_factory, "000002", date(2024, 1, 15), date(2024, 1, 31))
    client = _client_with(session_factory)

    response = client.post(
        "/api/backtests/run",
        json={
            "symbol_source": "research_pool",
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "fast_window": 5,
            "slow_window": 20,
        },
    )

    assert response.status_code == 200
    assert response.json()["selected_symbols"] == ["000001", "000002"]


def test_run_backtest_research_pool_empty_returns_400():
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/backtests/run",
        json={
            "symbol_source": "research_pool",
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "fast_window": 5,
            "slow_window": 20,
        },
    )

    assert response.status_code == 400
    assert "research" in response.json()["detail"].lower()


def test_run_backtest_research_pool_stock_without_bars_returns_400():
    session_factory = _make_session_factory()
    # Stock exists but has no bars -> not a covered research symbol.
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    client = _client_with(session_factory)

    response = client.post(
        "/api/backtests/run",
        json={
            "symbol_source": "research_pool",
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "fast_window": 5,
            "slow_window": 20,
        },
    )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/backtests/run -- pool_max_symbols validation (422)
# ---------------------------------------------------------------------------


def test_run_backtest_pool_max_symbols_above_max_returns_422():
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/backtests/run",
        json={
            "symbol_source": "research_pool",
            "pool_max_symbols": 6001,
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
        },
    )

    assert response.status_code == 422


def test_run_backtest_pool_max_symbols_below_min_returns_422():
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/backtests/run",
        json={
            "symbol_source": "research_pool",
            "pool_max_symbols": 0,
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
        },
    )

    assert response.status_code == 422


def test_run_backtest_invalid_symbol_source_returns_422():
    client = _client_with(_make_session_factory())

    response = client.post(
        "/api/backtests/run",
        json={
            "symbol_source": "watchlist",
            "symbols": ["000001"],
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
        },
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/backtests/{run_id} -- historical detail reconstruction
# ---------------------------------------------------------------------------


def test_get_run_reconstructs_selected_symbols_from_trades_and_defaults_to_manual():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    _seed_stock(session_factory, "600000", "SPDB", "SH")
    _seed_daily_bars(session_factory, "000001", date(2024, 1, 1), date(2024, 2, 15))
    _seed_daily_bars(session_factory, "600000", date(2024, 1, 1), date(2024, 2, 15))
    client = _client_with(session_factory)

    run_response = client.post(
        "/api/backtests/run",
        json={
            "strategy_name": "moving_average",
            "symbols": ["000001", "600000"],
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-02-15",
            "fast_window": 5,
            "slow_window": 20,
            "max_position_weight": 0.5,
        },
    )
    assert run_response.status_code == 200
    run_id = run_response.json()["run_id"]

    detail = client.get(f"/api/backtests/{run_id}")

    assert detail.status_code == 200
    body = detail.json()
    # Historical run detail is not persisted with its source, so it defaults
    # to "manual" and reconstructs selected_symbols from the executed trades.
    assert body["symbol_source"] == "manual"
    traded_symbols = {trade["symbol"] for trade in body["trades"]}
    if traded_symbols:
        assert set(body["selected_symbols"]) == traded_symbols
        # Deduplicated (no repeats) and stable.
        assert len(body["selected_symbols"]) == len(set(body["selected_symbols"]))


def test_get_run_with_no_trades_returns_empty_selected_symbols():
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Ping An Bank", "SZ")
    # Seed only a few days so the slow_window never gets enough data to go
    # long, leaving the strategy flat with no trades.
    _seed_daily_bars(session_factory, "000001", date(2024, 1, 1), date(2024, 1, 5))
    client = _client_with(session_factory)

    run_response = client.post(
        "/api/backtests/run",
        json={
            "symbols": ["000001"],
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-01-05",
            "fast_window": 5,
            "slow_window": 20,
        },
    )
    assert run_response.status_code == 200
    run_id = run_response.json()["run_id"]

    detail = client.get(f"/api/backtests/{run_id}")

    assert detail.status_code == 200
    body = detail.json()
    assert body["symbol_source"] == "manual"
    assert body["selected_symbols"] == []


# ---------------------------------------------------------------------------
# Research pool — edge cases
# ---------------------------------------------------------------------------


def test_run_backtest_research_pool_end_date_beyond_latest_bars():
    """Symbols whose last bar is before the requested end_date still qualify."""
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Test", "SZ")
    # Bars only through Jan-15 but the request extends to Jan-31
    _seed_daily_bars(session_factory, "000001", date(2024, 1, 1), date(2024, 1, 15))
    client = _client_with(session_factory)

    response = client.post(
        "/api/backtests/run",
        json={
            "strategy_name": "moving_average",
            "symbol_source": "research_pool",
            "pool_max_symbols": 100,
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "fast_window": 5,
            "slow_window": 20,
        },
    )

    # 15 bars in a 31-day window is enough to qualify
    assert response.status_code == 200
    assert response.json()["selected_symbols"] == ["000001"]


def test_run_backtest_research_pool_diagnostics_on_no_symbols():
    """When zero research symbols match, the 400 response carries diagnostic info."""
    session_factory = _make_session_factory()
    # No stocks at all — no symbols will match
    client = _client_with(session_factory)

    response = client.post(
        "/api/backtests/run",
        json={
            "symbol_source": "research_pool",
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "fast_window": 5,
            "slow_window": 20,
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "research" in detail.lower()
    # Diagnostic fields should appear in the error
    assert "eligible" in detail.lower()


def test_run_backtest_research_pool_stock_with_few_bars_excluded():
    """A stock with only 1 bar in a 31-day window is excluded by the trading-day threshold."""
    session_factory = _make_session_factory()
    _seed_stock(session_factory, "000001", "Sparse", "SZ")
    # Only 1 bar — far below the effective minimum for a 31-day range
    _seed_daily_bars(session_factory, "000001", date(2024, 1, 15), date(2024, 1, 15))
    client = _client_with(session_factory)

    response = client.post(
        "/api/backtests/run",
        json={
            "symbol_source": "research_pool",
            "benchmark_symbol": None,
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "fast_window": 5,
            "slow_window": 20,
        },
    )

    assert response.status_code == 400
    assert "research" in response.json()["detail"].lower()


def test_backtest_request_allows_large_research_pool():
    request = BacktestRequest(
        strategy_name="stable_reversal",
        symbol_source="research_pool",
        pool_max_symbols=6000,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
    )

    assert request.pool_max_symbols == 6000


def test_backtest_request_rejects_unbounded_research_pool():
    from pydantic import ValidationError

    try:
        BacktestRequest(
            strategy_name="stable_reversal",
            symbol_source="research_pool",
            pool_max_symbols=6001,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
    except ValidationError:
        return
    raise AssertionError("pool_max_symbols above 6000 should be rejected")
