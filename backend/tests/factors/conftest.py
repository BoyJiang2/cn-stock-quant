"""Shared fixtures and builders for factor-lab tests."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

import pandas as pd
import pytest

from app.factors import FactorLab


@pytest.fixture
def factor_lab() -> FactorLab:
    return FactorLab()


def _dates(n: int, start: date = date(2024, 1, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


@pytest.fixture
def build_bars():
    """Return a flexible synthetic-bars builder.

    Usage::

        bars = build_bars({"000001": [10, 11, 12, ...]})

    Optional per-symbol overrides accept dicts ``{symbol: [values]}``:
    ``high``, ``low``, ``volume``, ``amount``.  When an override is absent,
    sensible defaults are used (high/low from ``spread``, amount = close*volume).
    """

    def _build(
        close_by_symbol: dict[str, Iterable[float]],
        *,
        start: date = date(2024, 1, 1),
        spread: float = 0.0,
        volume: float = 100_000.0,
        amount: float | None = None,
        high: dict[str, Iterable[float]] | None = None,
        low: dict[str, Iterable[float]] | None = None,
        volume_by_symbol: dict[str, Iterable[float]] | None = None,
        amount_by_symbol: dict[str, Iterable[float]] | None = None,
    ) -> pd.DataFrame:
        n = max(len(list(v)) for v in close_by_symbol.values())
        ds = _dates(n, start)
        rows: list[dict] = []
        for sym, prices in close_by_symbol.items():
            prices = list(prices)
            highs = list(high[sym]) if high else [p * (1 + spread) for p in prices]
            lows = list(low[sym]) if low else [p * (1 - spread) for p in prices]
            vols = list(volume_by_symbol[sym]) if volume_by_symbol else [volume] * len(prices)
            if amount_by_symbol and sym in amount_by_symbol:
                amts = list(amount_by_symbol[sym])
            elif amount is not None:
                amts = [amount] * len(prices)
            else:
                amts = [p * v for p, v in zip(prices, vols)]
            for i, p in enumerate(prices):
                rows.append(
                    {
                        "symbol": sym,
                        "trade_date": ds[i],
                        "open": p,
                        "high": highs[i],
                        "low": lows[i],
                        "close": p,
                        "volume": vols[i],
                        "amount": amts[i],
                    }
                )
        return pd.DataFrame(rows)

    return _build
