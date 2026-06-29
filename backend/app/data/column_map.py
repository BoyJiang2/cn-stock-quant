"""Column-name standardisation for market data providers.

AkShare's ``stock_zh_a_hist`` returns Chinese column names (日期, 开盘, …)
while ``stock_zh_a_daily`` returns English names (date, open, …).  This
module maps every known variant to a single canonical set so downstream
code never has to guess.
"""

from __future__ import annotations

import pandas as pd

# Canonical output columns in display order.
STANDARD_COLUMNS: list[str] = [
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "adj",
]

# Subset used for price validation.
PRICE_COLUMNS: list[str] = ["open", "high", "low", "close"]

# Subset that *must* be present before symbol / adj enrichment.
REQUIRED_COLUMNS: list[str] = [
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
]

# --------------------------------------------------------------------------
# Every canonical column maps to a *priority-ordered* list of known aliases.
# The first alias found in a source DataFrame wins.
# --------------------------------------------------------------------------

COLUMN_ALIASES: dict[str, list[str]] = {
    "trade_date": ["日期", "date", "trade_date", "time", "day", "tradeDate", "trade-date"],
    "open":       ["开盘", "open", "开盘价", "open_price", "openPrice"],
    "high":       ["最高", "high", "最高价", "high_price", "highPrice"],
    "low":        ["最低", "low", "最低价", "low_price", "lowPrice"],
    "close":      ["收盘", "close", "收盘价", "close_price", "closePrice"],
    "volume":     ["成交量", "volume", "vol", "trade_volume", "tradeVolume"],
    "amount":     ["成交额", "amount", "amt", "trade_amount", "tradeAmount"],
    "symbol":     ["股票代码", "code", "symbol", "stock_code", "stockCode"],
    "adj":        ["复权类型", "adj", "adjust", "adjust_type", "adjustType", "复权"],
}


def build_column_map(source_columns: list[str]) -> dict[str, str]:
    """Build a rename dictionary ``{source_name: canonical_name}``.

    For each canonical column the *first* matching alias present in
    *source_columns* is used.  Aliases are tried in the order listed in
    :data:`COLUMN_ALIASES`.
    """
    mapping: dict[str, str] = {}
    source_set = set(source_columns)
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in source_set and alias not in mapping:
                mapping[alias] = canonical
                break
    return mapping


def apply_column_map(df: pd.DataFrame, mapping: dict[str, str] | None = None) -> pd.DataFrame:
    """Rename columns using *mapping* and return a frame with only standard columns.

    If *mapping* is ``None`` it is built automatically from ``df.columns``.
    """
    if mapping is None:
        mapping = build_column_map(list(df.columns))

    renamed = df.rename(columns=mapping)

    # Keep every standard column that was successfully mapped.
    available = [c for c in STANDARD_COLUMNS if c in renamed.columns]
    return renamed[available]
