import json
from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_session
from app.main import create_app
from app.models.entities import AdvisoryRun, BacktestRun, BacktestRunProvenance, BacktestWalkForwardValidation, Base, DailyBar, Stock, TradingCalendar


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


def _seed_advisory(
    session_factory: sessionmaker,
    *,
    as_of_date: date,
    trade_plan: list[dict],
    accepted: dict[str, float],
    total_equity: float = 10_000.0,
    status: str = "draft",
) -> int:
    session = session_factory()
    try:
        record = AdvisoryRun(
            as_of_date=as_of_date,
            strategy_name="moving_average",
            status=status,
            total_equity=total_equity,
            request_hash="a" * 64,
            request_json=json.dumps(
                {
                    "positions": {
                        item["symbol"]: item["current_quantity"]
                        for item in trade_plan
                        if isinstance(item.get("symbol"), str) and "current_quantity" in item
                    }
                }
            ),
            risk_json=json.dumps({"accepted": accepted}),
            trade_plan_json=json.dumps(trade_plan),
            remote_llm_requested=False,
            llm_summary="",
            reviewed_at=datetime.utcnow() if status == "reviewed" else None,
        )
        session.add(record)
        session.commit()
        return record.id
    finally:
        session.close()


def _attach_multi_window_validation(
    session_factory: sessionmaker,
    *,
    advisory_id: int,
    as_of_date: date,
    window_count: int = 3,
) -> int:
    session = session_factory()
    try:
        advisory = session.get(AdvisoryRun, advisory_id)
        assert advisory is not None
        run = BacktestRun(
            strategy_name=advisory.strategy_name,
            start_date=as_of_date.replace(year=as_of_date.year - 1),
            end_date=as_of_date,
            initial_cash=100_000,
            final_equity=110_000,
            total_return=0.1,
            annual_return=0.1,
            max_drawdown=-0.1,
            sharpe=1.0,
        )
        session.add(run)
        session.flush()
        provenance = BacktestRunProvenance(
            run_id=run.id,
            status="recorded_unvalidated",
            spec_json="{}",
            universe_json="{}",
            result_json="{}",
            fingerprint="promotion-source",
        )
        session.add(provenance)
        window_dates = [as_of_date - timedelta(days=30 * offset) for offset in range(window_count - 1, -1, -1)]
        validation = BacktestWalkForwardValidation(
            backtest_run_id=run.id,
            status="completed",
            eligibility_status="eligible",
            spec_json=json.dumps(
                {
                    "strategy_name": advisory.strategy_name,
                    "source_backtest_run_id": run.id,
                    "source_provenance_fingerprint": provenance.fingerprint,
                    "windows": [
                        {
                            "oos_start_date": (item - timedelta(days=10)).isoformat(),
                            "oos_end_date": item.isoformat(),
                        }
                        for item in window_dates
                    ],
                }
            ),
            result_json=json.dumps({"window_results": [{} for _ in window_dates]}),
            quality_json="{}",
            source_provenance_fingerprint=provenance.fingerprint,
            fingerprint="promotion-validation",
        )
        session.add(validation)
        session.flush()
        risk = json.loads(advisory.risk_json)
        risk["evidence"] = {
            "validation": {
                "validation_id": validation.id,
                "backtest_run_id": run.id,
                "source_as_of_date": as_of_date.isoformat(),
                "fingerprint": validation.fingerprint,
            }
        }
        advisory.risk_json = json.dumps(risk)
        session.commit()
        return validation.id
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


def test_paper_portfolio_diagnostics_use_saved_positions_and_valuation_history():
    session_factory = _session_factory()
    first_date = date(2026, 7, 14)
    second_date = date(2026, 7, 15)
    for symbol, first_close, second_close in (("000001", 10.0, 8.0), ("000002", 10.0, 10.0)):
        _seed_close(session_factory, symbol, first_date, first_close)
        _seed_close(session_factory, symbol, second_date, second_close)
    client = _client(session_factory)
    assert client.put(
        "/api/portfolio/snapshot",
        json={"as_of_date": first_date.isoformat(), "cash": 0, "positions": [{"symbol": "000001", "quantity": 100}, {"symbol": "000002", "quantity": 100}]},
    ).status_code == 200
    assert client.put(
        "/api/portfolio/snapshot",
        json={"as_of_date": second_date.isoformat(), "cash": 0, "positions": [{"symbol": "000001", "quantity": 100}, {"symbol": "000002", "quantity": 100}]},
    ).status_code == 200

    diagnostics = client.get("/api/portfolio/diagnostics")
    assert diagnostics.status_code == 200
    body = diagnostics.json()
    assert body["gross_exposure"] == 1.0
    assert body["largest_position_weight"] == 0.555556
    assert body["current_drawdown"] == -0.1
    assert body["max_drawdown"] == -0.1
    assert any("Largest holding" in warning for warning in body["warnings"])


