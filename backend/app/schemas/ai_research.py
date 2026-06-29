from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.factors import FactorSummaryOut


class AIFactorCandidateRequest(BaseModel):
    candidate_name: str = Field(min_length=1, max_length=100)
    components: dict[str, float] = Field(min_length=1, max_length=12)
    symbol_source: Literal["manual", "research_pool"] = "research_pool"
    symbols: list[str] = Field(default_factory=list)
    pool_max_symbols: int = Field(default=100, ge=5, le=300)
    start_date: date
    end_date: date
    horizon: int = Field(default=5, ge=1, le=60)
    n_groups: int = Field(default=5, ge=2, le=10)


class AIFactorCandidateResponse(BaseModel):
    candidate_name: str
    components: dict[str, float]
    selected_symbols: list[str]
    warnings: list[str]
    summary: FactorSummaryOut


class MarketRegimeRequest(BaseModel):
    benchmark_symbol: str = "000300"
    as_of_date: date
    lookback_calendar_days: int = Field(default=260, ge=120, le=1000)
    include_market_breadth: bool = True
    breadth_max_symbols: int = Field(default=1000, ge=50, le=6000)


class MarketRegimeResponse(BaseModel):
    benchmark_symbol: str
    as_of_date: date
    regime: str
    confidence: float
    trend_score: float
    breadth_score: float
    volatility_score: float
    drawdown: float
    reasons: list[str]
    breadth_symbol_count: int
    llm_context: dict
    can_trade_directly: bool = False
