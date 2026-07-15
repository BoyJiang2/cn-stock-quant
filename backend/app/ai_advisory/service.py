from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta
from math import isfinite

import pandas as pd
from sqlalchemy.orm import Session

from app.ai_advisory.providers import TextStreamingProvider
from app.ai_research.market_regime import MarketRegimeAnalyzer
from app.data.repository import MarketDataRepository
from app.factors import FACTOR_DIRECTIONS, FactorLab, FactorSpec
from app.models.entities import AdvisoryRun, BacktestRun, BacktestWalkForwardValidation
from app.portfolio.trade_plan import build_trade_plan
from app.risk.rules import RiskConfig, RiskEngine
from app.schemas.advisory import (
    AdvisoryRequest,
    AdvisoryResponse,
    AdvisoryTradeOut,
    FactorEvidenceOut,
    FactorSymbolEvidenceOut,
    FactorValueOut,
    MarketEvidenceOut,
    NewsEvidenceItemOut,
    NewsEvidenceOut,
    ValidationEvidenceOut,
)
from app.strategy.base import StrategyContext
from app.strategy.registry import get_strategy


class AdvisoryInputError(ValueError):
    """Raised when a deterministic advisory snapshot cannot be constructed."""


def create_advisory(
    session: Session,
    payload: AdvisoryRequest,
    *,
    remote_llm_available: bool,
) -> AdvisoryResponse:
    repository = MarketDataRepository(session)
    validation_evidence = _validation_evidence(session, payload)
    positions = _resolve_positions(repository, payload)
    try:
        symbols = repository.resolve_symbols(
            [*payload.symbols, *positions]
        )
    except ValueError as exc:
        raise AdvisoryInputError(str(exc)) from exc

    start_date = payload.as_of_date - timedelta(days=payload.lookback_calendar_days)
    bars = repository.daily_bars(symbols, start_date, payload.as_of_date)
    if bars.empty:
        raise AdvisoryInputError(
            "No local daily bars for the selected symbols and as-of date. Sync data first."
        )
    bars = bars.copy()
    bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.date
    latest = bars.sort_values("trade_date").groupby("symbol", as_index=False).tail(1)
    latest_dates = {row.symbol: row.trade_date for row in latest.itertuples()}
    stale = [symbol for symbol in symbols if latest_dates.get(symbol) != payload.as_of_date]
    if stale:
        raise AdvisoryInputError(
            "No local close on the requested as-of date for: " + ", ".join(sorted(stale))
        )
    latest_prices = {row.symbol: float(row.close) for row in latest.itertuples()}
    total_equity = round(
        float(payload.cash) + sum(quantity * latest_prices[symbol] for symbol, quantity in positions.items()),
        2,
    )
    if total_equity <= 0:
        raise AdvisoryInputError("cash plus marked-to-market positions must be positive")

    market_evidence, benchmark_history = _market_evidence(
        repository,
        payload.as_of_date,
        payload.lookback_calendar_days,
    )
    news_evidence = _news_evidence(repository, symbols, payload.as_of_date)

    try:
        strategy = get_strategy(payload.strategy_name)
        raw_target_weights = strategy.generate_target_weights(
            StrategyContext(
                current_date=payload.as_of_date,
                cash=float(payload.cash),
                positions=positions.copy(),
                params=dict(payload.strategy_parameters),
                benchmark_history=benchmark_history,
            ),
            bars,
        )
    except ValueError as exc:
        raise AdvisoryInputError(str(exc)) from exc
    normalized_weights = _normalize_weights(raw_target_weights, symbols, positions)
    risk = RiskEngine().evaluate(
        normalized_weights,
        RiskConfig(
            max_symbol_weight=payload.max_symbol_weight,
            max_total_weight=payload.max_total_weight,
            max_positions=payload.max_positions,
        ),
    )
    plan = build_trade_plan(
        positions=positions,
        target_weights=risk.accepted,
        latest_prices=latest_prices,
        total_equity=total_equity,
    )
    factor_symbols = list(dict.fromkeys([*positions, *risk.accepted])) or symbols[:30]
    factor_evidence = _factor_evidence(bars, payload.as_of_date, factor_symbols)
    request_json = _request_json(payload, symbols, positions)
    execution_dates = repository.trading_dates(
        payload.as_of_date + timedelta(days=1),
        payload.as_of_date + timedelta(days=14),
    )
    earliest_execution_date = execution_dates[0] if execution_dates else None
    status = "draft"
    warnings = [
        "This is a research and paper-trading draft, not an order or personalized investment instruction.",
        "Prices are local research closes only; do not use them as broker order prices.",
        "Signals are generated after the close and require manual review before the next trading session.",
        "Trade-plan estimates do not reserve cash for commission, taxes, T+1, limit rules, or intraday liquidity.",
    ]
    if validation_evidence is not None:
        warnings.append(
            f"Eligible rolling OOS validation #{validation_evidence.validation_id} is attached as historical evidence, not a future-return guarantee."
        )
    if earliest_execution_date is None:
        warnings.append("No next trading date is available in the local calendar.")
    if payload.allow_remote_llm and not remote_llm_available:
        status = "llm_disabled"
        warnings.append(
            "Remote LLM was requested but is not enabled and configured; the deterministic draft was retained."
        )

    record = AdvisoryRun(
        as_of_date=payload.as_of_date,
        strategy_name=strategy.name,
        status=status,
        total_equity=total_equity,
        request_hash=hashlib.sha256(request_json.encode("utf-8")).hexdigest(),
        request_json=request_json,
        risk_json=json.dumps(
            {
                "raw": normalized_weights,
                "accepted": risk.accepted,
                "rejected": risk.rejected,
                "evidence": {
                    "market": market_evidence.model_dump(mode="json"),
                    "news": news_evidence.model_dump(mode="json"),
                    "factors": factor_evidence.model_dump(mode="json"),
                    "validation": validation_evidence.model_dump(mode="json") if validation_evidence else None,
                },
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        trade_plan_json=json.dumps([item.__dict__ for item in plan], ensure_ascii=True),
        llm_provider="openai_responses" if payload.allow_remote_llm and remote_llm_available else None,
        llm_model=None,
        remote_llm_requested=payload.allow_remote_llm,
        llm_summary="",
        created_at=datetime.utcnow(),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return AdvisoryResponse(
        id=record.id,
        status=status,
        as_of_date=payload.as_of_date,
        earliest_execution_date=earliest_execution_date,
        price_basis="research_close_only",
        strategy_name=strategy.name,
        total_equity=total_equity,
        raw_target_weights=normalized_weights,
        accepted_target_weights=risk.accepted,
        rejected_target_weights=risk.rejected,
        trade_plan=[AdvisoryTradeOut(**item.__dict__) for item in plan],
        market_evidence=market_evidence,
        news_evidence=news_evidence,
        factor_evidence=factor_evidence,
        validation_evidence=validation_evidence,
        warnings=warnings,
        remote_llm_enabled=payload.allow_remote_llm and remote_llm_available,
        llm_summary=None,
    )


def _validation_evidence(session: Session, payload: AdvisoryRequest) -> ValidationEvidenceOut | None:
    if payload.validation_id is None:
        return None
    validation = session.get(BacktestWalkForwardValidation, payload.validation_id)
    if validation is None:
        raise AdvisoryInputError("Selected rolling OOS validation was not found.")
    if validation.status != "completed" or validation.eligibility_status != "eligible":
        raise AdvisoryInputError("Selected rolling OOS validation is not eligible evidence.")
    run = session.get(BacktestRun, validation.backtest_run_id)
    if run is None:
        raise AdvisoryInputError("Selected rolling OOS validation has no source backtest.")
    spec = json.loads(validation.spec_json)
    if spec.get("strategy_name") != payload.strategy_name:
        raise AdvisoryInputError("Selected rolling OOS validation uses a different strategy.")
    if _canonical_json(spec.get("strategy_parameters", {})) != _canonical_json(payload.strategy_parameters):
        raise AdvisoryInputError("Selected rolling OOS validation uses different strategy parameters.")
    source_as_of_date = _validation_as_of_date(spec)
    if source_as_of_date != payload.as_of_date:
        raise AdvisoryInputError("Selected rolling OOS validation does not match the advisory as-of date.")
    result = json.loads(validation.result_json)
    return ValidationEvidenceOut(
        validation_id=validation.id,
        backtest_run_id=validation.backtest_run_id,
        source_as_of_date=source_as_of_date,
        fingerprint=validation.fingerprint,
        aggregate=result.get("aggregate", {}),
        cost_stress_aggregate=result.get("cost_stress_aggregate", {}),
        quality=json.loads(validation.quality_json),
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _validation_as_of_date(spec: dict) -> date:
    windows = spec.get("windows")
    if not isinstance(windows, list) or not windows:
        raise AdvisoryInputError("Selected rolling OOS validation has no completed OOS windows.")
    final_window = windows[-1]
    try:
        return date.fromisoformat(str(final_window["oos_end_date"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise AdvisoryInputError("Selected rolling OOS validation has an invalid OOS cutoff date.") from exc


def stream_advisory_summary(
    session: Session,
    advisory_id: int,
    provider: TextStreamingProvider,
    *,
    provider_name: str,
    model_name: str,
):
    record = session.get(AdvisoryRun, advisory_id)
    if record is None:
        raise AdvisoryInputError("Advisory draft was not found.")
    if not record.remote_llm_requested:
        raise AdvisoryInputError("Remote LLM was not approved when this draft was created.")

    text_parts: list[str] = []
    try:
        for delta in provider.stream_text(
            system_prompt=_advisory_system_prompt(),
            user_prompt=_advisory_user_prompt(record),
        ):
            text_parts.append(delta)
            yield delta
    except Exception:
        record.status = "failed"
        session.commit()
        raise
    else:
        record.status = "llm_complete"
        record.llm_provider = provider_name
        record.llm_model = model_name
        record.llm_summary = "".join(text_parts)
        session.commit()


def _advisory_system_prompt() -> str:
    return (
        "You are an A-share research assistant. Use only the supplied, time-stamped "
        "snapshot. Explain the existing risk-gated trade-plan draft in Chinese. "
        "Do not invent data, prices, symbols, target weights, orders, or broker actions. "
        "State uncertainty, data limitations, contrary evidence, and that human review "
        "is required before the next trading session. This is research, not investment advice."
    )


def _advisory_user_prompt(record: AdvisoryRun) -> str:
    request = json.loads(record.request_json)
    risk = json.loads(record.risk_json)
    plan = json.loads(record.trade_plan_json)
    evidence = risk.get("evidence", {})
    accepted = {
        symbol: weight
        for symbol, weight in risk.get("accepted", {}).items()
        if float(weight) > 0
    }
    return json.dumps(
        {
            "as_of_date": record.as_of_date.isoformat(),
            "strategy_name": record.strategy_name,
            "total_equity_band": _amount_band(record.total_equity),
            "portfolio": {
                "position_count": len(request.get("positions", {})),
                "selected_symbol_count": len(request.get("symbols", [])),
            },
            "risk_decision": {
                "accepted_positive_weights": dict(
                    sorted(accepted.items(), key=lambda item: item[1], reverse=True)[:20]
                ),
                "rejected": dict(list(risk.get("rejected", {}).items())[:20]),
            },
            "market_evidence": evidence.get("market", {}),
            "news_evidence": _compact_news_evidence(evidence.get("news", {})),
            "factor_evidence": _compact_factor_evidence(evidence.get("factors", {})),
            "research_close_trade_plan": sorted(
                plan,
                key=lambda item: float(item.get("estimated_amount", 0.0)),
                reverse=True,
            )[:20],
            "truncated": len(plan) > 20 or len(accepted) > 20,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _market_evidence(
    repository: MarketDataRepository,
    as_of_date: date,
    lookback_calendar_days: int,
) -> tuple[MarketEvidenceOut, pd.DataFrame | None]:
    start_date = as_of_date - timedelta(days=max(120, lookback_calendar_days))
    bars = repository.index_daily_bars("000300", start_date, as_of_date)
    if bars.empty:
        return (
            MarketEvidenceOut(
                available=False,
                as_of_date=as_of_date,
                warning="No local CSI 300 bars are available for market-regime evidence.",
            ),
            None,
        )
    bars = bars.copy()
    bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.date
    data_end_date = max(bars["trade_date"])
    if data_end_date != as_of_date:
        return (
            MarketEvidenceOut(
                available=False,
                as_of_date=as_of_date,
                data_end_date=data_end_date,
                warning="CSI 300 local bars do not cover the advisory as-of date.",
            ),
            bars,
        )
    result = MarketRegimeAnalyzer().analyze(bars)
    return (
        MarketEvidenceOut(
            available=True,
            as_of_date=as_of_date,
            data_end_date=data_end_date,
            regime=result.regime,
            confidence=result.confidence,
            trend_score=result.trend_score,
            breadth_score=result.breadth_score,
            volatility_score=result.volatility_score,
            drawdown=result.drawdown,
            reasons=result.reasons[:6],
        ),
        bars,
    )


def _news_evidence(
    repository: MarketDataRepository,
    symbols: list[str],
    as_of_date: date,
) -> NewsEvidenceOut:
    as_of_at = datetime.combine(as_of_date, time.max)
    window_start = datetime.combine(as_of_date - timedelta(days=7), time.min)
    frames = [
        repository.news_items(symbol=symbol, known_end_at=as_of_at, limit=100)
        for symbol in symbols
    ]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return NewsEvidenceOut(window_start=window_start, as_of_at=as_of_at)

    news = pd.concat(frames, ignore_index=True)
    news["published_at"] = pd.to_datetime(news["published_at"], errors="coerce")
    news["fetched_at"] = pd.to_datetime(news["fetched_at"], errors="coerce")
    news["known_at"] = news[["published_at", "fetched_at"]].max(axis=1)
    news = news[(news["known_at"] >= window_start) & (news["known_at"] <= as_of_at)]
    if news.empty:
        return NewsEvidenceOut(window_start=window_start, as_of_at=as_of_at)

    priority = {"severe_company_risk": 0, "company_risk": 1}
    news["priority"] = news["event_type"].map(priority).fillna(2)
    news = news.sort_values(["priority", "known_at"], ascending=[True, False])
    items = [
        NewsEvidenceItemOut(
            symbol=row.symbol,
            source=row.source,
            title=row.title,
            event_type=row.event_type,
            sentiment_label=row.sentiment_label,
            published_at=row.published_at.to_pydatetime(),
            known_at=row.known_at.to_pydatetime(),
        )
        for row in news.head(20).itertuples()
    ]
    return NewsEvidenceOut(
        window_start=window_start,
        as_of_at=as_of_at,
        total_items=int(len(news)),
        severe_company_risk_count=int((news["event_type"] == "severe_company_risk").sum()),
        company_risk_count=int((news["event_type"] == "company_risk").sum()),
        items=items,
    )


def _compact_news_evidence(evidence: dict) -> dict:
    return {
        "availability_mode": evidence.get("availability_mode", "observed"),
        "window_start": evidence.get("window_start"),
        "as_of_at": evidence.get("as_of_at"),
        "total_items": evidence.get("total_items", 0),
        "severe_company_risk_count": evidence.get("severe_company_risk_count", 0),
        "company_risk_count": evidence.get("company_risk_count", 0),
        "items": list(evidence.get("items", []))[:10],
        "truncated": len(evidence.get("items", [])) > 10,
    }


_ADVISORY_FACTOR_NAMES = (
    "momentum_20d",
    "volatility_20d",
    "max_drawdown_20d",
    "amount_ratio_5d_20d",
    "amihud_illiquidity_20d",
)


def _factor_evidence(
    bars: pd.DataFrame,
    as_of_date: date,
    symbols: list[str],
) -> FactorEvidenceOut:
    focus_symbols = list(dict.fromkeys(symbols))[:30]
    evidence_bars = bars[bars["symbol"].isin(focus_symbols)].copy()
    if evidence_bars.empty:
        return FactorEvidenceOut(
            as_of_date=as_of_date,
            warnings=["No local bars are available for the advisory factor snapshot."],
        )

    evidence_bars["trade_date"] = pd.to_datetime(evidence_bars["trade_date"]).dt.date
    data_start_date = min(evidence_bars["trade_date"])
    data_end_date = max(evidence_bars["trade_date"])
    warnings = [
        "Factor values are observed trailing price/volume transforms only; no forward returns, IC, or historical effectiveness claim is included."
    ]
    if len(symbols) > len(focus_symbols):
        warnings.append("Factor evidence is limited to the first 30 target or held symbols.")
    try:
        panel = FactorLab().compute(
            evidence_bars,
            [FactorSpec(name) for name in _ADVISORY_FACTOR_NAMES],
        )
        as_of_panel = panel.xs(as_of_date, level="trade_date")
    except (KeyError, TypeError, ValueError) as exc:
        return FactorEvidenceOut(
            as_of_date=as_of_date,
            data_start_date=data_start_date,
            data_end_date=data_end_date,
            factor_names=list(_ADVISORY_FACTOR_NAMES),
            warnings=[*warnings, f"Factor snapshot is unavailable: {exc}"],
        )

    symbol_evidence: list[FactorSymbolEvidenceOut] = []
    for symbol in focus_symbols:
        if symbol not in as_of_panel.index:
            symbol_evidence.append(
                FactorSymbolEvidenceOut(
                    symbol=symbol,
                    available=False,
                    warning="No factor row is available on the advisory as-of date.",
                )
            )
            continue
        row = as_of_panel.loc[symbol]
        values = [
            FactorValueOut(
                name=name,
                direction=FACTOR_DIRECTIONS[name],
                raw_value=_finite_factor_value(row[name]),
            )
            for name in _ADVISORY_FACTOR_NAMES
        ]
        available = any(value.raw_value is not None for value in values)
        symbol_evidence.append(
            FactorSymbolEvidenceOut(
                symbol=symbol,
                available=available,
                values=values,
                warning=None if available else "Insufficient local warm-up history for the trailing factor set.",
            )
        )
    return FactorEvidenceOut(
        as_of_date=as_of_date,
        data_start_date=data_start_date,
        data_end_date=data_end_date,
        factor_names=list(_ADVISORY_FACTOR_NAMES),
        symbols=symbol_evidence,
        warnings=warnings,
    )


def _finite_factor_value(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    numeric = float(value)
    return round(numeric, 8) if isfinite(numeric) else None


def _compact_factor_evidence(evidence: dict) -> dict:
    return {
        "availability_mode": evidence.get("availability_mode", "observed_trailing"),
        "as_of_date": evidence.get("as_of_date"),
        "data_start_date": evidence.get("data_start_date"),
        "data_end_date": evidence.get("data_end_date"),
        "factor_names": evidence.get("factor_names", []),
        "symbols": list(evidence.get("symbols", []))[:10],
        "warnings": evidence.get("warnings", []),
        "truncated": len(evidence.get("symbols", [])) > 10,
    }


def _amount_band(value: float) -> str:
    if value < 100_000:
        return "under_100k_cny"
    if value < 500_000:
        return "100k_to_500k_cny"
    if value < 1_000_000:
        return "500k_to_1m_cny"
    if value < 5_000_000:
        return "1m_to_5m_cny"
    return "over_5m_cny"


def _resolve_positions(
    repository: MarketDataRepository,
    payload: AdvisoryRequest,
) -> dict[str, int]:
    positions: dict[str, int] = {}
    for item in payload.positions:
        try:
            symbol = repository.resolve_symbol(item.symbol)
        except ValueError as exc:
            raise AdvisoryInputError(str(exc)) from exc
        positions[symbol] = positions.get(symbol, 0) + item.quantity
    return positions


def _normalize_weights(
    raw_weights: dict[str, float],
    symbols: list[str],
    positions: dict[str, int],
) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for symbol in [*symbols, *positions]:
        value = raw_weights.get(symbol, 0.0)
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise AdvisoryInputError(f"Strategy returned a non-numeric weight for {symbol}") from exc
        if not isfinite(numeric):
            raise AdvisoryInputError(f"Strategy returned a non-finite weight for {symbol}")
        normalized[symbol] = numeric
    return normalized


def _request_json(
    payload: AdvisoryRequest,
    symbols: list[str],
    positions: dict[str, int],
) -> str:
    return json.dumps(
        {
            "as_of_date": payload.as_of_date.isoformat(),
            "cash": payload.cash,
            "lookback_calendar_days": payload.lookback_calendar_days,
            "max_positions": payload.max_positions,
            "max_symbol_weight": payload.max_symbol_weight,
            "max_total_weight": payload.max_total_weight,
            "positions": positions,
            "strategy_name": payload.strategy_name,
            "strategy_parameters": payload.strategy_parameters,
            "validation_id": payload.validation_id,
            "symbols": symbols,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
