"""Validation rules for daily-bar DataFrames.

Every rule raises a typed :class:`~app.data.errors.DataProviderError`
subclass so callers can handle specific failure modes.
"""

from __future__ import annotations

import pandas as pd

from app.data.column_map import PRICE_COLUMNS, REQUIRED_COLUMNS
from app.data.errors import (
    InvalidPriceError,
    MissingColumnError,
    NaNValueError,
    OHLCContradictionError,
)


def validate_daily_bars(df: pd.DataFrame, source_label: str = "") -> None:
    """Validate *df* against data-quality rules.

    Rules (in order):
        1. Every column in :data:`~app.data.column_map.REQUIRED_COLUMNS`
           must be present.
        2. No ``NaN`` values are allowed in those columns.
        3. ``open``, ``high``, ``low``, ``close`` must all be **strictly
           positive**.
        4. OHLC logical invariants must hold:
           ``high >= max(open, close, low)`` and
           ``low <= min(open, close, high)``.

    Zero volume is explicitly permitted.

    Args:
        df: A DataFrame that has already been column-normalised.
        source_label: Human-readable label for the data source, included in
            every raised exception.

    Raises:
        MissingColumnError: One or more required columns are absent.
        NaNValueError: A required column contains ``NaN``.
        InvalidPriceError: A price column contains values ≤ 0.
        OHLCContradictionError: OHLC records break logical constraints.
    """
    # 1. Missing columns ---------------------------------------------------
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise MissingColumnError(
            missing=missing,
            present=list(df.columns),
            source=source_label,
        )

    # 2. NaN checks --------------------------------------------------------
    for col in REQUIRED_COLUMNS:
        nan_mask = df[col].isna()
        if nan_mask.any():
            row_indices = _row_indices(df, nan_mask)
            raise NaNValueError(
                column=col,
                row_indices=row_indices,
                source=source_label,
            )

    # 3. Non-positive prices (volume may be zero) --------------------------
    for col in PRICE_COLUMNS:
        bad_mask = df[col] <= 0
        if bad_mask.any():
            row_indices = _row_indices(df, bad_mask)
            raise InvalidPriceError(
                column=col,
                row_indices=row_indices,
                source=source_label,
            )

    # 4. OHLC contradictions -----------------------------------------------
    contradictions: list[str] = []

    # high must be >= every other price
    high_violation = df["high"] < df[["open", "close", "low"]].max(axis=1)
    if high_violation.any():
        bad = _row_indices(df, high_violation)
        contradictions.append(
            f"high < max(open, close, low) at row indices {bad}"
        )

    # low must be <= every other price
    low_violation = df["low"] > df[["open", "close", "high"]].min(axis=1)
    if low_violation.any():
        bad = _row_indices(df, low_violation)
        contradictions.append(
            f"low > min(open, close, high) at row indices {bad}"
        )

    if contradictions:
        raise OHLCContradictionError(
            contradictions=contradictions,
            source=source_label,
        )


def _row_indices(df: pd.DataFrame, mask: pd.Series) -> list[int]:
    """Return integer row positions where *mask* is True."""
    return df.index[mask].tolist()
