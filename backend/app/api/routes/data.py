from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_session
from app.data.akshare_news_provider import AkShareNewsProvider
from app.data.akshare_provider import AkShareProvider
from app.data.full_market import FullMarketSyncConfig, FullMarketSyncCoordinator
from app.data.provider import MarketDataProvider
from app.data.repository import MarketDataRepository
from app.data.symbols import INDEX_SYMBOL_WHITELIST, normalize_a_share_symbol
from app.schemas.data import (
    BatchDailySyncItem,
    BatchDailySyncRequest,
    BatchDailySyncResponse,
    DailyBarOut,
    DailyStatusOut,
    DataDiagnosticsOut,
    DataQualityReportOut,
    DailySyncRequest,
    DailySyncResponse,
    IndexSyncRequest,
    CalendarSyncResponse,
    FullMarketSyncRequest,
    FullMarketSyncResponse,
    NewsItemOut,
    NewsSyncRequest,
    NewsSyncResponse,
    ResearchSyncNextRequest,
    ResearchSyncNextResponse,
    ResearchSyncProgressOut,
    StockOut,
    SymbolStatusOut,
    SyncJobOut,
)

router = APIRouter()

def _database_identifier() -> str:
    """Return a safe, non-sensitive database identifier for display.

    Absolute filesystem paths and connection credentials are never exposed.
    For SQLite the configured path is shown as-is (relative when configured
    relatively); for other backends the scheme/host/database are shown with
    any user/password stripped.
    """
    url = settings.database_url
    if url.startswith("sqlite:///"):
        path_part = url[len("sqlite:///"):]
        if Path(path_part).is_absolute():
            path_part = Path(path_part).name
        return f"sqlite:///{path_part}"
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or ""
    return f"{parsed.scheme}://{host}{path}"


def _datetime_to_date(value: datetime | None) -> date | None:
    return value.date() if value is not None else None


@router.post("/sync/stocks")
def sync_stocks(session: Session = Depends(get_session)) -> dict[str, int]:
    provider = AkShareProvider()
    repository = MarketDataRepository(session)
    try:
        stocks = provider.stock_list()
    except Exception as exc:
        repository.create_sync_job("stocks", "all", "failed", message=str(exc))
        raise HTTPException(status_code=502, detail=f"AkShare stock list request failed: {exc}") from exc
    count = repository.upsert_stocks(stocks)
    repository.create_sync_job("stocks", "all", "success", records=count)
    return {"synced": count}


@router.post("/sync/calendar", response_model=CalendarSyncResponse)
def sync_calendar(session: Session = Depends(get_session)) -> CalendarSyncResponse:
    provider = AkShareProvider()
    repository = MarketDataRepository(session)
    try:
        calendar = provider.trading_calendar()
    except Exception as exc:
        repository.create_sync_job("calendar", "A_SHARE", "failed", message=str(exc))
        raise HTTPException(status_code=502, detail=f"AkShare calendar request failed: {exc}") from exc
    count = repository.upsert_trading_calendar(calendar)
    repository.create_sync_job("calendar", "A_SHARE", "success", records=count)
    return CalendarSyncResponse(
        synced=count,
        start_date=calendar["trade_date"].min() if not calendar.empty else None,
        end_date=calendar["trade_date"].max() if not calendar.empty else None,
    )