def test_portfolio_review_recomputes_trade_deltas_from_current_snapshot():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_close(session_factory, "000001", as_of_date, 10.0)
    _seed_close(session_factory, "000002", as_of_date, 20.0)
    advisory_id = _seed_advisory(
        session_factory,
        as_of_date=as_of_date,
        accepted={"000001": 0.4, "000002": 0.2},
        trade_plan=[
            {
                "symbol": "000001",
                "side": "buy",
                "current_quantity": 100,
                "target_quantity": 300,
                "quantity": 200,
                "reference_price": 10.0,
                "estimated_amount": 2_000.0,
            },
            {
                "symbol": "000002",
                "side": "buy",
                "current_quantity": 0,
                "target_quantity": 100,
                "quantity": 100,
                "reference_price": 20.0,
                "estimated_amount": 2_000.0,
            },
        ],
    )
    client = _client(session_factory)
    assert client.put(
        "/api/portfolio/snapshot",
        json={"as_of_date": as_of_date.isoformat(), "cash": 1_000, "positions": [{"symbol": "000001", "quantity": 200}]},
    ).status_code == 200

    response = client.get(f"/api/portfolio/review?advisory_id={advisory_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requires_refresh"] is True
    assert "differs" in body["warnings"][0]
    rows = {row["symbol"]: row for row in body["rows"]}
    assert rows["000001"]["current_quantity"] == 200
    assert rows["000001"]["advisory_current_quantity"] == 100
    assert rows["000001"]["quantity_delta"] == 100
    assert rows["000001"]["suggested_side"] == "buy"
    assert rows["000002"]["quantity_delta"] == 100
    assert rows["000002"]["estimated_delta_amount"] == 2_000.0
    assert client.get("/api/portfolio/current").json()["positions"] == [
        {"symbol": "000001", "name": "Stock 000001", "quantity": 200, "reference_price": 10.0, "price_date": as_of_date.isoformat(), "market_value": 2_000.0}
    ]
    assert client.get("/api/portfolio/history").json() == [
        {"as_of_date": as_of_date.isoformat(), "cash": 1_000.0, "position_value": 2_000.0, "equity": 3_000.0}
    ]


def test_portfolio_review_requires_refresh_when_snapshot_date_differs_and_404s_for_unknown_advisory():
    session_factory = _session_factory()
    advisory_date = date(2026, 7, 14)
    snapshot_date = date(2026, 7, 15)
    _seed_close(session_factory, "000001", snapshot_date, 10.0)
    advisory_id = _seed_advisory(session_factory, as_of_date=advisory_date, accepted={}, trade_plan=[])
    client = _client(session_factory)
    assert client.put(
        "/api/portfolio/snapshot",
        json={"as_of_date": snapshot_date.isoformat(), "cash": 1_000, "positions": [{"symbol": "000001", "quantity": 100}]},
    ).status_code == 200

    stale = client.get(f"/api/portfolio/review?advisory_id={advisory_id}")
    assert stale.status_code == 200
    assert stale.json()["requires_refresh"] is True
    assert "differs" in stale.json()["warnings"][0]
    assert client.get("/api/portfolio/review?advisory_id=999").status_code == 404


def test_portfolio_review_is_current_only_when_snapshot_matches_advisory_positions_and_equity():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_close(session_factory, "000001", as_of_date, 10.0)
    advisory_id = _seed_advisory(
        session_factory,
        as_of_date=as_of_date,
        total_equity=2_000.0,
        accepted={"000001": 0.5},
        trade_plan=[
            {
                "symbol": "000001",
                "side": "buy",
                "current_quantity": 100,
                "target_quantity": 200,
                "quantity": 100,
                "reference_price": 10.0,
                "estimated_amount": 1_000.0,
            }
        ],
    )
    client = _client(session_factory)
    assert client.put(
        "/api/portfolio/snapshot",
        json={"as_of_date": as_of_date.isoformat(), "cash": 1_000, "positions": [{"symbol": "000001", "quantity": 100}]},
    ).status_code == 200

    response = client.get(f"/api/portfolio/review?advisory_id={advisory_id}")
    assert response.status_code == 200
    assert response.json()["requires_refresh"] is False
    assert response.json()["rows"][0]["quantity_delta"] == 100


def test_portfolio_review_rejects_corrupt_or_non_finite_persisted_advisory_data():
    session_factory = _session_factory()
    advisory_id = _seed_advisory(session_factory, as_of_date=date(2026, 7, 14), accepted={}, trade_plan=[])
    session = session_factory()
    try:
        record = session.get(AdvisoryRun, advisory_id)
        assert record is not None
        record.risk_json = '{"accepted":{"000001":NaN}}'
        session.commit()
    finally:
        session.close()

    response = _client(session_factory).get(f"/api/portfolio/review?advisory_id={advisory_id}")
    assert response.status_code == 409
    assert "cannot be reviewed" in response.json()["detail"]


