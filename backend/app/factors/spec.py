"""Stable interfaces for the factor laboratory.

This module defines the contracts that the rest of the factor lab relies on
and that callers (strategies, backtests, research notebooks) can depend on
without reaching into implementation details:

* :class:`FactorInputs`  -- the pivoted, wide-form market data that every
  factor function consumes (index = trade_date, columns = symbol).
* :class:`FactorSpec`    -- an immutable description of which factor to
  compute and any optional parameters.
* :class:`FactorRegistry` -- a name -> callable registry that resolves a
  :class:`FactorSpec` to a concrete vectorised computation.

The design is deliberately small and stable so that additional factors can be
registered later without changing call-sites.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

import pandas as pd

__all__ = ["FactorInputs", "FactorSpec", "FactorRegistry", "FactorFunc"]


@dataclass
class FactorInputs:
    """Wide-form (trade_date x symbol) market data consumed by factors.

    Every DataFrame shares the same sorted trade_date index and the same
    sorted symbol columns.  Factors operate column-wise so that each symbol
    is computed independently -- this is what guarantees cross-stock
    isolation and the absence of look-ahead bias.
    """

    close: pd.DataFrame
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    volume: pd.DataFrame
    amount: pd.DataFrame


# A factor function receives the wide inputs and the spec parameters and
# returns a wide DataFrame (trade_date x symbol) of factor values.
FactorFunc = Callable[[FactorInputs, Mapping[str, Any]], pd.DataFrame]


@dataclass(frozen=True)
class FactorSpec:
    """Immutable description of a factor to compute.

    Attributes:
        name: Registered factor key, e.g. ``"momentum_5d"``.
        params: Optional parameters forwarded to the factor function.
            Reserved for future / user-registered parametric factors; the
            built-in named factors use fixed windows and ignore overrides so
            their semantics stay stable.
    """

    name: str
    params: Mapping[str, Any] = field(default_factory=dict)


class FactorRegistry:
    """Name -> factor-function registry with a tiny, stable API."""

    def __init__(self) -> None:
        self._factors: dict[str, FactorFunc] = {}

    def register(self, name: str, func: FactorFunc) -> None:
        if not name:
            raise ValueError("factor name must be a non-empty string")
        self._factors[name] = func

    def get(self, name: str) -> FactorFunc:
        try:
            return self._factors[name]
        except KeyError as exc:
            raise KeyError(f"factor '{name}' is not registered") from exc

    def contains(self, name: str) -> bool:
        return name in self._factors

    def names(self) -> list[str]:
        # Preserve registration order so compute_all() emits columns in the
        # documented, logical grouping rather than alphabetical order.
        return list(self._factors)

    def compute(
        self,
        name: str,
        inputs: FactorInputs,
        params: Mapping[str, Any] | None = None,
    ) -> pd.DataFrame:
        func = self.get(name)
        return func(inputs, params or {})