@router.post("/sync/daily", response_model=DailySyncResponse)
def sync_daily(payload: DailySyncRequest, session: Session = Depends(get_session)) -> DailySyncResponse:
    provider = AkShareProvider()
    repository = MarketDataRepository(session)
    try:
        symbol = repository.resolve_symbol(payload.symbol)
    except ValueError as exc:
        repository.create_sync_job("daily", payload.symbol, "failed", message=str(exc), start_date=payload.start_date, end_date=payload.end_date)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        bars = provider.daily_bars(symbol, payload.start_date, payload.end_date, payload.adjust)
    except Exception as exc:
        cached_count = repository.daily_bar_count(symbol, payload.start_date, payload.end_date)
        if cached_count:
            repository.create_sync_job(
                "daily",
                symbol,
                "cached",
                records=cached_count,
                message=f"AkShare failed, reused local cached bars: {exc}",
                start_date=payload.start_date,
                end_date=payload.end_date,
            )
            return DailySyncResponse(symbol=symbol, synced=cached_count, status="cached", message="reused local cache")
        repository.create_sync_job("daily", symbol, "failed", message=str(exc), start_date=payload.start_date, end_date=payload.end_date)
        raise HTTPException(status_code=502, detail=f"AkShare daily bars request failed: {exc}") from exc
    if bars.empty:
        repository.create_sync_job("daily", symbol, "empty", start_date=payload.start_date, end_date=payload.end_date)
        return DailySyncResponse(
            symbol=symbol,
            synced=0,
            status="empty",
            message=f"no daily bars between {payload.start_date} and {payload.end_date}",
        )
    count = repository.replace_daily_bars(symbol, payload.start_date, payload.end_date, bars)
    repository.create_sync_job("daily", symbol, "success", records=count, start_date=payload.start_date, end_date=payload.end_date)
    return DailySyncResponse(symbol=symbol, synced=count, status="success")


