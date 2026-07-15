from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class BacktestRequest(BaseModel):
    strategy_name: str = "moving_average"
    symbol_source: Literal["manual", "research_pool"] = "manual"
    symbols: list[str] = Field(default_factory=lambda: ["000001"])
    pool_max_symbols: int = Field(default=100, ge=1, le=6000)
    point_in_time: bool = False
    universe_as_of_date: date | None = None
    pit_st_policy: Literal["exclude_known", "include_unknown", "strict"] = "exclude_known"
    pit_index_symbol: str | None = None
    benchmark_symbol: str | None = "000300"
    start_date: date
    end_date: date
    initial_cash: float = 1_000_000
    parameters: dict[str, Any] = Field(default_factory=dict)
    fast_window: int | None = None
    slow_window: int | None = None
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.001
    slippage_rate: float = 0.0005
    rebalance_interval: int = Field(default=1, ge=1)
    risk_max_symbol_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    risk_max_total_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    risk_max_positions: int | None = Field(default=None, ge=0)
    max_position_weight: float | None = None

    def strategy_parameters(self) -> dict[str, Any]:
        params = dict(self.parameters)
        for name in ("fast_window", "slow_window", "max_position_weight"):
            value = getattr(self, name)
            if value is not None:
                params[name] = value
        return params


class EquityPoint(BaseModel):
    trade_date: date
    equity: float
    cash: float
    position_value: float
    drawdown: float


class BenchmarkPoint(BaseModel):
    trade_date: date
    equity: float
    return_: float = Field(alias="return")

    class Config:
        populate_by_name = True


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
    benchmark_return: float = 0.0
    excess_return: float = 0.0


class BacktestResponse(BaseModel):
    run_id: int | None = None
    symbol_source: Literal["manual", "research_pool"] = "manual"
    selected_symbols: list[str] = Field(default_factory=list)
    universe_metadata: dict[str, Any] = Field(default_factory=dict)
    metrics: BacktestMetrics
    equity_curve: list[EquityPoint]
    benchmark_curve: list[BenchmarkPoint] = Field(default_factory=list)
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


class BacktestRunProvenanceOut(BaseModel):
    run_id: int
    status: Literal["recorded_unvalidated", "not_recorded"]
    fingerprint: str | None = None
    spec: dict[str, Any] = Field(default_factory=dict)
    universe: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    warning: str
