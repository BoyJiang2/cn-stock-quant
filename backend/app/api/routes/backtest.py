import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from statistics import fmean

from fastapi import APIRouter, Depends, HTTPException
import pandas as pd
from sqlalchemy.orm import Session

from app.backtest.engine import BacktestConfig, DailyBacktestEngine
from app.backtest.persistence import (
    get_backtest_equity,
    get_backtest_run,
    get_backtest_run_provenance,
    get_backtest_trades,
    list_backtest_runs,
    list_walk_forward_validations,
    save_backtest_result,
    save_walk_forward_validation,
)
from app.core.database import get_session
from app.data.akshare_provider import AkShareProvider
from app.data.pit_repository import PitRepository
from app.data.repository import MarketDataRepository
from app.data.symbols import INDEX_SYMBOL_WHITELIST, normalize_a_share_symbol
from app.schemas.backtest import (
    BacktestMetrics,
    BacktestRequest,
    BacktestResponse,
    BacktestRunOut,
    BacktestRunProvenanceOut,
    EquityPoint,
    TradeOut,
    WalkForwardValidationOut,
    WalkForwardValidationRequest,
)
from app.strategy.registry import get_strategy
from app.validation import rolling_oos_windows, run_walk_forward

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
            # Initial PIT eligibility must not use bars from the future
            # backtest period. Keep a fixed pre-start warm-up window only.
            warmup_start = as_of_date - timedelta(days=180)
            pit_result = PitRepository(
                session,
                bar_reader=repository,
            ).select_research_symbols_pit(
                as_of_date,
                warmup_start,
                as_of_date,
                index_symbol=payload.pit_index_symbol,
                st_policy=payload.pit_st_policy,
                limit=payload.pool_max_symbols,
            )
            warmup_degraded = False
            if not pit_result.symbols and warmup_start < as_of_date:
                # A run may begin at the first locally available date. In
                # that case, retain PIT safety by using only the as-of day,
                # rather than rejecting the run or peeking into future bars.
                pit_result = PitRepository(
                    session,
                    bar_reader=repository,
                ).select_research_symbols_pit(
                    as_of_date,
                    as_of_date,
                    as_of_date,
                    index_symbol=payload.pit_index_symbol,
                    st_policy=payload.pit_st_policy,
                    limit=payload.pool_max_symbols,
                )
                warmup_degraded = bool(pit_result.symbols)
            symbols = pit_result.symbols
            universe_metadata = {
                "mode": "pit_initial_universe",
                "warmup_start": warmup_start.isoformat(),
                "warmup_degraded": warmup_degraded,
                "warning": "The universe is fixed at the backtest start; intraperiod status changes are not yet rebalanced.",
                **pit_result.meta,
            }
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
    run_id = save_backtest_result(
        session,
        payload,
        result,
        selected_symbols=symbols,
        universe_metadata=universe_metadata,
    )
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


