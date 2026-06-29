"""Categorized exceptions for data provider failures.

Each exception carries the source label so callers can attribute the failure
to the main API, the fallback, or a validation rule.
"""

from __future__ import annotations


class DataProviderError(Exception):
    """Base exception for all data provider errors.

    Attributes:
        source: Label identifying the data source or validation step.
    """

    def __init__(self, message: str, source: str = "") -> None:
        super().__init__(message)
        self.source = source


class MissingColumnError(DataProviderError):
    """One or more required columns are absent from the source DataFrame.

    Attributes:
        missing: Names of required columns that were not found.
        present: Names of columns that *were* present (for diagnostics).
    """

    def __init__(self, missing: list[str], present: list[str], source: str = "") -> None:
        self.missing = missing
        self.present = present
        message = f"Missing required columns: {missing}. Present columns: {present}"
        super().__init__(message, source)


class NaNValueError(DataProviderError):
    """NaN values found in a required numeric column.

    Attributes:
        column: The column that contains NaN values.
        row_indices: Zero-based integer positions of the offending rows.
    """

    def __init__(self, column: str, row_indices: list[int], source: str = "") -> None:
        self.column = column
        self.row_indices = row_indices
        message = f"NaN values found in column '{column}' at row indices {row_indices}"
        super().__init__(message, source)


class InvalidPriceError(DataProviderError):
    """Non-positive price detected in an OHLC column.

    open, high, low, and close must all be strictly greater than zero.
    """

    def __init__(self, column: str, row_indices: list[int], source: str = "") -> None:
        self.column = column
        self.row_indices = row_indices
        message = f"Non-positive prices in column '{column}' at row indices {row_indices}"
        super().__init__(message, source)


class OHLCContradictionError(DataProviderError):
    """OHLC values violate logical constraints.

    Required invariants:
        high >= max(open, close, low)
        low  <= min(open, close, high)
    """

    def __init__(self, contradictions: list[str], source: str = "") -> None:
        self.contradictions = contradictions
        message = f"OHLC contradictions: {'; '.join(contradictions)}"
        super().__init__(message, source)


class ProviderUnavailableError(DataProviderError):
    """Both primary and fallback data sources are unavailable.

    Raised when the main API (after retries) and the fallback API both
    fail to return data.  The original exceptions from each source are
    preserved so callers can inspect the root causes.

    Attributes:
        symbol: The stock symbol that was being requested.
        main_error: The exception raised by the primary source, or ``None``.
        fallback_error: The exception raised by the fallback source, or ``None``.
    """

    def __init__(
        self,
        symbol: str,
        main_error: Exception | None = None,
        fallback_error: Exception | None = None,
        source: str = "",
    ) -> None:
        self.symbol = symbol
        self.main_error = main_error
        self.fallback_error = fallback_error
        parts: list[str] = []
        if main_error is not None:
            parts.append(f"main(stock_zh_a_hist): {main_error}")
        if fallback_error is not None:
            parts.append(f"fallback(stock_zh_a_daily): {fallback_error}")
        detail = "; ".join(parts) if parts else "unknown error"
        message = f"AkShare daily bars request failed for {symbol}: {detail}"
        super().__init__(message, source)


class DateParseError(DataProviderError):
    """Failed to parse the *trade_date* column to Python :class:`date` objects.

    Raised when ``pd.to_datetime()`` cannot interpret the values in the
    trade-date column returned by the data source.
    """

    def __init__(self, message: str, source: str = "") -> None:
        super().__init__(message, source)
