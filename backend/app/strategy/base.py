from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class StrategyParameter:
    name: str
    label: str
    type: str = "float"
    default: Any = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    description: str = ""


@dataclass
class StrategyContext:
    current_date: date
    cash: float
    positions: dict[str, int] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    benchmark_history: pd.DataFrame | None = None
    news_history: pd.DataFrame | None = None


class Strategy:
    name = "base"
    display_name = "Base Strategy"
    description = ""
    parameters: list[StrategyParameter] = []

    def generate_target_weights(self, context: StrategyContext, history: pd.DataFrame) -> dict[str, float]:
        raise NotImplementedError

    @classmethod
    def metadata(cls, source: str = "builtin") -> dict[str, Any]:
        return {
            "name": cls.name,
            "display_name": cls.display_name,
            "description": cls.description,
            "source": source,
            "parameters": [
                {
                    "name": parameter.name,
                    "label": parameter.label,
                    "type": parameter.type,
                    "default": parameter.default,
                    "min": parameter.min,
                    "max": parameter.max,
                    "step": parameter.step,
                    "description": parameter.description,
                }
                for parameter in cls.parameters
            ],
        }
