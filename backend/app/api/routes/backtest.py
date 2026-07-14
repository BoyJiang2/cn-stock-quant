from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException
import pandas as pd
from sqlalchemy.orm import Session

from app.backtest.engine import BacktestConfig, DailyBacktestEngine
from app.backtest.persistence import (
    get_backtest_equity,
    get_backtest_run,
    get_backtest_trades,
    list_backtest_runs,
    save_backtest_result,
)
from app.core.database import get_session
from app.data.akshare_provider import AkShareProvider
from app.data.pit_repository import PitRepository
from app.data.repository import MarketDataRepository
from app.data.symbols import INDEX_SYMBOL_WHITELIST, normalize_a_share_symbol
from app.schemas.backtest import BacktestMetrics, BacktestRequest, BacktestResponse, BacktestRunOut, EquityPoint, TradeOut
from app.strategy.registry import get_strategy

router = APIRouter()


@router.post("/run", response_model=BacktestResponse)
def run_backtest(payload: BacktestRequest, session: Session = Depends(get_session)):
    repository = MarketDataRepository(session)
    universe_metadata: dict = {"mode": "manual"}
    if payload.symbol_source == "research_pool":
        if payload.point_in_time:
            as_of_date = payload.universe_as_of_date or payload.start_date
            if as_of_date > payload.start_date:
                raise HTTPException(
                    status_code=400,
                    detail="universe_as_of_date cannot be later than backtest start_date.",
                )
            pit_result = PitRepository(
                session,
                bar_reader=repository,
            ).select_research_symbols_pit(
                as_of_date,
                payload.start_date,
                payload.end_date,
                index_symbol=payload.pit_index_symbol,
                st_policy=payload.pit_st_policy,
                limit=payload.pool_max_symbols,
            )
            symbols = pit_result.symbols
            universe_metadata = {"mode": "pit_fixed", **pit_result.meta}
        else:
            # Prefer the trading-day-based selection that tolerates gaps,
            # weekends/holidays, end-of-data, and recently-listed stocks.
            symbols = repository.select_research_symbols(
                payload.start_date,
                payload.end_date,
                limit=payload.pool_max_symbols,
            )
            if not symbols:
                symbols = repository.covered_research_symbols(
                    payload.start_date,
                    payload.end_date,
                    limit=payload.pool_max_symbols,
                )
            universe_metadata = {
                "mode": "current_snapshot",
                "pit_degraded": True,
                "warning": "Historical security status and index membership were not applied.",
            }
        if not symbols:
            diagnostics = repository.research_pool_diagnostics(
                payload.start_date,
                payload.end_date,
            )
            diag_lines = [
                f"Eligible research stocks: {diagnostics['eligible_stocks']}",
                f"Symbols with bars: {diagnostics['db_symbols_with_bars']}",
                f"DB bar range: {diagnostics['db_min_bar_date']} … {diagnostics['db_max_bar_date']}",
                f"Expected trading days: {diagnostics['expected_trading_days']}",
            ]
            if diagnostics["top_symbols_in_range"]:
                diag_lines.append(
                    "Top symbols in range: "
                    + ", ".join(
                        f"{s['symbol']}({s['bars_in_range']} bars)"
                        for s in diagnostics["top_symbols_in_range"]
                    )
                )
            diag_lines.append(diagnostics["hint"])
            raise HTTPException(
                status_code=400,
                detail="No research-pool stocks with useful data for this date range. "
                + " ".join(diag_lines),
            )
    else:
        try:
            symbols = repository.resolve_symbols(payload.symbols)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not symbols:
            raise HTTPException(status_code=400, detail="At least one stock symbol is required.")

    bars = repository.daily_bars(symbols, payload.start_date, payload.end_date)
    if bars.empty:
        statuses = [repository.symbol_data_status(symbol) for symbol in symbols]
        missing = [item["symbol"] for item in statuses if not item["stock_exists"]]
        without_bars = [item["symbol"] for item in statuses if item["stock_exists"] and not item["has_daily_bars"]]
        if missing:
            detail = f"Unknown stock symbols: {', '.join(missing)}. Sync the stock list first."
        elif without_bars:
            detail = f"Stocks exist but have no local daily bars: {', '.join(without_bars)}. Sync daily data first."
        else:
            detail = "Local daily bars exist, but not for the requested date range."
        raise HTTPException(status_code=400, detail=detail)

    try:
        strategy = get_strategy(payload.strategy_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    benchmark_bars = None
    if payload.benchmark_symbol:
        try:
            benchmark_symbol = normalize_a_share_symbol(payload.benchmark_symbol)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid benchmark symbol: {exc}") from exc
        if benchmark_symbol not in INDEX_SYMBOL_WHITELIST:
            raise HTTPException(
                status_code=400,
                detail=f"Benchmark must be one of: {', '.join(sorted(INDEX_SYMBOL_WHITELIST))}",
            )
        benchmark_bars = _ensure_benchmark_bars(
            repository,
            benchmark_symbol=benchmark_symbol,
            start_date=payload.start_date,
            end_date=payload.end_date,
            strategy_dates=bars["trade_date"],
        )

    strategy_params = payload.strategy_parameters()
    news_history = _load_negative_news_history(
        repository,
        symbols,
        start_date=payload.start_date,
        end_date=payload.end_date,
        params=strategy_params,
    )

    try:
        result = DailyBacktestEngine().run(
            strategy=strategy,
            bars=bars,
            benchmark_bars=benchmark_bars,
            config=BacktestConfig(
                start_date=payload.start_date,
                end_date=payload.end_date,
                initial_cash=payload.initial_cash,
                commission_rate=payload.commission_rate,
                stamp_tax_rate=payload.stamp_tax_rate,
                slippage_rate=payload.slippage_rate,
                rebalance_interval=payload.rebalance_interval,
                risk_max_symbol_weight=payload.risk_max_symbol_weight,
                risk_max_total_weight=payload.risk_max_total_weight,
                risk_max_positions=payload.risk_max_positions,
                params=strategy_params,
                news_history=news_history,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    run_id = save_backtest_result(session, payload, result)
    return BacktestResponse(
        run_id=run_id,
        symbol_source=payload.symbol_source,
        selected_symbols=symbols,
        universe_metadata=universe_metadata,
        metrics=result.metrics,
        equity_curve=result.equity_curve,
        benchmark_curve=result.benchmark_curve,
        trades=result.trades,
    )


def _load_negative_news_history(
    repository: MarketDataRepository,
    symbols: list[str],
    *,
    start_date,
    end_date,
    params: dict,
) -> pd.DataFrame | None:
    if not _truthy(params.get("use_db_negative_news", False)):
        return None
    lookback_days = max(0, int(params.get("negative_news_lookback_days", 3)))
    start_at = datetime.combine(start_date - timedelta(days=lookback_days), time.min)
    end_at = datetime.combine(end_date, time.max)
    availability_mode = str(params.get("news_availability", "observed")).strip().lower()
    use_observed_window = availability_mode in {"observed", "fetched", "fetched_at", "live"}
    frames = [
        repository.news_items(
            symbol=symbol,
            start_at=None if use_observed_window else start_at,
            end_at=None if use_observed_window else end_at,
            known_start_at=start_at if use_observed_window else None,
            known_end_at=end_at if use_observed_window else None,
            limit=5000,
        )
        for symbol in symbols
    ]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return None
    news_history = pd.concat(frames, ignore_index=True)
    return _apply_news_availability(news_history, str(params.get("news_availability", "observed")))


def _apply_news_availability(news_history: pd.DataFrame, mode: str) -> pd.DataFrame:
    frame = news_history.copy()
    normalized = (mode or "observed").strip().lower()
    if normalized in {"published", "published_at", "source_published", "research"}:
        frame["known_at"] = pd.to_datetime(frame["published_at"], errors="coerce")
    elif normalized in {"observed", "fetched", "fetched_at", "live"}:
        frame["published_at"] = pd.to_datetime(frame["published_at"], errors="coerce")
        frame["fetched_at"] = pd.to_datetime(frame["fetched_at"], errors="coerce")
        frame["known_at"] = frame[["published_at", "fetched_at"]].max(axis=1)
    else:
        raise HTTPException(
            status_code=400,
            detail="news_availability must be 'observed' or 'published_at'.",
        )
    return frame


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _ensure_benchmark_bars(
    repository: MarketDataRepository,
    *,
    benchmark_symbol: str,
    start_date: date,
    end_date: date,
    strategy_dates: pd.Series,
) -> pd.DataFrame:
    benchmark_bars = repository.index_daily_bars(benchmark_symbol, start_date, end_date)
    first_strategy_date = strategy_dates.min()
    last_strategy_date = strategy_dates.max()
    if _benchmark_covers_strategy_dates(benchmark_bars, first_strategy_date, last_strategy_date):
        return benchmark_bars

    try:
        fetched_benchmark = AkShareProvider().index_daily_bars(
            benchmark_symbol,
            start_date,
            end_date,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Benchmark {benchmark_symbol} auto-sync failed: {exc}",
        ) from exc
    if fetched_benchmark.empty:
        raise HTTPException(
            status_code=400,
            detail=f"Benchmark {benchmark_symbol} returned no data for the requested range.",
        )

    repository.replace_index_daily_bars(
        benchmark_symbol,
        start_date,
        end_date,
        fetched_benchmark,
    )
    repository.create_sync_job(
        "index_daily",
        benchmark_symbol,
        "success",
        records=len(fetched_benchmark),
        message="auto-synced by backtest",
        start_date=start_date,
        end_date=end_date,
    )

    benchmark_bars = repository.index_daily_bars(benchmark_symbol, start_date, end_date)
    if _benchmark_covers_strategy_dates(benchmark_bars, first_strategy_date, last_strategy_date):
        return benchmark_bars

    raise HTTPException(
        status_code=400,
        detail=(
            f"Benchmark {benchmark_symbol} does not cover strategy trading dates "
            f"{first_strategy_date} through {last_strategy_date} after auto-sync."
        ),
    )


def _benchmark_covers_strategy_dates(
    benchmark_bars: pd.DataFrame,
    first_strategy_date: date,
    last_strategy_date: date,
) -> bool:
    if benchmark_bars.empty:
        return False
    benchmark_dates = set(pd.to_datetime(benchmark_bars["trade_date"]).dt.date)
    return first_strategy_date in benchmark_dates and last_strategy_date in benchmark_dates


@router.get("", response_model=list[BacktestRunOut])
def list_runs(limit: int = 50, session: Session = Depends(get_session)):
    runs = list_backtest_runs(session, limit=limit)
    return [
        BacktestRunOut(
            id=run.id,
            strategy_name=run.strategy_name,
            start_date=run.start_date,
            end_date=run.end_date,
            initial_cash=run.initial_cash,
            final_equity=run.final_equity,
            total_return=run.total_return,
            annual_return=run.annual_return,
            max_drawdown=run.max_drawdown,
            sharpe=run.sharpe,
        )
        for run in runs
    ]


@router.get("/{run_id}", response_model=BacktestResponse)
def get_run(run_id: int, session: Session = Depends(get_session)):
    run = get_backtest_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found.")

    equity = get_backtest_equity(session, run_id)
    trades = get_backtest_trades(session, run_id)
    selected_symbols: list[str] = []
    seen: set[str] = set()
    for trade in trades:
        if trade.symbol not in seen:
            seen.add(trade.symbol)
            selected_symbols.append(trade.symbol)
    return BacktestResponse(
        run_id=run.id,
        symbol_source="manual",
        selected_symbols=selected_symbols,
        universe_metadata={},
        metrics=BacktestMetrics(
            total_return=run.total_return,
            annual_return=run.annual_return,
            max_drawdown=run.max_drawdown,
            sharpe=run.sharpe,
            final_equity=run.final_equity,
        ),
        equity_curve=[
            EquityPoint(
                trade_date=point.trade_date,
                equity=point.equity,
                cash=point.cash,
                position_value=point.position_value,
                drawdown=point.drawdown,
            )
            for point in equity
        ],
        benchmark_curve=[],
        trades=[
            TradeOut(
                trade_date=trade.trade_date,
                symbol=trade.symbol,
                side=trade.side,
                price=trade.price,
                quantity=trade.quantity,
                amount=trade.amount,
            )
            for trade in trades
        ],
    )
