from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AdvisoryPositionIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    quantity: int = Field(ge=0)


class AdvisoryRequest(BaseModel):
    strategy_name: str = Field(default="moving_average", min_length=1, max_length=128)
    as_of_date: date
    symbols: list[str] = Field(min_length=1, max_length=6000)
    cash: float = Field(ge=0)
    positions: list[AdvisoryPositionIn] = Field(default_factory=list)
    strategy_parameters: dict[str, Any] = Field(default_factory=dict)
    lookback_calendar_days: int = Field(default=365, ge=30, le=2000)
    max_symbol_weight: float = Field(default=0.1, ge=0.0, le=1.0)
    max_total_weight: float = Field(default=0.8, ge=0.0, le=1.0)
    max_positions: int | None = Field(default=20, ge=1, le=500)
    validation_id: int | None = Field(default=None, ge=1)
    allow_remote_llm: bool = False


class AdvisoryTradeOut(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    current_quantity: int
    target_quantity: int
    quantity: int
    reference_price: float
    estimated_amount: float


class MarketEvidenceOut(BaseModel):
    available: bool
    benchmark_symbol: str = "000300"
    as_of_date: date
    data_end_date: date | None = None
    regime: str | None = None
    confidence: float | None = None
    trend_score: float | None = None
    breadth_score: float | None = None
    volatility_score: float | None = None
    drawdown: float | None = None
    reasons: list[str] = Field(default_factory=list)
    warning: str | None = None


class NewsEvidenceItemOut(BaseModel):
    symbol: str | None = None
    source: str
    title: str
    event_type: str
    sentiment_label: str
    published_at: datetime
    known_at: datetime


class NewsEvidenceOut(BaseModel):
    availability_mode: Literal["observed"] = "observed"
    window_start: datetime
    as_of_at: datetime
    total_items: int = 0
    severe_company_risk_count: int = 0
    company_risk_count: int = 0
    items: list[NewsEvidenceItemOut] = Field(default_factory=list)


class FactorValueOut(BaseModel):
    name: str
    direction: int
    raw_value: float | None = None


class FactorSymbolEvidenceOut(BaseModel):
    symbol: str
    available: bool
    values: list[FactorValueOut] = Field(default_factory=list)
    warning: str | None = None


class FactorEvidenceOut(BaseModel):
    availability_mode: Literal["observed_trailing"] = "observed_trailing"
    as_of_date: date
    data_start_date: date | None = None
    data_end_date: date | None = None
    factor_names: list[str] = Field(default_factory=list)
    symbols: list[FactorSymbolEvidenceOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ValidationEvidenceOut(BaseModel):
    validation_id: int
    backtest_run_id: int
    source_as_of_date: date
    fingerprint: str
    aggregate: dict[str, float] = Field(default_factory=dict)
    cost_stress_aggregate: dict[str, float] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)


class EligibleValidationOptionOut(BaseModel):
    id: int
    backtest_run_id: int
    strategy_name: str
    as_of_date: date
    strategy_parameters: dict[str, Any] = Field(default_factory=dict)
    aggregate: dict[str, float] = Field(default_factory=dict)
    cost_stress_aggregate: dict[str, float] = Field(default_factory=dict)


class AdvisoryResponse(BaseModel):
    id: int
    status: Literal["draft", "llm_disabled", "failed"]
    as_of_date: date
    earliest_execution_date: date | None = None
    price_basis: Literal["research_close_only"]
    strategy_name: str
    total_equity: float
    raw_target_weights: dict[str, float]
    accepted_target_weights: dict[str, float]
    rejected_target_weights: dict[str, str]
    trade_plan: list[AdvisoryTradeOut]
    market_evidence: MarketEvidenceOut
    news_evidence: NewsEvidenceOut
    factor_evidence: FactorEvidenceOut
    validation_evidence: ValidationEvidenceOut | None = None
    warnings: list[str] = Field(default_factory=list)
    remote_llm_enabled: bool = False
    llm_summary: str | None = None


class AdvisoryReviewResponse(BaseModel):
    id: int
    status: Literal["reviewed"]
    reviewed_at: str


class AdvisoryRejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class AdvisoryRejectResponse(BaseModel):
    id: int
    status: Literal["rejected"]
    rejection_reason: str | None = None


class AdvisoryStatusResponse(BaseModel):
    id: int
    status: Literal["draft", "reviewed", "expired", "rejected"]
    as_of_date: date
    earliest_execution_date: date | None = None
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None


class AdvisoryNotificationResponse(BaseModel):
    delivery_id: int
    status: Literal["sent"]
    channel: str
    provider_message: str