@router.post("/{run_id}/walk-forward-validations", response_model=WalkForwardValidationOut)
def run_walk_forward_validation(
    run_id: int,
    payload: WalkForwardValidationRequest,
    session: Session = Depends(get_session),
):
    run = get_backtest_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found.")
    provenance = get_backtest_run_provenance(session, run_id)
    if provenance is None:
        raise HTTPException(
            status_code=409,
            detail="This legacy backtest has no immutable provenance record.",
        )

    source_spec = json.loads(provenance.spec_json)
    source_request = BacktestRequest.model_validate(source_spec["request"])
    selected_symbols = list(source_spec.get("selected_symbols", []))
    if not selected_symbols:
        raise HTTPException(status_code=409, detail="The provenance record has no selected symbols.")

    repository = MarketDataRepository(session)
    is_pit_source = source_request.symbol_source == "research_pool" and source_request.point_in_time
    if is_pit_source:
        trading_dates = repository.trading_dates(source_request.start_date, source_request.end_date)
    else:
        bars = repository.daily_bars(selected_symbols, source_request.start_date, source_request.end_date)
        if bars.empty:
            raise HTTPException(status_code=409, detail="Local bars for this recorded backtest are no longer available.")
        bars = bars.copy()
        bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.date
        trading_dates = sorted(bars["trade_date"].unique())
    windows = rolling_oos_windows(
        trading_dates,
        warmup_trading_days=payload.warmup_trading_days,
        oos_window_trading_days=payload.oos_window_trading_days,
    )

    strategy = get_strategy(source_request.strategy_name)
    strategy_params = source_request.strategy_parameters()
    base_config = BacktestConfig(
        start_date=source_request.start_date,
        end_date=source_request.end_date,
        initial_cash=source_request.initial_cash,
        commission_rate=source_request.commission_rate,
        stamp_tax_rate=source_request.stamp_tax_rate,
        slippage_rate=source_request.slippage_rate,
        rebalance_interval=source_request.rebalance_interval,
        risk_max_symbol_weight=source_request.risk_max_symbol_weight,
        risk_max_total_weight=source_request.risk_max_total_weight,
        risk_max_positions=source_request.risk_max_positions,
        params=strategy_params,
    )
    window_results: list[dict]
    stressed_results: list[dict]
    benchmark_complete = False
    dynamic_quality_flags: list[str] = []
    window_specs: list[dict] = []
    try:
        if is_pit_source:
            pit_repository = PitRepository(session, bar_reader=repository)
            inputs: list[tuple] = []
            for window in windows:
                warmup_start = window.warmup_start_date or window.start_date
                pit_result = pit_repository.select_research_symbols_pit(
                    window.start_date,
                    warmup_start,
                    window.start_date,
                    index_symbol=source_request.pit_index_symbol,
                    st_policy=source_request.pit_st_policy,
                    limit=source_request.pool_max_symbols,
                )
                universe_snapshot = {**pit_result.meta, "symbols": pit_result.symbols}
                universe_snapshot["symbols_fingerprint"] = _validation_input_fingerprint(
                    pit_result.symbols
                )
                window_spec = {
                    "name": window.name,
                    "warmup_start_date": warmup_start,
                    "oos_start_date": window.start_date,
                    "oos_end_date": window.end_date,
                    "universe": universe_snapshot,
                }
                window_specs.append(window_spec)
                if pit_result.meta.get("pit_degraded", True):
                    dynamic_quality_flags.append(f"{window.name}: point-in-time universe metadata is degraded")
                if not pit_result.symbols:
                    dynamic_quality_flags.append(f"{window.name}: no eligible point-in-time symbols")
                    continue
                window_bars = repository.daily_bars(pit_result.symbols, warmup_start, window.end_date)
                if window_bars.empty:
                    dynamic_quality_flags.append(
                        f"{window.name}: point-in-time selected universe has no local bars"
                    )
                    continue
                window_spec["bars_fingerprint"] = _validation_input_fingerprint(
                    window_bars.to_dict("records")
                )
                window_benchmark = None
                window_benchmark_complete = False
                if source_request.benchmark_symbol:
                    window_benchmark = repository.index_daily_bars(
                        source_request.benchmark_symbol,
                        warmup_start,
                        window.end_date,
                    )
                    benchmark_dates = (
                        set(pd.to_datetime(window_benchmark["trade_date"]).dt.date)
                        if not window_benchmark.empty
                        else set()
                    )
                    oos_dates = {day for day in trading_dates if window.start_date <= day <= window.end_date}
                    window_benchmark_complete = bool(oos_dates) and oos_dates.issubset(benchmark_dates)
                    if not window_benchmark_complete:
                        dynamic_quality_flags.append(f"{window.name}: local benchmark does not cover every OOS trading day")
                window_spec["benchmark_complete"] = window_benchmark_complete
                window_spec["benchmark_fingerprint"] = _validation_input_fingerprint(
                    window_benchmark.to_dict("records") if window_benchmark is not None else []
                )
                news_history = _load_negative_news_history(
                    repository,
                    pit_result.symbols,
                    start_date=warmup_start,
                    end_date=window.end_date,
                    params=strategy_params,
                )
                window_spec["news_fingerprint"] = _validation_input_fingerprint(
                    news_history.to_dict("records") if news_history is not None else []
                )
                inputs.append(
                    (
                        window,
                        universe_snapshot,
                        window_bars,
                        window_benchmark,
                        news_history,
                    )
                )

            window_results = []
            stressed_results = []
            for window, universe_snapshot, window_bars, window_benchmark, news_history in inputs:
                window_config = replace(base_config, news_history=news_history)
                normal = run_walk_forward(
                    strategy,
                    window_bars,
                    window_config,
                    [window],
                    benchmark_bars=window_benchmark,
                )[0]
                stressed = run_walk_forward(
                    strategy,
                    window_bars,
                    replace(
                        window_config,
                        commission_rate=window_config.commission_rate * payload.cost_stress_multiplier,
                        stamp_tax_rate=window_config.stamp_tax_rate * payload.cost_stress_multiplier,
                        slippage_rate=window_config.slippage_rate * payload.cost_stress_multiplier,
                    ),
                    [window],
                    benchmark_bars=window_benchmark,
                )[0]
                normal["universe"] = universe_snapshot
                stressed["universe"] = universe_snapshot
                window_results.append(normal)
                stressed_results.append(stressed)
            benchmark_complete = bool(windows) and not any(
                "local benchmark" in flag for flag in dynamic_quality_flags
            )
        else:
            benchmark_bars = None
            if source_request.benchmark_symbol:
                benchmark_bars = repository.index_daily_bars(
                    source_request.benchmark_symbol,
                    source_request.start_date,
                    source_request.end_date,
                )
                benchmark_dates = (
                    set(pd.to_datetime(benchmark_bars["trade_date"]).dt.date)
                    if not benchmark_bars.empty
                    else set()
                )
                benchmark_complete = all(
                    window.start_date in benchmark_dates and window.end_date in benchmark_dates
                    for window in windows
                )
            news_history = _load_negative_news_history(
                repository,
                selected_symbols,
                start_date=source_request.start_date,
                end_date=source_request.end_date,
                params=strategy_params,
            )
            fixed_config = replace(base_config, news_history=news_history)
            window_results = run_walk_forward(
                strategy,
                bars,
                fixed_config,
                windows,
                benchmark_bars=benchmark_bars,
            )
            stressed_results = run_walk_forward(
                strategy,
                bars,
                replace(
                    fixed_config,
                    commission_rate=fixed_config.commission_rate * payload.cost_stress_multiplier,
                    stamp_tax_rate=fixed_config.stamp_tax_rate * payload.cost_stress_multiplier,
                    slippage_rate=fixed_config.slippage_rate * payload.cost_stress_multiplier,
                ),
                windows,
                benchmark_bars=benchmark_bars,
            )
            window_specs = [
                {
                    "name": window.name,
                    "warmup_start_date": window.warmup_start_date,
                    "oos_start_date": window.start_date,
                    "oos_end_date": window.end_date,
                }
                for window in windows
            ]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    total_oos_trading_days = sum(
        1 for day in trading_dates for window in windows if window.start_date <= day <= window.end_date
    )
    quality_flags: list[str] = list(dynamic_quality_flags)
    if not is_pit_source:
        quality_flags.append("source backtest was not built from a point-in-time research universe")
    if is_pit_source and not trading_dates:
        quality_flags.append("local trading calendar has no dates for the recorded backtest range")
    if not source_request.benchmark_symbol or not benchmark_complete:
        quality_flags.append("local benchmark does not cover every OOS window boundary")
    if len(windows) < payload.minimum_windows:
        quality_flags.append("fewer than the requested number of independent OOS windows were available")
    if total_oos_trading_days < 126:
        quality_flags.append("fewer than 126 OOS trading days were available")

    if any("point-in-time" in flag or "trading calendar" in flag for flag in quality_flags):
        eligibility_status = "not_eligible_pit_degraded"
    elif any("benchmark" in flag for flag in quality_flags):
        eligibility_status = "not_eligible_benchmark_missing"
    elif any("OOS" in flag for flag in quality_flags):
        eligibility_status = "not_eligible_insufficient_oos_history"
    else:
        eligibility_status = "eligible"

    spec = {
        "source_backtest_run_id": run_id,
        "source_provenance_fingerprint": provenance.fingerprint,
        "strategy_name": source_request.strategy_name,
        "strategy_parameters": strategy_params,
        "selected_symbols": selected_symbols,
        "benchmark_symbol": source_request.benchmark_symbol,
        "window_protocol": payload.model_dump(mode="json"),
        "windows": window_specs,
    }
    result = {
        "window_results": window_results,
        "cost_stress_multiplier": payload.cost_stress_multiplier,
        "cost_stress_window_results": stressed_results,
        "aggregate": _aggregate_validation_metrics(window_results),
        "cost_stress_aggregate": _aggregate_validation_metrics(stressed_results),
    }
    quality = {
        "eligibility_status": eligibility_status,
        "quality_flags": quality_flags,
        "window_count": len(windows),
        "oos_trading_days": total_oos_trading_days,
        "benchmark_complete": benchmark_complete,
        "news_availability": strategy_params.get("news_availability", "observed"),
    }
    validation = save_walk_forward_validation(
        session,
        backtest_run_id=run_id,
        eligibility_status=eligibility_status,
        spec=spec,
        result=result,
        quality=quality,
        source_provenance_fingerprint=provenance.fingerprint,
    )
    return _walk_forward_validation_out(validation)


