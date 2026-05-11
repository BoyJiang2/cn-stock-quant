from dataclasses import dataclass


@dataclass
class TradePlanItem:
    symbol: str
    side: str
    current_quantity: int
    target_quantity: int
    quantity: int
    reference_price: float
    estimated_amount: float


def build_trade_plan(
    positions: dict[str, int],
    target_weights: dict[str, float],
    latest_prices: dict[str, float],
    total_equity: float,
    lot_size: int = 100,
) -> list[TradePlanItem]:
    plan: list[TradePlanItem] = []
    for symbol, target_weight in target_weights.items():
        price = latest_prices.get(symbol)
        if not price or price <= 0:
            continue
        current_quantity = positions.get(symbol, 0)
        target_value = total_equity * target_weight
        target_quantity = int((target_value / price) // lot_size * lot_size)
        delta = target_quantity - current_quantity
        if delta == 0:
            continue
        side = "buy" if delta > 0 else "sell"
        plan.append(
            TradePlanItem(
                symbol=symbol,
                side=side,
                current_quantity=current_quantity,
                target_quantity=target_quantity,
                quantity=abs(delta),
                reference_price=price,
                estimated_amount=round(abs(delta) * price, 2),
            )
        )
    return plan

