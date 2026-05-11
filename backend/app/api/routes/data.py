from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.data.akshare_provider import AkShareProvider
from app.data.repository import MarketDataRepository
from app.data.symbols import normalize_a_share_symbol
from app.schemas.data import (
    BatchDailySyncItem,
    BatchDailySyncRequest,
    BatchDailySyncResponse,
    DailyBarOut,
    DailyStatusOut,
    DailySyncRequest,
    StockOut,
    SyncJobOut,
)

router = APIRouter()


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


@router.post("/sync/daily")
def sync_daily(payload: DailySyncRequest, session: Session = Depends(get_session)) -> dict[str, int | str]:
    provider = AkShareProvider()
    repository = MarketDataRepository(session)
    try:
        symbol = normalize_a_share_symbol(payload.symbol)
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
            return {"symbol": symbol, "synced": cached_count, "status": "cached"}
        repository.create_sync_job("daily", symbol, "failed", message=str(exc), start_date=payload.start_date, end_date=payload.end_date)
        raise HTTPException(status_code=502, detail=f"AkShare daily bars request failed: {exc}") from exc
    if bars.empty:
        repository.create_sync_job("daily", symbol, "empty", start_date=payload.start_date, end_date=payload.end_date)
        raise HTTPException(
            status_code=404,
            detail=f"No daily bars found for {symbol} between {payload.start_date} and {payload.end_date}.",
        )
    count = repository.replace_daily_bars(symbol, payload.start_date, payload.end_date, bars)
    repository.create_sync_job("daily", symbol, "success", records=count, start_date=payload.start_date, end_date=payload.end_date)
    return {"symbol": symbol, "synced": count}


@router.post("/sync/daily/batch", response_model=BatchDailySyncResponse)
def sync_daily_batch(payload: BatchDailySyncRequest, session: Session = Depends(get_session)):
    provider = AkShareProvider()
    repository = MarketDataRepository(session)
    items: list[BatchDailySyncItem] = []

    for raw_symbol in payload.symbols:
        try:
            symbol = normalize_a_share_symbol(raw_symbol)
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
                    items.append(BatchDailySyncItem(symbol=symbol, status="cached", synced=cached_count, message="reused local cache"))
                    continue
                raise
            if bars.empty:
                repository.create_sync_job("daily", symbol, "empty", start_date=payload.start_date, end_date=payload.end_date)
                items.append(BatchDailySyncItem(symbol=symbol, status="empty", message="no data"))
                continue
            count = repository.replace_daily_bars(symbol, payload.start_date, payload.end_date, bars)
            repository.create_sync_job("daily", symbol, "success", records=count, start_date=payload.start_date, end_date=payload.end_date)
            items.append(BatchDailySyncItem(symbol=symbol, status="success", synced=count))
        except Exception as exc:
            target = raw_symbol.strip()
            repository.create_sync_job("daily", target, "failed", message=str(exc), start_date=payload.start_date, end_date=payload.end_date)
            items.append(BatchDailySyncItem(symbol=target, status="failed", message=str(exc)))

    success = sum(1 for item in items if item.status in {"success", "cached"})
    failed = sum(1 for item in items if item.status == "failed")
    return BatchDailySyncResponse(total=len(items), success=success, failed=failed, items=items)


@router.get("/stocks", response_model=list[StockOut])
def list_stocks(limit: int = 100, keyword: str | None = None, session: Session = Depends(get_session)):
    stocks = MarketDataRepository(session).list_stocks(limit=limit, keyword=keyword)
    return [StockOut(symbol=s.symbol, name=s.name, exchange=s.exchange, status=s.status) for s in stocks]


@router.get("/daily", response_model=list[DailyBarOut])
def list_daily(symbol: str, start_date: str, end_date: str, session: Session = Depends(get_session)):
    from datetime import date

    try:
        normalized_symbol = normalize_a_share_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    bars = MarketDataRepository(session).daily_bars(
        [normalized_symbol],
        date.fromisoformat(start_date),
        date.fromisoformat(end_date),
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
