from dataclasses import dataclass
from typing import Protocol


@dataclass
class BrokerOrder:
    symbol: str
    side: str
    quantity: int
    price: float | None = None


class Broker(Protocol):
    def positions(self) -> dict[str, int]:
        """Return current account positions."""

    def cash(self) -> float:
        """Return available cash."""

    def submit_order(self, order: BrokerOrder) -> str:
        """Submit an order and return broker order id."""

