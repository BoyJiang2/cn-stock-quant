import json
from datetime import date, timedelta
from math import isfinite

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.data.repository import MarketDataRepository
from app.models.entities import AdvisoryRun, BacktestRun, BacktestRunProvenance, BacktestWalkForwardValidation, DailyBar, PaperPortfolio, PaperPortfolioPosition, PaperPortfolioValuation, Stock
from app.schemas.portfolio import (
    PaperPortfolioAdvisoryReviewOut,
    PaperPortfolioAdvisoryReviewRowOut,
    PaperPortfolioPositionOut,
    PaperPortfolioDiagnosticsOut,
    PaperPortfolioSnapshotIn,
    PaperPortfolioStateOut,
    PaperPortfolioValuationOut,
    PaperPortfolioPromotionEligibilityOut,
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


@router.get("/promotion-eligibility", response_model=PaperPortfolioPromotionEligibilityOut)
def promotion_eligibility(
    advisory_id: int = Query(ge=1),
    session: Session = Depends(get_session),
) -> PaperPortfolioPromotionEligibilityOut:
    advisory = session.get(AdvisoryRun, advisory_id)
    if advisory is None:
        raise HTTPException(status_code=404, detail="Advisory draft was not found.")
    return _promotion_eligibility(session, advisory)


@router.post("/promote-advisory/{advisory_id}", response_model=PaperPortfolioStateOut)
def promote_advisory_to_paper_portfolio(
    advisory_id: int,
    session: Session = Depends(get_session),
) -> PaperPortfolioStateOut:
    """Apply an explicitly reviewed, multi-window OOS validated advisory to the local paper portfolio only."""
    advisory = session.get(AdvisoryRun, advisory_id)
    if advisory is None:
        raise HTTPException(status_code=404, detail="Advisory draft was not found.")
    eligibility = _promotion_eligibility(session, advisory)
    if not eligibility.eligible:
        raise HTTPException(status_code=409, detail="Advisory cannot be promoted: " + " ".join(eligibility.reasons))
    try:
        weights = _accepted_weights(advisory.risk_json)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"Advisory draft cannot be promoted: {exc}") from exc
    if sum(weights.values()) > 1 + 1e-9:
        raise HTTPException(status_code=409, detail="Advisory cannot be promoted: accepted target weights exceed total capital.")
    total_equity = _finite_positive(advisory.total_equity)
    if total_equity is None:
        raise HTTPException(status_code=409, detail="Advisory cannot be promoted: total equity is not a finite positive value.")
    repository = MarketDataRepository(session)
    symbols = sorted(weights)
    bars = repository.daily_bars(symbols, advisory.as_of_date, advisory.as_of_date) if symbols else None
    prices = {row.symbol: float(row.close) for row in bars.itertuples() if float(row.close) > 0} if bars is not None else {}
    missing = sorted(set(symbols) - set(prices))
    if missing:
        raise HTTPException(status_code=409, detail="Advisory cannot be promoted; local close is missing for: " + ", ".join(missing))
    positions = []
    position_value = 0.0
    for symbol in symbols:
        quantity = int((total_equity * weights[symbol] / prices[symbol]) // 100 * 100)
        if quantity > 0:
            positions.append({"symbol": symbol, "quantity": quantity})
            position_value += quantity * prices[symbol]
    desired_quantities = {item["symbol"]: item["quantity"] for item in positions}
    desired_cash = round(total_equity - position_value, 2)
    existing_portfolio = session.scalar(select(PaperPortfolio).where(PaperPortfolio.name == _DEFAULT_PORTFOLIO_NAME))
    if existing_portfolio is not None and existing_portfolio.as_of_date == advisory.as_of_date:
        state = _state_out(session, existing_portfolio)
        existing_quantities = {item.symbol: item.quantity for item in state.positions}
        if existing_quantities == desired_quantities and abs(state.cash - desired_cash) <= 0.01:
            return state
        raise HTTPException(
            status_code=409,
            detail="Paper portfolio already has a different snapshot on this advisory date; promotion will not overwrite it.",
        )
    return save_portfolio_snapshot(
        PaperPortfolioSnapshotIn(
            as_of_date=advisory.as_of_date,
            cash=desired_cash,
            positions=positions,
        ),
        session,
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


def _finite_positive(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) and parsed > 0 else None


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


def _promotion_eligibility(session: Session, advisory: AdvisoryRun) -> PaperPortfolioPromotionEligibilityOut:
    reasons: list[str] = []
    validation_id: int | None = None
    window_count = 0
    if advisory.status != "reviewed" or advisory.reviewed_at is None:
        reasons.append("Advisory must be explicitly reviewed before paper promotion.")
    if _advisory_is_expired(session, advisory):
        reasons.append("Advisory has expired because newer local bars are available after its execution window.")
    try:
        risk = _json_object(advisory.risk_json, "risk_json")
        evidence = risk.get("evidence")
        validation_evidence = evidence.get("validation") if isinstance(evidence, dict) else None
    except ValueError:
        validation_evidence = None
        reasons.append("Advisory risk evidence is corrupt.")
    if not isinstance(validation_evidence, dict) or isinstance(validation_evidence.get("validation_id"), bool):
        reasons.append("Advisory has no attached rolling OOS validation evidence.")
        return PaperPortfolioPromotionEligibilityOut(
            advisory_id=advisory.id,
            eligible=False,
            reasons=reasons,
        )
    raw_validation_id = validation_evidence.get("validation_id")
    if not isinstance(raw_validation_id, int) or raw_validation_id < 1:
        reasons.append("Advisory validation evidence has an invalid validation ID.")
        return PaperPortfolioPromotionEligibilityOut(advisory_id=advisory.id, eligible=False, reasons=reasons)
    validation_id = raw_validation_id
    validation = session.get(BacktestWalkForwardValidation, validation_id)
    if validation is None:
        reasons.append("Attached rolling OOS validation no longer exists.")
        return PaperPortfolioPromotionEligibilityOut(advisory_id=advisory.id, eligible=False, validation_id=validation_id, reasons=reasons)
    run = session.get(BacktestRun, validation.backtest_run_id)
    provenance = session.scalar(select(BacktestRunProvenance).where(BacktestRunProvenance.run_id == validation.backtest_run_id))
    if validation.status != "completed" or validation.eligibility_status != "eligible":
        reasons.append("Attached rolling OOS validation is not completed and eligible.")
    if run is None or provenance is None:
        reasons.append("Attached rolling OOS validation has incomplete source provenance.")
        return PaperPortfolioPromotionEligibilityOut(advisory_id=advisory.id, eligible=False, validation_id=validation_id, reasons=reasons)
    try:
        spec = _json_object(validation.spec_json, "validation spec_json")
        result = _json_object(validation.result_json, "validation result_json")
        windows = spec.get("windows")
        if not isinstance(windows, list):
            raise ValueError("windows must be a list")
        window_ranges = [
            (date.fromisoformat(str(item["oos_start_date"])), date.fromisoformat(str(item["oos_end_date"])))
            for item in windows
            if isinstance(item, dict)
        ]
        if len(window_ranges) != len(windows) or any(start > end for start, end in window_ranges):
            raise ValueError("window range is invalid")
        ordered_ranges = sorted(window_ranges)
        if any(previous_end >= current_start for (_, previous_end), (current_start, _) in zip(ordered_ranges, ordered_ranges[1:])):
            raise ValueError("windows overlap")
        window_dates = [end for _, end in window_ranges]
        window_results = result.get("window_results")
        if not isinstance(window_results, list) or len(window_results) != len(windows) or not all(isinstance(item, dict) for item in window_results):
            raise ValueError("window results are invalid")
    except (KeyError, TypeError, ValueError):
        reasons.append("Attached rolling OOS validation has invalid window metadata.")
        return PaperPortfolioPromotionEligibilityOut(advisory_id=advisory.id, eligible=False, validation_id=validation_id, reasons=reasons)
    window_count = len(set(window_dates))
    if window_count < 3:
        reasons.append("At least three distinct rolling OOS windows are required for paper promotion.")
    if max(window_dates, default=advisory.as_of_date) != advisory.as_of_date:
        reasons.append("The latest rolling OOS window must end on the advisory as-of date.")
    if (
        run.strategy_name != advisory.strategy_name
        or spec.get("strategy_name") != advisory.strategy_name
        or spec.get("source_backtest_run_id") != run.id
        or spec.get("source_provenance_fingerprint") != provenance.fingerprint
        or validation.source_provenance_fingerprint != provenance.fingerprint
        or validation_evidence.get("backtest_run_id") != run.id
        or validation_evidence.get("fingerprint") != validation.fingerprint
        or validation_evidence.get("source_as_of_date") != advisory.as_of_date.isoformat()
    ):
        reasons.append("Rolling OOS validation provenance does not match this advisory.")
    return PaperPortfolioPromotionEligibilityOut(
        advisory_id=advisory.id,
        eligible=not reasons,
        validation_id=validation_id,
        oos_window_count=window_count,
        reasons=reasons,
    )


def _advisory_is_expired(session: Session, advisory: AdvisoryRun) -> bool:
    try:
        request = _json_object(advisory.request_json, "request_json")
        symbols = sorted({symbol for symbol in request.get("symbols", []) if isinstance(symbol, str) and symbol})
    except ValueError:
        return False
    if not symbols:
        return False
    repository = MarketDataRepository(session)
    execution_dates = repository.trading_dates(
        advisory.as_of_date + timedelta(days=1),
        advisory.as_of_date + timedelta(days=14),
    )
    if not execution_dates:
        return False
    latest_rows = session.execute(
        select(DailyBar.symbol, func.max(DailyBar.trade_date))
        .where(DailyBar.symbol.in_(symbols))
        .group_by(DailyBar.symbol)
    )
    latest_dates = {symbol: latest for symbol, latest in latest_rows if latest is not None}
    return len(latest_dates) == len(symbols) and min(latest_dates.values()) > execution_dates[0]
