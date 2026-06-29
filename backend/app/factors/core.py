"""In-memory vectorised factor laboratory.

:class:`FactorLab` is the entry point: it accepts a long-form bars
DataFrame (the same shape returned by
:meth:`~app.data.repository.MarketDataRepository.daily_bars`) and produces a
``MultiIndex(trade_date, symbol)`` DataFrame whose columns are the requested
factors.
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd

from app.factors.factors import default_registry
from app.factors.spec import FactorInputs, FactorRegistry, FactorSpec

__all__ = ["FactorLab"]

_REQUIRED_COLUMNS = {
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
}


class FactorLab:
    """Compute vectorised factors from long-form bars.

    The lab is pure and stateless apart from its registry, so it can be
    reused across runs.  All computation happens in memory.
    """

    def __init__(self, registry: FactorRegistry | None = None) -> None:
        self.registry = registry or default_registry()

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------
    @staticmethod
    def _validate(bars: pd.DataFrame) -> None:
        if not isinstance(bars, pd.DataFrame):
            raise TypeError("bars must be a pandas DataFrame")
        missing = _REQUIRED_COLUMNS - set(bars.columns)
        if missing:
            raise ValueError(f"bars missing required columns: {sorted(missing)}")
        if bars.empty:
            raise ValueError("bars must not be empty")
        dupes = bars.duplicated(subset=["symbol", "trade_date"]).any()
        if dupes:
            raise ValueError("bars contain duplicate (symbol, trade_date) rows")

    @staticmethod
    def _pivot(bars: pd.DataFrame, column: str) -> pd.DataFrame:
        """Pivot a long column to wide (trade_date x symbol), sorted."""
        return bars.pivot(index="trade_date", columns="symbol", values=column).sort_index()

    def build_inputs(self, bars: pd.DataFrame) -> FactorInputs:
        """Validate *bars* and return the wide :class:`FactorInputs`."""
        self._validate(bars)
        return FactorInputs(
            close=self._pivot(bars, "close"),
            open=self._pivot(bars, "open"),
            high=self._pivot(bars, "high"),
            low=self._pivot(bars, "low"),
            volume=self._pivot(bars, "volume"),
            amount=self._pivot(bars, "amount"),
        )

    # ------------------------------------------------------------------
    # Output assembly
    # ------------------------------------------------------------------
    @staticmethod
    def _stack(wide: pd.DataFrame, name: str) -> pd.Series:
        """Stack a wide frame to a ``MultiIndex(trade_date, symbol)`` Series.

        The Cartesian product of the sorted index and columns is used so that
        every (trade_date, symbol) combination is present, with ``NaN`` where
        a factor is undefined (e.g. the warm-up period).  This keeps the index
        identical across all factors so they align perfectly when combined.
        """
        index = pd.MultiIndex.from_product(
            [wide.index, wide.columns], names=["trade_date", "symbol"]
        )
        values = wide.to_numpy().ravel(order="C")
        return pd.Series(values, index=index, name=name)

    def compute(self, bars: pd.DataFrame, specs: Iterable[FactorSpec]) -> pd.DataFrame:
        """Compute the factors described by *specs*.

        Returns a DataFrame indexed by ``MultiIndex(trade_date, symbol)`` with
        one column per factor, in the order given by *specs*.
        """
        spec_list = list(specs)
        if not spec_list:
            raise ValueError("specs must not be empty")

        inputs = self.build_inputs(bars)
        panels: list[pd.Series] = []
        for spec in spec_list:
            wide = self.registry.compute(spec.name, inputs, spec.params)
            if not isinstance(wide, pd.DataFrame):
                raise TypeError(
                    f"factor '{spec.name}' must return a DataFrame, got {type(wide).__name__}"
                )
            panels.append(self._stack(wide, spec.name))

        result = pd.concat(panels, axis=1)
        result.index.names = ["trade_date", "symbol"]
        return result.sort_index()

    def compute_all(self, bars: pd.DataFrame) -> pd.DataFrame:
        """Compute every registered built-in factor."""
        return self.compute(bars, [FactorSpec(name) for name in self.registry.names()])
