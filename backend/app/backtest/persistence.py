from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.engine import BacktestResult
from app.models.entities import BacktestEquity, BacktestRun, TradeRecord
from app.schemas.backtest import BacktestRequest


def save_backtest_result(session: Session, request: BacktestRequest, result: BacktestResult) -> int:
    run = BacktestRun(
        strategy_name=request.strategy_name,
        start_date=request.start_date,
        end_date=request.end_date,
        initial_cash=request.initial_cash,
        final_equity=result.metrics["final_equity"],
        total_return=result.metrics["total_return"],
        annual_return=result.metrics["annual_return"],
        max_drawdown=result.metrics["max_drawdown"],
        sharpe=result.metrics["sharpe"],
    )
    session.add(run)
    session.flush()

    session.add_all(
        BacktestEquity(
            run_id=run.id,
            trade_date=point["trade_date"],
            equity=point["equity"],
            cash=point["cash"],
            position_value=point["position_value"],
            drawdown=point["drawdown"],
        )
        for point in result.equity_curve
    )
    session.add_all(
        TradeRecord(
            run_id=run.id,
            trade_date=trade["trade_date"],
            symbol=trade["symbol"],
            side=trade["side"],
            price=trade["price"],
            quantity=trade["quantity"],
            amount=trade["amount"],
            commission=trade["commission"],
            stamp_tax=trade["stamp_tax"],
        )
        for trade in result.trades
    )
    session.commit()
    return run.id


def list_backtest_runs(session: Session, limit: int = 50) -> list[BacktestRun]:
    stmt = select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit)
    return list(session.scalars(stmt))


def get_backtest_run(session: Session, run_id: int) -> BacktestRun | None:
    return session.get(BacktestRun, run_id)


def get_backtest_equity(session: Session, run_id: int) -> list[BacktestEquity]:
    stmt = select(BacktestEquity).where(BacktestEquity.run_id == run_id).order_by(BacktestEquity.trade_date)
    return list(session.scalars(stmt))


def get_backtest_trades(session: Session, run_id: int) -> list[TradeRecord]:
    stmt = select(TradeRecord).where(TradeRecord.run_id == run_id).order_by(TradeRecord.trade_date, TradeRecord.id)
    return list(session.scalars(stmt))

