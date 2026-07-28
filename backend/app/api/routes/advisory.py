import hashlib
import json
from datetime import date, datetime, timedelta
from math import isfinite

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
    advisory_snapshot_fingerprint,
    create_advisory,
    stream_advisory_summary,
)
from app.core.config import settings
from app.core.database import SessionLocal, get_session
from app.data.repository import MarketDataRepository
from app.models.entities import AdvisoryAgentSnapshot, AdvisoryNotificationDelivery, AdvisoryRun, BacktestRun, BacktestRunProvenance, BacktestWalkForwardValidation, DailyBar
from app.notifications import NotificationDeliveryError, WeComGroupWebhookSender
from app.schemas.advisory import (
    AdvisoryNotificationResponse,
    AdvisoryAgentSnapshotOut,
    AdvisoryReplayResponse,
    AdvisoryRejectRequest,
    AdvisoryRejectResponse,
    AdvisoryRequest,
    AdvisoryResponse,
    AdvisoryReviewResponse,
    CriticAgentResponse,
    CriticFindingOut,
    RiskAgentResponse,
    RiskRejectionOut,
    ResearchAgentResponse,
    ResearchFactOut,
    StrategyAgentResponse,
    StrategyCandidateOut,
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


@router.get("/strategy-candidates", response_model=StrategyAgentResponse)
def strategy_candidates(session: Session = Depends(get_session)) -> StrategyAgentResponse:
    """Rank only eligible rolling-OOS records with a transparent deterministic score."""
    rows = session.execute(
        select(BacktestWalkForwardValidation, BacktestRun, BacktestRunProvenance)
        .join(BacktestRun, BacktestRun.id == BacktestWalkForwardValidation.backtest_run_id)
        .join(BacktestRunProvenance, BacktestRunProvenance.run_id == BacktestRun.id)
        .where(
            BacktestWalkForwardValidation.status == "completed",
            BacktestWalkForwardValidation.eligibility_status == "eligible",
        )
        .order_by(BacktestWalkForwardValidation.created_at.desc())
    )
    candidates: list[StrategyCandidateOut] = []
    for validation, run, provenance in rows:
        try:
            spec = json.loads(validation.spec_json)
            result = json.loads(validation.result_json)
            quality = json.loads(validation.quality_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(spec, dict) or not isinstance(result, dict) or not isinstance(quality, dict):
            continue
        aggregate = result.get("aggregate", {}) if isinstance(result, dict) else {}
        stressed = result.get("cost_stress_aggregate", {}) if isinstance(result, dict) else {}
        if not isinstance(aggregate, dict) or not isinstance(stressed, dict):
            continue
        annual = _finite_metric(aggregate.get("annual_return"))
        sharpe = _finite_metric(aggregate.get("sharpe"))
        drawdown = _finite_metric(aggregate.get("max_drawdown"))
        stressed_sharpe = _finite_metric(stressed.get("sharpe"))
        if None in {annual, sharpe, drawdown, stressed_sharpe}:
            continue
        score = annual * 100 + sharpe * 10 - abs(drawdown) * 40 + stressed_sharpe * 5
        windows = spec.get("windows", [])
        strategy_name = spec.get("strategy_name")
        if (
            strategy_name != run.strategy_name
            or spec.get("source_backtest_run_id") != run.id
            or spec.get("source_provenance_fingerprint") != provenance.fingerprint
            or validation.source_provenance_fingerprint != provenance.fingerprint
        ):
            continue
        try:
            as_of_date = date.fromisoformat(str(windows[-1]["oos_end_date"]))
        except (IndexError, KeyError, TypeError, ValueError):
            continue
        if not _candidate_metrics_are_valid(annual, sharpe, drawdown, stressed_sharpe):
            continue
        flags = quality.get("quality_flags", []) if isinstance(quality, dict) else []
        rationale = [
            f"OOS annual return {annual:.2%}; Sharpe {sharpe:.2f}; max drawdown {drawdown:.2%}.",
            f"Cost-stressed Sharpe {stressed_sharpe:.2f}.",
        ]
        candidates.append(StrategyCandidateOut(
            rank=0,
            validation_id=validation.id,
            backtest_run_id=run.id,
            strategy_name=run.strategy_name,
            as_of_date=as_of_date,
            score=round(score, 6),
            aggregate=_finite_metrics(aggregate),
            cost_stress_aggregate=_finite_metrics(stressed),
            quality_flags=[flag for flag in flags if isinstance(flag, str)] if isinstance(flags, list) else [],
            rationale=rationale,
        ))
    candidates.sort(key=lambda item: (item.score, item.as_of_date, item.validation_id), reverse=True)
    candidates = candidates[:100]
    for rank, candidate in enumerate(candidates, start=1):
        candidate.rank = rank
    return StrategyAgentResponse(
        candidates=candidates,
        scoring_method="score = annual_return*100 + sharpe*10 - abs(max_drawdown)*40 + cost_stress_sharpe*5; eligible rolling OOS records only",
        warnings=["This ranking is historical validation evidence, not a future-return forecast or trade instruction."],
    )


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
    changed, _ = _refresh_advisory_status(session, record)
    if changed:
        session.commit()
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


@router.get("/drafts/{advisory_id}/research", response_model=ResearchAgentResponse)
def research_facts(
    advisory_id: int,
    session: Session = Depends(get_session),
) -> ResearchAgentResponse:
    """Expose deterministic, cited facts from the immutable advisory evidence snapshot."""
    record = session.get(AdvisoryRun, advisory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Advisory draft was not found.")
    try:
        risk_payload = json.loads(record.risk_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail="Advisory evidence snapshot is corrupt.") from exc
    if not isinstance(risk_payload, dict) or not isinstance(risk_payload.get("evidence", {}), dict):
        raise HTTPException(status_code=409, detail="Advisory evidence snapshot is corrupt.")
    evidence = risk_payload.get("evidence", {})
    facts: list[ResearchFactOut] = []
    market = evidence.get("market", {})
    if market is not None and not isinstance(market, dict):
        raise HTTPException(status_code=409, detail="Advisory market evidence is corrupt.")
    for reason in (market or {}).get("reasons", [])[:5] if isinstance((market or {}).get("reasons", []), list) else []:
        if isinstance(reason, str):
            facts.append(ResearchFactOut(claim=reason, source_type="market", citation="local CSI 300 research-close snapshot", observed_at=str(market.get("data_end_date") or record.as_of_date)))
    news = evidence.get("news", {})
    if news is not None and not isinstance(news, dict):
        raise HTTPException(status_code=409, detail="Advisory news evidence is corrupt.")
    news_items = (news or {}).get("items", [])
    for item in news_items[:10] if isinstance(news_items, list) else []:
        known_at = item.get("known_at") if isinstance(item, dict) else None
        try:
            known_date = datetime.fromisoformat(str(known_at).replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if isinstance(item.get("title"), str) and known_date <= record.as_of_date:
            citation = " | ".join(str(item.get(key) or "-") for key in ("source", "symbol", "published_at", "known_at"))
            facts.append(ResearchFactOut(claim=item["title"], source_type="news", citation=citation, observed_at=str(known_at)))
    factors = evidence.get("factors", {})
    if factors is not None and not isinstance(factors, dict):
        raise HTTPException(status_code=409, detail="Advisory factor evidence is corrupt.")
    factor_symbols = (factors or {}).get("symbols", [])
    for symbol in factor_symbols[:10] if isinstance(factor_symbols, list) else []:
        if isinstance(symbol, dict) and symbol.get("available"):
            facts.append(ResearchFactOut(claim=f"Observed trailing factors are available for {symbol.get('symbol')}.", source_type="factor", citation="local price/volume factor snapshot", observed_at=str(factors.get("data_end_date") or record.as_of_date)))
    validation = evidence.get("validation") if isinstance(evidence, dict) else None
    if isinstance(validation, dict) and validation.get("validation_id"):
        facts.append(ResearchFactOut(claim=f"Eligible rolling OOS validation #{validation['validation_id']} is attached.", source_type="validation", citation=f"backtest #{validation.get('backtest_run_id', 'unknown')}", observed_at=str(validation.get("source_as_of_date") or record.as_of_date)))
    warnings = ["Facts are extracted from the persisted local snapshot; they are not predictions or trade instructions."]
    if not facts:
        warnings.append("The advisory has no extractable research facts in its saved evidence snapshot.")
    return ResearchAgentResponse(advisory_id=record.id, as_of_date=record.as_of_date, facts=facts, warnings=warnings)


@router.get("/drafts/{advisory_id}/critique", response_model=CriticAgentResponse)
def critique_advisory(
    advisory_id: int,
    session: Session = Depends(get_session),
) -> CriticAgentResponse:
    """Return deterministic evidence-quality and concentration objections for human review."""
    record = session.get(AdvisoryRun, advisory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Advisory draft was not found.")
    changed, _ = _refresh_advisory_status(session, record)
    if changed:
        session.commit()
    try:
        risk = json.loads(record.risk_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail="Advisory evidence snapshot is corrupt.") from exc
    if not isinstance(risk, dict) or not isinstance(risk.get("evidence", {}), dict):
        raise HTTPException(status_code=409, detail="Advisory evidence snapshot is corrupt.")
    findings: list[CriticFindingOut] = []
    evidence = risk["evidence"]
    market = evidence.get("market", {})
    factors = evidence.get("factors", {})
    news = evidence.get("news", {})
    validation = evidence.get("validation")
    if not isinstance(market, dict) or not isinstance(factors, dict) or not isinstance(news, dict):
        raise HTTPException(status_code=409, detail="Advisory evidence snapshot is corrupt.")
    for label, value in (("market", market.get("data_end_date")), ("factor", factors.get("data_end_date"))):
        if not value:
            continue
        try:
            evidence_date = date.fromisoformat(str(value))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=f"Advisory {label} evidence is corrupt.") from exc
        if evidence_date > record.as_of_date:
            findings.append(CriticFindingOut(severity="blocker", code=f"future_{label}_evidence", message=f"{label.title()} evidence is later than the advisory date.", citation=str(value)))
    news_items = news.get("items", [])
    if not isinstance(news_items, list):
        raise HTTPException(status_code=409, detail="Advisory news evidence is corrupt.")
    for item in news_items:
        if not isinstance(item, dict):
            raise HTTPException(status_code=409, detail="Advisory news evidence is corrupt.")
        known_at = item.get("known_at")
        try:
            known_date = datetime.fromisoformat(str(known_at).replace("Z", "+00:00")).date()
        except ValueError:
            raise HTTPException(status_code=409, detail="Advisory news evidence is corrupt.") from None
        if known_date > record.as_of_date:
            findings.append(CriticFindingOut(severity="blocker", code="future_news_evidence", message="A news item was known after the advisory date.", citation=str(known_at)))
            break
    severe_count = _finite_metric(news.get("severe_company_risk_count") or 0)
    if severe_count is None or severe_count < 0 or not severe_count.is_integer():
        raise HTTPException(status_code=409, detail="Advisory news evidence is corrupt.")
    if severe_count > 0:
        findings.append(CriticFindingOut(severity="warning", code="severe_company_news", message="Severe company-risk news is present in the observed window.", citation="persisted news evidence"))
    accepted = risk.get("accepted", {})
    if not isinstance(accepted, dict):
        raise HTTPException(status_code=409, detail="Advisory risk decision is corrupt.")
    weights = [_finite_metric(value) for value in accepted.values()]
    if any(weight is None or weight < 0 or weight > 1 for weight in weights):
        raise HTTPException(status_code=409, detail="Advisory risk decision is corrupt.")
    weights = [weight for weight in weights if weight is not None]
    if weights and max(weights) > 0.3:
        findings.append(CriticFindingOut(severity="warning", code="concentrated_target", message="A single accepted target exceeds 30% weight.", citation="risk-gated target weights"))
    if validation is None:
        findings.append(CriticFindingOut(severity="warning", code="missing_oos_validation", message="No eligible rolling OOS validation is attached to this draft.", citation="advisory evidence snapshot"))
    elif not _validation_snapshot_matches(session, record, validation):
        findings.append(CriticFindingOut(severity="blocker", code="invalid_oos_validation", message="The attached rolling OOS validation no longer matches the draft provenance.", citation="advisory evidence snapshot"))
    if record.status == "expired":
        findings.append(CriticFindingOut(severity="blocker", code="expired_draft", message="The draft has expired and must be regenerated.", citation=record.as_of_date.isoformat()))
    return CriticAgentResponse(
        advisory_id=record.id,
        as_of_date=record.as_of_date,
        findings=findings,
        approved_for_human_review=not any(item.severity == "blocker" for item in findings),
    )


@router.get("/drafts/{advisory_id}/risk", response_model=RiskAgentResponse)
def risk_decision_explanation(
    advisory_id: int,
    session: Session = Depends(get_session),
) -> RiskAgentResponse:
    """Explain the persisted deterministic risk gate without recomputing targets."""
    record = session.get(AdvisoryRun, advisory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Advisory draft was not found.")
    try:
        risk = json.loads(record.risk_json)
        request = json.loads(record.request_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail="Advisory risk snapshot is corrupt.") from exc
    accepted = risk.get("accepted") if isinstance(risk, dict) else None
    rejected = risk.get("rejected") if isinstance(risk, dict) else None
    if not isinstance(accepted, dict) or not isinstance(rejected, dict) or not isinstance(request, dict):
        raise HTTPException(status_code=409, detail="Advisory risk snapshot is corrupt.")
    weights = {symbol: _finite_metric(weight) for symbol, weight in accepted.items() if isinstance(symbol, str)}
    if len(weights) != len(accepted) or any(weight is None or weight < 0 or weight > 1 for weight in weights.values()):
        raise HTTPException(status_code=409, detail="Advisory accepted weights are corrupt.")
    if any(not isinstance(symbol, str) or not isinstance(reason, str) for symbol, reason in rejected.items()):
        raise HTTPException(status_code=409, detail="Advisory rejection reasons are corrupt.")
    accepted_values = [weight for weight in weights.values() if weight is not None]
    total_weight = round(sum(accepted_values), 6)
    raw_symbol_cap = request.get("max_symbol_weight")
    raw_total_cap = request.get("max_total_weight")
    max_symbol_weight = _finite_metric(raw_symbol_cap)
    max_total_weight = _finite_metric(raw_total_cap)
    max_positions = request.get("max_positions")
    if (
        max_symbol_weight is None
        or max_total_weight is None
        or not 0 <= max_symbol_weight <= 1
        or not 0 <= max_total_weight <= 1
        or isinstance(max_positions, bool)
        or not isinstance(max_positions, int)
        or max_positions < 1
    ):
        raise HTTPException(status_code=409, detail="Advisory risk constraints are corrupt.")
    if (
        any(weight > max_symbol_weight for weight in accepted_values)
        or total_weight > max_total_weight
        or len(accepted_values) > max_positions
    ):
        raise HTTPException(status_code=409, detail="Advisory risk gate result violates its saved constraints.")
    explanation = [
        f"Risk gate retained {len(accepted_values)} target positions with total target weight {total_weight:.2%}.",
        f"Largest accepted target weight is {(max(accepted_values) if accepted_values else 0.0):.2%}.",
        f"Saved caps are {max_symbol_weight:.2%} per symbol, {max_total_weight:.2%} total, and {max_positions} positions.",
    ]
    if rejected:
        explanation.append(f"Risk gate rejected {len(rejected)} target exposures; see per-symbol reasons.")
    return RiskAgentResponse(
        advisory_id=record.id,
        accepted_weight=total_weight,
        accepted_position_count=len(accepted_values),
        largest_accepted_weight=round(max(accepted_values), 6) if accepted_values else 0.0,
        max_symbol_weight=max_symbol_weight,
        max_total_weight=max_total_weight,
        max_positions=max_positions,
        rejections=[RiskRejectionOut(symbol=symbol, reason=reason) for symbol, reason in sorted(rejected.items())],
        explanation=explanation,
    )


@router.get("/drafts/{advisory_id}/replay", response_model=AdvisoryReplayResponse)
def replay_advisory_evidence(
    advisory_id: int,
    session: Session = Depends(get_session),
) -> AdvisoryReplayResponse:
    """Return the immutable, fingerprint-verified agent evidence captured with a draft."""
    record = session.get(AdvisoryRun, advisory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Advisory draft was not found.")
    records = list(
        session.scalars(
            select(AdvisoryAgentSnapshot)
            .where(AdvisoryAgentSnapshot.advisory_run_id == advisory_id)
            .order_by(AdvisoryAgentSnapshot.agent_name)
        )
    )
    expected_agents = {"research", "strategy", "critic", "risk", "synthesis"}
    if not records:
        raise HTTPException(
            status_code=409,
            detail="Advisory draft predates agent snapshots; create a new draft to obtain replay evidence.",
        )
    if len(records) != len(expected_agents) or {item.agent_name for item in records} != expected_agents:
        raise HTTPException(status_code=409, detail="Advisory agent replay snapshot is incomplete.")
    snapshots: list[AdvisoryAgentSnapshotOut] = []
    fingerprints: dict[str, str] = {}
    for item in records:
        try:
            payload = json.loads(item.payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=409, detail="Advisory agent replay snapshot is corrupt.") from exc
        if not isinstance(payload, dict) or item.fingerprint != advisory_snapshot_fingerprint(advisory_id, item.agent_name, payload):
            raise HTTPException(status_code=409, detail="Advisory agent replay fingerprint does not match.")
        fingerprints[item.agent_name] = item.fingerprint
        snapshots.append(
            AdvisoryAgentSnapshotOut(
                agent_name=item.agent_name,
                payload=payload,
                fingerprint=item.fingerprint,
                created_at=item.created_at,
            )
        )
    synthesis = next(item.payload for item in snapshots if item.agent_name == "synthesis")
    source_fingerprints = {name: fingerprint for name, fingerprint in fingerprints.items() if name != "synthesis"}
    if synthesis.get("agent_fingerprints") != source_fingerprints:
        raise HTTPException(status_code=409, detail="Advisory synthesis does not match its agent snapshots.")
    return AdvisoryReplayResponse(
        advisory_id=record.id,
        as_of_date=record.as_of_date,
        replay_fingerprint=advisory_snapshot_fingerprint(record.id, "replay", {"agents": fingerprints}),
        snapshots=snapshots,
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


def _finite_metric(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if isfinite(numeric) else None


def _finite_metrics(values: dict) -> dict[str, float]:
    return {
        str(key): numeric
        for key, value in values.items()
        if (numeric := _finite_metric(value)) is not None
    }


def _candidate_metrics_are_valid(
    annual: float | None,
    sharpe: float | None,
    drawdown: float | None,
    stressed_sharpe: float | None,
) -> bool:
    return (
        annual is not None
        and sharpe is not None
        and drawdown is not None
        and stressed_sharpe is not None
        and -1 <= annual <= 5
        and -10 <= sharpe <= 10
        and -1 <= drawdown <= 0
        and -10 <= stressed_sharpe <= 10
    )


def _validation_snapshot_matches(session: Session, record: AdvisoryRun, snapshot: object) -> bool:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("validation_id"), int):
        return False
    validation = session.get(BacktestWalkForwardValidation, snapshot["validation_id"])
    if validation is None or validation.status != "completed" or validation.eligibility_status != "eligible":
        return False
    run = session.get(BacktestRun, validation.backtest_run_id)
    provenance = session.scalar(select(BacktestRunProvenance).where(BacktestRunProvenance.run_id == validation.backtest_run_id))
    if run is None or provenance is None or validation.source_provenance_fingerprint != provenance.fingerprint:
        return False
    try:
        spec = json.loads(validation.spec_json)
        windows = spec["windows"]
        source_as_of_date = date.fromisoformat(str(windows[-1]["oos_end_date"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return (
        spec.get("strategy_name") == record.strategy_name
        and spec.get("source_backtest_run_id") == run.id
        and spec.get("source_provenance_fingerprint") == provenance.fingerprint
        and source_as_of_date == record.as_of_date
        and snapshot.get("backtest_run_id") == run.id
        and snapshot.get("fingerprint") == validation.fingerprint
        and str(snapshot.get("source_as_of_date")) == source_as_of_date.isoformat()
    )


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
