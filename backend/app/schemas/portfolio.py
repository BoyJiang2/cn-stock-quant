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
