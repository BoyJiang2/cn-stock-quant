import json
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_session
from app.main import create_app
from app.models.entities import Base, DailyBar, FactorExperiment, Stock
from app.schemas.factors import FactorExperimentRequest


def _client() -> TestClient:
    return _client_with_factory()[0]


def _client_with_factory() -> tuple[TestClient, sessionmaker]:
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
    return TestClient(app), factory


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
    assert len(body["run_metadata"]["run_hash"]) == 64
    assert body["run_metadata"]["factor_implementation_version"] == "builtin-factor-lab-v1"
    assert len(body["run_metadata"]["ohlcv_snapshot_fingerprint"]) == 64
    assert len(body["run_metadata"]["experiment_fingerprint"]) == 64
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


def test_factor_experiment_persists_replay_inputs_summary_and_metadata():
    client, factory = _client_with_factory()
    payload = {
        "symbol_source": "manual",
        "symbols": ["000001", "000002", "000003", "000004", "000005"],
        "factor_names": ["momentum_5d"],
        "start_date": "2024-01-01",
        "end_date": "2024-03-20",
        "horizon": 5,
        "n_groups": 5,
    }

    response = client.post("/api/factors/experiments/run", json=payload)

    assert response.status_code == 200
    body = response.json()
    session = factory()
    try:
        experiment = session.scalar(select(FactorExperiment))
        assert experiment is not None
        assert experiment.experiment_fingerprint == body["run_metadata"]["experiment_fingerprint"]
        assert json.loads(experiment.request_json) == {**payload, "pool_max_symbols": 100}
        assert json.loads(experiment.response_summary_json)["summaries"] == body["summaries"]
        metadata = json.loads(experiment.run_metadata_json)
        assert metadata["ohlcv_snapshot_fingerprint"] == body["run_metadata"]["ohlcv_snapshot_fingerprint"]
        assert metadata["run_hash"] == body["run_metadata"]["run_hash"]
    finally:
        session.close()


def test_factor_run_hash_changes_when_used_ohlcv_snapshot_changes():
    client, factory = _client_with_factory()
    payload = {
        "symbol_source": "manual",
        "symbols": ["000001", "000002", "000003", "000004", "000005"],
        "factor_names": ["momentum_5d"],
        "start_date": "2024-01-01",
        "end_date": "2024-03-20",
        "horizon": 5,
        "n_groups": 5,
    }
    first = client.post("/api/factors/experiments/run", json=payload)
    assert first.status_code == 200

    session = factory()
    try:
        bar = session.scalar(
            select(DailyBar).where(
                DailyBar.symbol == "000001",
                DailyBar.trade_date == date(2024, 2, 1),
            )
        )
        assert bar is not None
        bar.close += 1.0
        bar.high = max(bar.high, bar.close)
        session.commit()
    finally:
        session.close()

    second = client.post("/api/factors/experiments/run", json=payload)

    assert second.status_code == 200
    assert first.json()["run_metadata"]["ohlcv_snapshot_fingerprint"] != second.json()["run_metadata"]["ohlcv_snapshot_fingerprint"]
    assert first.json()["run_metadata"]["run_hash"] != second.json()["run_metadata"]["run_hash"]
    assert first.json()["run_metadata"]["experiment_fingerprint"] != second.json()["run_metadata"]["experiment_fingerprint"]


def test_factor_run_hash_changes_when_adjustment_mode_changes():
    client, factory = _client_with_factory()
    payload = {
        "symbol_source": "manual",
        "symbols": ["000001", "000002", "000003", "000004", "000005"],
        "factor_names": ["momentum_5d"],
        "start_date": "2024-01-01",
        "end_date": "2024-03-20",
        "horizon": 5,
        "n_groups": 5,
    }
    first = client.post("/api/factors/experiments/run", json=payload)
    assert first.status_code == 200

    session = factory()
    try:
        bar = session.scalar(
            select(DailyBar).where(
                DailyBar.symbol == "000001",
                DailyBar.trade_date == date(2024, 2, 1),
            )
        )
        assert bar is not None
        bar.adj = "hfq"
        session.commit()
    finally:
        session.close()

    second = client.post("/api/factors/experiments/run", json=payload)

    assert second.status_code == 200
    assert first.json()["run_metadata"]["ohlcv_snapshot_fingerprint"] != second.json()["run_metadata"]["ohlcv_snapshot_fingerprint"]


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
