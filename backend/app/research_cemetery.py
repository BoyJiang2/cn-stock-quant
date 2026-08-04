import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.entities import (
    BacktestRun,
    BacktestWalkForwardValidation,
    FactorExperiment,
    ResearchCemeteryEntry,
)


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
    try:
        # The unique constraint is the authority for concurrent workers. A savepoint
        # keeps an already-open caller transaction usable when another worker wins.
        with session.begin_nested():
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
            session.flush()
    except IntegrityError:
        return False
    return True


def backfill_noneligible_strategy_entries(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(BacktestWalkForwardValidation, BacktestRun)
        .join(BacktestRun, BacktestRun.id == BacktestWalkForwardValidation.backtest_run_id)
        .where(BacktestWalkForwardValidation.eligibility_status != "eligible")
    )
    inserted = 0
    corrupt_sources = 0
    for validation, run in rows:
        try:
            quality = json.loads(validation.quality_json)
            result = json.loads(validation.result_json)
        except (TypeError, json.JSONDecodeError) as exc:
            corrupt_sources += 1
            if record_cemetery_entry(
                session,
                research_type="strategy",
                subject_name=run.strategy_name,
                source_ref=str(validation.id),
                source_fingerprint=validation.fingerprint,
                reason="corrupt_source_data: validation quality or result JSON could not be parsed",
                metrics={
                    "eligibility_status": validation.eligibility_status,
                    "corrupt_source_data": True,
                    "parse_error": type(exc).__name__,
                },
            ):
                inserted += 1
            continue
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
    return {"inserted": inserted, "corrupt_sources": corrupt_sources}


def factor_cemetery_reason(summary: dict) -> str | None:
    reasons: list[str] = []
    n_dates = summary.get("n_dates")
    if not isinstance(n_dates, int) or n_dates < 20:
        reasons.append(f"only {n_dates if isinstance(n_dates, int) else 'unknown'} valid evaluation dates")
    rankic_mean = summary.get("rankic_mean")
    if not isinstance(rankic_mean, (int, float)):
        reasons.append("RankIC is unavailable")
    elif rankic_mean <= 0:
        reasons.append(f"non-positive RankIC ({rankic_mean:.4f})")
    return "; ".join(reasons) if reasons else None


def backfill_factor_entries(session: Session) -> dict[str, int]:
    """Create idempotent cemetery records from persisted factor experiments."""
    inserted = 0
    corrupt_sources = 0
    for experiment in session.scalars(select(FactorExperiment)):
        try:
            response = json.loads(experiment.response_summary_json)
            summaries = response.get("summaries")
            if not isinstance(summaries, list):
                raise ValueError("response summary has no summaries list")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            corrupt_sources += 1
            if record_cemetery_entry(
                session,
                research_type="factor",
                subject_name="factor_experiment",
                source_ref=str(experiment.id),
                source_fingerprint=experiment.experiment_fingerprint,
                reason="corrupt_source_data: persisted factor experiment response could not be parsed",
                metrics={"corrupt_source_data": True, "parse_error": type(exc).__name__},
            ):
                inserted += 1
            continue
        for index, summary in enumerate(summaries):
            if not isinstance(summary, dict) or not isinstance(summary.get("name"), str):
                corrupt_sources += 1
                if record_cemetery_entry(
                    session,
                    research_type="factor",
                    subject_name=f"invalid_factor_summary_{index}",
                    source_ref=str(experiment.id),
                    source_fingerprint=experiment.experiment_fingerprint,
                    reason="corrupt_source_data: factor summary is missing a valid name",
                    metrics={"corrupt_source_data": True, "summary": summary},
                ):
                    inserted += 1
                continue
            reason = factor_cemetery_reason(summary)
            if reason and record_cemetery_entry(
                session,
                research_type="factor",
                subject_name=summary["name"],
                source_ref=str(experiment.id),
                source_fingerprint=experiment.experiment_fingerprint,
                reason=reason,
                metrics=summary,
            ):
                inserted += 1
    session.commit()
    return {"inserted": inserted, "corrupt_sources": corrupt_sources}
