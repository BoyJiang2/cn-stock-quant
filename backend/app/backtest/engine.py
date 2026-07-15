from dataclasses import dataclass, field
from datetime import date, timedelta
from math import sqrt

import numpy as np
import pandas as pd

from app.risk.rules import RiskConfig, RiskEngine
from app.strategy.base import Strategy, StrategyContext


@dataclass
class BacktestConfig:
    start_date: date
    end_date: date
    initial_cash: float
    evaluation_start_date: date | None = None
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.001
    slippage_rate: float = 0.0005
    lot_size: int = 100
    rebalance_interval: int = 1
    risk_max_symbol_weight: float = 1.0
    risk_max_total_weight: float = 1.0
    risk_max_positions: int | None = None
    params: dict = field(default_factory=dict)
    news_history: pd.DataFrame | None = None


@dataclass
class BacktestResult:
    metrics: dict
    equity_curve: list[dict]
    trades: list[dict]
    benchmark_curve: list[dict] = field(default_factory=list)


class DailyBacktestEngine:
    def run(
        self,
        strategy: Strategy,
        bars: pd.DataFrame,
        config: BacktestConfig,
        benchmark_bars: pd.DataFrame | None = None,
    ) -> BacktestResult:
        if bars.empty:
            raise ValueError("No daily bars available for the requested backtest range.")

        bars = bars.copy()
        bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.date
        bars = bars[(bars["trade_date"] >= config.start_date) & (bars["trade_date"] <= config.end_date)]
        if bars.empty:
            raise ValueError("No daily bars available for the requested backtest range.")
        bars = bars.sort_values(["trade_date", "symbol"])
        evaluation_start_date = config.evaluation_start_date or config.start_date
        if evaluation_start_date < config.start_date or evaluation_start_date > config.end_date:
            raise ValueError("evaluation_start_date must be within the backtest range.")
        dates = sorted(bars["trade_date"].unique())
        next_trade_date = {current: dates[index + 1] for index, current in enumerate(dates[:-1])}
        normalized_benchmark = None
        if benchmark_bars is not None and not benchmark_bars.empty:
            normalized_benchmark = benchmark_bars.copy()
            normalized_benchmark["trade_date"] = pd.to_datetime(normalized_benchmark["trade_date"]).dt.date
            normalized_benchmark = normalized_benchmark.sort_values("trade_date")
        normalized_news = _normalise_news_history(config.news_history)

        cash = float(config.initial_cash)
        positions: dict[str, int] = {}
        lots: dict[str, list[dict]] = {}
        last_prices: dict[str, float] = {}
        previous_close: dict[str, float] = {}
        pending_target_weights: dict[str, float] | None = None
        equity_curve: list[dict] = []
        trades: list[dict] = []
        rebalance_interval = max(1, int(config.rebalance_interval))

        for date_index, current_date in enumerate(dates):
            today = bars[bars["trade_date"] == current_date]
            price_map = {row.symbol: float(row.close) for row in today.itertuples() if float(row.close) > 0}
            volume_map = {row.symbol: float(row.volume) for row in today.itertuples()}
            tradable_symbols = {symbol for symbol, volume in volume_map.items() if volume > 0}
            last_prices.update(price_map)

            if current_date < evaluation_start_date:
                previous_close.update(price_map)
                continue

            position_value = _position_value(positions, last_prices)
            equity = cash + position_value

            if pending_target_weights is not None:
                cash = _execute_target_weights(
                    current_date=current_date,
                    target_weights=pending_target_weights,
                    equity=equity,
                    cash=cash,
                    positions=positions,
                    lots=lots,
                    price_map=price_map,
                    previous_close=previous_close,
                    tradable_symbols=tradable_symbols,
                    next_available_date=next_trade_date.get(current_date, current_date + timedelta(days=1)),
                    config=config,
                    trades=trades,
                )
                pending_target_weights = None

            position_value = _position_value(positions, last_prices)
            equity = cash + position_value
            history = bars[bars["trade_date"] <= current_date]
            context = StrategyContext(
                current_date=current_date,
                cash=cash,
                positions=positions.copy(),
                params=config.params,
                benchmark_history=(
                    normalized_benchmark[normalized_benchmark["trade_date"] <= current_date].copy()
                    if normalized_benchmark is not None
                    else None
                ),
                news_history=(
                    normalized_news[normalized_news["known_at"] <= _end_of_day(current_date)].copy()
                    if normalized_news is not None
                    else None
                ),
            )

            if date_index % rebalance_interval == 0:
                raw_target_weights = strategy.generate_target_weights(context, history)
                pending_target_weights = RiskEngine().evaluate(
                    raw_target_weights,
                    RiskConfig(
                        max_symbol_weight=config.risk_max_symbol_weight,
                        max_total_weight=config.risk_max_total_weight,
                        max_positions=config.risk_max_positions,
                    ),
                ).accepted

            position_value = _position_value(positions, last_prices)
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
            previous_close.update(price_map)

        equity_curve = _attach_drawdown(equity_curve)
        benchmark_curve = _build_benchmark_curve(
            benchmark_bars,
            [point["trade_date"] for point in equity_curve],
            config.initial_cash,
        )
        metrics = _calculate_metrics(equity_curve, config.initial_cash, benchmark_curve)
        return BacktestResult(metrics=metrics, equity_curve=equity_curve, benchmark_curve=benchmark_curve, trades=trades)


