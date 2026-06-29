"""AkShare-based implementation of the :class:`MarketDataProvider` protocol.

Uses two AkShare endpoints:

* **Primary** — ``ak.stock_zh_a_hist`` (3 retries with back-off).
* **Fallback** — ``ak.stock_zh_a_daily`` (single attempt).

Both sources are column-normalised to the canonical set:
``symbol / trade_date / open / high / low / close / volume / amount / adj``,
and every returned row is validated.
"""

from __future__ import annotations

from datetime import date
from time import sleep

import pandas as pd

from app.data.column_map import (
    STANDARD_COLUMNS,
    apply_column_map,
    build_column_map,
)
from app.data.errors import DateParseError, ProviderUnavailableError
from app.data.symbols import normalize_a_share_symbol
from app.data.validation import validate_daily_bars


class AkShareProvider:
    """Market data provider backed by the AkShare library."""

    def trading_calendar(self) -> pd.DataFrame:
        """Return the historical A-share open-day calendar."""
        import akshare as ak

        try:
            raw = ak.tool_trade_date_hist_sina()
        except Exception as exc:
            raise ProviderUnavailableError(
                symbol="calendar",
                main_error=exc,
                source="akshare.tool_trade_date_hist_sina",
            ) from exc
        if raw is None or raw.empty:
            return pd.DataFrame(columns=["trade_date", "is_open"])
        date_column = "trade_date" if "trade_date" in raw.columns else raw.columns[0]
        try:
            dates = pd.to_datetime(raw[date_column]).dt.date
        except Exception as exc:
            raise DateParseError(
                f"Failed to parse trading calendar: {exc}",
                source="akshare.tool_trade_date_hist_sina",
            ) from exc
        return pd.DataFrame({"trade_date": dates, "is_open": True}).drop_duplicates("trade_date")

    def index_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Return daily OHLCV bars with Eastmoney/Sina/Tencent fallback.

        Indices are not adjusted, so ``adj`` is always ``"none"``.
        """
        import akshare as ak

        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")
        errors: list[tuple[str, Exception]] = []

        raw: pd.DataFrame | None = None
        empty_result_seen = False
        source_label = "akshare.index_zh_a_hist"
        for attempt in range(3):
            try:
                candidate = ak.index_zh_a_hist(
                    symbol=symbol,
                    period="daily",
                    start_date=start_str,
                    end_date=end_str,
                )
                if isinstance(candidate, pd.DataFrame):
                    if candidate.empty:
                        empty_result_seen = True
                    else:
                        raw = candidate
                        break
                elif candidate is None:
                    empty_result_seen = True
                else:
                    errors.append(
                        (source_label, TypeError(f"unexpected result type: {type(candidate).__name__}"))
                    )
                if raw is not None:
                    break
            except Exception as exc:
                errors.append((source_label, exc))
                sleep(0.8 * (attempt + 1))

        prefixed_symbol = _akshare_prefixed_index_symbol(symbol)
        if raw is None:
            source_label = "akshare.stock_zh_index_daily"
            try:
                candidate = ak.stock_zh_index_daily(symbol=prefixed_symbol)
                if isinstance(candidate, pd.DataFrame):
                    if candidate.empty:
                        empty_result_seen = True
                    else:
                        raw = candidate
                elif candidate is None:
                    empty_result_seen = True
                else:
                    errors.append(
                        (source_label, TypeError(f"unexpected result type: {type(candidate).__name__}"))
                    )
            except Exception as exc:
                errors.append((source_label, exc))

        if raw is None:
            source_label = "akshare.stock_zh_index_daily_tx"
            try:
                candidate = ak.stock_zh_index_daily_tx(symbol=prefixed_symbol)
                if isinstance(candidate, pd.DataFrame):
                    if candidate.empty:
                        empty_result_seen = True
                    else:
                        raw = candidate
                elif candidate is None:
                    empty_result_seen = True
                else:
                    errors.append(
                        (source_label, TypeError(f"unexpected result type: {type(candidate).__name__}"))
                    )
            except Exception as exc:
                errors.append((source_label, exc))

        if raw is None:
            if empty_result_seen:
                return pd.DataFrame(columns=STANDARD_COLUMNS)
            detail = "; ".join(f"{source}: {error}" for source, error in errors)
            raise ProviderUnavailableError(
                symbol=symbol,
                main_error=RuntimeError(detail or "all index providers failed"),
                source="akshare.index",
            )

        col_map = build_column_map(list(raw.columns))
        frame = apply_column_map(raw, col_map)
        # Sina omits amount; Tencent omits volume. These fields are optional
        # for index benchmarking, so fill the unavailable side with zero.
        if "volume" not in frame.columns:
            frame["volume"] = 0.0
        if "amount" not in frame.columns:
            frame["amount"] = 0.0
        frame["symbol"] = symbol
        frame["adj"] = "none"
        if "trade_date" in frame.columns:
            try:
                frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
            except Exception as exc:
                raise DateParseError(
                    f"Failed to parse trade_date column: {exc}",
                    source=source_label,
                ) from exc
        frame = frame[
            (frame["trade_date"] >= start_date)
            & (frame["trade_date"] <= end_date)
        ].copy()
        if frame.empty:
            return pd.DataFrame(columns=STANDARD_COLUMNS)

        validate_daily_bars(frame, source_label=source_label)
        return frame[STANDARD_COLUMNS]

    def stock_list(self) -> pd.DataFrame:
        """Return A-share stock list via ``ak.stock_info_a_code_name``."""
        import akshare as ak

        raw = ak.stock_info_a_code_name()
        frame = raw.rename(columns={"code": "symbol", "name": "name"})
        frame["exchange"] = frame["symbol"].map(_guess_exchange)
        frame["status"] = "active"
        return frame[["symbol", "name", "exchange", "status"]]

    def daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """Return daily OHLCV bars for *symbol*.

        See :meth:`MarketDataProvider.daily_bars` for the column contract.
        """
        import akshare as ak

        normalized_symbol = normalize_a_share_symbol(symbol)
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")

        # ── Primary source: stock_zh_a_hist (3 retries) ──────────────────
        raw = None
        main_error: Exception | None = None
        for attempt in range(3):
            try:
                raw = ak.stock_zh_a_hist(
                    symbol=normalized_symbol,
                    period="daily",
                    start_date=start_str,
                    end_date=end_str,
                    adjust=adjust,
                )
                break
            except Exception as exc:
                main_error = exc
                sleep(0.8 * (attempt + 1))

        source_label = "akshare.stock_zh_a_hist"

        # ── Fallback: stock_zh_a_daily ───────────────────────────────────
        fallback_error: Exception | None = None
        if raw is None:
            try:
                raw = ak.stock_zh_a_daily(
                    symbol=_akshare_prefixed_symbol(normalized_symbol),
                    start_date=start_str,
                    end_date=end_str,
                    adjust=adjust,
                )
                source_label = "akshare.stock_zh_a_daily"
            except Exception as exc:
                fallback_error = exc

        # ── Both sources failed ──────────────────────────────────────────
        if raw is None:
            raise _build_dual_error(
                normalized_symbol, main_error, fallback_error
            )

        # ── Empty result ─────────────────────────────────────────────────
        if raw.empty:
            return pd.DataFrame(columns=STANDARD_COLUMNS)

        # ── Column normalisation ─────────────────────────────────────────
        col_map = build_column_map(list(raw.columns))
        frame = apply_column_map(raw, col_map)

        # ── Enrich with symbol / adj / typed trade_date ──────────────────
        frame["symbol"] = normalized_symbol
        frame["adj"] = adjust
        if "trade_date" in frame.columns:
            try:
                frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
            except Exception as exc:
                raise DateParseError(
                    f"Failed to parse trade_date column: {exc}",
                    source=source_label,
                ) from exc

        # ── Validate ─────────────────────────────────────────────────────
        validate_daily_bars(frame, source_label=source_label)

        # ── Return canonical column set ──────────────────────────────────
        return frame[STANDARD_COLUMNS]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _guess_exchange(symbol: str) -> str:
    """Map a 6-digit symbol to its exchange abbreviation."""
    if symbol.startswith("920"):
        return "BJ"
    if symbol.startswith(("6", "9")):
        return "SH"
    if symbol.startswith(("0", "2", "3")):
        return "SZ"
    if symbol.startswith(("4", "8")):
        return "BJ"
    return ""


def _akshare_prefixed_symbol(symbol: str) -> str:
    """Prefix *symbol* with exchange for AkShare APIs that require it."""
    exchange = _guess_exchange(symbol).lower()
    if exchange in {"sh", "sz", "bj"}:
        return f"{exchange}{symbol}"
    return symbol


def _akshare_prefixed_index_symbol(symbol: str) -> str:
    """Prefix an index code using its index exchange, not stock-code rules."""
    if symbol.startswith("399"):
        return f"sz{symbol}"
    return f"sh{symbol}"


def _build_dual_error(
    symbol: str,
    main_error: Exception | None,
    fallback_error: Exception | None,
) -> ProviderUnavailableError:
    """Build a :class:`ProviderUnavailableError` that preserves context
    from both the primary and fallback sources."""
    return ProviderUnavailableError(
        symbol=symbol,
        main_error=main_error,
        fallback_error=fallback_error,
        source="akshare",
    )