@router.get("/{run_id}/walk-forward-validations", response_model=list[WalkForwardValidationOut])
def list_run_walk_forward_validations(run_id: int, session: Session = Depends(get_session)):
    if get_backtest_run(session, run_id) is None:
        raise HTTPException(status_code=404, detail="Backtest run not found.")
    return [
        _walk_forward_validation_out(validation)
        for validation in list_walk_forward_validations(session, run_id)
    ]


@router.get("/{run_id}/provenance", response_model=BacktestRunProvenanceOut)
def get_run_provenance(run_id: int, session: Session = Depends(get_session)):
    run = get_backtest_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found.")
    provenance = get_backtest_run_provenance(session, run_id)
    if provenance is None:
        return BacktestRunProvenanceOut(
            run_id=run_id,
            status="not_recorded",
            warning="This legacy backtest has no immutable provenance record and cannot be used as validation evidence.",
        )
    return BacktestRunProvenanceOut(
        run_id=run_id,
        status="recorded_unvalidated",
        fingerprint=provenance.fingerprint,
        spec=json.loads(provenance.spec_json),
        universe=json.loads(provenance.universe_json),
        result=json.loads(provenance.result_json),
        warning="This run is reproducibly recorded but is not walk-forward out-of-sample validation evidence.",
    )


def _aggregate_validation_metrics(results: list[dict]) -> dict[str, float]:
    metric_names = (
        "total_return",
        "annual_return",
        "max_drawdown",
        "sharpe",
        "benchmark_return",
        "excess_return",
    )
    return {
        name: round(fmean(float(item["metrics"].get(name, 0.0)) for item in results), 6)
        for name in metric_names
    } if results else {}


def _validation_input_fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _walk_forward_validation_out(validation) -> WalkForwardValidationOut:
    return WalkForwardValidationOut(
        id=validation.id,
        backtest_run_id=validation.backtest_run_id,
        status="completed",
        eligibility_status=validation.eligibility_status,
        fingerprint=validation.fingerprint,
        spec=json.loads(validation.spec_json),
        result=json.loads(validation.result_json),
        quality=json.loads(validation.quality_json),
    )


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
