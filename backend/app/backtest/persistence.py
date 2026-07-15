import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.engine import BacktestResult
from app.models.entities import BacktestEquity, BacktestRun, BacktestRunProvenance, TradeRecord
from app.schemas.backtest import BacktestRequest


def save_backtest_result(
    session: Session,
    request: BacktestRequest,
    result: BacktestResult,
    *,
    selected_symbols: list[str],
    universe_metadata: dict,
) -> int:
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

    spec = request.model_dump(mode="json")
    provenance = {
        "request": spec,
        "selected_symbols": selected_symbols,
        "data_window": {
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "point_in_time": request.point_in_time,
            "universe_as_of_date": (
                request.universe_as_of_date.isoformat()
                if request.universe_as_of_date is not None
                else None
            ),
        },
        "benchmark": {
            "symbol": request.benchmark_symbol,
            "benchmark_return": result.metrics.get("benchmark_return", 0.0),
            "excess_return": result.metrics.get("excess_return", 0.0),
        },
    }
    fingerprint_payload = {
        "spec": spec,
        "universe": universe_metadata,
        "result": result.metrics,
        "selected_symbols": selected_symbols,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    session.add(
        BacktestRunProvenance(
            run_id=run.id,
            status="recorded_unvalidated",
            spec_json=json.dumps(provenance, ensure_ascii=True, sort_keys=True),
            universe_json=json.dumps(universe_metadata, ensure_ascii=True, sort_keys=True, default=str),
            result_json=json.dumps(result.metrics, ensure_ascii=True, sort_keys=True),
            fingerprint=fingerprint,
        )
    )

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


def get_backtest_run_provenance(session: Session, run_id: int) -> BacktestRunProvenance | None:
    stmt = select(BacktestRunProvenance).where(BacktestRunProvenance.run_id == run_id)
    return session.scalar(stmt)


def get_backtest_equity(session: Session, run_id: int) -> list[BacktestEquity]:
    stmt = select(BacktestEquity).where(BacktestEquity.run_id == run_id).order_by(BacktestEquity.trade_date)
    return list(session.scalars(stmt))


def get_backtest_trades(session: Session, run_id: int) -> list[TradeRecord]:
    stmt = select(TradeRecord).where(TradeRecord.run_id == run_id).order_by(TradeRecord.trade_date, TradeRecord.id)
    return list(session.scalars(stmt))
