import hashlib
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
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
from app.models.entities import AdvisoryNotificationDelivery, AdvisoryRun
from app.notifications import NotificationDeliveryError, WeComGroupWebhookSender
from app.schemas.advisory import (
    AdvisoryNotificationResponse,
    AdvisoryRequest,
    AdvisoryResponse,
    AdvisoryReviewResponse,
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
    if record.status == "streaming":
        raise HTTPException(status_code=409, detail="LLM summary is still streaming.")
    record.status = "reviewed"
    record.reviewed_at = datetime.utcnow()
    session.commit()
    return AdvisoryReviewResponse(
        id=record.id,
        status="reviewed",
        reviewed_at=record.reviewed_at.isoformat(),
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
    record.status = "streaming"
    record.llm_provider = "openai_responses"
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
