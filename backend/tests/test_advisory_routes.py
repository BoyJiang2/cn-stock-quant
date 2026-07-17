from dataclasses import replace
from datetime import date, datetime, timedelta
import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_session
from app.core.config import settings as app_settings
from app.ai_advisory.service import create_advisory, stream_advisory_summary
from app.main import create_app
from app.models.entities import (
    AdvisoryNotificationDelivery,
    AdvisoryRun,
    BacktestRun,
    BacktestWalkForwardValidation,
    Base,
    DailyBar,
    IndexDailyBar,
    NewsItem,
    Stock,
    TradingCalendar,
)
from app.notifications import NotificationReceipt
from app.schemas.advisory import AdvisoryRequest


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


def _seed_bars(session_factory: sessionmaker, symbol: str, as_of_date: date) -> None:
    session = session_factory()
    try:
        session.add(Stock(symbol=symbol, name=f"Stock {symbol}", exchange="SZ", status="active"))
        for index in range(70):
            trade_date = as_of_date - timedelta(days=69 - index)
            close = 10.0 + index * 0.1
            session.add(
                DailyBar(
                    symbol=symbol,
                    trade_date=trade_date,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=1000.0,
                    amount=100_000.0,
                    adj="qfq",
                )
            )
        session.commit()
    finally:
        session.close()


def _seed_index_bars(session_factory: sessionmaker, as_of_date: date) -> None:
    session = session_factory()
    try:
        for index in range(130):
            trade_date = as_of_date - timedelta(days=129 - index)
            close = 3000.0 + index * 2.0
            session.add(
                IndexDailyBar(
                    symbol="000300",
                    trade_date=trade_date,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=1000.0,
                    amount=100_000.0,
                )
            )
        session.commit()
    finally:
        session.close()


def _seed_open_calendar(session_factory: sessionmaker, start_date: date, end_date: date) -> None:
    session = session_factory()
    try:
        current = start_date
        while current <= end_date:
            session.merge(TradingCalendar(trade_date=current, is_open=True))
            current += timedelta(days=1)
        session.commit()
    finally:
        session.close()


def _seed_news(
    session_factory: sessionmaker,
    *,
    symbol: str,
    source_id: str,
    published_at: datetime,
    fetched_at: datetime,
    event_type: str,
) -> None:
    session = session_factory()
    try:
        session.add(
            NewsItem(
                source="test_news",
                source_id=source_id,
                symbol=symbol,
                title=f"{event_type} test news",
                body="",
                event_type=event_type,
                sentiment_label="negative" if event_type != "neutral" else "",
                published_at=published_at,
                fetched_at=fetched_at,
            )
        )
        session.commit()
    finally:
        session.close()


def _seed_walk_forward_validation(
    session_factory: sessionmaker,
    *,
    as_of_date: date,
    eligibility_status: str = "eligible",
    strategy_name: str = "moving_average",
    strategy_parameters: dict | None = None,
    backtest_end_date: date | None = None,
) -> int:
    session = session_factory()
    try:
        run = BacktestRun(
            strategy_name=strategy_name,
            start_date=as_of_date - timedelta(days=180),
            end_date=backtest_end_date or as_of_date,
            initial_cash=100_000,
            final_equity=110_000,
            total_return=0.1,
            annual_return=0.1,
            max_drawdown=-0.1,
            sharpe=1.0,
        )
        session.add(run)
        session.flush()
        validation = BacktestWalkForwardValidation(
            backtest_run_id=run.id,
            status="completed",
            eligibility_status=eligibility_status,
            spec_json=json.dumps(
                {
                    "strategy_name": strategy_name,
                    "strategy_parameters": strategy_parameters or {"fast_window": 5, "slow_window": 20},
                    "windows": [{"oos_end_date": as_of_date.isoformat()}],
                },
                sort_keys=True,
            ),
            result_json=json.dumps(
                {
                    "aggregate": {"excess_return": 0.02},
                    "cost_stress_aggregate": {"excess_return": 0.01},
                },
                sort_keys=True,
            ),
            quality_json=json.dumps({"oos_trading_days": 126}, sort_keys=True),
            source_provenance_fingerprint="source-test",
            fingerprint=f"validation-{run.id}",
        )
        session.add(validation)
        session.commit()
        return validation.id
    finally:
        session.close()


