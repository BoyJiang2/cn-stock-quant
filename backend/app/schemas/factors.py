from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class FactorExperimentRequest(BaseModel):
    symbol_source: Literal["manual", "research_pool"] = "research_pool"
    symbols: list[str] = Field(default_factory=list)
    pool_max_symbols: int = Field(default=100, ge=5, le=300)
    factor_names: list[str] = Field(default_factory=list)
    start_date: date
    end_date: date
    horizon: int = Field(default=5, ge=1, le=60)
    n_groups: int = Field(default=5, ge=2, le=10)


class FactorMetadataOut(BaseModel):
    name: str
    direction: int


class FactorSummaryOut(BaseModel):
    name: str
    direction: int
    ic_mean: float | None
    ic_ir: float | None
    rankic_mean: float | None
    rankic_ir: float | None
    long_short_return: float | None
    long_short_turnover: float | None
    n_dates: int
    group_returns: dict[int, float | None]


class FactorExperimentResponse(BaseModel):
    selected_symbols: list[str]
    factor_count: int
    horizon: int
    n_groups: int
    warnings: list[str]
    summaries: list[FactorSummaryOut]
