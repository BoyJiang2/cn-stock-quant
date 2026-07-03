from datetime import timedelta
from math import isfinite

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.ai_research import build_composite_factor
from app.ai_research.market_regime import MarketRegimeAnalyzer, build_llm_market_context
from app.core.database import get_session
from app.data.repository import MarketDataRepository
from app.data.symbols import (
    INDEX_SYMBOL_WHITELIST,
    normalize_a_share_symbol,
)
from app.factors import FactorLab, FactorSpec, evaluate, forward_returns
from app.schemas.ai_research import (
    AIFactorCandidateRequest,
    AIFactorCandidateResponse,
    MarketRegimeRequest,
    MarketRegimeResponse,
)
from app.schemas.factors import FactorSummaryOut

router = APIRouter()


def _finite(value) -> float | None:
    numeric = float(value)
    return numeric if isfinite(numeric) else None


@router.get("/capabilities")
def capabilities() -> dict:
    return {
        "mode": "controlled_factor_candidate",
        "accepted_input": "JSON factor weights using registered factors",
        "arbitrary_code_execution": False,
        "validation": ["T+1 labels", "IC", "RankIC", "group returns", "turnover"],
        "compatible_projects": ["Microsoft Qlib", "Microsoft RD-Agent", "FinGPT", "vnpy.alpha"],
        "market_regime": True,
    }


@router.post("/market-regime", response_model=MarketRegimeResponse)
def market_regime(
    payload: MarketRegimeRequest,
    session: Session = Depends(get_session),
) -> MarketRegimeResponse:
    try:
        benchmark_symbol = normalize_a_share_symbol(payload.benchmark_symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if benchmark_symbol not in INDEX_SYMBOL_WHITELIST:
        raise HTTPException(
            status_code=400,
            detail=f"benchmark must be one of: {', '.join(sorted(INDEX_SYMBOL_WHITELIST))}",
        )
    repository = MarketDataRepository(session)
    start_date = payload.as_of_date - timedelta(days=payload.lookback_calendar_days)
    benchmark = repository.index_daily_bars(
        benchmark_symbol,
        start_date,
        payload.as_of_date,
    )
    if benchmark.empty:
        raise HTTPException(
            status_code=400,
            detail=f"No local index bars for {benchmark_symbol}; sync index data first.",
        )
    market_history = None
    breadth_symbol_count = 0
    if payload.include_market_breadth:
        symbols = repository.active_symbols(limit=payload.breadth_max_symbols)
        market_history = repository.daily_bars(symbols, start_date, payload.as_of_date)
        breadth_symbol_count = (
            int(market_history["symbol"].nunique()) if not market_history.empty else 0
        )
    result = MarketRegimeAnalyzer().analyze(benchmark, market_history)
    context = build_llm_market_context(
        result,
        symbol=benchmark_symbol,
        model_family="deepseek",
        extra_context={
            "as_of_date": payload.as_of_date.isoformat(),
            "breadth_symbol_count": breadth_symbol_count,
            "direct_trading_allowed": False,
        },
    )
    return MarketRegimeResponse(
        benchmark_symbol=benchmark_symbol,
        as_of_date=payload.as_of_date,
        regime=result.regime,
        confidence=result.confidence,
        trend_score=result.trend_score,
        breadth_score=result.breadth_score,
        volatility_score=result.volatility_score,
        drawdown=result.drawdown,
        reasons=result.reasons,
        breadth_symbol_count=breadth_symbol_count,
        llm_context=context,
        can_trade_directly=False,
    )


@router.post("/factor-candidates/evaluate", response_model=AIFactorCandidateResponse)
def evaluate_candidate(
    payload: AIFactorCandidateRequest,
    session: Session = Depends(get_session),
) -> AIFactorCandidateResponse:
    if payload.start_date > payload.end_date:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")
    repository = MarketDataRepository(session)
    if payload.symbol_source == "research_pool":
        symbols = repository.covered_research_symbols(
            payload.start_date, payload.end_date, limit=payload.pool_max_symbols
        )
    else:
        try:
            symbols = repository.resolve_symbols(payload.symbols)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not symbols:
        raise HTTPException(status_code=400, detail="No eligible symbols for the candidate.")

    warmup_start = payload.start_date - timedelta(days=200)
    label_end = payload.end_date + timedelta(days=payload.horizon * 3 + 10)
    bars = repository.daily_bars(symbols, warmup_start, label_end)
    if bars.empty:
        raise HTTPException(status_code=400, detail="No local daily bars for the candidate.")
    try:
        panel = FactorLab().compute(
            bars, [FactorSpec(name) for name in payload.components]
        )
        composite = build_composite_factor(panel, payload.components)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    labels = forward_returns(bars, horizons=(payload.horizon,))
    signal_dates = composite.index.get_level_values("trade_date")
    composite = composite[
        (signal_dates >= payload.start_date) & (signal_dates <= payload.end_date)
    ]
    label_dates = labels.index.get_level_values("trade_date")
    labels = labels[
        (label_dates >= payload.start_date) & (label_dates <= payload.end_date)
    ]
    report = evaluate(composite, labels[f"fwd_{payload.horizon}d"], n_groups=payload.n_groups)
    warnings = [
        "This candidate was generated from a fixed factor vocabulary; it is not approved for trading.",
        "Walk-forward and cost-stress validation are required before promotion.",
    ]
    if len(symbols) < 30:
        warnings.append(f"Only {len(symbols)} symbols are available.")
    if report["n_dates"] < 20:
        warnings.append(f"Only {report['n_dates']} valid evaluation dates are available.")

    return AIFactorCandidateResponse(
        candidate_name=payload.candidate_name,
        components=payload.components,
        selected_symbols=symbols,
        warnings=warnings,
        summary=FactorSummaryOut(
            name=payload.candidate_name,
            direction=1,
            ic_mean=_finite(report["ic_mean"]),
            ic_ir=_finite(report["ic_ir"]),
            rankic_mean=_finite(report["rankic_mean"]),
            rankic_ir=_finite(report["rankic_ir"]),
            long_short_return=_finite(report["long_short_return"]),
            long_short_turnover=_finite(report["long_short_turnover"]),
            n_dates=report["n_dates"],
            group_returns={
                group: _finite(value)
                for group, value in report["group_returns"].items()
            },
        ),
    )
