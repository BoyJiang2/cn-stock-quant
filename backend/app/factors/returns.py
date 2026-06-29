"""Forward returns with strict T+1 entry.

A signal formed using data up to and including trade date *t* can only be
acted upon on the next trading day (A-share T+1 rule).  Therefore the forward
return *label* attached to date *t* is the return from the close of *t+1*
(the entry) to the close of *t+1+h* (the exit after a *h*-day horizon):

    fwd_h(t) = close(t + 1 + h) / close(t + 1) - 1

This guarantees the label only depends on prices strictly after the signal
date -- no look-ahead, and robust to the T+1 execution constraint.
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd

from app.factors.core import FactorLab

__all__ = ["forward_returns", "DEFAULT_HORIZONS"]

DEFAULT_HORIZONS: tuple[int, ...] = (1, 5, 10, 20)


def _validate_bars(bars: pd.DataFrame) -> None:
    FactorLab._validate(bars)  # reuse the same validation rules


def forward_returns(
    bars: pd.DataFrame,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
) -> pd.DataFrame:
    """Compute T+1-entry forward returns for each horizon.

    Args:
        bars: Long-form DataFrame with the standard bar columns.
        horizons: Holding periods (in trading days) after the T+1 entry.

    Returns:
        A ``MultiIndex(trade_date, symbol)`` DataFrame with one column per
        horizon, named ``fwd_{h}d``.  The value at signal date *t* is the
        return from close(t+1) to close(t+1+h).  Trailing labels that would
        require future dates beyond the available data are ``NaN``.
    """
    _validate_bars(bars)
    horizon_list = [int(h) for h in horizons]
    if not horizon_list:
        raise ValueError("horizons must not be empty")
    if any(h < 1 for h in horizon_list):
        raise ValueError("all horizons must be >= 1")

    close = FactorLab._pivot(bars, "close").sort_index()

    panels: list[pd.Series] = []
    for h in horizon_list:
        # entry = close.shift(-1)  -> close of t+1
        # exit  = close.shift(-(h+1)) -> close of t+1+h
        entry = close.shift(-1)
        exit_ = close.shift(-(h + 1))
        wide = exit_ / entry - 1.0
        panels.append(FactorLab._stack(wide, f"fwd_{h}d"))

    result = pd.concat(panels, axis=1)
    result.index.names = ["trade_date", "symbol"]
    return result.sort_index()