def test_advisory_capabilities_are_safe_by_default():
    response = _client(_session_factory()).get("/api/advisory/capabilities")

    assert response.status_code == 200
    assert response.json()["broker_execution"] is False
    assert response.json()["requires_human_confirmation"] is True


def test_advisory_draft_runs_strategy_risk_and_round_lot_plan():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_bars(session_factory, "000001", as_of_date)

    response = _client(session_factory).post(
        "/api/advisory/drafts",
        json={
            "strategy_name": "moving_average",
            "as_of_date": as_of_date.isoformat(),
            "symbols": ["000001"],
            "cash": 100_000,
            "strategy_parameters": {"fast_window": 5, "slow_window": 20},
            "max_symbol_weight": 0.1,
            "max_total_weight": 0.1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "draft"
    assert body["accepted_target_weights"] == {"000001": 0.1}
    assert body["trade_plan"][0]["side"] == "buy"
    assert body["trade_plan"][0]["quantity"] % 100 == 0
    session = session_factory()
    try:
        record = session.get(AdvisoryRun, body["id"])
        assert record is not None
        assert record.status == "draft"
        assert "accepted" in record.risk_json
    finally:
        session.close()


def test_advisory_attaches_matching_eligible_walk_forward_validation():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_bars(session_factory, "000001", as_of_date)
    validation_id = _seed_walk_forward_validation(
        session_factory,
        as_of_date=as_of_date,
        backtest_end_date=as_of_date + timedelta(days=1),
    )

    response = _client(session_factory).post(
        "/api/advisory/drafts",
        json={
            "strategy_name": "moving_average",
            "as_of_date": as_of_date.isoformat(),
            "symbols": ["000001"],
            "cash": 100_000,
            "strategy_parameters": {"fast_window": 5, "slow_window": 20},
            "validation_id": validation_id,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["validation_evidence"]["validation_id"] == validation_id
    options = _client(session_factory).get(
        "/api/advisory/eligible-validations",
        params={"as_of_date": as_of_date.isoformat()},
    )
    assert options.status_code == 200
    assert options.json()[0]["id"] == validation_id
    assert options.json()[0]["as_of_date"] == as_of_date.isoformat()
    session = session_factory()
    try:
        record = session.get(AdvisoryRun, body["id"])
        assert json.loads(record.risk_json)["evidence"]["validation"]["validation_id"] == validation_id
    finally:
        session.close()


def test_advisory_rejects_validation_with_parameter_or_as_of_mismatch():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_bars(session_factory, "000001", as_of_date)
    validation_id = _seed_walk_forward_validation(session_factory, as_of_date=as_of_date)
    client = _client(session_factory)
    base_payload = {
        "strategy_name": "moving_average",
        "as_of_date": as_of_date.isoformat(),
        "symbols": ["000001"],
        "cash": 100_000,
        "validation_id": validation_id,
    }

    parameter_mismatch = client.post(
        "/api/advisory/drafts",
        json={**base_payload, "strategy_parameters": {"fast_window": 6, "slow_window": 20}},
    )
    assert parameter_mismatch.status_code == 400
    assert "different strategy parameters" in parameter_mismatch.json()["detail"]
    as_of_mismatch = client.post(
        "/api/advisory/drafts",
        json={
            **base_payload,
            "as_of_date": (as_of_date - timedelta(days=1)).isoformat(),
            "strategy_parameters": {"fast_window": 5, "slow_window": 20},
        },
    )
    assert as_of_mismatch.status_code == 400
    assert "as-of date" in as_of_mismatch.json()["detail"]


def test_advisory_rejects_ineligible_walk_forward_validation():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_bars(session_factory, "000001", as_of_date)
    validation_id = _seed_walk_forward_validation(
        session_factory,
        as_of_date=as_of_date,
        eligibility_status="not_eligible_pit_degraded",
    )

    response = _client(session_factory).post(
        "/api/advisory/drafts",
        json={
            "strategy_name": "moving_average",
            "as_of_date": as_of_date.isoformat(),
            "symbols": ["000001"],
            "cash": 100_000,
            "strategy_parameters": {"fast_window": 5, "slow_window": 20},
            "validation_id": validation_id,
        },
    )

    assert response.status_code == 400
    assert "not eligible" in response.json()["detail"]


def test_advisory_retains_deterministic_draft_when_remote_llm_is_not_enabled():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_bars(session_factory, "000001", as_of_date)

    response = _client(session_factory).post(
        "/api/advisory/drafts",
        json={
            "strategy_name": "moving_average",
            "as_of_date": as_of_date.isoformat(),
            "symbols": ["000001"],
            "cash": 100_000,
            "strategy_parameters": {"fast_window": 5, "slow_window": 20},
            "allow_remote_llm": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "draft"
    assert body["remote_llm_enabled"] is False
    assert body["trade_plan"]
    assert any("Remote LLM was requested" in warning for warning in body["warnings"])


def test_advisory_snapshot_uses_only_market_and_news_known_by_as_of_date():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_bars(session_factory, "000001", as_of_date)
    _seed_index_bars(session_factory, as_of_date)
    _seed_news(
        session_factory,
        symbol="000001",
        source_id="known-severe",
        published_at=datetime(2026, 7, 13, 9, 0),
        fetched_at=datetime(2026, 7, 13, 9, 5),
        event_type="severe_company_risk",
    )
    _seed_news(
        session_factory,
        symbol="000001",
        source_id="future-observed",
        published_at=datetime(2026, 7, 13, 10, 0),
        fetched_at=datetime(2026, 7, 15, 9, 0),
        event_type="company_risk",
    )

    response = _client(session_factory).post(
        "/api/advisory/drafts",
        json={
            "strategy_name": "moving_average",
            "as_of_date": as_of_date.isoformat(),
            "symbols": ["000001"],
            "cash": 100_000,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["market_evidence"]["available"] is True
    assert body["market_evidence"]["data_end_date"] == as_of_date.isoformat()
    assert body["news_evidence"]["availability_mode"] == "observed"
    assert body["news_evidence"]["severe_company_risk_count"] == 1
    assert body["news_evidence"]["company_risk_count"] == 0
    assert [item["title"] for item in body["news_evidence"]["items"]] == [
        "severe_company_risk test news"
    ]
    assert body["factor_evidence"]["availability_mode"] == "observed_trailing"
    assert body["factor_evidence"]["data_end_date"] == as_of_date.isoformat()
    assert body["factor_evidence"]["symbols"][0]["available"] is True
    assert all(
        "return" not in value["name"]
        for value in body["factor_evidence"]["symbols"][0]["values"]
    )
    session = session_factory()
    try:
        record = session.get(AdvisoryRun, body["id"])
        assert record is not None
        assert json.loads(record.risk_json)["evidence"]["factors"] == body["factor_evidence"]
    finally:
        session.close()


def test_advisory_factor_snapshot_excludes_future_bars():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_bars(session_factory, "000001", as_of_date)
    client = _client(session_factory)
    payload = {
        "strategy_name": "moving_average",
        "as_of_date": as_of_date.isoformat(),
        "symbols": ["000001"],
        "cash": 100_000,
        "strategy_parameters": {"fast_window": 5, "slow_window": 20},
    }
    before = client.post("/api/advisory/drafts", json=payload)
    assert before.status_code == 200

    session = session_factory()
    try:
        for offset in range(1, 11):
            future_date = as_of_date + timedelta(days=offset)
            session.add(
                DailyBar(
                    symbol="000001",
                    trade_date=future_date,
                    open=1_000.0,
                    high=1_000.0,
                    low=1_000.0,
                    close=1_000.0,
                    volume=1.0,
                    amount=1_000.0,
                    adj="qfq",
                )
            )
        session.commit()
    finally:
        session.close()

    after = client.post("/api/advisory/drafts", json=payload)
    assert after.status_code == 200
    assert after.json()["factor_evidence"] == before.json()["factor_evidence"]


def test_advisory_requires_an_as_of_close_for_every_selected_symbol():
    session_factory = _session_factory()
    _seed_bars(session_factory, "000001", date(2026, 7, 13))

    response = _client(session_factory).post(
        "/api/advisory/drafts",
        json={
            "strategy_name": "moving_average",
            "as_of_date": "2026-07-14",
            "symbols": ["000001"],
            "cash": 100_000,
        },
    )

    assert response.status_code == 400
    assert "requested as-of date" in response.json()["detail"]


def test_advisory_stream_requires_approval_and_server_configuration():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_bars(session_factory, "000001", as_of_date)
    client = _client(session_factory)
    draft = client.post(
        "/api/advisory/drafts",
        json={
            "strategy_name": "moving_average",
            "as_of_date": as_of_date.isoformat(),
            "symbols": ["000001"],
            "cash": 100_000,
            "allow_remote_llm": True,
        },
    )

    response = client.post(f"/api/advisory/drafts/{draft.json()['id']}/stream")

    assert response.status_code == 409
    assert "not enabled" in response.json()["detail"]


def test_advisory_stream_persists_text_from_the_provider():
    class FakeProvider:
        def stream_text(self, *, system_prompt: str, user_prompt: str):
            assert "risk-gated" in system_prompt
            assert '"strategy_name": "moving_average"' in user_prompt
            assert '"market_evidence"' in user_prompt
            assert '"news_evidence"' in user_prompt
            assert '"factor_evidence"' in user_prompt
            yield "First part. "
            yield "Second part."

    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_bars(session_factory, "000001", as_of_date)
    session = session_factory()
    try:
        draft = create_advisory(
            session,
            AdvisoryRequest(
                strategy_name="moving_average",
                as_of_date=as_of_date,
                symbols=["000001"],
                cash=100_000,
                allow_remote_llm=True,
            ),
            remote_llm_available=True,
        )
        assert list(
            stream_advisory_summary(
                session,
                draft.id,
                FakeProvider(),
                provider_name="test",
                model_name="test-model",
            )
        ) == ["First part. ", "Second part."]
        record = session.get(AdvisoryRun, draft.id)
        assert record is not None
        assert record.status == "draft"
        assert record.llm_summary == "First part. Second part."
        assert record.llm_provider == "test"
    finally:
        session.close()


def test_advisory_lifecycle_requires_human_review_or_rejection_and_persists_reason():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_bars(session_factory, "000001", as_of_date)
    _seed_open_calendar(session_factory, as_of_date + timedelta(days=1), as_of_date + timedelta(days=14))
    client = _client(session_factory)
    draft = client.post(
        "/api/advisory/drafts",
        json={
            "strategy_name": "moving_average",
            "as_of_date": as_of_date.isoformat(),
            "symbols": ["000001"],
            "cash": 100_000,
        },
    )
    advisory_id = draft.json()["id"]

    initial = client.get(f"/api/advisory/drafts/{advisory_id}/status")
    assert initial.status_code == 200
    assert initial.json()["status"] == "draft"
    assert initial.json()["earliest_execution_date"] == (as_of_date + timedelta(days=1)).isoformat()

    reviewed = client.post(f"/api/advisory/drafts/{advisory_id}/review")
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "reviewed"
    assert client.post(f"/api/advisory/drafts/{advisory_id}/review").status_code == 200

    rejected = client.post(f"/api/advisory/drafts/{advisory_id}/reject", json={"reason": "Position size is too concentrated."})
    assert rejected.status_code == 200
    assert rejected.json() == {
        "id": advisory_id,
        "status": "rejected",
        "rejection_reason": "Position size is too concentrated.",
    }
    assert client.get(f"/api/advisory/drafts/{advisory_id}/status").json()["status"] == "rejected"


def test_research_agent_returns_only_cited_snapshot_facts():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_bars(session_factory, "000001", as_of_date)
    _seed_news(
        session_factory,
        symbol="000001",
        source_id="research-fact",
        published_at=datetime(2026, 7, 13, 9, 0),
        fetched_at=datetime(2026, 7, 13, 9, 5),
        event_type="announcement",
    )
    client = _client(session_factory)
    draft = client.post(
        "/api/advisory/drafts",
        json={"strategy_name": "moving_average", "as_of_date": as_of_date.isoformat(), "symbols": ["000001"], "cash": 100_000},
    )
    response = client.get(f"/api/advisory/drafts/{draft.json()['id']}/research")

    assert response.status_code == 200
    facts = response.json()["facts"]
    assert any(item["source_type"] == "news" and "2026-07-13" in item["citation"] for item in facts)
    assert client.get("/api/advisory/drafts/999999/research").status_code == 404


def test_advisory_expires_after_a_later_local_trading_date_is_available():
    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_bars(session_factory, "000001", as_of_date)
    _seed_open_calendar(session_factory, as_of_date + timedelta(days=1), as_of_date + timedelta(days=14))
    client = _client(session_factory)
    draft = client.post(
        "/api/advisory/drafts",
        json={
            "strategy_name": "moving_average",
            "as_of_date": as_of_date.isoformat(),
            "symbols": ["000001"],
            "cash": 100_000,
        },
    )
    advisory_id = draft.json()["id"]
    session = session_factory()
    try:
        for offset in range(1, 5):
            trade_date = as_of_date + timedelta(days=offset)
            session.add(
                DailyBar(
                    symbol="000001",
                    trade_date=trade_date,
                    open=20.0,
                    high=20.0,
                    low=20.0,
                    close=20.0,
                    volume=1_000.0,
                    amount=20_000.0,
                    adj="qfq",
                )
            )
        session.commit()
    finally:
        session.close()

    status = client.get(f"/api/advisory/drafts/{advisory_id}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "expired"
    assert client.post(f"/api/advisory/drafts/{advisory_id}/review").status_code == 409


def test_reviewed_advisory_can_send_one_audited_wecom_notification(monkeypatch):
    class FakeWeComSender:
        sent_messages: list[str] = []

        def __init__(self, webhook_url: str) -> None:
            assert webhook_url == "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test"

        def send_text(self, text: str) -> NotificationReceipt:
            self.sent_messages.append(text)
            return NotificationReceipt(channel="wecom_group_webhook", provider_message="ok")

    session_factory = _session_factory()
    as_of_date = date(2026, 7, 14)
    _seed_bars(session_factory, "000001", as_of_date)
    client = _client(session_factory)
    draft = client.post(
        "/api/advisory/drafts",
        json={
            "strategy_name": "moving_average",
            "as_of_date": as_of_date.isoformat(),
            "symbols": ["000001"],
            "cash": 100_000,
        },
    )
    advisory_id = draft.json()["id"]
    reviewed = client.post(f"/api/advisory/drafts/{advisory_id}/review")
    assert reviewed.status_code == 200

    monkeypatch.setattr(
        "app.api.routes.advisory.settings",
        replace(
            app_settings,
            wecom_webhook_configured=True,
            wecom_webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
        ),
    )
    monkeypatch.setattr("app.api.routes.advisory.WeComGroupWebhookSender", FakeWeComSender)

    sent = client.post(f"/api/advisory/drafts/{advisory_id}/notify/wecom")

    assert sent.status_code == 200
    assert sent.json()["status"] == "sent"
    assert "仅研究参考" in FakeWeComSender.sent_messages[0]
    duplicate = client.post(f"/api/advisory/drafts/{advisory_id}/notify/wecom")
    assert duplicate.status_code == 409
    session = session_factory()
    try:
        delivery = session.query(AdvisoryNotificationDelivery).one()
        assert delivery.status == "sent"
        assert delivery.attempts == 1
        assert delivery.content_hash
    finally:
        session.close()
