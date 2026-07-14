from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from math import isfinite

import pandas as pd
from sqlalchemy.orm import Session

from app.ai_advisory.providers import TextStreamingProvider
from app.data.repository import MarketDataRepository
from app.models.entities import AdvisoryRun
from app.portfolio.trade_plan import build_trade_plan
from app.risk.rules import RiskConfig, RiskEngine
from app.schemas.advisory import AdvisoryRequest, AdvisoryResponse, AdvisoryTradeOut
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

    try:
        strategy = get_strategy(payload.strategy_name)
        raw_target_weights = strategy.generate_target_weights(
            StrategyContext(
                current_date=payload.as_of_date,
                cash=float(payload.cash),
                positions=positions.copy(),
                params=dict(payload.strategy_parameters),
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
            {"raw": normalized_weights, "accepted": risk.accepted, "rejected": risk.rejected},
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
        warnings=warnings,
        remote_llm_enabled=payload.allow_remote_llm and remote_llm_available,
        llm_summary=None,
    )


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
            "symbols": symbols,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
