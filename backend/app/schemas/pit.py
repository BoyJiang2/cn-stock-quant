"""Pydantic schemas for the Point-in-Time API surface.

Owned by GLM "Module A".  These models are *additive* — they do not
modify ``schemas/data.py`` or ``schemas/backtest.py``.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "PitSyncSecurityStatusRequest",
    "PitSyncSecurityStatusResponse",
    "PitSyncSecurityNamesRequest",
    "PitSyncSecurityNamesResponse",
    "PitSyncSecurityNameHistoryResponse",
    "PitSyncSecurityDelistRequest",
    "PitSyncSecurityDelistResponse",
    "PitSyncIndexConstituentsRequest",
    "PitSyncIndexConstituentsResponse",
    "PitSyncIndexWeightsRequest",
    "PitSyncIndexWeightsResponse",
    "PitSecurityStatusOut",
    "PitSecurityNameOut",
    "PitIndexConstituentOut",
    "PitIndexConstituentsOut",
    "PitResearchPoolRequest",
    "PitResearchPoolMemberOut",
    "PitResearchPoolOut",
    "PitCoverageReportOut",
]


# ---------------------------------------------------------------------------
# Sync request / response
# ---------------------------------------------------------------------------


class PitSyncSecurityStatusRequest(BaseModel):
    exchanges: list[str] | None = Field(
        None, description="Optional exchange filter; reserved for future use."
    )


class PitSyncSecurityStatusResponse(BaseModel):
    synced: int
    listed: int
    st: int
    source: str


class PitSyncSecurityNamesRequest(BaseModel):
    symbols: list[str] | None = Field(
        None, description="Optional symbol filter; reserved for future use."
    )


class PitSyncSecurityNamesResponse(BaseModel):
    synced: int
    source: str


class PitSyncSecurityNameHistoryResponse(BaseModel):
    synced: int
    name_changes: int
    st_intervals: int
    source: str


class PitSyncSecurityDelistRequest(BaseModel):
    pass


class PitSyncSecurityDelistResponse(BaseModel):
    synced: int
    sh: int
    sz: int
    source: str


class PitSyncIndexConstituentsRequest(BaseModel):
    index_symbol: str = Field(..., examples=["000300"])
    backfill: bool = Field(
        False,
        description="Reserved for future historical backfill (csi index website).",
    )


class PitSyncIndexConstituentsResponse(BaseModel):
    index_symbol: str
    constituents: int
    snapshot_date: date | None = None
    source: str


class PitSyncIndexWeightsRequest(BaseModel):
    index_symbol: str = Field(..., examples=["000300"])


class PitSyncIndexWeightsResponse(BaseModel):
    index_symbol: str
    weights_synced: int
    trade_date: date | None = None
    source: str


# ---------------------------------------------------------------------------
# Query response
# ---------------------------------------------------------------------------


class PitSecurityStatusOut(BaseModel):
    symbol: str
    # Legacy mixed status retained for existing API consumers.
    status: str
    availability_status: str | None = None
    st_status: str | None = None
    valid_from: date
    valid_to: date | None = None
    announced_at: date | None = None
    confidence: str
    source: str
    delist_reason: str | None = None
    degraded: bool = False
    name_at: str | None = None


class PitSecurityNameOut(BaseModel):
    symbol: str
    name: str
    valid_from: date
    valid_to: date | None = None
    announced_at: date | None = None
    confidence: str
    source: str
    degraded: bool = False


class PitIndexConstituentOut(BaseModel):
    symbol: str
    name: str | None = None
    weight: float | None = None
    valid_from: date
    valid_to: date | None = None
    announced_at: date | None = None
    confidence: str
    source: str


class PitIndexConstituentsOut(BaseModel):
    index_symbol: str
    as_of: date
    constituents: list[PitIndexConstituentOut]
    source: str
    confidence: str
    degraded: bool = False


class PitResearchPoolRequest(BaseModel):
    as_of: date
    start_date: date
    end_date: date
    exchanges: list[str] = Field(
        default_factory=lambda: ["SH", "SZ", "BJ"]
    )
    exclude_st: bool = True
    index_symbol: str | None = None
    min_trading_days: int | None = None
    min_coverage_ratio: float = 0.8
    limit: int = Field(300, ge=1, le=10_000)
    st_policy: str = "exclude_known"
    materialize: bool = Field(
        True, description="Persist the build to research_pool_member for audit."
    )


class PitResearchPoolMemberOut(BaseModel):
    symbol: str
    eligible: bool
    exclusion_reason: str | None = None
    name_at: str | None = None
    status_at: str | None = None


class PitResearchPoolOut(BaseModel):
    pool_key: str
    as_of: date
    symbols: list[str]
    members: list[PitResearchPoolMemberOut]
    meta: dict[str, Any]


class PitCoverageReportOut(BaseModel):
    security_status_rows: int
    security_name_rows: int
    security_st_status_rows: int = 0
    status_missing_announced_at: int
    name_missing_announced_at: int
    st_status_missing_announced_at: int = 0
    index_constituent_rows: int
    index_weight_snapshot_rows: int
    pit_ready: bool