def test_multi_window_oos_validation_is_required_before_paper_promotion():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_close(session_factory, "000001", as_of_date, 10.0)
    advisory_id = _seed_advisory(
        session_factory,
        as_of_date=as_of_date,
        accepted={"000001": 0.4},
        trade_plan=[],
        status="reviewed",
    )
    _attach_multi_window_validation(session_factory, advisory_id=advisory_id, as_of_date=as_of_date, window_count=3)
    client = _client(session_factory)

    eligibility = client.get(f"/api/portfolio/promotion-eligibility?advisory_id={advisory_id}")
    promoted = client.post(f"/api/portfolio/promote-advisory/{advisory_id}")

    assert eligibility.status_code == 200
    assert eligibility.json()["eligible"] is True
    assert eligibility.json()["oos_window_count"] == 3
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["cash"] == 6_000.0
    assert promoted.json()["positions"][0]["symbol"] == "000001"
    assert promoted.json()["positions"][0]["quantity"] == 400


def test_paper_promotion_rejects_single_window_or_unreviewed_advisories():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_close(session_factory, "000001", as_of_date, 10.0)
    advisory_id = _seed_advisory(
        session_factory,
        as_of_date=as_of_date,
        accepted={"000001": 0.4},
        trade_plan=[],
        status="reviewed",
    )
    _attach_multi_window_validation(session_factory, advisory_id=advisory_id, as_of_date=as_of_date, window_count=1)
    client = _client(session_factory)

    response = client.post(f"/api/portfolio/promote-advisory/{advisory_id}")

    assert response.status_code == 409
    assert "three distinct rolling OOS windows" in response.json()["detail"]
    assert client.get("/api/portfolio/current").json()["positions"] == []


def test_paper_promotion_rejects_overallocated_persisted_target_weights():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_close(session_factory, "000001", as_of_date, 10.0)
    _seed_close(session_factory, "000002", as_of_date, 10.0)
    advisory_id = _seed_advisory(
        session_factory,
        as_of_date=as_of_date,
        accepted={"000001": 0.7, "000002": 0.7},
        trade_plan=[],
        status="reviewed",
    )
    _attach_multi_window_validation(session_factory, advisory_id=advisory_id, as_of_date=as_of_date, window_count=3)

    response = _client(session_factory).post(f"/api/portfolio/promote-advisory/{advisory_id}")

    assert response.status_code == 409
    assert "exceed total capital" in response.json()["detail"]


def test_paper_promotion_rejects_reviewed_advisory_after_its_execution_window_expires():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_close(session_factory, "000001", as_of_date, 10.0)
    advisory_id = _seed_advisory(
        session_factory,
        as_of_date=as_of_date,
        accepted={"000001": 0.4},
        trade_plan=[],
        status="reviewed",
    )
    _attach_multi_window_validation(session_factory, advisory_id=advisory_id, as_of_date=as_of_date, window_count=3)
    session = session_factory()
    try:
        advisory = session.get(AdvisoryRun, advisory_id)
        assert advisory is not None
        advisory.request_json = json.dumps({"symbols": ["000001"], "positions": {}})
        for offset in range(1, 4):
            session.add(TradingCalendar(trade_date=as_of_date + timedelta(days=offset), is_open=True))
            session.add(
                DailyBar(
                    symbol="000001",
                    trade_date=as_of_date + timedelta(days=offset),
                    open=10.0,
                    high=10.0,
                    low=10.0,
                    close=10.0,
                    volume=1_000.0,
                    amount=10_000.0,
                    adj="qfq",
                )
            )
        session.commit()
    finally:
        session.close()

    response = _client(session_factory).post(f"/api/portfolio/promote-advisory/{advisory_id}")

    assert response.status_code == 409
    assert "expired" in response.json()["detail"]


def test_paper_promotion_does_not_overwrite_a_different_same_day_snapshot():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_close(session_factory, "000001", as_of_date, 10.0)
    advisory_id = _seed_advisory(
        session_factory,
        as_of_date=as_of_date,
        accepted={"000001": 0.4},
        trade_plan=[],
        status="reviewed",
    )
    _attach_multi_window_validation(session_factory, advisory_id=advisory_id, as_of_date=as_of_date, window_count=3)
    client = _client(session_factory)
    assert client.put(
        "/api/portfolio/snapshot",
        json={"as_of_date": as_of_date.isoformat(), "cash": 9_000, "positions": [{"symbol": "000001", "quantity": 100}]},
    ).status_code == 200

    response = client.post(f"/api/portfolio/promote-advisory/{advisory_id}")

    assert response.status_code == 409
    assert "will not overwrite" in response.json()["detail"]
    assert client.get("/api/portfolio/current").json()["cash"] == 9_000.0


def test_paper_promotion_rejects_non_positive_persisted_total_equity():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_close(session_factory, "000001", as_of_date, 10.0)
    advisory_id = _seed_advisory(
        session_factory,
        as_of_date=as_of_date,
        accepted={"000001": 0.4},
        trade_plan=[],
        status="reviewed",
        total_equity=-1.0,
    )
    _attach_multi_window_validation(session_factory, advisory_id=advisory_id, as_of_date=as_of_date, window_count=3)

    response = _client(session_factory).post(f"/api/portfolio/promote-advisory/{advisory_id}")

    assert response.status_code == 409
    assert "finite positive" in response.json()["detail"]
