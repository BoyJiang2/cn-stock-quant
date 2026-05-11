from datetime import date
from typing import Protocol

import pandas as pd


class MarketDataProvider(Protocol):
    def stock_list(self) -> pd.DataFrame:
        """Return A-share stock list with at least symbol and name columns."""

    def daily_bars(self, symbol: str, start_date: date, end_date: date, adjust: str = "qfq") -> pd.DataFrame:
        """Return daily OHLCV bars for one symbol."""

