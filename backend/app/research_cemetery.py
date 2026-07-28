import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import BacktestRun, BacktestWalkForwardValidation, ResearchCemeteryEntry


def record_cemetery_entry(
    session: Session,
    *,
    research_type: str,
    subject_name: str,
    source_ref: str,
    source_fingerprint: str,
    reason: str,
    metrics: dict,
) -> bool:
    existing = session.scalar(
        select(ResearchCemeteryEntry).where(
            ResearchCemeteryEntry.research_type == research_type,
            ResearchCemeteryEntry.subject_name == subject_name,
            ResearchCemeteryEntry.source_fingerprint == source_fingerprint,
        )
    )
    if existing is not None:
        return False
    session.add(
        ResearchCemeteryEntry(
            research_type=research_type,
            subject_name=subject_name,
            source_ref=source_ref,
            source_fingerprint=source_fingerprint,
            reason=reason,
            metrics_json=json.dumps(metrics, ensure_ascii=True, sort_keys=True, default=str),
        )
    )
    return True


def backfill_noneligible_strategy_entries(session: Session) -> int:
    rows = session.execute(
        select(BacktestWalkForwardValidation, BacktestRun)
        .join(BacktestRun, BacktestRun.id == BacktestWalkForwardValidation.backtest_run_id)
        .where(BacktestWalkForwardValidation.eligibility_status != "eligible")
    )
    inserted = 0
    for validation, run in rows:
        try:
            quality = json.loads(validation.quality_json)
            result = json.loads(validation.result_json)
        except (TypeError, json.JSONDecodeError):
            quality, result = {}, {}
        flags = quality.get("quality_flags", []) if isinstance(quality, dict) else []
        if record_cemetery_entry(
            session,
            research_type="strategy",
            subject_name=run.strategy_name,
            source_ref=str(validation.id),
            source_fingerprint=validation.fingerprint,
            reason="; ".join(flag for flag in flags if isinstance(flag, str)) or validation.eligibility_status,
            metrics={
                "eligibility_status": validation.eligibility_status,
                "aggregate": result.get("aggregate", {}) if isinstance(result, dict) else {},
                "window_count": quality.get("window_count") if isinstance(quality, dict) else None,
            },
        ):
            inserted += 1
    session.commit()
    return inserted
