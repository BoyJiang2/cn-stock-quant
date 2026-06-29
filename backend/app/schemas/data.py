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


class DailySyncResponse(BaseModel):
    symbol: str
    synced: int
    status: str
    message: str = ""


class IndexSyncRequest(BaseModel):
    symbol: str = Field(..., examples=["000300"])
    start_date: date
    end_date: date


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


class ResearchSyncProgressOut(BaseModel):
    total: int
    covered: int
    remaining: int
    percent: float


class ResearchSyncNextRequest(BaseModel):
    start_date: date
    end_date: date
    batch_size: int = Field(20, ge=1, le=50)


class ResearchSyncNextResponse(BaseModel):
    total: int
    success: int
    failed: int
    items: list[BatchDailySyncItem]
    progress: ResearchSyncProgressOut


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


class DataDiagnosticsOut(BaseModel):
    stock_count: int
    bar_count: int
    symbols_with_bars: int
    start_date: date | None
    end_date: date | None
    database: str


class SymbolStatusOut(BaseModel):
    symbol: str
    stock_exists: bool
    name: str | None
    exchange: str | None
    has_daily_bars: bool
    start_date: date | None
    end_date: date | None
    bar_count: int


class CalendarSyncResponse(BaseModel):
    synced: int
    start_date: date | None = None
    end_date: date | None = None


class FullMarketSyncRequest(BaseModel):
    start_date: date
    end_date: date
    batch_size: int = Field(20, ge=1, le=50)
    max_failures: int = Field(3, ge=1, le=20)
    min_request_interval: float = Field(0.35, ge=0.0, le=5.0)
    adjust: str = "qfq"
    retry_failed: bool = False


class FullMarketSyncItemOut(BaseModel):
    symbol: str
    status: str
    synced: int = 0
    message: str = ""


class FullMarketSyncResponse(BaseModel):
    total: int
    processed: int
    success: int
    empty: int
    failed: int
    skipped: int
    completed: bool
    blocked: bool
    items: list[FullMarketSyncItemOut]
    progress: ResearchSyncProgressOut


class DataQualityItemOut(BaseModel):
    symbol: str
    expected: int
    present: int
    missing: int
    missing_dates: list[date]


class DataQualityReportOut(BaseModel):
    expected_trading_days: int
    symbols_checked: int
    symbols_fully_covered: int
    symbols_with_gaps: int
    total_missing_bars: int
    items: list[DataQualityItemOut]
    warning: str
