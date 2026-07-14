from datetime import date, timedelta
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_session
from app.core.config import settings as app_settings
from app.ai_advisory.service import create_advisory, stream_advisory_summary
from app.main import create_app
from app.models.entities import AdvisoryNotificationDelivery, AdvisoryRun, Base, DailyBar, Stock
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
    assert body["status"] == "llm_disabled"
    assert body["remote_llm_enabled"] is False
    assert body["trade_plan"]
    assert any("Remote LLM was requested" in warning for warning in body["warnings"])


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
        assert record.status == "llm_complete"
        assert record.llm_summary == "First part. Second part."
        assert record.llm_provider == "test"
    finally:
        session.close()


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
