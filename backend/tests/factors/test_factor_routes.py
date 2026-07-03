from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_session
from app.main import create_app
from app.models.entities import Base, DailyBar, Stock
from app.schemas.factors import FactorExperimentRequest


def _client() -> TestClient:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    session = factory()
    try:
        start = date(2024, 1, 1)
        for symbol_index in range(5):
            symbol = f"{symbol_index + 1:06d}"
            session.add(Stock(symbol=symbol, name=f"Stock {symbol}", exchange="SZ", status="active"))
            close = 10.0 + symbol_index
            for day in range(80):
                close *= 1.0 + (symbol_index + 1) * 0.0005
                session.add(
                    DailyBar(
                        symbol=symbol,
                        trade_date=start + timedelta(days=day),
                        open=close,
                        high=close * 1.01,
                        low=close * 0.99,
                        close=close,
                        volume=1000.0 + day,
                        amount=(1000.0 + day) * close,
                        adj="qfq",
                    )
                )
        session.commit()
    finally:
        session.close()

    app = create_app()

    def override_session():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def test_factor_list_exposes_builtin_metadata():
    response = _client().get("/api/factors")

    assert response.status_code == 200
    body = response.json()
    assert len(body) >= 20
    assert {"name": "momentum_20d", "direction": 1} in body
    assert {"name": "volatility_20d", "direction": -1} in body


def test_factor_experiment_runs_and_returns_json_safe_summary():
    response = _client().post(
        "/api/factors/experiments/run",
        json={
            "symbol_source": "manual",
            "symbols": ["000001", "000002", "000003", "000004", "000005"],
            "factor_names": ["momentum_5d", "volatility_20d"],
            "start_date": "2024-01-01",
            "end_date": "2024-03-20",
            "horizon": 5,
            "n_groups": 5,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["factor_count"] == 2
    assert len(body["selected_symbols"]) == 5
    assert len(body["summaries"]) == 2
    assert body["warnings"]
    assert any("not point-in-time" in warning for warning in body["warnings"])
    assert body["run_metadata"]["run_hash"]
    assert body["run_metadata"]["selected_symbol_count"] == 5
    assert body["run_metadata"]["selected_symbols"] == [
        "000001",
        "000002",
        "000003",
        "000004",
        "000005",
    ]
    assert body["run_metadata"]["factor_names"] == ["momentum_5d", "volatility_20d"]
    assert body["run_metadata"]["point_in_time"] is False
    assert body["run_metadata"]["degraded"] is True
    assert all(summary["n_dates"] >= 0 for summary in body["summaries"])


def test_factor_experiment_accepts_stock_name_in_manual_symbols():
    response = _client().post(
        "/api/factors/experiments/run",
        json={
            "symbol_source": "manual",
            "symbols": ["Stock 000001", "000002", "000003", "000004", "000005"],
            "factor_names": ["momentum_5d"],
            "start_date": "2024-01-01",
            "end_date": "2024-03-20",
            "horizon": 5,
            "n_groups": 5,
        },
    )

    assert response.status_code == 200
    assert response.json()["selected_symbols"] == [
        "000001",
        "000002",
        "000003",
        "000004",
        "000005",
    ]


def test_factor_experiment_rejects_unknown_factor():
    response = _client().post(
        "/api/factors/experiments/run",
        json={
            "symbol_source": "manual",
            "symbols": ["000001", "000002", "000003", "000004", "000005"],
            "factor_names": ["future_profit_magic"],
            "start_date": "2024-01-01",
            "end_date": "2024-03-20",
        },
    )

    assert response.status_code == 400
    assert "Unknown factors" in response.json()["detail"]


def test_factor_experiment_schema_allows_large_universe_for_research_runs():
    request = FactorExperimentRequest(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        pool_max_symbols=6000,
    )

    assert request.pool_max_symbols == 6000


def test_factor_experiment_schema_rejects_unbounded_universe():
    from pydantic import ValidationError

    try:
        FactorExperimentRequest(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            pool_max_symbols=6001,
        )
    except ValidationError:
        return
    raise AssertionError("pool_max_symbols above 6000 should be rejected")
