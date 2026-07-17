import hashlib
import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai_advisory.providers import (
    LLMProviderConfigurationError,
    OpenAIResponsesConfig,
    OpenAIResponsesProvider,
)
from app.ai_advisory.service import (
    AdvisoryInputError,
    create_advisory,
    stream_advisory_summary,
)
from app.core.config import settings
from app.core.database import SessionLocal, get_session
from app.data.repository import MarketDataRepository
from app.models.entities import AdvisoryNotificationDelivery, AdvisoryRun, BacktestRun, BacktestWalkForwardValidation, DailyBar
from app.notifications import NotificationDeliveryError, WeComGroupWebhookSender
from app.schemas.advisory import (
    AdvisoryNotificationResponse,
    AdvisoryRejectRequest,
    AdvisoryRejectResponse,
    AdvisoryRequest,
    AdvisoryResponse,
    AdvisoryReviewResponse,
    AdvisoryStatusResponse,
    EligibleValidationOptionOut,
)

router = APIRouter()


@router.get("/capabilities")
def capabilities() -> dict:
    """Expose safe runtime capability state without leaking provider secrets."""
    return {
        "product": "a_share_valuecell",
        "remote_llm_configured": settings.remote_llm_configured,
        "remote_llm_default_enabled": settings.allow_remote_llm,
        "streaming": settings.remote_llm_configured,
        "broker_execution": False,
        "wecom_outbound_configured": settings.wecom_webhook_configured,
        "requires_human_confirmation": True,
    }


@router.get("/eligible-validations", response_model=list[EligibleValidationOptionOut])
def list_eligible_validations(
    strategy_name: str | None = None,
    as_of_date: date | None = None,
    session: Session = Depends(get_session),
) -> list[EligibleValidationOptionOut]:
    stmt = (
        select(BacktestWalkForwardValidation, BacktestRun)
        .join(BacktestRun, BacktestRun.id == BacktestWalkForwardValidation.backtest_run_id)
        .where(BacktestWalkForwardValidation.eligibility_status == "eligible")
        .order_by(BacktestWalkForwardValidation.created_at.desc())
        .limit(100)
    )
    options: list[EligibleValidationOptionOut] = []
    for validation, run in session.execute(stmt):
        spec = json.loads(validation.spec_json)
        validation_strategy_name = spec.get("strategy_name")
        if validation_strategy_name != run.strategy_name:
            continue
        if strategy_name and validation_strategy_name != strategy_name:
            continue
        windows = spec.get("windows")
        if not isinstance(windows, list) or not windows:
            continue
        try:
            validation_as_of_date = date.fromisoformat(str(windows[-1]["oos_end_date"]))
        except (KeyError, TypeError, ValueError):
            continue
        if as_of_date and validation_as_of_date != as_of_date:
            continue
        result = json.loads(validation.result_json)
        options.append(
            EligibleValidationOptionOut(
                id=validation.id,
                backtest_run_id=run.id,
                strategy_name=validation_strategy_name,
                as_of_date=validation_as_of_date,
                strategy_parameters=spec.get("strategy_parameters", {}),
                aggregate=result.get("aggregate", {}),
                cost_stress_aggregate=result.get("cost_stress_aggregate", {}),
            )
        )
    return options


@router.post("/drafts", response_model=AdvisoryResponse)
def create_draft(
    payload: AdvisoryRequest,
    session: Session = Depends(get_session),
) -> AdvisoryResponse:
    try:
        return create_advisory(
            session,
            payload,
            remote_llm_available=settings.allow_remote_llm and settings.remote_llm_configured,
        )
    except AdvisoryInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/drafts/{advisory_id}/review", response_model=AdvisoryReviewResponse)
