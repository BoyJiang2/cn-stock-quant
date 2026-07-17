from datetime import date

from pydantic import BaseModel, Field


class PaperPortfolioPositionIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    quantity: int = Field(ge=1)


class PaperPortfolioSnapshotIn(BaseModel):
    as_of_date: date
    cash: float = Field(ge=0)
    positions: list[PaperPortfolioPositionIn] = Field(default_factory=list, max_length=500)


class PaperPortfolioPositionOut(BaseModel):
    symbol: str
    name: str | None = None
    quantity: int
    reference_price: float
    price_date: date
    market_value: float


class PaperPortfolioValuationOut(BaseModel):
    as_of_date: date
    cash: float
    position_value: float
    equity: float


class PaperPortfolioStateOut(BaseModel):
    id: int
    name: str
    as_of_date: date | None = None
    cash: float
    position_value: float
    equity: float
    positions: list[PaperPortfolioPositionOut] = Field(default_factory=list)


class PaperPortfolioDiagnosticsOut(BaseModel):
    as_of_date: date | None = None
    cash_weight: float
    gross_exposure: float
    largest_position_weight: float
    top_three_weight: float
    concentration_hhi: float
    current_drawdown: float
    max_drawdown: float
    warnings: list[str] = Field(default_factory=list)


class PaperPortfolioAdvisoryReviewRowOut(BaseModel):
    symbol: str
    name: str | None = None
    current_quantity: int
    advisory_current_quantity: int | None = None
    target_quantity: int | None = None
    quantity_delta: int | None = None
    suggested_side: str | None = None
    target_weight: float | None = None
    reference_price: float | None = None
    estimated_delta_amount: float | None = None


class PaperPortfolioAdvisoryReviewOut(BaseModel):
    advisory_id: int
    advisory_strategy_name: str
    advisory_as_of_date: date
    advisory_status: str
    portfolio_as_of_date: date | None = None
    portfolio_equity: float
    requires_refresh: bool
    rows: list[PaperPortfolioAdvisoryReviewRowOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
