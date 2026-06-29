"""AkShare-backed provider for Point-in-Time security metadata.

Owned by GLM "Module A" (see ``backend/glm-point-in-time-plan.md``
section 9).  This provider fetches:

* the **current** ST / *ST list        → :meth:`current_st_list`
* the **historical** delisted names    → :meth:`sh_delist`, :meth:`sz_delist`
* the **current** index constituents   → :meth:`index_constituents_current`
* the **current** index weights        → :meth:`index_weights_current`
* the **listing-date** enriched stock
  list                                → :meth:`stock_list_with_list_date`

AkShare does **not** expose historical ST intervals or historical index
rebalance lists; that gap is documented in the plan (section 3.2 / 3.3)
and handled by the synchronisation layer, which writes what we *can*
fetch today as interval rows whose ``valid_from = today`` and marks the
``confidence`` down to ``"medium"`` when ``announced_at`` is missing.

All fetchers return :class:`pandas.DataFrame` with English column names
so :mod:`app.data.pit_sync` can stay source-agnostic.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.data.errors import DataProviderError, ProviderUnavailableError
from app.data.symbols import normalize_a_share_symbol

__all__ = [
    "AkSharePitProvider",
    "PitProvider",
]


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class PitProvider:
    """Minimal structural protocol for PIT providers.

    The concrete :class:`AkSharePitProvider` satisfies this; tests can
    substitute a fake that returns hard-coded DataFrames.
    """

    def current_st_list(self) -> pd.DataFrame: ...
    def sh_delist(self) -> pd.DataFrame: ...
    def sz_delist(self) -> pd.DataFrame: ...
    def stock_list_with_list_date(self) -> pd.DataFrame: ...
    def index_constituents_current(self, index_symbol: str) -> pd.DataFrame: ...
    def index_weights_current(self, index_symbol: str) -> pd.DataFrame: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ST_PREFIX_TO_STATUS: list[tuple[str, str]] = [
    ("S*ST", "st_star"),
    ("*ST", "st_star"),
    ("SST", "sst"),
    ("ST", "st"),
]


def _classify_st_name(name: str) -> str:
    """Map an ST-prefixed display name to a canonical status code.

    Returns one of ``st`` / ``sst`` / ``st_star``. Both ``*ST`` and
    the rare ``S*ST`` form map to ``st_star``.
    """
    upper = (name or "").upper().lstrip()
    for prefix, status in _ST_PREFIX_TO_STATUS:
        if upper.startswith(prefix):
            return status
    return "st"


def _is_st_name(name: str) -> bool:
    upper = (name or "").upper().lstrip()
    for prefix in ("S*ST", "*ST", "SST", "ST"):
        if not upper.startswith(prefix):
            continue
        if len(upper) == len(prefix):
            return True
        next_character = upper[len(prefix)]
        if next_character.isspace() or not next_character.isascii():
            return True
    return False


def _guess_exchange(symbol: str) -> str:
    if symbol.startswith("920"):
        return "BJ"
    if symbol.startswith(("6", "9")):
        return "SH"
    if symbol.startswith(("4", "8")):
        return "BJ"
    return "SZ"


def _to_date(value: Any) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, date) and not isinstance(value, pd.Timestamp):
        return value
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# AkShare implementation
# ---------------------------------------------------------------------------


class AkSharePitProvider:
    """AkShare-backed implementation of :class:`PitProvider`."""

    # --- ST ---------------------------------------------------------------

    def current_st_list(self) -> pd.DataFrame:
        """Return the current ST / *ST list as one row per symbol.

        Output columns: ``symbol``, ``name``, ``status``, ``source``.
        ``valid_from`` is intentionally NOT set here — the sync layer
        stamps it with "today" (or with the announcement date when
        known) so the provider stays a pure data fetcher.
        """
        import akshare as ak

        source = "akshare.st_em"
        try:
            raw = ak.stock_zh_a_st_em()
        except Exception as main_error:
            try:
                stock_list = ak.stock_info_a_code_name()
                filtered = stock_list[
                    stock_list["name"].astype(str).map(_is_st_name)
                ]
                raw = pd.DataFrame(
                    {
                        "sequence": range(1, len(filtered) + 1),
                        "symbol": filtered["code"].astype(str),
                        "name": filtered["name"].astype(str),
                    }
                )
                source = "akshare.stock_info_a_code_name_fallback"
            except Exception as fallback_error:
                raise ProviderUnavailableError(
                    symbol="st_list",
                    main_error=main_error,
                    fallback_error=fallback_error,
                    source="akshare.st_list",
                ) from fallback_error
        if raw is None or raw.empty:
            return pd.DataFrame(columns=["symbol", "name", "status", "source"])

        # AkShare columns: 序号, 代码, 名称, ... — use the code/name pair.
        symbol_col = "代码" if "代码" in raw.columns else raw.columns[1]
        name_col = "名称" if "名称" in raw.columns else raw.columns[2]
        rows: list[dict[str, Any]] = []
        for _, row in raw.iterrows():
            try:
                symbol = normalize_a_share_symbol(str(row[symbol_col]))
            except ValueError:
                continue
            name = str(row[name_col])
            rows.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "status": _classify_st_name(name),
                    "source": source,
                }
            )
        return pd.DataFrame(rows, columns=["symbol", "name", "status", "source"])

    # --- Delisted ---------------------------------------------------------

    def sh_delist(self) -> pd.DataFrame:
        """Return Shanghai delisted securities.

        Output columns: ``symbol``, ``name``, ``list_date``,
        ``delist_date``, ``source``.
        """
        import akshare as ak

        try:
            raw = ak.stock_info_sh_delist()
        except Exception as exc:
            raise ProviderUnavailableError(
                symbol="sh_delist",
                main_error=exc,
                source="akshare.stock_info_sh_delist",
            ) from exc
        return _normalise_delist(
            raw,
            symbol_col="公司代码",
            name_col="公司简称",
            list_col="上市日期",
            delist_col="暂停上市日期",
            source="akshare.sh_delist",
        )

    def sz_delist(self) -> pd.DataFrame:
        """Return Shenzhen delisted securities.

        Output columns: ``symbol``, ``name``, ``list_date``,
        ``delist_date``, ``source``.
        """
        import akshare as ak

        try:
            raw = ak.stock_info_sz_delist()
        except Exception as exc:
            raise ProviderUnavailableError(
                symbol="sz_delist",
                main_error=exc,
                source="akshare.stock_info_sz_delist",
            ) from exc
        return _normalise_delist(
            raw,
            symbol_col="证券代码",
            name_col="证券简称",
            list_col="上市日期",
            delist_col="终止上市日期",
            source="akshare.sz_delist",
        )

    # --- Listing dates ----------------------------------------------------

    def stock_list_with_list_date(self) -> pd.DataFrame:
        """Return A-share list with listing dates.

        Combines ``ak.stock_info_sh_name_code`` (Shanghai main / STAR /
        B) with ``ak.stock_info_sz_name_code`` (Shenzhen main / ChiNext /
        B).  Output columns: ``symbol``, ``name``, ``exchange``,
        ``list_date``, ``source``.

        Listing dates that cannot be parsed are returned as ``NaT`` /
        ``None`` — the sync layer is responsible for back-filling from
        the earliest ``DailyBar.trade_date`` when this happens.
        """
        import akshare as ak

        frames: list[pd.DataFrame] = []

        # Shanghai has multiple boards; iterate the documented symbols.
        for board in ("主板A股", "科创板", "主板B股"):
            try:
                raw = ak.stock_info_sh_name_code(symbol=board)
            except Exception:
                continue
            if raw is None or raw.empty:
                continue
            frames.append(
                pd.DataFrame(
                    {
                        "symbol": raw["证券代码"].astype(str).str.zfill(6),
                        "name": raw["证券简称"].astype(str),
                        "exchange": "SH",
                        "list_date": raw["上市日期"],
                        "source": "akshare.sh_name_code",
                    }
                )
            )

        # Shenzhen has multiple boards too.
        for board in ("A股列表", "B股列表", "创业板列表"):
            try:
                raw = ak.stock_info_sz_name_code(symbol=board)
            except Exception:
                continue
            if raw is None or raw.empty:
                continue
            frames.append(
                pd.DataFrame(
                    {
                        "symbol": raw["A股代码"].astype(str).str.zfill(6)
                        if "A股代码" in raw.columns
                        else raw.iloc[:, 1].astype(str).str.zfill(6),
                        "name": raw["A股简称"].astype(str)
                        if "A股简称" in raw.columns
                        else raw.iloc[:, 2].astype(str),
                        "exchange": "SZ",
                        "list_date": raw["A股上市日期"]
                        if "A股上市日期" in raw.columns
                        else pd.NaT,
                        "source": "akshare.sz_name_code",
                    }
                )
            )

        if not frames:
            try:
                raw = ak.stock_info_a_code_name()
            except Exception:
                return pd.DataFrame(
                    columns=["symbol", "name", "exchange", "list_date", "source"]
                )
            fallback = raw.rename(columns={"code": "symbol", "name": "name"}).copy()
            fallback["symbol"] = fallback["symbol"].astype(str).str.zfill(6)
            fallback["exchange"] = fallback["symbol"].map(_guess_exchange)
            fallback["list_date"] = None
            fallback["source"] = "akshare.stock_info_a_code_name"
            return fallback[
                ["symbol", "name", "exchange", "list_date", "source"]
            ].drop_duplicates("symbol")
        combined = pd.concat(frames, ignore_index=True)
        combined = combined.drop_duplicates(subset=["symbol"], keep="first")
        return combined.reset_index(drop=True)

    # --- Index constituents / weights ------------------------------------

    def index_constituents_current(self, index_symbol: str) -> pd.DataFrame:
        """Return the *current* constituent list for *index_symbol*.

        Tries the CSI official endpoint first (returns a snapshot date
        in the ``日期`` column) and falls back to the Sina generic
        endpoint.  Output columns: ``index_symbol``, ``symbol``,
        ``name``, ``snapshot_date``, ``source``.
        """
        import akshare as ak

        target = normalize_a_share_symbol(index_symbol)
        errors: list[tuple[str, Exception]] = []

        # Primary: csindex (has dates, used by 000300/000905/000852).
        try:
            raw = ak.index_stock_cons_csindex(symbol=target)
        except Exception as exc:
            raw = None
            errors.append(("akshare.index_stock_cons_csindex", exc))

        if isinstance(raw, pd.DataFrame) and not raw.empty:
            snapshot = raw["日期"].iloc[0] if "日期" in raw.columns else None
            snapshot_date = _to_date(snapshot)
            return pd.DataFrame(
                {
                    "index_symbol": target,
                    "symbol": raw["成分券代码"].astype(str).str.zfill(6),
                    "name": raw["成分券名称"].astype(str),
                    "snapshot_date": snapshot_date,
                    "source": "akshare.index_stock_cons_csindex",
                }
            )

        # Fallback: Sina generic (used for 399006 etc.).
        try:
            raw = ak.index_stock_cons(symbol=target)
        except Exception as exc:
            errors.append(("akshare.index_stock_cons", exc))
            raise ProviderUnavailableError(
                symbol=target,
                main_error=RuntimeError(
                    "; ".join(f"{src}: {err}" for src, err in errors)
                ),
                source="akshare.index_constituents",
            ) from exc

        if raw is None or raw.empty:
            return pd.DataFrame(
                columns=["index_symbol", "symbol", "name", "snapshot_date", "source"]
            )
        snapshot = raw["纳入日期"].iloc[0] if "纳入日期" in raw.columns else None
        snapshot_date = _to_date(snapshot)
        return pd.DataFrame(
            {
                "index_symbol": target,
                "symbol": raw["品种代码"].astype(str).str.zfill(6),
                "name": raw["品种名称"].astype(str),
                "snapshot_date": snapshot_date,
                "source": "akshare.index_stock_cons_sina",
            }
        )

    def index_weights_current(self, index_symbol: str) -> pd.DataFrame:
        """Return the *current* weight snapshot for *index_symbol*.

        Output columns: ``index_symbol``, ``symbol``, ``name``,
        ``trade_date``, ``weight``, ``source``.
        """
        import akshare as ak

        target = normalize_a_share_symbol(index_symbol)
        try:
            raw = ak.index_stock_cons_weight_csindex(symbol=target)
        except Exception as exc:
            raise ProviderUnavailableError(
                symbol=target,
                main_error=exc,
                source="akshare.index_stock_cons_weight_csindex",
            ) from exc
        if raw is None or raw.empty:
            return pd.DataFrame(
                columns=["index_symbol", "symbol", "name", "trade_date", "weight", "source"]
            )
        snapshot = raw["日期"].iloc[0] if "日期" in raw.columns else None
        trade_date = _to_date(snapshot)
        return pd.DataFrame(
            {
                "index_symbol": target,
                "symbol": raw["成分券代码"].astype(str).str.zfill(6),
                "name": raw["成分券名称"].astype(str),
                "trade_date": trade_date,
                "weight": pd.to_numeric(raw["权重"], errors="coerce") / 100.0,
                "source": "akshare.index_stock_cons_weight_csindex",
            }
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_delist(
    raw: pd.DataFrame,
    *,
    symbol_col: str,
    name_col: str,
    list_col: str,
    delist_col: str,
    source: str,
) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(
            columns=["symbol", "name", "list_date", "delist_date", "source"]
        )
    rows: list[dict[str, Any]] = []
    for _, row in raw.iterrows():
        try:
            symbol = normalize_a_share_symbol(str(row[symbol_col]))
        except ValueError:
            continue
        rows.append(
            {
                "symbol": symbol,
                "name": str(row[name_col]),
                "list_date": _to_date(row[list_col]) if list_col in raw.columns else None,
                "delist_date": _to_date(row[delist_col]) if delist_col in raw.columns else None,
                "source": source,
            }
        )
    return pd.DataFrame(rows, columns=["symbol", "name", "list_date", "delist_date", "source"])