@router.post("/sync/news", response_model=NewsSyncResponse)
def sync_news(payload: NewsSyncRequest, session: Session = Depends(get_session)) -> NewsSyncResponse:
    repository = MarketDataRepository(session)
    try:
        symbol = repository.resolve_symbol(payload.symbol)
    except ValueError as exc:
        repository.create_sync_job(
            "news",
            payload.symbol,
            "failed",
            message=str(exc),
            start_date=_datetime_to_date(payload.start_at),
            end_date=_datetime_to_date(payload.end_at),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    provider = AkShareNewsProvider()
    try:
        items = provider.stock_news(
            symbol,
            start_at=payload.start_at,
            end_at=payload.end_at,
        )
    except Exception as exc:
        repository.create_sync_job(
            "news",
            symbol,
            "failed",
            message=str(exc),
            start_date=_datetime_to_date(payload.start_at),
            end_date=_datetime_to_date(payload.end_at),
        )
        raise HTTPException(status_code=502, detail=f"AkShare news request failed: {exc}") from exc

    if items.empty:
        repository.create_sync_job(
            "news",
            symbol,
            "empty",
            start_date=_datetime_to_date(payload.start_at),
            end_date=_datetime_to_date(payload.end_at),
        )
        return NewsSyncResponse(symbol=symbol, synced=0, status="empty", message="no news")

    count = repository.upsert_news_items(items)
    repository.create_sync_job(
        "news",
        symbol,
        "success",
        records=count,
        start_date=_datetime_to_date(payload.start_at),
        end_date=_datetime_to_date(payload.end_at),
    )
    return NewsSyncResponse(symbol=symbol, synced=count, status="success")


@router.post("/sync/index", response_model=DailySyncResponse)
def sync_index(payload: IndexSyncRequest, session: Session = Depends(get_session)) -> DailySyncResponse:
    repository = MarketDataRepository(session)
    if payload.start_date > payload.end_date:
        repository.create_sync_job(
            "index_daily",
            payload.symbol,
            "failed",
            message="start_date must be <= end_date",
            start_date=payload.start_date,
            end_date=payload.end_date,
        )
        raise HTTPException(
            status_code=400,
            detail=f"start_date ({payload.start_date}) must be <= end_date ({payload.end_date})",
        )
    try:
        symbol = normalize_a_share_symbol(payload.symbol)
    except ValueError as exc:
        repository.create_sync_job(
            "index_daily",
            payload.symbol,
            "failed",
            message=str(exc),
            start_date=payload.start_date,
            end_date=payload.end_date,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if symbol not in INDEX_SYMBOL_WHITELIST:
        message = f"index symbol {symbol} is not allowed; permitted: {sorted(INDEX_SYMBOL_WHITELIST)}"
        repository.create_sync_job(
            "index_daily",
            symbol,
            "failed",
            message=message,
            start_date=payload.start_date,
            end_date=payload.end_date,
        )
        raise HTTPException(
            status_code=400,
            detail=message,
        )
    provider = AkShareProvider()
    try:
        bars = provider.index_daily_bars(symbol, payload.start_date, payload.end_date)
    except Exception as exc:
        cached_count = repository.index_daily_bar_count(symbol, payload.start_date, payload.end_date)
        if cached_count:
            repository.create_sync_job(
                "index_daily",
                symbol,
                "cached",
                records=cached_count,
                message=f"AkShare failed, reused local cached bars: {exc}",
                start_date=payload.start_date,
                end_date=payload.end_date,
            )
            return DailySyncResponse(symbol=symbol, synced=cached_count, status="cached", message="reused local cache")
        repository.create_sync_job("index_daily", symbol, "failed", message=str(exc), start_date=payload.start_date, end_date=payload.end_date)
        raise HTTPException(status_code=502, detail=f"AkShare index bars request failed: {exc}") from exc
    if bars.empty:
        repository.create_sync_job("index_daily", symbol, "empty", start_date=payload.start_date, end_date=payload.end_date)
        return DailySyncResponse(
            symbol=symbol,
            synced=0,
            status="empty",
            message=f"no index bars between {payload.start_date} and {payload.end_date}",
        )
    count = repository.replace_index_daily_bars(symbol, payload.start_date, payload.end_date, bars)
    repository.create_sync_job("index_daily", symbol, "success", records=count, start_date=payload.start_date, end_date=payload.end_date)
    return DailySyncResponse(symbol=symbol, synced=count, status="success")


def _sync_symbol_batch(
    provider: MarketDataProvider,
    repository: MarketDataRepository,
    symbols: list[str],
    start_date: date,
    end_date: date,
    adjust: str,
) -> list[BatchDailySyncItem]:
    """Run the per-symbol daily sync loop shared by the batch endpoints.

    Each candidate is normalized, fetched via the provider, and persisted.
    Provider failures fall back to local cached bars when available. The
    same logic powers ``/sync/daily/batch`` and ``/sync/research/next``.
    """
    items: list[BatchDailySyncItem] = []

    for raw_symbol in symbols:
        symbol: str | None = None
        try:
            symbol = repository.resolve_symbol(raw_symbol)
            try:
                bars = provider.daily_bars(symbol, start_date, end_date, adjust)
            except Exception as exc:
                cached_count = repository.daily_bar_count(symbol, start_date, end_date)
                if cached_count:
                    repository.create_sync_job(
                        "daily",
                        symbol,
                        "cached",
                        records=cached_count,
                        message=f"AkShare failed, reused local cached bars: {exc}",
                        start_date=start_date,
                        end_date=end_date,
                    )
                    items.append(BatchDailySyncItem(symbol=symbol, status="cached", synced=cached_count, message="reused local cache"))
                    continue
                raise
            if bars.empty:
                repository.create_sync_job("daily", symbol, "empty", start_date=start_date, end_date=end_date)
                items.append(BatchDailySyncItem(symbol=symbol, status="empty", message="no data"))
                continue
            count = repository.replace_daily_bars(symbol, start_date, end_date, bars)
            repository.create_sync_job("daily", symbol, "success", records=count, start_date=start_date, end_date=end_date)
            items.append(BatchDailySyncItem(symbol=symbol, status="success", synced=count))
        except Exception as exc:
            target = symbol if symbol is not None else raw_symbol.strip()
            repository.create_sync_job("daily", target, "failed", message=str(exc), start_date=start_date, end_date=end_date)
            items.append(BatchDailySyncItem(symbol=target, status="failed", message=str(exc)))

    return items


@router.post("/sync/daily/batch", response_model=BatchDailySyncResponse)
def sync_daily_batch(payload: BatchDailySyncRequest, session: Session = Depends(get_session)):
    provider = AkShareProvider()
    repository = MarketDataRepository(session)
    items = _sync_symbol_batch(provider, repository, payload.symbols, payload.start_date, payload.end_date, payload.adjust)
    success = sum(1 for item in items if item.status in {"success", "cached"})
    failed = sum(1 for item in items if item.status == "failed")
    return BatchDailySyncResponse(total=len(items), success=success, failed=failed, items=items)


@router.get("/sync/research/progress", response_model=ResearchSyncProgressOut)
def research_sync_progress(
    start_date: date = Query(..., description="Range start (inclusive), YYYY-MM-DD"),
    end_date: date = Query(..., description="Range end (inclusive), YYYY-MM-DD"),
    session: Session = Depends(get_session),
) -> ResearchSyncProgressOut:
    if start_date > end_date:
        raise HTTPException(
            status_code=400,
            detail=f"start_date ({start_date}) must be <= end_date ({end_date})",
        )
    progress = MarketDataRepository(session).research_sync_progress(start_date, end_date)
    return ResearchSyncProgressOut(**progress)


@router.post("/sync/research/next", response_model=ResearchSyncNextResponse)
def sync_research_next(payload: ResearchSyncNextRequest, session: Session = Depends(get_session)):
    if payload.start_date > payload.end_date:
        raise HTTPException(
            status_code=400,
            detail=f"start_date ({payload.start_date}) must be <= end_date ({payload.end_date})",
        )
    provider = AkShareProvider()
    repository = MarketDataRepository(session)
    symbols = repository.next_research_sync_symbols(
        payload.start_date,
        payload.end_date,
        batch_size=payload.batch_size,
    )
    items = _sync_symbol_batch(provider, repository, symbols, payload.start_date, payload.end_date, "qfq")
    # `cached` does not advance coverage; success/empty do.
    success = sum(1 for item in items if item.status in {"success", "empty"})
    failed = sum(1 for item in items if item.status == "failed")
    progress = repository.research_sync_progress(payload.start_date, payload.end_date)
    return ResearchSyncNextResponse(
        total=len(items),
        success=success,
        failed=failed,
        items=items,
        progress=ResearchSyncProgressOut(**progress),
    )


@router.get("/sync/full-market/progress", response_model=ResearchSyncProgressOut)
def full_market_sync_progress(
    start_date: date = Query(...),
    end_date: date = Query(...),
    session: Session = Depends(get_session),
) -> ResearchSyncProgressOut:
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")
    return ResearchSyncProgressOut(
        **MarketDataRepository(session).full_market_sync_progress(start_date, end_date)
    )


@router.post("/sync/full-market/next", response_model=FullMarketSyncResponse)
def sync_full_market_next(
    payload: FullMarketSyncRequest,
    session: Session = Depends(get_session),
) -> FullMarketSyncResponse:
    if payload.start_date > payload.end_date:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")
    repository = MarketDataRepository(session)
    coordinator = FullMarketSyncCoordinator(
        repository,
        AkShareProvider(),
        FullMarketSyncConfig(
            batch_size=payload.batch_size,
            exchanges=("SH", "SZ", "BJ"),
            max_failures=payload.max_failures,
            min_request_interval=payload.min_request_interval,
            adjust=payload.adjust,
            exclude_risk_names=False,
            retry_failed=payload.retry_failed,
        ),
    )
    summary = coordinator.run_batch(payload.start_date, payload.end_date)
    progress = repository.full_market_sync_progress(payload.start_date, payload.end_date)
    return FullMarketSyncResponse(
        total=summary.total,
        processed=summary.processed,
        success=summary.success,
        empty=summary.empty,
        failed=summary.failed,
        skipped=summary.skipped,
        completed=summary.completed,
        blocked=summary.blocked,
        items=[
            {
                "symbol": item.symbol,
                "status": item.status,
                "synced": item.synced,
                "message": item.message,
            }
            for item in summary.items
        ],
        progress=ResearchSyncProgressOut(**progress),
    )


@router.get("/quality", response_model=DataQualityReportOut)
def data_quality(
    start_date: date = Query(...),
    end_date: date = Query(...),
    limit: int = Query(200, ge=1, le=6000),
    session: Session = Depends(get_session),
) -> DataQualityReportOut:
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")
    return DataQualityReportOut(
        **MarketDataRepository(session).data_quality_report(
            start_date,
            end_date,
            limit=limit,
        )
    )


@router.get("/stocks", response_model=list[StockOut])
def list_stocks(limit: int = 100, keyword: str | None = None, session: Session = Depends(get_session)):
    stocks = MarketDataRepository(session).list_stocks(limit=limit, keyword=keyword)
    return [StockOut(symbol=s.symbol, name=s.name, exchange=s.exchange, status=s.status) for s in stocks]


@router.get("/daily", response_model=list[DailyBarOut])
def list_daily(
    symbol: str,
    start_date: date = Query(..., description="Range start (inclusive), YYYY-MM-DD"),
    end_date: date = Query(..., description="Range end (inclusive), YYYY-MM-DD"),
    session: Session = Depends(get_session),
):
    if start_date > end_date:
        raise HTTPException(
            status_code=400,
            detail=f"start_date ({start_date}) must be <= end_date ({end_date})",
        )

    repository = MarketDataRepository(session)
    try:
        normalized_symbol = repository.resolve_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    bars = repository.daily_bars(
        [normalized_symbol],
        start_date,
        end_date,
    )
    return bars.to_dict("records")


@router.get("/daily/status", response_model=list[DailyStatusOut])
def daily_status(limit: int = 200, session: Session = Depends(get_session)):
    return MarketDataRepository(session).daily_status(limit=limit)


@router.get("/sync/jobs", response_model=list[SyncJobOut])
def sync_jobs(limit: int = 100, session: Session = Depends(get_session)):
    jobs = MarketDataRepository(session).list_sync_jobs(limit=limit)
    return [
        SyncJobOut(
            id=job.id,
            job_type=job.job_type,
            target=job.target,
            status=job.status,
            start_date=job.start_date,
            end_date=job.end_date,
            records=job.records,
            message=job.message,
        )
        for job in jobs
    ]


@router.get("/news", response_model=list[NewsItemOut])
def list_news(
    symbol: str | None = None,
    start_at: datetime | None = Query(None),
    end_at: datetime | None = Query(None),
    source: str | None = None,
    limit: int = Query(200, ge=1, le=5000),
    session: Session = Depends(get_session),
) -> list[dict]:
    repository = MarketDataRepository(session)
    normalized_symbol = None
    if symbol is not None and symbol.strip():
        try:
            normalized_symbol = repository.resolve_symbol(symbol)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    frame = repository.news_items(
        symbol=normalized_symbol,
        start_at=start_at,
        end_at=end_at,
        source=source,
        limit=limit,
    )
    return frame.to_dict("records")


@router.get("/diagnostics", response_model=DataDiagnosticsOut)
def diagnostics(session: Session = Depends(get_session)):
    overview = MarketDataRepository(session).market_data_overview()
    return DataDiagnosticsOut(
        stock_count=overview["stock_count"],
        bar_count=overview["bar_count"],
        symbols_with_bars=overview["symbols_with_bars"],
        start_date=overview["start_date"],
        end_date=overview["end_date"],
        database=_database_identifier(),
    )


@router.get("/symbol-status", response_model=SymbolStatusOut)
def symbol_status(
    symbol: str = Query(..., min_length=1, description="A-share symbol to diagnose"),
    session: Session = Depends(get_session),
):
    repository = MarketDataRepository(session)
    try:
        normalized_symbol = repository.resolve_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    status = repository.symbol_data_status(normalized_symbol)
    return SymbolStatusOut(
        symbol=status["symbol"],
        stock_exists=status["stock_exists"],
        name=status["name"],
        exchange=status["exchange"],
        has_daily_bars=status["has_daily_bars"],
        start_date=status["start_date"],
        end_date=status["end_date"],
        bar_count=status["bar_count"],
    )
