import json
from datetime import date, datetime
from math import ceil

import pandas as pd
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.data.symbols import normalize_a_share_symbol, normalize_a_share_symbols
from app.models.entities import DailyBar, IndexDailyBar, NewsItem, Stock, SyncJob, TradingCalendar


class MarketDataRepository:
    def __init__(self, session: Session):
        self.session = session

    def resolve_symbol(self, identifier: str) -> str:
        """Resolve a user-facing stock identifier to the canonical 6-digit code.

        API inputs often come from search boxes where users type a Chinese
        stock name instead of a code. Provider and storage layers still work
        with normalized symbols only, so name resolution is kept at the
        repository boundary where the stock master table is available.
        """
        raw = str(identifier).strip()
        try:
            return normalize_a_share_symbol(raw)
        except ValueError as original_exc:
            if not raw:
                raise original_exc

            exact_matches = list(
                self.session.scalars(
                    select(Stock.symbol).where(Stock.name == raw).order_by(Stock.symbol)
                )
            )
            if len(exact_matches) == 1:
                return exact_matches[0]
            if len(exact_matches) > 1:
                raise ValueError(
                    f"ambiguous stock name: {identifier}; matches: {', '.join(exact_matches[:10])}"
                ) from original_exc

            fuzzy_matches = list(
                self.session.scalars(
                    select(Stock.symbol)
                    .where(Stock.name.contains(raw))
                    .order_by(Stock.symbol)
                    .limit(11)
                )
            )
            if len(fuzzy_matches) == 1:
                return fuzzy_matches[0]
            if len(fuzzy_matches) > 1:
                raise ValueError(
                    f"ambiguous stock name: {identifier}; matches: {', '.join(fuzzy_matches[:10])}"
                ) from original_exc
            raise ValueError(f"unknown A-share symbol or stock name: {identifier}") from original_exc

    def resolve_symbols(self, identifiers: list[str]) -> list[str]:
        resolved: list[str] = []
        seen: set[str] = set()
        for identifier in identifiers:
            symbol = self.resolve_symbol(identifier)
            if symbol not in seen:
                resolved.append(symbol)
                seen.add(symbol)
        return resolved

    def upsert_stocks(self, stocks: pd.DataFrame) -> int:
        count = 0
        for row in stocks.to_dict("records"):
            symbol = normalize_a_share_symbol(row["symbol"])
            stock = self.session.get(Stock, symbol) or Stock(symbol=symbol, name=row["name"])
            stock.name = row["name"]
            stock.exchange = row.get("exchange", "")
            stock.status = row.get("status", "active")
            self.session.merge(stock)
            count += 1
        self.session.commit()
        return count

    def upsert_trading_calendar(self, calendar: pd.DataFrame) -> int:
        if calendar.empty:
            return 0
        count = 0
        for row in calendar.to_dict("records"):
            trade_date = row["trade_date"]
            if hasattr(trade_date, "date"):
                trade_date = trade_date.date()
            item = self.session.get(TradingCalendar, trade_date) or TradingCalendar(
                trade_date=trade_date
            )
            item.is_open = bool(row.get("is_open", True))
            self.session.merge(item)
            count += 1
        self.session.commit()
        return count

    def upsert_news_items(self, items: pd.DataFrame) -> int:
        if items.empty:
            return 0
        required = {"source", "source_id", "title", "published_at", "fetched_at"}
        missing = required - set(items.columns)
        if missing:
            raise ValueError(f"news items missing required columns: {sorted(missing)}")

        count = 0
        for row in items.to_dict("records"):
            source = str(row["source"]).strip()
            source_id = str(row["source_id"]).strip()
            if not source or not source_id:
                raise ValueError("news source and source_id must be non-empty")
            published_at = _to_datetime(row["published_at"])
            fetched_at = _to_datetime(row["fetched_at"])
            symbol = row.get("symbol")
            normalized_symbol = (
                normalize_a_share_symbol(symbol)
                if symbol is not None and str(symbol).strip()
                else None
            )
            existing = self.session.scalar(
                select(NewsItem).where(
                    NewsItem.source == source,
                    NewsItem.source_id == source_id,
                )
            )
            item = existing or NewsItem(
                source=source,
                source_id=source_id,
                published_at=published_at,
                fetched_at=fetched_at,
                title=str(row["title"]),
            )
            item.symbol = normalized_symbol
            item.title = str(row["title"])
            item.body = str(row.get("body") or "")
            item.url = str(row.get("url") or "")
            item.event_type = str(row.get("event_type") or "")
            item.sentiment_label = str(row.get("sentiment_label") or "")
            item.sentiment_score = _optional_float(row.get("sentiment_score"))
            item.relevance_score = _optional_float(row.get("relevance_score"))
            item.published_at = published_at
            item.fetched_at = fetched_at
            item.raw = _raw_to_text(row.get("raw"))
            item.updated_at = datetime.utcnow()
            self.session.merge(item)
            count += 1
        self.session.commit()
        return count

    def news_items(
        self,
        *,
        symbol: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        source: str | None = None,
        limit: int = 200,
    ) -> pd.DataFrame:
        stmt = select(NewsItem)
        if symbol is not None and str(symbol).strip():
            stmt = stmt.where(NewsItem.symbol == normalize_a_share_symbol(symbol))
        if source is not None and str(source).strip():
            stmt = stmt.where(NewsItem.source == str(source).strip())
        if start_at is not None:
            stmt = stmt.where(NewsItem.published_at >= start_at)
        if end_at is not None:
            stmt = stmt.where(NewsItem.published_at <= end_at)
        stmt = stmt.order_by(NewsItem.published_at.desc(), NewsItem.id.desc()).limit(
            max(1, min(int(limit), 5000))
        )
        rows = list(self.session.scalars(stmt))
        return pd.DataFrame(
            [
                {
                    "id": row.id,
                    "source": row.source,
                    "source_id": row.source_id,
                    "symbol": row.symbol,
                    "title": row.title,
                    "body": row.body,
                    "url": row.url,
                    "event_type": row.event_type,
                    "sentiment_label": row.sentiment_label,
                    "sentiment_score": row.sentiment_score,
                    "relevance_score": row.relevance_score,
                    "published_at": row.published_at,
                    "fetched_at": row.fetched_at,
                    "raw": row.raw,
                }
                for row in rows
            ],
            columns=[
                "id",
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
            ],
        )

    def trading_dates(self, start_date: date, end_date: date) -> list[date]:
        stmt = (
            select(TradingCalendar.trade_date)
            .where(
                TradingCalendar.is_open.is_(True),
                TradingCalendar.trade_date >= start_date,
                TradingCalendar.trade_date <= end_date,
            )
            .order_by(TradingCalendar.trade_date)
        )
        return list(self.session.scalars(stmt))

    def replace_daily_bars(self, symbol: str, start_date: date, end_date: date, bars: pd.DataFrame) -> int:
        symbol = normalize_a_share_symbol(symbol)
        normalized_bar_symbols = {
            normalize_a_share_symbol(row_symbol)
            for row_symbol in bars.get("symbol", pd.Series([symbol])).dropna().tolist()
        }
        if normalized_bar_symbols and normalized_bar_symbols != {symbol}:
            raise ValueError(
                f"daily bars contain symbols {sorted(normalized_bar_symbols)} but target symbol is {symbol}"
            )
        self.session.execute(
            delete(DailyBar).where(
                DailyBar.symbol == symbol,
                DailyBar.trade_date >= start_date,
                DailyBar.trade_date <= end_date,
            )
        )
        records = [
            DailyBar(
                symbol=normalize_a_share_symbol(row.get("symbol", symbol)),
                trade_date=row["trade_date"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0.0)),
                amount=float(row.get("amount", 0.0)),
                adj=row.get("adj", "qfq"),
            )
            for row in bars.to_dict("records")
        ]
        self.session.add_all(records)
        self.session.commit()
        return len(records)

    def replace_index_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        bars: pd.DataFrame,
    ) -> int:
        symbol = normalize_a_share_symbol(symbol)
        normalized_bar_symbols = {
            normalize_a_share_symbol(row_symbol)
            for row_symbol in bars.get("symbol", pd.Series([symbol])).dropna().tolist()
        }
        if normalized_bar_symbols and normalized_bar_symbols != {symbol}:
            raise ValueError(
                f"index bars contain symbols {sorted(normalized_bar_symbols)} but target symbol is {symbol}"
            )
        self.session.execute(
            delete(IndexDailyBar).where(
                IndexDailyBar.symbol == symbol,
                IndexDailyBar.trade_date >= start_date,
                IndexDailyBar.trade_date <= end_date,
            )
        )
        records = [
            IndexDailyBar(
                symbol=normalize_a_share_symbol(row.get("symbol", symbol)),
                trade_date=row["trade_date"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0.0)),
                amount=float(row.get("amount", 0.0)),
            )
            for row in bars.to_dict("records")
        ]
        self.session.add_all(records)
        self.session.commit()
        return len(records)

    def list_stocks(self, limit: int = 100, keyword: str | None = None) -> list[Stock]:
        stmt = select(Stock).order_by(Stock.symbol).limit(limit)
        if keyword and keyword.strip():
            cleaned_keyword = keyword.strip()
            symbol_keyword = cleaned_keyword
            compact_keyword = cleaned_keyword.upper().replace(".", "")
            for prefix in ("SH", "SZ", "BJ"):
                if compact_keyword.startswith(prefix):
                    compact_keyword = compact_keyword[len(prefix) :]
                if compact_keyword.endswith(prefix):
                    compact_keyword = compact_keyword[: -len(prefix)]
            if compact_keyword.isdigit() and len(compact_keyword) == 6:
                symbol_keyword = compact_keyword
            stmt = (
                select(Stock)
                .where(Stock.symbol.contains(symbol_keyword) | Stock.name.contains(cleaned_keyword))
                .order_by(Stock.symbol)
                .limit(limit)
            )
        return list(self.session.scalars(stmt))

    def daily_bars(self, symbols: list[str], start_date: date, end_date: date) -> pd.DataFrame:
        normalized_symbols = normalize_a_share_symbols(symbols)
        columns = ["symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"]
        if not normalized_symbols:
            return pd.DataFrame(columns=columns)
        stmt = (
            select(
                DailyBar.symbol,
                DailyBar.trade_date,
                DailyBar.open,
                DailyBar.high,
                DailyBar.low,
                DailyBar.close,
                DailyBar.volume,
                DailyBar.amount,
            )
            .where(DailyBar.symbol.in_(normalized_symbols), DailyBar.trade_date >= start_date, DailyBar.trade_date <= end_date)
            .order_by(DailyBar.trade_date, DailyBar.symbol)
        )
        rows = self.session.execute(stmt).all()
        return pd.DataFrame(rows, columns=columns)

    def daily_bar_count(self, symbol: str, start_date: date, end_date: date) -> int:
        symbol = normalize_a_share_symbol(symbol)
        stmt = select(func.count(DailyBar.id)).where(
            DailyBar.symbol == symbol,
            DailyBar.trade_date >= start_date,
            DailyBar.trade_date <= end_date,
        )
        return int(self.session.scalar(stmt) or 0)

    def index_daily_bars(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        symbol = normalize_a_share_symbol(symbol)
        stmt = (
            select(IndexDailyBar)
            .where(
                IndexDailyBar.symbol == symbol,
                IndexDailyBar.trade_date >= start_date,
                IndexDailyBar.trade_date <= end_date,
            )
            .order_by(IndexDailyBar.trade_date)
        )
        rows = list(self.session.scalars(stmt))
        return pd.DataFrame(
            [
                {
                    "symbol": row.symbol,
                    "trade_date": row.trade_date,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                    "amount": row.amount,
                }
                for row in rows
            ],
            columns=["symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"],
        )

    def index_daily_bar_count(self, symbol: str, start_date: date, end_date: date) -> int:
        symbol = normalize_a_share_symbol(symbol)
        stmt = select(func.count(IndexDailyBar.id)).where(
            IndexDailyBar.symbol == symbol,
            IndexDailyBar.trade_date >= start_date,
            IndexDailyBar.trade_date <= end_date,
        )
        return int(self.session.scalar(stmt) or 0)

    def symbol_data_status(self, symbol: str) -> dict:
        normalized_symbol = normalize_a_share_symbol(symbol)
        stock = self.session.get(Stock, normalized_symbol)
        coverage_stmt = select(
            func.min(DailyBar.trade_date),
            func.max(DailyBar.trade_date),
            func.count(DailyBar.id),
        ).where(DailyBar.symbol == normalized_symbol)
        start_date, end_date, bar_count = self.session.execute(coverage_stmt).one()
        return {
            "symbol": normalized_symbol,
            "stock_exists": stock is not None,
            "name": stock.name if stock else None,
            "exchange": stock.exchange if stock else None,
            "has_daily_bars": bool(bar_count),
            "start_date": start_date,
            "end_date": end_date,
            "bar_count": int(bar_count or 0),
        }

    def market_data_overview(self) -> dict:
        stock_count = int(self.session.scalar(select(func.count(Stock.symbol))) or 0)
        bar_count, symbols_with_bars, start_date, end_date = self.session.execute(
            select(
                func.count(DailyBar.id),
                func.count(func.distinct(DailyBar.symbol)),
                func.min(DailyBar.trade_date),
                func.max(DailyBar.trade_date),
            )
        ).one()
        return {
            "stock_count": stock_count,
            "bar_count": int(bar_count or 0),
            "symbols_with_bars": int(symbols_with_bars or 0),
            "start_date": start_date,
            "end_date": end_date,
        }

    def research_sync_progress(
        self,
        start_date: date,
        end_date: date,
        exchanges: tuple[str, ...] = ("SH", "SZ"),
        exclude_risk_names: bool = True,
    ) -> dict:
        coverage = (
            select(
                DailyBar.symbol.label("symbol"),
                func.min(DailyBar.trade_date).label("start_date"),
                func.max(DailyBar.trade_date).label("end_date"),
            )
            .group_by(DailyBar.symbol)
            .subquery()
        )
        completed_job = (
            select(SyncJob.id)
            .where(
                SyncJob.job_type == "daily",
                SyncJob.target == Stock.symbol,
                SyncJob.status.in_(("success", "empty")),
                SyncJob.start_date <= start_date,
                SyncJob.end_date >= end_date,
            )
            .exists()
        )
        filters = self._research_stock_filters(exchanges, exclude_risk_names)
        rows = self.session.execute(
            select(
                Stock.symbol,
                coverage.c.start_date,
                coverage.c.end_date,
                completed_job.label("job_completed"),
            )
            .outerjoin(coverage, coverage.c.symbol == Stock.symbol)
            .where(*filters)
        ).all()
        total = len(rows)
        covered = sum(
            1
            for row in rows
            if row.job_completed
            or (
                row.start_date is not None
                and row.end_date is not None
                and row.start_date <= start_date
                and row.end_date >= end_date
            )
        )
        remaining = total - covered
        return {
            "total": total,
            "covered": covered,
            "remaining": remaining,
            "percent": round((covered / total * 100.0) if total else 100.0, 2),
        }

    def next_research_sync_symbols(
        self,
        start_date: date,
        end_date: date,
        batch_size: int = 20,
        exchanges: tuple[str, ...] = ("SH", "SZ"),
        exclude_risk_names: bool = True,
    ) -> list[str]:
        coverage = (
            select(
                DailyBar.symbol.label("symbol"),
                func.min(DailyBar.trade_date).label("start_date"),
                func.max(DailyBar.trade_date).label("end_date"),
            )
            .group_by(DailyBar.symbol)
            .subquery()
        )
        completed_job = (
            select(SyncJob.id)
            .where(
                SyncJob.job_type == "daily",
                SyncJob.target == Stock.symbol,
                SyncJob.status.in_(("success", "empty")),
                SyncJob.start_date <= start_date,
                SyncJob.end_date >= end_date,
            )
            .exists()
        )
        filters = self._research_stock_filters(exchanges, exclude_risk_names)
        incomplete = or_(
            completed_job.is_(False),
            coverage.c.symbol.is_(None),
            coverage.c.start_date > start_date,
            coverage.c.end_date < end_date,
        )
        incomplete = ~or_(
            completed_job,
            (
                coverage.c.symbol.is_not(None)
                & (coverage.c.start_date <= start_date)
                & (coverage.c.end_date >= end_date)
            ),
        )
        stmt = (
            select(Stock.symbol)
            .outerjoin(coverage, coverage.c.symbol == Stock.symbol)
            .where(*filters, incomplete)
            .order_by(Stock.symbol)
            .limit(max(1, min(int(batch_size), 50)))
        )
        return list(self.session.scalars(stmt))

    def consecutive_sync_failures(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        limit: int = 10,
    ) -> int:
        symbol = normalize_a_share_symbol(symbol)
        stmt = (
            select(SyncJob.status)
            .where(
                SyncJob.job_type == "daily",
                SyncJob.target == symbol,
                SyncJob.start_date <= start_date,
                SyncJob.end_date >= end_date,
            )
            .order_by(SyncJob.created_at.desc(), SyncJob.id.desc())
            .limit(max(1, int(limit)))
        )
        failures = 0
        for status in self.session.scalars(stmt):
            if status != "failed":
                break
            failures += 1
        return failures

    def full_market_sync_progress(self, start_date: date, end_date: date) -> dict:
        return self.research_sync_progress(
            start_date,
            end_date,
            exchanges=("SH", "SZ", "BJ"),
            exclude_risk_names=False,
        )

    def data_quality_report(
        self,
        start_date: date,
        end_date: date,
        *,
        limit: int = 200,
        exchanges: tuple[str, ...] = ("SH", "SZ", "BJ"),
    ) -> dict:
        expected_dates = self.trading_dates(start_date, end_date)
        expected_set = set(expected_dates)
        symbols = list(
            self.session.scalars(
                select(Stock.symbol)
                .where(Stock.exchange.in_(exchanges), Stock.status == "active")
                .order_by(Stock.symbol)
                .limit(max(1, min(int(limit), 6000)))
            )
        )
        if not expected_dates:
            return {
                "expected_trading_days": 0,
                "symbols_checked": len(symbols),
                "symbols_fully_covered": 0,
                "symbols_with_gaps": len(symbols),
                "total_missing_bars": 0,
                "items": [],
                "warning": "Trading calendar is empty; sync it before judging coverage.",
            }
        count_rows = self.session.execute(
            select(
                DailyBar.symbol,
                func.count(func.distinct(DailyBar.trade_date)),
            )
            .where(
                DailyBar.symbol.in_(symbols),
                DailyBar.trade_date >= start_date,
                DailyBar.trade_date <= end_date,
            )
            .group_by(DailyBar.symbol)
        ).all()
        present_counts = {symbol: int(count) for symbol, count in count_rows}
        present_dates: dict[str, set[date]] = {}
        if len(symbols) <= 200:
            date_rows = self.session.execute(
                select(DailyBar.symbol, DailyBar.trade_date).where(
                    DailyBar.symbol.in_(symbols),
                    DailyBar.trade_date >= start_date,
                    DailyBar.trade_date <= end_date,
                )
            ).all()
            present_dates = {symbol: set() for symbol in symbols}
            for symbol, trade_date in date_rows:
                present_dates.setdefault(symbol, set()).add(trade_date)
        items = []
        total_missing = 0
        fully_covered = 0
        for symbol in symbols:
            present_count = min(present_counts.get(symbol, 0), len(expected_dates))
            missing_count = max(0, len(expected_dates) - present_count)
            missing_dates = (
                sorted(expected_set - present_dates.get(symbol, set()))[:20]
                if present_dates
                else []
            )
            total_missing += missing_count
            if missing_count == 0:
                fully_covered += 1
            items.append(
                {
                    "symbol": symbol,
                    "expected": len(expected_dates),
                    "present": present_count,
                    "missing": missing_count,
                    "missing_dates": missing_dates,
                }
            )
        return {
            "expected_trading_days": len(expected_dates),
            "symbols_checked": len(symbols),
            "symbols_fully_covered": fully_covered,
            "symbols_with_gaps": len(symbols) - fully_covered,
            "total_missing_bars": total_missing,
            "items": items,
            "warning": (
                "Missing bars can represent suspension, listing dates, delisting, or provider gaps; "
                "point-in-time security status is not yet available."
            ),
        }

    def active_symbols(
        self,
        *,
        exchanges: tuple[str, ...] = ("SH", "SZ", "BJ"),
        limit: int = 6000,
    ) -> list[str]:
        stmt = (
            select(Stock.symbol)
            .where(Stock.exchange.in_(exchanges), Stock.status == "active")
            .order_by(Stock.symbol)
            .limit(max(1, min(int(limit), 6000)))
        )
        return list(self.session.scalars(stmt))

    def covered_research_symbols(
        self,
        start_date: date,
        end_date: date,
        limit: int = 100,
        exchanges: tuple[str, ...] = ("SH", "SZ", "BJ"),
        exclude_risk_names: bool = True,
    ) -> list[str]:
        coverage = (
            select(
                DailyBar.symbol.label("symbol"),
                func.min(DailyBar.trade_date).label("start_date"),
                func.max(DailyBar.trade_date).label("end_date"),
            )
            .group_by(DailyBar.symbol)
            .subquery()
        )
        filters = self._research_stock_filters(exchanges, exclude_risk_names)
        stmt = (
            select(Stock.symbol)
            .join(coverage, coverage.c.symbol == Stock.symbol)
            .where(
                *filters,
                coverage.c.start_date <= start_date,
                coverage.c.end_date >= end_date,
            )
            .order_by(Stock.symbol)
            .limit(max(1, min(int(limit), 6000)))
        )
        return list(self.session.scalars(stmt))

    def select_research_symbols(
        self,
        start_date: date,
        end_date: date,
        *,
        limit: int = 100,
        min_trading_days: int | None = None,
        min_coverage_ratio: float = 0.8,
        exchanges: tuple[str, ...] = ("SH", "SZ", "BJ"),
        exclude_risk_names: bool = True,
    ) -> list[str]:
        """Return research-pool symbols with useful data in *start_date* … *end_date*.

        Instead of requiring an exact min-max coverage span, this method counts
        distinct trading days each symbol has bars for inside the requested
        window and filters to symbols with at least ``min_trading_days`` (or
        ``min_coverage_ratio`` × expected trading days).  Symbols are returned
        ordered by coverage quality (most bars first).

        Gracefully handles weekends / holidays, end-of-data gaps, suspensions,
        and newly listed stocks — any symbol with enough bars to be useful
        qualifies.  No look-ahead.
        """
        expected_trading_dates = self.trading_dates(start_date, end_date)
        expected_count = len(expected_trading_dates)

        if expected_count == 0:
            # Trading calendar may be empty (e.g. in-memory test DB or not yet
            # synced).  Estimate trading days from the calendar span.
            span_days = max(1, (end_date - start_date).days + 1)
            estimated = max(1, round(span_days * 5.0 / 7.0))
            effective_min = (
                max(1, min(15, ceil(estimated * min_coverage_ratio)))
                if min_trading_days is None
                else max(1, int(min_trading_days))
            )
        elif min_trading_days is not None:
            effective_min = max(1, min(min_trading_days, expected_count))
        else:
            effective_min = min(
                expected_count,
                max(1, min(15, ceil(expected_count * min_coverage_ratio))),
            )

        filters = self._research_stock_filters(exchanges, exclude_risk_names)

        bar_counts = (
            select(
                DailyBar.symbol,
                func.count(func.distinct(DailyBar.trade_date)).label("bar_count"),
            )
            .where(
                DailyBar.symbol.in_(
                    select(Stock.symbol).where(*filters)
                ),
                DailyBar.trade_date >= start_date,
                DailyBar.trade_date <= end_date,
            )
            .group_by(DailyBar.symbol)
            .having(func.count(func.distinct(DailyBar.trade_date)) >= effective_min)
            .order_by(func.count(func.distinct(DailyBar.trade_date)).desc())
            .limit(max(1, min(int(limit), 6000)))
        )

        rows = self.session.execute(bar_counts).all()
        return [row.symbol for row in rows]

    def research_pool_diagnostics(
        self,
        start_date: date,
        end_date: date,
        *,
        exchanges: tuple[str, ...] = ("SH", "SZ"),
        exclude_risk_names: bool = True,
    ) -> dict:
        """Return diagnostic detail when no research symbols match a range.

        Useful for the backtest error path so the user (or UI) understands
        *why* the pool is empty and how to adjust the date range.
        """
        expected_dates = self.trading_dates(start_date, end_date)
        overview = self.market_data_overview()

        filters = self._research_stock_filters(exchanges, exclude_risk_names)
        eligible_stocks = int(
            self.session.scalar(
                select(func.count(Stock.symbol)).where(*filters)
            )
            or 0
        )

        bar_range = (
            select(
                func.min(DailyBar.trade_date),
                func.max(DailyBar.trade_date),
            )
            .where(
                DailyBar.symbol.in_(select(Stock.symbol).where(*filters))
            )
        )
        db_min, db_max = self.session.execute(bar_range).one()

        # Top-N symbols with any bars in the range, ordered by bar count
        top_symbols = self.session.execute(
            select(
                DailyBar.symbol,
                func.count(func.distinct(DailyBar.trade_date)).label("cnt"),
                func.min(DailyBar.trade_date).label("first"),
                func.max(DailyBar.trade_date).label("last"),
            )
            .where(
                DailyBar.symbol.in_(select(Stock.symbol).where(*filters)),
                DailyBar.trade_date >= start_date,
                DailyBar.trade_date <= end_date,
            )
            .group_by(DailyBar.symbol)
            .order_by(func.count(func.distinct(DailyBar.trade_date)).desc())
            .limit(5)
        ).all()

        return {
            "eligible_stocks": eligible_stocks,
            "expected_trading_days": len(expected_dates),
            "expected_date_range": (
                f"{expected_dates[0]}..{expected_dates[-1]}"
                if expected_dates
                else "no trading days in range"
            ),
            "db_bar_count": overview["bar_count"],
            "db_symbols_with_bars": overview["symbols_with_bars"],
            "db_min_bar_date": db_min,
            "db_max_bar_date": db_max,
            "top_symbols_in_range": [
                {
                    "symbol": row.symbol,
                    "bars_in_range": row.cnt,
                    "first": row.first,
                    "last": row.last,
                }
                for row in top_symbols
            ],
            "hint": (
                "Try a date range within the database bar coverage "
                f"({db_min} … {db_max}) or run full-market sync to pull more data. "
                "If the requested end_date is in the future, set it to the latest "
                "available trading day."
            ),
        }

    @staticmethod
    def _research_stock_filters(
        exchanges: tuple[str, ...],
        exclude_risk_names: bool,
    ) -> list:
        filters = [Stock.exchange.in_(exchanges), Stock.status == "active"]
        if exclude_risk_names:
            upper_name = func.upper(Stock.name)
            # Match A-share ST / *ST / SST / S*ST prefix conventions.
            # Previously used ~contains("ST") which was too broad and
            # accidentally matched names like "Test" or "Best" (or even
            # "NewListing").  Prefix-based matching matches real ST stocks
            # while keeping innocent names in the pool.
            st_prefixes = (
                upper_name.startswith("ST"),
                upper_name.startswith("*ST"),
                upper_name.startswith("SST"),
                upper_name.startswith("S*ST"),
            )
            filters.extend(
                [
                    ~or_(*st_prefixes),
                    ~Stock.name.contains("退"),
                ]
            )
        return filters

    def create_sync_job(
        self,
        job_type: str,
        target: str,
        status: str,
        records: int = 0,
        message: str = "",
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> SyncJob:
        job = SyncJob(
            job_type=job_type,
            target=target,
            status=status,
            records=records,
            message=message[:500],
            start_date=start_date,
            end_date=end_date,
        )
        self.session.add(job)
        self.session.commit()
        return job

    def list_sync_jobs(self, limit: int = 100) -> list[SyncJob]:
        stmt = select(SyncJob).order_by(SyncJob.created_at.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def daily_status(self, limit: int = 200) -> list[dict]:
        stmt = (
            select(
                DailyBar.symbol,
                func.min(DailyBar.trade_date).label("start_date"),
                func.max(DailyBar.trade_date).label("end_date"),
                func.count(DailyBar.id).label("bar_count"),
            )
            .group_by(DailyBar.symbol)
            .order_by(DailyBar.symbol)
            .limit(limit)
        )
        return [
            {
                "symbol": row.symbol,
                "start_date": row.start_date,
                "end_date": row.end_date,
                "bar_count": row.bar_count,
            }
            for row in self.session.execute(stmt)
        ]


def _to_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    parsed = pd.to_datetime(value)
    if pd.isna(parsed):
        raise ValueError("datetime value must not be NaT")
    return parsed.to_pydatetime()


def _optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return float(value)


def _raw_to_text(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
