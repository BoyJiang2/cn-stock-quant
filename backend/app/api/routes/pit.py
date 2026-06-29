"""Point-in-Time data API routes.

Owned by GLM "Module A".  All endpoints are *additive* — they do not
modify ``routes/data.py`` or ``routes/backtest.py``.  Routes are mounted
under ``/api/data/pit`` by :mod:`app.main`.

Endpoints
---------

Sync::

    POST /api/data/pit/sync/security-status
    POST /api/data/pit/sync/security-names
    POST /api/data/pit/sync/security-delist
    POST /api/data/pit/sync/index-constituents
    POST /api/data/pit/sync/index-weights

Query::

    GET  /api/data/pit/security-status?symbol=&as_of=
    GET  /api/data/pit/security-name?symbol=&as_of=
    GET  /api/data/pit/index-constituents?index_symbol=&as_of=&with_weights=
    POST /api/data/pit/research-pool
    GET  /api/data/pit/coverage
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.data.akshare_pit_provider import AkSharePitProvider
from app.data.pit_repository import PitRepository
from app.data.pit_sync import PitSyncConfig, PitSyncCoordinator
from app.data.repository import MarketDataRepository
from app.data.symbols import normalize_a_share_symbol
from app.schemas.pit import (
    PitCoverageReportOut,
    PitIndexConstituentOut,
    PitIndexConstituentsOut,
    PitResearchPoolMemberOut,
    PitResearchPoolOut,
    PitResearchPoolRequest,
    PitSecurityNameOut,
    PitSecurityStatusOut,
    PitSyncIndexConstituentsRequest,
    PitSyncIndexConstituentsResponse,
    PitSyncIndexWeightsRequest,
    PitSyncIndexWeightsResponse,
    PitSyncSecurityDelistResponse,
    PitSyncSecurityNamesResponse,
    PitSyncSecurityStatusResponse,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(session: Session) -> PitSyncCoordinator:
    repository = PitRepository(session)
    market_repo = MarketDataRepository(session)
    provider = AkSharePitProvider()
    return PitSyncCoordinator(
        repository,
        provider,
        PitSyncConfig(),
        job_recorder=market_repo.create_sync_job,
    )


def _make_pit_repository(session: Session) -> PitRepository:
    return PitRepository(session, bar_reader=MarketDataRepository(session))


# ---------------------------------------------------------------------------
# Sync endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/sync/security-status",
    response_model=PitSyncSecurityStatusResponse,
)
def sync_security_status(session: Session = Depends(get_session)) -> PitSyncSecurityStatusResponse:
    coordinator = _make_coordinator(session)
    try:
        summary = coordinator.sync_security_status_current()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"PIT security status sync failed: {exc}",
        ) from exc
    extras = summary.extras
    return PitSyncSecurityStatusResponse(
        synced=summary.records,
        listed=int(extras.get("listed", 0)),
        st=int(extras.get("st", 0)),
        source=coordinator.config.default_source,
    )


@router.post(
    "/sync/security-names",
    response_model=PitSyncSecurityNamesResponse,
)
def sync_security_names(session: Session = Depends(get_session)) -> PitSyncSecurityNamesResponse:
    coordinator = _make_coordinator(session)
    try:
        summary = coordinator.sync_security_names()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"PIT security names sync failed: {exc}",
        ) from exc
    return PitSyncSecurityNamesResponse(
        synced=summary.records,
        source=coordinator.config.default_source,
    )


@router.post(
    "/sync/security-delist",
    response_model=PitSyncSecurityDelistResponse,
)
def sync_security_delist(session: Session = Depends(get_session)) -> PitSyncSecurityDelistResponse:
    coordinator = _make_coordinator(session)
    try:
        summary = coordinator.sync_security_delist()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"PIT delist sync failed: {exc}",
        ) from exc
    extras = summary.extras
    return PitSyncSecurityDelistResponse(
        synced=summary.records,
        sh=int(extras.get("sh", 0)),
        sz=int(extras.get("sz", 0)),
        source=coordinator.config.default_source,
    )


@router.post(
    "/sync/index-constituents",
    response_model=PitSyncIndexConstituentsResponse,
)
def sync_index_constituents(
    payload: PitSyncIndexConstituentsRequest,
    session: Session = Depends(get_session),
) -> PitSyncIndexConstituentsResponse:
    try:
        target = normalize_a_share_symbol(payload.index_symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    coordinator = _make_coordinator(session)
    try:
        summary = coordinator.sync_index_constituents(target)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"PIT index constituents sync failed: {exc}",
        ) from exc
    extras = summary.extras
    snapshot = extras.get("snapshot_date")
    return PitSyncIndexConstituentsResponse(
        index_symbol=target,
        constituents=summary.records,
        snapshot_date=date.fromisoformat(snapshot) if snapshot else None,
        source=coordinator.config.default_source,
    )


@router.post(
    "/sync/index-weights",
    response_model=PitSyncIndexWeightsResponse,
)
def sync_index_weights(
    payload: PitSyncIndexWeightsRequest,
    session: Session = Depends(get_session),
) -> PitSyncIndexWeightsResponse:
    try:
        target = normalize_a_share_symbol(payload.index_symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    coordinator = _make_coordinator(session)
    try:
        summary = coordinator.sync_index_weights(target)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"PIT index weights sync failed: {exc}",
        ) from exc
    extras = summary.extras
    trade_date = extras.get("trade_date")
    return PitSyncIndexWeightsResponse(
        index_symbol=target,
        weights_synced=summary.records,
        trade_date=date.fromisoformat(trade_date) if trade_date else None,
        source=coordinator.config.default_source,
    )


# ---------------------------------------------------------------------------
# Query endpoints
# ---------------------------------------------------------------------------


@router.get("/security-status", response_model=PitSecurityStatusOut)
def get_security_status(
    symbol: str = Query(..., min_length=1),
    as_of: date = Query(..., description="PIT date, YYYY-MM-DD"),
    session: Session = Depends(get_session),
) -> PitSecurityStatusOut:
    try:
        normalized = normalize_a_share_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    pit = _make_pit_repository(session)
    status = pit.status_as_of(normalized, as_of)
    if status is None:
        raise HTTPException(
            status_code=404,
            detail=f"No PIT status for {normalized} on or before {as_of}.",
        )
    name = pit.name_as_of(normalized, as_of)
    return PitSecurityStatusOut(
        symbol=status.symbol,
        status=status.status,
        valid_from=status.valid_from,
        valid_to=status.valid_to,
        announced_at=status.announced_at,
        confidence=status.confidence,
        source=status.source,
        delist_reason=status.delist_reason,
        degraded=status.degraded,
        name_at=name.name if name else None,
    )


@router.get("/security-name", response_model=PitSecurityNameOut)
def get_security_name(
    symbol: str = Query(..., min_length=1),
    as_of: date = Query(..., description="PIT date, YYYY-MM-DD"),
    session: Session = Depends(get_session),
) -> PitSecurityNameOut:
    try:
        normalized = normalize_a_share_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    pit = _make_pit_repository(session)
    name = pit.name_as_of(normalized, as_of)
    if name is None:
        raise HTTPException(
            status_code=404,
            detail=f"No PIT name for {normalized} on or before {as_of}.",
        )
    return PitSecurityNameOut(
        symbol=name.symbol,
        name=name.name,
        valid_from=name.valid_from,
        valid_to=name.valid_to,
        announced_at=name.announced_at,
        confidence=name.confidence,
        source=name.source,
        degraded=name.degraded,
    )


@router.get(
    "/index-constituents",
    response_model=PitIndexConstituentsOut,
)
def get_index_constituents(
    index_symbol: str = Query(..., min_length=1),
    as_of: date = Query(..., description="PIT date, YYYY-MM-DD"),
    with_weights: bool = Query(False),
    session: Session = Depends(get_session),
) -> PitIndexConstituentsOut:
    try:
        target = normalize_a_share_symbol(index_symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    pit = _make_pit_repository(session)
    members = pit.index_constituents_as_of(target, as_of, with_weights=with_weights)
    if not members:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No PIT constituents for index {target} on {as_of}. "
                "Run /api/data/pit/sync/index-constituents first."
            ),
        )
    confidence = (
        "high"
        if all(m.announced_at is not None for m in members)
        else "medium"
    )
    sources = {m.source for m in members}
    return PitIndexConstituentsOut(
        index_symbol=target,
        as_of=as_of,
        constituents=[
            PitIndexConstituentOut(
                symbol=m.symbol,
                name=m.name,
                weight=m.weight,
                valid_from=m.valid_from,
                valid_to=m.valid_to,
                announced_at=m.announced_at,
                confidence=m.confidence,
                source=m.source,
            )
            for m in members
        ],
        source=",".join(sorted(sources)),
        confidence=confidence,
        degraded=confidence != "high",
    )


@router.post("/research-pool", response_model=PitResearchPoolOut)
def build_research_pool(
    payload: PitResearchPoolRequest,
    session: Session = Depends(get_session),
) -> PitResearchPoolOut:
    if payload.start_date > payload.end_date:
        raise HTTPException(
            status_code=400,
            detail="start_date must be <= end_date",
        )
    pit = _make_pit_repository(session)
    result = pit.select_research_symbols_pit(
        payload.as_of,
        payload.start_date,
        payload.end_date,
        exchanges=tuple(payload.exchanges),
        exclude_st=payload.exclude_st,
        index_symbol=payload.index_symbol,
        min_trading_days=payload.min_trading_days,
        min_coverage_ratio=payload.min_coverage_ratio,
        limit=payload.limit,
        st_policy=payload.st_policy,
    )

    members: list[dict[str, Any]] = []
    eligible_set = set(result.symbols)
    excluded_reasons: dict[str, str] = {}
    # Build a per-symbol member row, including excluded ones, for audit.
    excluded_by_reason: dict[str, list[str]] = {}
    for reason, count in result.meta.get("excluded", {}).items():
        # We don't have the symbol-level mapping here; the meta only
        # contains counts.  Persist only eligible members to keep the
        # audit table compact (eligible rows are what backtests select).
        excluded_by_reason[reason] = []
    for symbol in result.symbols:
        name = pit.name_as_of(symbol, payload.as_of)
        status = pit.status_as_of(symbol, payload.as_of)
        members.append(
            {
                "symbol": symbol,
                "eligible": True,
                "exclusion_reason": None,
                "name_at": name.name if name else None,
                "status_at": status.status if status else None,
            }
        )

    if payload.materialize:
        pit.materialize_research_pool(
            result.meta["pool_key"], payload.as_of, members
        )

    return PitResearchPoolOut(
        pool_key=result.meta["pool_key"],
        as_of=payload.as_of,
        symbols=result.symbols,
        members=[PitResearchPoolMemberOut(**m) for m in members],
        meta=result.meta,
    )


@router.get("/coverage", response_model=PitCoverageReportOut)
def pit_coverage(session: Session = Depends(get_session)) -> PitCoverageReportOut:
    pit = _make_pit_repository(session)
    return PitCoverageReportOut(**pit.pit_coverage_report())
