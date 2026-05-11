from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd


@dataclass
class StrategyContext:
    current_date: date
    cash: float
    positions: dict[str, int] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)


class Strategy:
    name = "base"
    display_name = "Base Strategy"

    def generate_target_weights(self, context: StrategyContext, history: pd.DataFrame) -> dict[str, float]:
        raise NotImplementedError

