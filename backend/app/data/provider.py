"""Market data provider protocol.

Defines the contract that every data provider must fulfill so that
routes and services can swap implementations without changing call-sites.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class MarketDataProvider(Protocol):
    """Contract for market data providers.

    Implementations must return DataFrames with the documented columns.
    Routes instantiate concrete providers (e.g. :class:`AkShareProvider`)
    directly; the protocol exists so that providers are structurally
    substitutable.
    """

    def stock_list(self) -> pd.DataFrame:
        """Return the full A-share stock list.

        Returns:
            DataFrame with columns: ``symbol``, ``name``, ``exchange``,
            ``status``.
        """
        ...

    def daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """Return daily OHLCV bars for a single symbol.

        Args:
            symbol: Stock symbol in any conventional format (will be
                normalised internally).
            start_date: Inclusive start of the date range.
            end_date: Inclusive end of the date range.
            adjust: Adjustment type — ``"qfq"`` (forward), ``"hfq"``
                (backward), or ``""`` (none).

        Returns:
            DataFrame with columns: ``symbol``, ``trade_date``, ``open``,
            ``high``, ``low``, ``close``, ``volume``, ``amount``, ``adj``.
            Returns an empty DataFrame with those columns when no data
            exists for the range.

        Raises:
            DataProviderError: On fetch or validation failure.
                Subclasses include:

                * :class:`~app.data.errors.ProviderUnavailableError` —
                  both primary and fallback sources are unreachable.
                * :class:`~app.data.errors.DateParseError` —
                  the *trade_date* column could not be parsed.
                * :class:`~app.data.errors.MissingColumnError` —
                  required columns are absent.
                * :class:`~app.data.errors.NaNValueError` —
                  ``NaN`` found in a numeric column.
                * :class:`~app.data.errors.InvalidPriceError` —
                  non-positive OHLC prices.
                * :class:`~app.data.errors.OHLCContradictionError` —
                  OHLC logical invariants violated.
        """
        ...

    def index_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Return daily OHLCV bars for a market index.

        Args:
            symbol: Index symbol (e.g. ``"000300"`` for CSI 300).
            start_date: Inclusive start of the date range.
            end_date: Inclusive end of the date range.

        Returns:
            DataFrame with columns: ``symbol``, ``trade_date``, ``open``,
            ``high``, ``low``, ``close``, ``volume``, ``amount``, ``adj``.
            The ``adj`` column is set to ``"none"`` (indices are not
            adjusted).  Returns an empty DataFrame with those columns when
            no data exists for the range.

        Raises:
            DataProviderError: On fetch or validation failure.
                Subclasses include:

                * :class:`~app.data.errors.ProviderUnavailableError` —
                  the index source is unreachable.
                * :class:`~app.data.errors.DateParseError` —
                  the *trade_date* column could not be parsed.
                * :class:`~app.data.errors.MissingColumnError` —
                  required columns are absent.
                * :class:`~app.data.errors.NaNValueError` —
                  ``NaN`` found in a numeric column.
                * :class:`~app.data.errors.InvalidPriceError` —
                  non-positive OHLC prices.
                * :class:`~app.data.errors.OHLCContradictionError` —
                  OHLC logical invariants violated.
        """
        ...

    def trading_calendar(self) -> pd.DataFrame:
        """Return the A-share trading calendar.

        Returns:
            DataFrame with ``trade_date`` and ``is_open`` columns.
        """
        ...
