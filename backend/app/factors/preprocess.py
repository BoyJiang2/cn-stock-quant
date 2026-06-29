"""Cross-sectional (per-trade-date) preprocessing.

Two complementary transforms are provided, both applied **per trade date**
across the stock universe:

* :func:`winsorize_mad`   -- robust outlier clipping using the median and the
  median absolute deviation (MAD).
* :func:`percentile_rank` -- map each value to its cross-sectional
  percentile in ``[0, 1]``.

A convenience :func:`standardize_robust` chains MAD winsorisation with a
robust z-score (median centred, scaled by ``1.4826 * MAD``), and
:func:`preprocess` returns a structured bundle suitable for evaluation.

All transforms accept either a ``MultiIndex(trade_date, symbol)`` DataFrame
(one or more factor columns) or a single Series.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "winsorize_mad",
    "percentile_rank",
    "standardize_robust",
    "preprocess",
]

_MAD_SCALE = 1.4826  # makes MAD a consistent estimator of the std for normal data


def _to_frame(df: pd.DataFrame | pd.Series) -> pd.DataFrame:
    if isinstance(df, pd.Series):
        df = df.to_frame()
    return df


def _per_date_transform(df: pd.DataFrame, func) -> pd.DataFrame:
    """Apply *func* (Series -> Series) to each column within each trade_date group."""
    df = _to_frame(df)
    out: dict[str, pd.Series] = {}
    for col in df.columns:
        grouped = df.groupby(level="trade_date", group_keys=False, sort=False)[col]
        out[col] = grouped.transform(func)
    return pd.DataFrame(out, index=df.index)


def winsorize_mad(df: pd.DataFrame | pd.Series, k: float = 3.0) -> pd.DataFrame:
    """Per-date MAD winsorisation.

    For each trade date and each factor column the values are clipped to
    ``[median - k * 1.4826 * MAD, median + k * 1.4826 * MAD]``.  Dates with no
    dispersion (MAD == 0) or all-NaN cross-sections are returned unchanged.
    """
    if k <= 0:
        raise ValueError("k must be > 0")

    def _clip(col: pd.Series) -> pd.Series:
        finite = col.dropna()
        if finite.empty:
            return col
        med = finite.median()
        mad = (finite - med).abs().median()
        scale = _MAD_SCALE * mad
        if scale <= 0 or np.isnan(scale):
            return col
        lower = med - k * scale
        upper = med + k * scale
        return col.clip(lower, upper)

    return _per_date_transform(_to_frame(df), _clip)


def percentile_rank(df: pd.DataFrame | pd.Series) -> pd.DataFrame:
    """Per-date cross-sectional percentile rank in ``[0, 1]``.

    Higher original values map to higher percentiles.  Uses average ranks for
    ties, matching the rest of the codebase.
    """
    df = _to_frame(df)
    grouped = df.groupby(level="trade_date", group_keys=False, sort=False)
    return grouped.rank(pct=True, method="average")


def standardize_robust(df: pd.DataFrame | pd.Series, k: float = 3.0) -> pd.DataFrame:
    """MAD winsorise then robust z-score per date.

    ``(x - median) / (1.4826 * MAD)``.  Dates with no dispersion are centred
    on the median (yielding 0) but not scaled.
    """

    def _z(col: pd.Series) -> pd.Series:
        finite = col.dropna()
        if finite.empty:
            return col
        med = finite.median()
        mad = (finite - med).abs().median()
        scale = _MAD_SCALE * mad
        if scale <= 0 or np.isnan(scale):
            return col - med
        return (col - med) / scale

    return _per_date_transform(winsorize_mad(df, k=k), _z)


def preprocess(
    df: pd.DataFrame | pd.Series,
    *,
    winsorize: bool = True,
    k: float = 3.0,
    standardize: bool = True,
) -> dict[str, pd.DataFrame]:
    """Run the standard preprocessing pipeline and return intermediate stages.

    Returns a dict with keys:

    * ``raw``           -- the input unchanged (as a DataFrame).
    * ``winsorized``    -- after MAD clipping (or ``raw`` if skipped).
    * ``standardized``  -- robust z-score of the winsorised values (or the
      winsorised frame if standardisation is skipped).
    * ``percentile``    -- percentile rank of the standardised frame.
    """
    raw = _to_frame(df)
    win = winsorize_mad(raw, k=k) if winsorize else raw
    std = standardize_robust(win, k=k) if standardize else win
    pct = percentile_rank(std)
    return {
        "raw": raw,
        "winsorized": win,
        "standardized": std,
        "percentile": pct,
    }
