from datetime import date

from pydantic import BaseModel, Field


class StockOut(BaseModel):
    symbol: str
    name: str
    exchange: str
    status: str


class DailySyncRequest(BaseModel):
    symbol: str = Field(..., examples=["000001"])
    start_date: date
    end_date: date
    adjust: str = "qfq"


class DailyBarOut(BaseModel):
    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float


class BatchDailySyncRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1, max_length=50)
    start_date: date
    end_date: date
    adjust: str = "qfq"


class BatchDailySyncItem(BaseModel):
    symbol: str
    status: str
    synced: int = 0
    message: str = ""


class BatchDailySyncResponse(BaseModel):
    total: int
    success: int
    failed: int
    items: list[BatchDailySyncItem]


class SyncJobOut(BaseModel):
    id: int
    job_type: str
    target: str
    status: str
    start_date: date | None
    end_date: date | None
    records: int
    message: str


class DailyStatusOut(BaseModel):
    symbol: str
    start_date: date
    end_date: date
    bar_count: int