def mark_draft_reviewed(
    advisory_id: int,
    session: Session = Depends(get_session),
) -> AdvisoryReviewResponse:
    record = session.get(AdvisoryRun, advisory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Advisory draft was not found.")
    _refresh_advisory_status(session, record)
    if record.llm_provider == "streaming":
        raise HTTPException(status_code=409, detail="LLM explanation is still streaming.")
    if record.status in {"expired", "rejected"}:
        session.commit()
        raise HTTPException(status_code=409, detail=f"An {record.status} advisory draft cannot be reviewed.")
    if record.status == "reviewed" and record.reviewed_at is not None:
        return AdvisoryReviewResponse(
            id=record.id,
            status="reviewed",
            reviewed_at=record.reviewed_at.isoformat(),
        )
    record.status = "reviewed"
    record.reviewed_at = datetime.utcnow()
    session.commit()
    return AdvisoryReviewResponse(
        id=record.id,
        status="reviewed",
        reviewed_at=record.reviewed_at.isoformat(),
    )


@router.post("/drafts/{advisory_id}/reject", response_model=AdvisoryRejectResponse)
def reject_draft(
    advisory_id: int,
    payload: AdvisoryRejectRequest,
    session: Session = Depends(get_session),
) -> AdvisoryRejectResponse:
    record = session.get(AdvisoryRun, advisory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Advisory draft was not found.")
    _refresh_advisory_status(session, record)
    if record.llm_provider == "streaming":
        raise HTTPException(status_code=409, detail="LLM explanation is still streaming.")
    if record.status == "expired":
        session.commit()
        raise HTTPException(status_code=409, detail="An expired advisory draft cannot be rejected.")
    if record.status == "rejected":
        return AdvisoryRejectResponse(
            id=record.id,
            status="rejected",
            rejection_reason=_rejection_reason(record),
        )
    if payload.reason:
        _set_rejection_reason(record, payload.reason)
    record.status = "rejected"
    session.commit()
    return AdvisoryRejectResponse(
        id=record.id,
        status="rejected",
        rejection_reason=_rejection_reason(record),
    )


@router.get("/drafts/{advisory_id}/status", response_model=AdvisoryStatusResponse)
def advisory_status(
    advisory_id: int,
    session: Session = Depends(get_session),
) -> AdvisoryStatusResponse:
    record = session.get(AdvisoryRun, advisory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Advisory draft was not found.")
    changed, earliest_execution_date = _refresh_advisory_status(session, record)
    if changed:
        session.commit()
    return AdvisoryStatusResponse(
        id=record.id,
        status=record.status,
        as_of_date=record.as_of_date,
        earliest_execution_date=earliest_execution_date,
        reviewed_at=record.reviewed_at,
        rejection_reason=_rejection_reason(record),
    )


@router.post(
    "/drafts/{advisory_id}/notify/wecom",
    response_model=AdvisoryNotificationResponse,
)
def notify_reviewed_draft_to_wecom(
    advisory_id: int,
    session: Session = Depends(get_session),
) -> AdvisoryNotificationResponse:
    record = session.get(AdvisoryRun, advisory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Advisory draft was not found.")
    changed, _ = _refresh_advisory_status(session, record)
    if changed:
        session.commit()
    if record.status != "reviewed" or record.reviewed_at is None:
        raise HTTPException(
            status_code=409,
            detail="Only an explicitly reviewed advisory draft can be sent to WeCom.",
        )
    if not settings.wecom_webhook_configured or not settings.wecom_webhook_url:
        raise HTTPException(status_code=409, detail="WeCom webhook is not configured on this server.")

    message = _wecom_message(record)
    content_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
    delivery = session.scalar(
        select(AdvisoryNotificationDelivery).where(
            AdvisoryNotificationDelivery.advisory_run_id == record.id,
            AdvisoryNotificationDelivery.channel == "wecom_group_webhook",
            AdvisoryNotificationDelivery.idempotency_key == "reviewed-v1",
        )
    )
    if delivery is not None and delivery.status in {"pending", "sent"}:
        raise HTTPException(status_code=409, detail="This reviewed draft has already been queued or sent to WeCom.")
    if delivery is None:
        delivery = AdvisoryNotificationDelivery(
            advisory_run_id=record.id,
            channel="wecom_group_webhook",
            idempotency_key="reviewed-v1",
            status="pending",
            content_hash=content_hash,
            attempts=0,
        )
        session.add(delivery)
    else:
        delivery.status = "pending"
        delivery.content_hash = content_hash
        delivery.error_message = ""
    delivery.attempts = (delivery.attempts or 0) + 1
    session.commit()
    session.refresh(delivery)

    try:
        receipt = WeComGroupWebhookSender(settings.wecom_webhook_url).send_text(message)
    except (NotificationDeliveryError, ValueError) as exc:
        delivery.status = "failed"
        delivery.error_message = str(exc)[:500]
        session.commit()
        raise HTTPException(status_code=502, detail="WeCom notification delivery failed.") from exc

    delivery.status = "sent"
    delivery.provider_message = receipt.provider_message[:500]
    delivery.sent_at = datetime.utcnow()
    session.commit()
    return AdvisoryNotificationResponse(
        delivery_id=delivery.id,
        status="sent",
        channel=delivery.channel,
        provider_message=delivery.provider_message,
    )


@router.post("/drafts/{advisory_id}/stream")
def stream_draft_summary(
    advisory_id: int,
    session: Session = Depends(get_session),
) -> StreamingResponse:
    record = session.get(AdvisoryRun, advisory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Advisory draft was not found.")
    changed, _ = _refresh_advisory_status(session, record)
    if changed:
        session.commit()
    if record.status in {"expired", "rejected"}:
        raise HTTPException(status_code=409, detail=f"An {record.status} advisory draft cannot request an LLM explanation.")
    if record.llm_provider == "streaming":
        raise HTTPException(status_code=409, detail="LLM explanation is already streaming.")
    if not record.remote_llm_requested:
        raise HTTPException(
            status_code=409,
            detail="Remote LLM was not approved when this draft was created.",
        )
    if not (settings.allow_remote_llm and settings.remote_llm_configured):
        raise HTTPException(
            status_code=409,
            detail="Remote LLM is not enabled and fully configured on this server.",
        )

    provider = OpenAIResponsesProvider(
        OpenAIResponsesConfig(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            remote_enabled=True,
        )
    )
    try:
        provider.validate_configuration()
    except LLMProviderConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    record.llm_provider = "streaming"
    record.llm_model = settings.openai_model
    session.commit()

    def event_stream():
        stream_session = SessionLocal()
        try:
            yield _sse("meta", {"advisory_id": advisory_id, "model": settings.openai_model})
            for delta in stream_advisory_summary(
                stream_session,
                advisory_id,
                provider,
                provider_name="openai_responses",
                model_name=str(settings.openai_model),
            ):
                yield _sse("delta", {"text": delta})
            yield _sse("complete", {"advisory_id": advisory_id})
        except (AdvisoryInputError, LLMProviderConfigurationError) as exc:
            yield _sse("error", {"message": str(exc)})
        except Exception:
            yield _sse("error", {"message": "LLM streaming failed; see server logs for details."})
        finally:
            stream_session.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\\ndata: {json.dumps(data, ensure_ascii=False)}\\n\\n"


_LEGACY_DRAFT_STATUSES = {"llm_complete", "llm_disabled", "streaming"}


def _refresh_advisory_status(session: Session, record: AdvisoryRun) -> tuple[bool, date | None]:
    """Normalize legacy technical states and expire drafts after their execution window."""
    changed = False
    if record.status in _LEGACY_DRAFT_STATUSES:
        record.status = "draft"
        changed = True
    elif record.status == "failed":
        record.status = "rejected"
        _set_rejection_reason(record, "Legacy LLM explanation failed; create a fresh research draft.")
        changed = True
    repository = MarketDataRepository(session)
    execution_dates = repository.trading_dates(
        record.as_of_date + timedelta(days=1),
        record.as_of_date + timedelta(days=14),
    )
    earliest_execution_date = execution_dates[0] if execution_dates else None
    symbols = _advisory_symbols(record)
    latest_dates = []
    if symbols:
        rows = session.execute(
            select(DailyBar.symbol, func.max(DailyBar.trade_date))
            .where(DailyBar.symbol.in_(symbols))
            .group_by(DailyBar.symbol)
        )
        latest_dates = [latest for _, latest in rows if latest is not None]
    fully_synced_through = min(latest_dates) if len(latest_dates) == len(symbols) else None
    if (
        record.status in {"draft", "reviewed"}
        and earliest_execution_date is not None
        and fully_synced_through is not None
        and fully_synced_through > earliest_execution_date
    ):
        record.status = "expired"
        changed = True
    return changed, earliest_execution_date


def _advisory_symbols(record: AdvisoryRun) -> list[str]:
    try:
        request = json.loads(record.request_json)
    except (TypeError, json.JSONDecodeError):
        return []
    symbols = request.get("symbols") if isinstance(request, dict) else None
    return sorted({symbol for symbol in symbols if isinstance(symbol, str) and symbol}) if isinstance(symbols, list) else []


def _rejection_reason(record: AdvisoryRun) -> str | None:
    try:
        payload = json.loads(record.risk_json)
    except (TypeError, json.JSONDecodeError):
        return None
    lifecycle = payload.get("lifecycle") if isinstance(payload, dict) else None
    reason = lifecycle.get("rejection_reason") if isinstance(lifecycle, dict) else None
    return reason if isinstance(reason, str) and reason else None


def _set_rejection_reason(record: AdvisoryRun, reason: str) -> None:
    try:
        payload = json.loads(record.risk_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail="Cannot store a rejection reason for corrupt advisory data.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=409, detail="Cannot store a rejection reason for corrupt advisory data.")
    lifecycle = payload.get("lifecycle")
    if not isinstance(lifecycle, dict):
        lifecycle = {}
        payload["lifecycle"] = lifecycle
    lifecycle["rejection_reason"] = reason.strip()
    record.risk_json = json.dumps(payload, ensure_ascii=True, sort_keys=True)


def _wecom_message(record: AdvisoryRun) -> str:
    risk = json.loads(record.risk_json)
    plan = json.loads(record.trade_plan_json)
    accepted = [weight for weight in risk.get("accepted", {}).values() if float(weight) > 0]
    buy_count = sum(1 for item in plan if item.get("side") == "buy")
    sell_count = sum(1 for item in plan if item.get("side") == "sell")
    lines = [
        f"A股研究草案 #{record.id} 已人工阅览",
        f"数据截至: {record.as_of_date.isoformat()}",
        f"策略: {record.strategy_name}",
        f"风险通过: {len(accepted)} 个目标, 总目标仓位 {sum(accepted):.1%}",
        f"交易草案: 买入 {buy_count} 条, 卖出 {sell_count} 条",
        "仅研究参考，需人工确认；不会自动下单。",
    ]
    if record.llm_summary:
        lines.extend(["模型摘要:", record.llm_summary[:800]])
    return "\n".join(lines)
