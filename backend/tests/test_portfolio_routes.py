from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_session
from app.main import create_app
from app.models.entities import Base, DailyBar, Stock


def _session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _client(session_factory: sessionmaker) -> TestClient:
    app = create_app()

    def override_get_session() -> Session:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)


def _seed_close(session_factory: sessionmaker, symbol: str, trade_date: date, close: float) -> None:
    session = session_factory()
    try:
        if session.get(Stock, symbol) is None:
            session.add(Stock(symbol=symbol, name=f"Stock {symbol}", exchange="SZ", status="active"))
        session.add(
            DailyBar(
                symbol=symbol,
                trade_date=trade_date,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1000.0,
                amount=close * 1000,
                adj="qfq",
            )
        )
        session.commit()
    finally:
        session.close()


def test_paper_portfolio_snapshot_persists_current_state_and_history():
    session_factory = _session_factory()
    first_date = date(2026, 7, 14)
    second_date = date(2026, 7, 15)
    _seed_close(session_factory, "000001", first_date, 10.0)
    _seed_close(session_factory, "000001", second_date, 12.0)
    client = _client(session_factory)

    empty = client.get("/api/portfolio/current")
    assert empty.status_code == 200
    assert empty.json()["cash"] == 0.0
    assert empty.json()["positions"] == []

    first = client.put(
        "/api/portfolio/snapshot",
        json={"as_of_date": first_date.isoformat(), "cash": 1_000, "positions": [{"symbol": "000001", "quantity": 200}]},
    )
    assert first.status_code == 200, first.text
    assert first.json()["position_value"] == 2_000.0
    assert first.json()["equity"] == 3_000.0
    assert first.json()["positions"][0]["reference_price"] == 10.0

    second = client.put(
        "/api/portfolio/snapshot",
        json={"as_of_date": second_date.isoformat(), "cash": 1_500, "positions": [{"symbol": "000001", "quantity": 100}]},
    )
    assert second.status_code == 200, second.text
    assert second.json()["position_value"] == 1_200.0
    assert second.json()["equity"] == 2_700.0

    history = client.get("/api/portfolio/history")
    assert history.status_code == 200
    assert history.json() == [
        {"as_of_date": first_date.isoformat(), "cash": 1_000.0, "position_value": 2_000.0, "equity": 3_000.0},
        {"as_of_date": second_date.isoformat(), "cash": 1_500.0, "position_value": 1_200.0, "equity": 2_700.0},
    ]


def test_paper_portfolio_rejects_unknown_symbol_and_missing_local_close():
    session_factory = _session_factory()
    client = _client(session_factory)

    unknown = client.put(
        "/api/portfolio/snapshot",
        json={"as_of_date": "2026-07-14", "cash": 1_000, "positions": [{"symbol": "000001", "quantity": 100}]},
    )
    assert unknown.status_code == 400
    assert "unknown" in unknown.json()["detail"]

    _seed_close(session_factory, "000001", date(2026, 7, 13), 10.0)
    missing_close = client.put(
        "/api/portfolio/snapshot",
        json={"as_of_date": "2026-07-14", "cash": 1_000, "positions": [{"symbol": "000001", "quantity": 100}]},
    )
    assert missing_close.status_code == 400
    assert "No local close" in missing_close.json()["detail"]


def test_paper_portfolio_rejects_backdated_snapshot_after_current_state_exists():
    session_factory = _session_factory()
    first_date = date(2026, 7, 14)
    later_date = date(2026, 7, 15)
    _seed_close(session_factory, "000001", first_date, 10.0)
    _seed_close(session_factory, "000001", later_date, 11.0)
    client = _client(session_factory)
    assert client.put(
        "/api/portfolio/snapshot",
        json={"as_of_date": later_date.isoformat(), "cash": 1_000, "positions": [{"symbol": "000001", "quantity": 100}]},
    ).status_code == 200

    backdated = client.put(
        "/api/portfolio/snapshot",
        json={"as_of_date": first_date.isoformat(), "cash": 1_000, "positions": [{"symbol": "000001", "quantity": 100}]},
    )
    assert backdated.status_code == 409
    assert "earlier" in backdated.json()["detail"]


def test_paper_portfolio_replaces_a_same_day_snapshot_instead_of_duplicating_history():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_close(session_factory, "000001", as_of_date, 10.0)
    client = _client(session_factory)
    payload = {"as_of_date": as_of_date.isoformat(), "cash": 1_000, "positions": [{"symbol": "000001", "quantity": 100}]}
    assert client.put("/api/portfolio/snapshot", json=payload).status_code == 200
    corrected = client.put("/api/portfolio/snapshot", json={**payload, "cash": 2_000})

    assert corrected.status_code == 200
    assert corrected.json()["equity"] == 3_000.0
    history = client.get("/api/portfolio/history")
    assert len(history.json()) == 1
    assert history.json()[0]["cash"] == 2_000.0
