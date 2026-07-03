"""News and announcement provider contracts.

News is treated as first-class market data but kept separate from OHLCV.  The
critical anti-leakage rule is that every item must carry both:

* ``published_at`` -- when the source says the item became public;
* ``fetched_at`` -- when this system first observed it.

Backtests should use the later of those timestamps when deciding whether the
item was available to a strategy.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

import pandas as pd

NEWS_COLUMNS: list[str] = [
    "source",
    "source_id",
    "symbol",
    "title",
    "body",
    "url",
    "event_type",
    "sentiment_label",
    "sentiment_score",
    "relevance_score",
    "published_at",
    "fetched_at",
    "raw",
]


@runtime_checkable
class NewsProvider(Protocol):
    """Contract for news/announcement providers."""

    def stock_news(
        self,
        symbol: str,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> pd.DataFrame:
        """Return stock-specific news items using ``NEWS_COLUMNS``."""
        ...

    def announcements(
        self,
        symbol: str | None = None,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> pd.DataFrame:
        """Return company announcements using ``NEWS_COLUMNS``."""
        ...
