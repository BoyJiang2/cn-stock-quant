from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.data.repository import MarketDataRepository
from app.models.entities import PaperPortfolio, PaperPortfolioPosition, PaperPortfolioValuation, Stock
from app.schemas.portfolio import (
    PaperPortfolioPositionOut,
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
