from fastapi import APIRouter, Depends, HTTPException
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
from app.data.repository import MarketDataRepository
from app.schemas.backtest import BacktestMetrics, BacktestRequest, BacktestResponse, BacktestRunOut, EquityPoint, TradeOut
from app.strategy.registry import get_strategy

router = APIRouter()


@router.post("/run", response_model=BacktestResponse)
def run_backtest(payload: BacktestRequest, session: Session = Depends(get_session)):
    repository = MarketDataRepository(session)
    bars = repository.daily_bars(payload.symbols, payload.start_date, payload.end_date)
    if bars.empty:
        raise HTTPException(status_code=400, detail="No local daily bars. Sync data first.")

    try:
        strategy = get_strategy(payload.strategy_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    result = DailyBacktestEngine().run(
        strategy=strategy,
        bars=bars,
        config=BacktestConfig(
            start_date=payload.start_date,
            end_date=payload.end_date,
            initial_cash=payload.initial_cash,
            commission_rate=payload.commission_rate,
            stamp_tax_rate=payload.stamp_tax_rate,
            slippage_rate=payload.slippage_rate,
            params={
                "fast_window": payload.fast_window,
                "slow_window": payload.slow_window,
                "max_position_weight": payload.max_position_weight,
            },
        ),
    )
    run_id = save_backtest_result(session, payload, result)
    return BacktestResponse(run_id=run_id, metrics=result.metrics, equity_curve=result.equity_curve, trades=result.trades)


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
    return BacktestResponse(
        run_id=run.id,
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
