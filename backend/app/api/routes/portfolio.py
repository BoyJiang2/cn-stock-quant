import json
from math import isfinite

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.data.repository import MarketDataRepository
from app.models.entities import AdvisoryRun, PaperPortfolio, PaperPortfolioPosition, PaperPortfolioValuation, Stock
from app.schemas.portfolio import (
    PaperPortfolioAdvisoryReviewOut,
    PaperPortfolioAdvisoryReviewRowOut,
    PaperPortfolioPositionOut,
    PaperPortfolioDiagnosticsOut,
    PaperPortfolioSnapshotIn,
    PaperPortfolioStateOut,
    PaperPortfolioValuationOut,
)

router = APIRouter()
_DEFAULT_PORTFOLIO_NAME = "default"


@router.get("/current", response_model=PaperPortfolioStateOut)
def current_portfolio(session: Session = Depends(get_session)) -> PaperPortfolioStateOut:
    return _state_out(session, _default_portfolio(session))


@router.get("/history", response_model=list[PaperPortfolioValuationOut])
def portfolio_history(
    limit: int = Query(default=180, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> list[PaperPortfolioValuationOut]:
    portfolio = _default_portfolio(session)
    rows = list(
        session.scalars(
            select(PaperPortfolioValuation)
            .where(PaperPortfolioValuation.portfolio_id == portfolio.id)
            .order_by(PaperPortfolioValuation.as_of_date.desc())
            .limit(limit)
        )
    )
    return [
        PaperPortfolioValuationOut(
            as_of_date=row.as_of_date,
            cash=row.cash,
            position_value=row.position_value,
            equity=row.equity,
        )
        for row in reversed(rows)
    ]


@router.get("/diagnostics", response_model=PaperPortfolioDiagnosticsOut)
def portfolio_diagnostics(session: Session = Depends(get_session)) -> PaperPortfolioDiagnosticsOut:
    portfolio = _default_portfolio(session)
    state = _state_out(session, portfolio)
    equity = state.equity
    weights = sorted(
        [item.market_value / equity for item in state.positions] if equity > 0 else [],
        reverse=True,
    )
    valuations = list(
        session.scalars(
            select(PaperPortfolioValuation)
            .where(PaperPortfolioValuation.portfolio_id == portfolio.id)
            .order_by(PaperPortfolioValuation.as_of_date)
        )
    )
    peak = 0.0
    max_drawdown = 0.0
    current_drawdown = 0.0
    for valuation in valuations:
        peak = max(peak, valuation.equity)
        drawdown = valuation.equity / peak - 1 if peak > 0 else 0.0
        max_drawdown = min(max_drawdown, drawdown)
        current_drawdown = drawdown
    cash_weight = state.cash / equity if equity > 0 else 0.0
    gross_exposure = state.position_value / equity if equity > 0 else 0.0
    largest = weights[0] if weights else 0.0
    top_three = sum(weights[:3])
    hhi = sum(weight * weight for weight in weights)
    warnings: list[str] = []
    if equity <= 0:
        warnings.append("No positive portfolio equity is available for risk diagnostics.")
    if largest > 0.3:
        warnings.append("Largest holding exceeds 30% of portfolio equity.")
    if top_three > 0.65:
        warnings.append("Top three holdings exceed 65% of portfolio equity.")
    if cash_weight < 0.05 and equity > 0:
        warnings.append("Cash reserve is below 5% of portfolio equity.")
    if current_drawdown <= -0.1:
        warnings.append("Current equity is more than 10% below its recorded peak.")
    return PaperPortfolioDiagnosticsOut(
        as_of_date=state.as_of_date,
        cash_weight=round(cash_weight, 6),
        gross_exposure=round(gross_exposure, 6),
        largest_position_weight=round(largest, 6),
        top_three_weight=round(top_three, 6),
        concentration_hhi=round(hhi, 6),
        current_drawdown=round(current_drawdown, 6),
        max_drawdown=round(max_drawdown, 6),
        warnings=warnings,
    )


@router.get("/review", response_model=PaperPortfolioAdvisoryReviewOut)
def portfolio_advisory_review(
    advisory_id: int = Query(ge=1),
    session: Session = Depends(get_session),
) -> PaperPortfolioAdvisoryReviewOut:
    """Compare one persisted advisory draft with the current paper snapshot without mutating either."""
    advisory = session.get(AdvisoryRun, advisory_id)
    if advisory is None:
        raise HTTPException(status_code=404, detail="Advisory draft was not found.")

    try:
        accepted_weights = _accepted_weights(advisory.risk_json)
        plan_by_symbol = _trade_plan_by_symbol(advisory.trade_plan_json)
        advisory_positions = _advisory_positions(advisory.request_json)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"Advisory draft cannot be reviewed: {exc}") from exc

    portfolio = session.scalar(select(PaperPortfolio).where(PaperPortfolio.name == _DEFAULT_PORTFOLIO_NAME))
    state = _state_out(session, portfolio) if portfolio is not None else None
    current_positions = {position.symbol: position for position in state.positions} if state else {}
    symbols = sorted(set(current_positions) | set(accepted_weights) | set(plan_by_symbol))
    rows: list[PaperPortfolioAdvisoryReviewRowOut] = []
    for symbol in symbols:
        current = current_positions.get(symbol)
        plan = plan_by_symbol.get(symbol)
        target_quantity = _integer(plan.get("target_quantity")) if plan else None
        current_quantity = current.quantity if current else 0
        quantity_delta = target_quantity - current_quantity if target_quantity is not None else None
        reference_price = _number(plan.get("reference_price")) if plan else None
        if reference_price is None and current is not None:
            reference_price = current.reference_price
        target_weight = _number(accepted_weights.get(symbol))
        stock = session.get(Stock, symbol)
        rows.append(
            PaperPortfolioAdvisoryReviewRowOut(
                symbol=symbol,
                name=current.name if current and current.name else (stock.name if stock else None),
                current_quantity=current_quantity,
                advisory_current_quantity=advisory_positions.get(symbol, 0),
                target_quantity=target_quantity,
                quantity_delta=quantity_delta,
                suggested_side=("buy" if quantity_delta > 0 else "sell" if quantity_delta < 0 else "hold")
                if quantity_delta is not None
                else None,
                target_weight=target_weight,
                reference_price=reference_price,
                estimated_delta_amount=round(abs(quantity_delta) * reference_price, 2)
                if quantity_delta is not None and reference_price is not None
                else None,
            )
        )

    position_changed = any(
        (current_positions.get(symbol).quantity if symbol in current_positions else 0) != quantity
        for symbol, quantity in advisory_positions.items()
    ) or any(symbol not in advisory_positions for symbol in current_positions)
    equity_changed = state is not None and abs(state.equity - advisory.total_equity) > 0.01
    requires_refresh = state is None or state.as_of_date != advisory.as_of_date or position_changed or equity_changed
    warnings = [
        "Read-only comparison only. It does not create orders or modify the paper portfolio.",
        "Reference prices are research closes and are not executable broker prices.",
    ]
    if requires_refresh:
        warnings.insert(
            0,
            "The current paper snapshot differs from this advisory draft. Refresh the advisory before acting on any delta.",
        )
    if advisory.status != "draft":
        warnings.append(f"Advisory status is {advisory.status}; it is not an active executable instruction.")
    return PaperPortfolioAdvisoryReviewOut(
        advisory_id=advisory.id,
        advisory_strategy_name=advisory.strategy_name,
        advisory_as_of_date=advisory.as_of_date,
        advisory_status=advisory.status,
        portfolio_as_of_date=state.as_of_date if state else None,
        portfolio_equity=state.equity if state else 0.0,
        requires_refresh=requires_refresh,
        rows=rows,
        warnings=warnings,
    )


@router.put("/snapshot", response_model=PaperPortfolioStateOut)
def save_portfolio_snapshot(
    payload: PaperPortfolioSnapshotIn,
    session: Session = Depends(get_session),
) -> PaperPortfolioStateOut:
    repository = MarketDataRepository(session)
    quantities: dict[str, int] = {}
    try:
        for position in payload.positions:
            symbol = repository.resolve_symbol(position.symbol)
            if session.get(Stock, symbol) is None:
                raise ValueError(f"unknown A-share symbol or stock name: {position.symbol}")
            quantities[symbol] = quantities.get(symbol, 0) + position.quantity
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    prices: dict[str, float] = {}
    if quantities:
        bars = repository.daily_bars(list(quantities), payload.as_of_date, payload.as_of_date)
        prices = {row.symbol: float(row.close) for row in bars.itertuples() if float(row.close) > 0}
        missing = sorted(set(quantities) - set(prices))
        if missing:
            raise HTTPException(
                status_code=400,
                detail="No local close on the snapshot date for: " + ", ".join(missing),
            )

    portfolio = session.scalar(select(PaperPortfolio).where(PaperPortfolio.name == _DEFAULT_PORTFOLIO_NAME))
    if portfolio is not None and portfolio.as_of_date is not None and payload.as_of_date < portfolio.as_of_date:
        raise HTTPException(
            status_code=409,
            detail="A snapshot earlier than the current portfolio date cannot be applied.",
        )
    portfolio = portfolio or _default_portfolio(session)
    portfolio.cash = float(payload.cash)
    portfolio.as_of_date = payload.as_of_date
    session.execute(delete(PaperPortfolioPosition).where(PaperPortfolioPosition.portfolio_id == portfolio.id))
    for symbol, quantity in sorted(quantities.items()):
        session.add(
            PaperPortfolioPosition(
                portfolio_id=portfolio.id,
                symbol=symbol,
                quantity=quantity,
                reference_price=prices[symbol],
                price_date=payload.as_of_date,
            )
        )
    position_value = round(sum(quantity * prices[symbol] for symbol, quantity in quantities.items()), 2)
    valuation = session.scalar(
        select(PaperPortfolioValuation).where(
            PaperPortfolioValuation.portfolio_id == portfolio.id,
            PaperPortfolioValuation.as_of_date == payload.as_of_date,
        )
    )
    if valuation is None:
        valuation = PaperPortfolioValuation(portfolio_id=portfolio.id, as_of_date=payload.as_of_date)
        session.add(valuation)
    valuation.cash = round(float(payload.cash), 2)
    valuation.position_value = position_value
    valuation.equity = round(valuation.cash + position_value, 2)
    session.commit()
    session.refresh(portfolio)
    return _state_out(session, portfolio)


def _default_portfolio(session: Session) -> PaperPortfolio:
    portfolio = session.scalar(select(PaperPortfolio).where(PaperPortfolio.name == _DEFAULT_PORTFOLIO_NAME))
    if portfolio is None:
        portfolio = PaperPortfolio(name=_DEFAULT_PORTFOLIO_NAME, cash=0.0)
        session.add(portfolio)
        session.commit()
        session.refresh(portfolio)
    return portfolio


def _state_out(session: Session, portfolio: PaperPortfolio) -> PaperPortfolioStateOut:
    positions = list(
        session.scalars(
            select(PaperPortfolioPosition)
            .where(PaperPortfolioPosition.portfolio_id == portfolio.id)
            .order_by(PaperPortfolioPosition.symbol)
        )
    )
    position_out = []
    for position in positions:
        stock = session.get(Stock, position.symbol)
        position_out.append(
            PaperPortfolioPositionOut(
                symbol=position.symbol,
                name=stock.name if stock else None,
                quantity=position.quantity,
                reference_price=position.reference_price,
                price_date=position.price_date,
                market_value=round(position.quantity * position.reference_price, 2),
            )
        )
    position_value = round(sum(item.market_value for item in position_out), 2)
    return PaperPortfolioStateOut(
        id=portfolio.id,
        name=portfolio.name,
        as_of_date=portfolio.as_of_date,
        cash=round(portfolio.cash, 2),
        position_value=position_value,
        equity=round(portfolio.cash + position_value, 2),
        positions=position_out,
    )


def _json_object(value: str, label: str) -> dict:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        raise ValueError(f"{label} is not valid JSON") from None
    if not isinstance(decoded, dict):
        raise ValueError(f"{label} must be a JSON object")
    return decoded


def _json_list(value: str, label: str) -> list[dict]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        raise ValueError(f"{label} is not valid JSON") from None
    if not isinstance(decoded, list) or not all(isinstance(item, dict) for item in decoded):
        raise ValueError(f"{label} must be a JSON list of objects")
    return decoded


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not isfinite(numeric) or not numeric.is_integer():
        return None
    parsed = int(numeric)
    return parsed if parsed >= 0 else None


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) and parsed >= 0 else None