def _execute_target_weights(
    current_date: date,
    target_weights: dict[str, float],
    equity: float,
    cash: float,
    positions: dict[str, int],
    lots: dict[str, list[dict]],
    price_map: dict[str, float],
    previous_close: dict[str, float],
    tradable_symbols: set[str],
    next_available_date: date,
    config: BacktestConfig,
    trades: list[dict],
) -> float:
    symbols = set(target_weights) | set(positions)
    for side_to_execute in ("sell", "buy"):
        for symbol in sorted(symbols):
            if symbol not in price_map or symbol not in tradable_symbols:
                continue
            close_price = float(price_map[symbol])
            target_weight = target_weights.get(symbol, 0.0)
            target_value = equity * max(0.0, min(float(target_weight), 1.0))
            current_qty = positions.get(symbol, 0)
            current_value = current_qty * close_price
            delta_value = target_value - current_value
            side = "buy" if delta_value > 0 else "sell"
            if side != side_to_execute:
                continue

            if side == "buy" and _is_limit_up(close_price, previous_close.get(symbol)):
                continue
            if side == "sell" and _is_limit_down(close_price, previous_close.get(symbol)):
                continue

            trade_price = close_price * (1 + config.slippage_rate if side == "buy" else 1 - config.slippage_rate)
            raw_qty = abs(delta_value) / trade_price
            qty = int(raw_qty // config.lot_size * config.lot_size)
            if qty <= 0:
                continue

            amount = qty * trade_price
            commission = _commission(amount, config.commission_rate)
            stamp_tax = amount * config.stamp_tax_rate if side == "sell" else 0.0

            if side == "buy":
                total_cost = amount + commission
                if total_cost > cash:
                    affordable_qty = int((cash / (trade_price * (1 + config.commission_rate))) // config.lot_size * config.lot_size)
                    qty = max(0, affordable_qty)
                    amount = qty * trade_price
                    commission = _commission(amount, config.commission_rate) if qty else 0.0
                    total_cost = amount + commission
                while qty > 0 and total_cost > cash:
                    qty -= config.lot_size
                    amount = qty * trade_price
                    commission = _commission(amount, config.commission_rate) if qty else 0.0
                    total_cost = amount + commission
                if qty <= 0:
                    continue
                cash -= total_cost
                positions[symbol] = current_qty + qty
                lots.setdefault(symbol, []).append({"quantity": qty, "available_date": next_available_date})
            else:
                sellable_qty = _sellable_quantity(lots.get(symbol, []), current_date)
                qty = min(qty, current_qty, sellable_qty)
                qty = int(qty // config.lot_size * config.lot_size)
                if qty <= 0:
                    continue
                amount = qty * trade_price
                commission = _commission(amount, config.commission_rate)
                stamp_tax = amount * config.stamp_tax_rate
                cash += amount - commission - stamp_tax
                _consume_lots(lots.setdefault(symbol, []), qty, current_date)
                remaining_qty = current_qty - qty
                if remaining_qty:
                    positions[symbol] = remaining_qty
                else:
                    positions.pop(symbol, None)
                    lots.pop(symbol, None)

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
    return cash


def _normalise_news_history(news_history: pd.DataFrame | None) -> pd.DataFrame | None:
    if news_history is None or news_history.empty:
        return None
    frame = news_history.copy()
    if "known_at" not in frame.columns:
        if not {"published_at", "fetched_at"}.issubset(frame.columns):
            return None
        frame["published_at"] = pd.to_datetime(frame["published_at"], errors="coerce")
        frame["fetched_at"] = pd.to_datetime(frame["fetched_at"], errors="coerce")
        frame["known_at"] = frame[["published_at", "fetched_at"]].max(axis=1)
    else:
        frame["known_at"] = pd.to_datetime(frame["known_at"], errors="coerce")
    if "symbol" in frame.columns:
        frame["symbol"] = frame["symbol"].astype(str).str.zfill(6)
    frame = frame.dropna(subset=["known_at"])
    return frame.sort_values("known_at")


def _end_of_day(value: date) -> pd.Timestamp:
    return pd.Timestamp(value) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)


def _position_value(positions: dict[str, int], last_prices: dict[str, float]) -> float:
    return sum(qty * last_prices.get(symbol, 0.0) for symbol, qty in positions.items())


def _commission(amount: float, commission_rate: float) -> float:
    if commission_rate <= 0:
        return 0.0
    return max(amount * commission_rate, 5.0)


def _sellable_quantity(symbol_lots: list[dict], current_date: date) -> int:
    return sum(int(lot["quantity"]) for lot in symbol_lots if lot["available_date"] <= current_date)


def _consume_lots(symbol_lots: list[dict], quantity: int, current_date: date) -> None:
    remaining = quantity
    for lot in symbol_lots:
        if remaining <= 0 or lot["available_date"] > current_date:
            continue
        consumed = min(int(lot["quantity"]), remaining)
        lot["quantity"] -= consumed
        remaining -= consumed
    symbol_lots[:] = [lot for lot in symbol_lots if lot["quantity"] > 0]


def _is_limit_up(close_price: float, prev_close: float | None, limit_pct: float = 0.10) -> bool:
    if prev_close is None or prev_close <= 0:
        return False
    return close_price >= prev_close * (1 + limit_pct) * 0.999


def _is_limit_down(close_price: float, prev_close: float | None, limit_pct: float = 0.10) -> bool:
    if prev_close is None or prev_close <= 0:
        return False
    return close_price <= prev_close * (1 - limit_pct) * 1.001


def _attach_drawdown(equity_curve: list[dict]) -> list[dict]:
    peak = 0.0
    for point in equity_curve:
        equity = point["equity"]
        peak = max(peak, equity)
        point["drawdown"] = round((equity / peak - 1.0) if peak else 0.0, 6)
    return equity_curve


def _build_benchmark_curve(benchmark_bars: pd.DataFrame | None, dates: list[date], initial_cash: float) -> list[dict]:
    if benchmark_bars is None or benchmark_bars.empty or not dates:
        return []

    frame = benchmark_bars.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    frame = frame[frame["trade_date"].isin(dates)].sort_values("trade_date")
    frame = frame.drop_duplicates("trade_date", keep="last")
    frame = frame[frame["close"] > 0]
    if frame.empty:
        return []

    first_close = float(frame.iloc[0]["close"])
    if first_close <= 0:
        return []

    return [
        {
            "trade_date": row.trade_date,
            "equity": round(initial_cash * float(row.close) / first_close, 2),
            "return": round(float(row.close) / first_close - 1.0, 6),
        }
        for row in frame.itertuples()
    ]


def _calculate_metrics(equity_curve: list[dict], initial_cash: float, benchmark_curve: list[dict] | None = None) -> dict:
    equities = np.array([point["equity"] for point in equity_curve], dtype=float)
    if len(equities) == 0:
        return {
            "total_return": 0.0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "final_equity": initial_cash,
            "benchmark_return": 0.0,
            "excess_return": 0.0,
        }

    daily_returns = pd.Series(equities).pct_change().dropna()
    total_return = equities[-1] / initial_cash - 1.0
    annual_return = (1.0 + total_return) ** (252 / max(len(equities), 1)) - 1.0
    max_drawdown = min(point["drawdown"] for point in equity_curve)
    sharpe = 0.0
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = float(daily_returns.mean() / daily_returns.std() * sqrt(252))

    benchmark_return = 0.0
    if benchmark_curve:
        benchmark_return = float(benchmark_curve[-1]["equity"]) / initial_cash - 1.0

    return {
        "total_return": round(float(total_return), 6),
        "annual_return": round(float(annual_return), 6),
        "max_drawdown": round(float(max_drawdown), 6),
        "sharpe": round(sharpe, 6),
        "final_equity": round(float(equities[-1]), 2),
        "benchmark_return": round(float(benchmark_return), 6),
        "excess_return": round(float(total_return - benchmark_return), 6),
    }
