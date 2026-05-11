from datetime import date

from pydantic import BaseModel, Field


class BacktestRequest(BaseModel):
    strategy_name: str = "moving_average"
    symbols: list[str] = Field(default_factory=lambda: ["000001"])
    start_date: date
    end_date: date
    initial_cash: float = 1_000_000
    fast_window: int = 20
    slow_window: int = 60
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.001
    slippage_rate: float = 0.0005
    max_position_weight: float = 0.95


class EquityPoint(BaseModel):
    trade_date: date
    equity: float
    cash: float
    position_value: float
    drawdown: float


class TradeOut(BaseModel):
    trade_date: date
    symbol: str
    side: str
    price: float
    quantity: int
    amount: float


class BacktestMetrics(BaseModel):
    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe: float
    final_equity: float


class BacktestResponse(BaseModel):
    run_id: int | None = None
    metrics: BacktestMetrics
    equity_curve: list[EquityPoint]
    trades: list[TradeOut]


class BacktestRunOut(BaseModel):
    id: int
    strategy_name: str
    start_date: date
    end_date: date
    initial_cash: float
    final_equity: float
    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe: float