def _accepted_weights(value: str) -> dict[str, float]:
    accepted = _json_object(value, "risk_json").get("accepted")
    if not isinstance(accepted, dict):
        raise ValueError("risk_json.accepted must be an object")
    weights: dict[str, float] = {}
    for symbol, weight in accepted.items():
        numeric = _number(weight)
        if not isinstance(symbol, str) or not symbol or numeric is None or numeric > 1:
            raise ValueError("risk_json.accepted contains an invalid target weight")
        weights[symbol] = numeric
    return weights


def _trade_plan_by_symbol(value: str) -> dict[str, dict]:
    plan_by_symbol: dict[str, dict] = {}
    for item in _json_list(value, "trade_plan_json"):
        symbol = item.get("symbol")
        if (
            not isinstance(symbol, str)
            or not symbol
            or symbol in plan_by_symbol
            or _integer(item.get("current_quantity")) is None
            or _integer(item.get("target_quantity")) is None
            or _number(item.get("reference_price")) is None
        ):
            raise ValueError("trade_plan_json contains an invalid trade")
        plan_by_symbol[symbol] = item
    return plan_by_symbol


def _advisory_positions(value: str) -> dict[str, int]:
    positions = _json_object(value, "request_json").get("positions")
    if not isinstance(positions, dict):
        raise ValueError("request_json.positions must be an object")
    normalized: dict[str, int] = {}
    for symbol, quantity in positions.items():
        parsed = _integer(quantity)
        if not isinstance(symbol, str) or not symbol or parsed is None:
            raise ValueError("request_json.positions contains an invalid position")
        normalized[symbol] = parsed
    return normalized
