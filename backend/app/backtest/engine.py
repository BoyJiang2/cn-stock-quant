from dataclasses import dataclass, field
from datetime import date
from math import sqrt

import numpy as np
import pandas as pd

from app.strategy.base import Strategy, StrategyContext


@dataclass
class BacktestConfig:
    start_date: date
    end_date: date
    initial_cash: float
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.001
    slippage_rate: float = 0.0005
    lot_size: int = 100
    params: dict = field(default_factory=dict)


@dataclass
class BacktestResult:
    metrics: dict
    equity_curve: list[dict]
    trades: list[dict]


class DailyBacktestEngine:
    def run(self, strategy: Strategy, bars: pd.DataFrame, config: BacktestConfig) -> BacktestResult:
        if bars.empty:
            raise ValueError("No daily bars available for the requested backtest range.")

        bars = bars.copy()
        bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.date
        bars = bars.sort_values(["trade_date", "symbol"])
        dates = sorted(bars["trade_date"].unique())

        cash = float(config.initial_cash)
        positions: dict[str, int] = {}
        equity_curve: list[dict] = []
        trades: list[dict] = []

        for current_date in dates:
            today = bars[bars["trade_date"] == current_date]
            history = bars[bars["trade_date"] <= current_date]
            price_map = dict(zip(today["symbol"], today["close"], strict=False))

            position_value = sum(qty * price_map.get(symbol, 0.0) for symbol, qty in positions.items())
            equity = cash + position_value
            context = StrategyContext(
                current_date=current_date,
                cash=cash,
                positions=positions.copy(),
                params=config.params,
            )
            target_weights = strategy.generate_target_weights(context, history)

            for symbol, target_weight in target_weights.items():
                if symbol not in price_map:
                    continue
                close_price = float(price_map[symbol])
                target_value = equity * max(0.0, min(float(target_weight), 1.0))
                current_qty = positions.get(symbol, 0)
                current_value = current_qty * close_price
                delta_value = target_value - current_value
                side = "buy" if delta_value > 0 else "sell"
                trade_price = close_price * (1 + config.slippage_rate if side == "buy" else 1 - config.slippage_rate)
                raw_qty = abs(delta_value) / trade_price
                qty = int(raw_qty // config.lot_size * config.lot_size)
                if qty <= 0:
                    continue

                amount = qty * trade_price
                commission = max(amount * config.commission_rate, 5.0)
                stamp_tax = amount * config.stamp_tax_rate if side == "sell" else 0.0

                if side == "buy":
                    total_cost = amount + commission
                    if total_cost > cash:
                        affordable_qty = int((cash / (trade_price * (1 + config.commission_rate))) // config.lot_size * config.lot_size)
                        qty = max(0, affordable_qty)
                        amount = qty * trade_price
                        commission = max(amount * config.commission_rate, 5.0) if qty else 0.0
                        total_cost = amount + commission
                    if qty <= 0:
                        continue
                    cash -= total_cost
                    positions[symbol] = current_qty + qty
                else:
                    qty = min(qty, current_qty)
                    if qty <= 0:
                        continue
                    cash += amount - commission - stamp_tax
                    remaining_qty = current_qty - qty
                    if remaining_qty:
                        positions[symbol] = remaining_qty
                    else:
                        positions.pop(symbol, None)

                trades.append(
                    {
                        "trade_date": current_date,
                        "symbol": symbol,
                        "side": side,
                        "price": round(trade_price, 4),
                        "quantity": qty,
                        "amount": round(amount, 2),
                        "commission": round(commission, 2),
                        "stamp_tax": round(stamp_tax, 2),
                    }
                )

            position_value = sum(qty * price_map.get(symbol, 0.0) for symbol, qty in positions.items())
            equity = cash + position_value
            equity_curve.append(
                {
                    "trade_date": current_date,
                    "equity": round(equity, 2),
                    "cash": round(cash, 2),
                    "position_value": round(position_value, 2),
                    "drawdown": 0.0,
                }
            )

        equity_curve = _attach_drawdown(equity_curve)
        metrics = _calculate_metrics(equity_curve, config.initial_cash)
        return BacktestResult(metrics=metrics, equity_curve=equity_curve, trades=trades)


def _attach_drawdown(equity_curve: list[dict]) -> list[dict]:
    peak = 0.0
    for point in equity_curve:
        equity = point["equity"]
        peak = max(peak, equity)
        point["drawdown"] = round((equity / peak - 1.0) if peak else 0.0, 6)
    return equity_curve


def _calculate_metrics(equity_curve: list[dict], initial_cash: float) -> dict:
    equities = np.array([point["equity"] for point in equity_curve], dtype=float)
    if len(equities) == 0:
        return {"total_return": 0.0, "annual_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0, "final_equity": initial_cash}

    daily_returns = pd.Series(equities).pct_change().dropna()
    total_return = equities[-1] / initial_cash - 1.0
    annual_return = (1.0 + total_return) ** (252 / max(len(equities), 1)) - 1.0
    max_drawdown = min(point["drawdown"] for point in equity_curve)
    sharpe = 0.0
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = float(daily_returns.mean() / daily_returns.std() * sqrt(252))

    return {
        "total_return": round(float(total_return), 6),
        "annual_return": round(float(annual_return), 6),
        "max_drawdown": round(float(max_drawdown), 6),
        "sharpe": round(sharpe, 6),
        "final_equity": round(float(equities[-1]), 2),
    }

