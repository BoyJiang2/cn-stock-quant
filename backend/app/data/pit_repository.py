"""Point-in-Time (PIT) repository: queries and materialisation.

This module is owned by the GLM "Module A" boundary.  It depends on the
shared :class:`~app.data.repository.MarketDataRepository` only through
*structural* duck-typing — the coordinator composes a
``MarketDataRepository`` instance and forwards bar/calendar queries to
it, but never writes through ``MarketDataRepository``'s mutation
methods.  This keeps the shared single-owner file
(``repository.py``) untouched while still letting callers use a single
``Session``.

Public surface
--------------

* :class:`UniverseSpec`           -- deterministic PIT universe spec
* :class:`PitRepository`          -- all PIT queries + materialisation
* :class:`IndexMember`            -- one row of ``index_constituents_as_of``
* :class:`PitSecurityStatus`      -- value object returned by ``status_as_of``
* :class:`PitNameRecord`          -- value object returned by ``name_as_of``
* :class:`PitUniverseResult`      -- ``(symbols, meta)`` from
  :meth:`PitRepository.select_research_symbols_pit`

Design notes
------------

* Every PIT query honours ``announced_at <= as_of`` to prevent
  look-ahead bias.  When ``announced_at`` is missing the row is still
  returned but downgraded to ``confidence="medium"`` and counted in the
  ``degraded_*`` meta counters.
* When the underlying ``security_status`` / ``security_name`` tables
  are empty the universe build *falls back* to the legacy
  ``Stock``-snapshot logic (and reports ``pit_degraded=True``) rather
  than raising — see plan section 6.6.  This keeps the PIT path safe
  to enable before the back-fill job has run.
* ST handling follows the plan's "conservative on missing data"
  principle: when ``st_policy="exclude_known"`` and we lack PIT name
  data for a symbol, the symbol is *kept*, not excluded.  Only
  ``st_policy="strict"`` lets the legacy current-snapshot ST filter
  take over for missing-data symbols.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Protocol, runtime_checkable

import pandas as pd
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.data.symbols import normalize_a_share_symbol, normalize_a_share_symbols
from app.data.universe import UniverseSpec
from app.models.pit import (
    PIT_CONFIDENCE_LEVELS,
    PIT_SECURITY_STATUSES,
    PIT_ST_POLICIES,
    PIT_TRADE_GAP_TYPES,
    IndexConstituent,
    IndexWeightSnapshot,
    ResearchPoolMember,
    SecurityName,
    SecurityStatus,
    SecurityTradeGap,
)

__all__ = [
    "FAR_FUTURE_DATE",
    "IndexMember",
    "PitNameRecord",
    "PitRepository",
    "PitSecurityStatus",
    "SecurityTradeGapRecord",
    "PitUniverseResult",
    "MarketDataRepositoryLike",
]

# Sentinel used in queries to make ``valid_to IS NULL`` behave like
# ``+infinity`` without SQLite NULL-comparison pitfalls.
FAR_FUTURE_DATE = date(9999, 12, 31)


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PitSecurityStatus:
    """PIT status of a single symbol on a single date."""

    symbol: str
    status: str
    valid_from: date
    valid_to: date | None
    announced_at: date | None
    confidence: str
    source: str
    delist_reason: str | None = None
    degraded: bool = False


@dataclass(frozen=True)
class PitNameRecord:
    """PIT name of a single symbol on a single date."""

    symbol: str
    name: str
    valid_from: date
    valid_to: date | None
    announced_at: date | None
    confidence: str
    source: str
    degraded: bool = False


@dataclass(frozen=True)
class IndexMember:
    """One constituent returned by ``index_constituents_as_of``."""

    symbol: str
    name: str | None
    weight: float | None
    valid_from: date
    valid_to: date | None
    announced_at: date | None
    confidence: str
    source: str


@dataclass(frozen=True)
class SecurityTradeGapRecord:
    """PIT trade-gap row for one symbol and trade date."""

    symbol: str
    trade_date: date
    expected_open: bool
    has_bar: bool
    gap_type: str
    source: str
    confidence: str


@dataclass
class PitUniverseResult:
    """Return type of :meth:`PitRepository.select_research_symbols_pit`.

    ``meta`` is a plain dict so the FastAPI response model can serialise
    it directly.  Key fields:

    * ``pool_key``           -- deterministic spec fingerprint
    * ``as_of``              -- the date the universe was rebuilt on
    * ``pit_degraded``       -- True when PIT tables lacked data and we
                                fell back to the Stock snapshot
    * ``st_policy``          -- the policy actually used
    * ``total_candidates``   -- symbols inspected before exclusion
    * ``eligible_count``     -- symbols returned in ``symbols``
    * ``excluded``           -- ``{reason: count}``
    * ``missing_status_rows``/``missing_name_rows`` -- count of symbols
      for which PIT tables had no row (used in the degraded-ratio report)
    * ``data_version``       -- opaque version tag for downstream caching
    """

    symbols: list[str]
    meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocol for the optional bar/calendar reader
# ---------------------------------------------------------------------------


@runtime_checkable
class MarketDataRepositoryLike(Protocol):
    """Subset of :class:`~app.data.repository.MarketDataRepository` used
    by :class:`PitRepository` for bar-count and trading-day lookups.

    Declared as a Protocol so unit tests can pass a minimal fake; the
    concrete repository satisfies it structurally.
    """

    def trading_dates(
        self, start_date: date, end_date: date
    ) -> list[date]: ...

    def daily_bar_count(
        self, symbol: str, start_date: date, end_date: date
    ) -> int: ...


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


# Statuses that count as "tradeable today" — i.e. the security is
# listed and not in a regulatory cage that prevents inclusion in a
# research pool.  ``suspended`` is intentionally excluded because a
# suspended name cannot transact on the as_of date.
_LISTED_STATUSES: frozenset[str] = frozenset({"listed", "normal"})


def _st_prefix(name: str | None) -> bool:
    """Return True if *name* carries an A-share ST / *ST / SST / S*ST prefix."""
    if not name:
        return False
    upper = name.upper().lstrip()
    for prefix in ("*ST", "S*ST", "SST", "ST"):
        if not upper.startswith(prefix):
            continue
        if len(upper) == len(prefix):
            return True
        next_character = upper[len(prefix)]
        if next_character.isspace() or not next_character.isascii():
            return True
    return False


def _has_delist_marker(name: str | None) -> bool:
    return bool(name) and "退" in (name or "")


class PitRepository:
    """PIT queries and universe builder.

    Construct with a SQLAlchemy ``Session`` plus an optional bar/calendar
    reader (typically the project's :class:`MarketDataRepository`).
    Methods are intentionally side-effect-free except for the explicit
    ``upsert_*`` and ``materialize_*`` writers.
    """

    def __init__(
        self,
        session: Session,
        bar_reader: MarketDataRepositoryLike | None = None,
    ) -> None:
        self.session = session
        self._bar_reader = bar_reader

    # ------------------------------------------------------------------
    # Upsert helpers (called by the sync layer)
    # ------------------------------------------------------------------

    def upsert_security_status(self, rows: Iterable[dict[str, Any]]) -> int:
        """Insert or replace :class:`SecurityStatus` rows.

        Each row dict must carry: ``symbol``, ``status``, ``valid_from``,
        ``valid_to`` (nullable), ``announced_at`` (nullable), ``source``,
        ``confidence`` and optional ``delist_reason``.  Existing rows
        matching the unique constraint are deleted first so re-syncs do
        not accumulate stale intervals.
        """
        count = 0
        normalized_rows = _deduplicate_rows(
            rows,
            lambda row: (
                normalize_a_share_symbol(row["symbol"]),
                str(row["status"]).strip().lower(),
                _coerce_date(row["valid_from"]),
            ),
        )
        for row in normalized_rows:
            symbol = normalize_a_share_symbol(row["symbol"])
            status = str(row["status"]).strip().lower()
            if status not in PIT_SECURITY_STATUSES:
                raise ValueError(f"unknown security status: {status!r}")
            confidence = str(row.get("confidence", "high")).strip().lower()
            if confidence not in PIT_CONFIDENCE_LEVELS:
                raise ValueError(f"unknown confidence: {confidence!r}")
            valid_from = _coerce_date(row["valid_from"])
            valid_to = _coerce_optional_date(row.get("valid_to"))
            _validate_interval(valid_from, valid_to)
            announced_at = _coerce_optional_date(row.get("announced_at"))
            if status == "listed" and confidence == "high" and announced_at == valid_from:
                # Replace an older current-snapshot placeholder when a later
                # sync obtains the actual listing date from the provider.
                self.session.execute(
                    SecurityStatus.__table__.delete().where(
                        SecurityStatus.symbol == symbol,
                        SecurityStatus.status == "listed",
                        SecurityStatus.valid_from > valid_from,
                        SecurityStatus.announced_at.is_(None),
                        SecurityStatus.confidence == "medium",
                    )
                )
            # Delete the conflicting unique-key row, then insert.
            self.session.execute(
                SecurityStatus.__table__.delete().where(
                    SecurityStatus.symbol == symbol,
                    SecurityStatus.status == status,
                    SecurityStatus.valid_from == valid_from,
                )
            )
            self.session.add(
                SecurityStatus(
                    symbol=symbol,
                    status=status,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    announced_at=announced_at,
                    delist_reason=row.get("delist_reason"),
                    source=str(row.get("source", "unknown")),
                    confidence=confidence,
                )
            )
            count += 1
        self.session.commit()
        return count

    def upsert_security_name(self, rows: Iterable[dict[str, Any]]) -> int:
        """Insert or replace :class:`SecurityName` rows."""
        count = 0
        normalized_rows = _deduplicate_rows(
            rows,
            lambda row: (
                normalize_a_share_symbol(row["symbol"]),
                _coerce_date(row["valid_from"]),
            ),
        )
        for row in normalized_rows:
            symbol = normalize_a_share_symbol(row["symbol"])
            valid_from = _coerce_date(row["valid_from"])
            valid_to = _coerce_optional_date(row.get("valid_to"))
            _validate_interval(valid_from, valid_to)
            announced_at = _coerce_optional_date(row.get("announced_at"))
            self.session.execute(
                SecurityName.__table__.delete().where(
                    SecurityName.symbol == symbol,
                    SecurityName.valid_from == valid_from,
                )
            )
            self.session.add(
                SecurityName(
                    symbol=symbol,
                    name=str(row["name"])[:64],
                    valid_from=valid_from,
                    valid_to=valid_to,
                    announced_at=announced_at,
                    source=str(row.get("source", "unknown")),
                )
            )
            count += 1
        self.session.commit()
        return count

    def upsert_index_constituent(self, rows: Iterable[dict[str, Any]]) -> int:
        """Insert or replace :class:`IndexConstituent` rows."""
        count = 0
        normalized_rows = _deduplicate_rows(
            rows,
            lambda row: (
                normalize_a_share_symbol(row["index_symbol"]),
                normalize_a_share_symbol(row["symbol"]),
                _coerce_date(row["valid_from"]),
            ),
        )
        for row in normalized_rows:
            index_symbol = normalize_a_share_symbol(row["index_symbol"])
            symbol = normalize_a_share_symbol(row["symbol"])
            valid_from = _coerce_date(row["valid_from"])
            valid_to = _coerce_optional_date(row.get("valid_to"))
            _validate_interval(valid_from, valid_to)
            announced_at = _coerce_optional_date(row.get("announced_at"))
            self.session.execute(
                IndexConstituent.__table__.delete().where(
                    IndexConstituent.index_symbol == index_symbol,
                    IndexConstituent.symbol == symbol,
                    IndexConstituent.valid_from == valid_from,
                )
            )
            self.session.add(
                IndexConstituent(
                    index_symbol=index_symbol,
                    symbol=symbol,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    announced_at=announced_at,
                    source=str(row.get("source", "unknown")),
                )
            )
            count += 1
        self.session.commit()
        return count

    def reconcile_current_st_snapshot(
        self,
        rows: Iterable[dict[str, Any]],
        as_of: date,
    ) -> int:
        """Apply a current ST snapshot while closing stale open intervals."""
        desired_rows = [dict(row) for row in rows]
        desired = {
            normalize_a_share_symbol(row["symbol"]): str(row["status"]).lower()
            for row in desired_rows
        }
        unchanged: set[str] = set()
        open_rows = list(
            self.session.scalars(
                select(SecurityStatus).where(
                    SecurityStatus.status.in_(("st", "sst", "st_star")),
                    SecurityStatus.valid_to.is_(None),
                )
            )
        )
        for existing in open_rows:
            desired_status = desired.get(existing.symbol)
            if desired_status == existing.status:
                unchanged.add(existing.symbol)
                continue
            if existing.valid_from < as_of:
                existing.valid_to = as_of
            else:
                self.session.delete(existing)
        self.session.commit()
        pending = [
            row
            for row in desired_rows
            if normalize_a_share_symbol(row["symbol"]) not in unchanged
        ]
        return self.upsert_security_status(pending) if pending else 0

    def reconcile_current_names(
        self,
        rows: Iterable[dict[str, Any]],
        as_of: date,
    ) -> int:
        """Apply today's names without backdating or duplicating intervals."""
        desired_rows = [dict(row) for row in rows]
        pending: list[dict[str, Any]] = []
        for row in desired_rows:
            symbol = normalize_a_share_symbol(row["symbol"])
            name = str(row["name"])
            existing = self.session.scalar(
                select(SecurityName)
                .where(
                    SecurityName.symbol == symbol,
                    SecurityName.valid_to.is_(None),
                )
                .order_by(SecurityName.valid_from.desc())
                .limit(1)
            )
            if existing is not None and existing.name == name:
                continue
            if existing is not None:
                if existing.valid_from < as_of:
                    existing.valid_to = as_of
                else:
                    self.session.delete(existing)
            pending.append(row)
        self.session.commit()
        return self.upsert_security_name(pending) if pending else 0

    def reconcile_index_constituent_snapshot(
        self,
        index_symbol: str,
        rows: Iterable[dict[str, Any]],
        as_of: date,
    ) -> int:
        """Apply an index snapshot and close members removed on *as_of*."""
        index_symbol = normalize_a_share_symbol(index_symbol)
        desired_rows = [dict(row) for row in rows]
        desired_symbols = {
            normalize_a_share_symbol(row["symbol"]) for row in desired_rows
        }
        unchanged: set[str] = set()
        open_rows = list(
            self.session.scalars(
                select(IndexConstituent).where(
                    IndexConstituent.index_symbol == index_symbol,
                    IndexConstituent.valid_to.is_(None),
                )
            )
        )
        for existing in open_rows:
            if existing.symbol in desired_symbols:
                unchanged.add(existing.symbol)
                continue
            if existing.valid_from < as_of:
                existing.valid_to = as_of
            else:
                self.session.delete(existing)
        self.session.commit()
        pending = [
            row
            for row in desired_rows
            if normalize_a_share_symbol(row["symbol"]) not in unchanged
        ]
        return self.upsert_index_constituent(pending) if pending else 0

    def upsert_index_weight_snapshot(self, rows: Iterable[dict[str, Any]]) -> int:
        """Insert or replace :class:`IndexWeightSnapshot` rows."""
        count = 0
        normalized_rows = _deduplicate_rows(
            rows,
            lambda row: (
                normalize_a_share_symbol(row["index_symbol"]),
                normalize_a_share_symbol(row["symbol"]),
                _coerce_date(row["trade_date"]),
            ),
        )
        for row in normalized_rows:
            index_symbol = normalize_a_share_symbol(row["index_symbol"])
            symbol = normalize_a_share_symbol(row["symbol"])
            trade_date = _coerce_date(row["trade_date"])
            weight = row.get("weight")
            weight_val = float(weight) if weight is not None else None
            self.session.execute(
                IndexWeightSnapshot.__table__.delete().where(
                    IndexWeightSnapshot.index_symbol == index_symbol,
                    IndexWeightSnapshot.symbol == symbol,
                    IndexWeightSnapshot.trade_date == trade_date,
                )
            )
            self.session.add(
                IndexWeightSnapshot(
                    index_symbol=index_symbol,
                    symbol=symbol,
                    trade_date=trade_date,
                    weight=weight_val,
                    source=str(row.get("source", "unknown")),
                )
            )
            count += 1
        self.session.commit()
        return count

    def upsert_security_trade_gap(self, rows: Iterable[dict[str, Any]]) -> int:
        """Insert or replace :class:`SecurityTradeGap` rows.

        Each row represents the observed availability of one symbol on
        one exchange trading date.  ``gap_type="normal"`` is valid when
        ``expected_open`` and ``has_bar`` are both true; provider gaps
        and suspensions are stored explicitly so callers do not infer
        them from missing OHLCV rows.
        """
        count = 0
        normalized_rows = _deduplicate_rows(
            rows,
            lambda row: (
                normalize_a_share_symbol(row["symbol"]),
                _coerce_date(row["trade_date"]),
            ),
        )
        for row in normalized_rows:
            symbol = normalize_a_share_symbol(row["symbol"])
            trade_date = _coerce_date(row["trade_date"])
            gap_type = str(row.get("gap_type", "unknown")).strip().lower()
            if gap_type not in PIT_TRADE_GAP_TYPES:
                raise ValueError(f"unknown trade gap type: {gap_type!r}")
            confidence = str(row.get("confidence", "high")).strip().lower()
            if confidence not in PIT_CONFIDENCE_LEVELS:
                raise ValueError(f"unknown confidence: {confidence!r}")
            self.session.execute(
                SecurityTradeGap.__table__.delete().where(
                    SecurityTradeGap.symbol == symbol,
                    SecurityTradeGap.trade_date == trade_date,
                )
            )
            self.session.add(
                SecurityTradeGap(
                    symbol=symbol,
                    trade_date=trade_date,
                    expected_open=bool(row["expected_open"]),
                    has_bar=bool(row["has_bar"]),
                    gap_type=gap_type,
                    source=str(row.get("source", "unknown")),
                    confidence=confidence,
                )
            )
            count += 1
        self.session.commit()
        return count

    # ------------------------------------------------------------------
    # Status / name PIT queries
    # ------------------------------------------------------------------

    def status_as_of(
        self, symbol: str, as_of: date
    ) -> PitSecurityStatus | None:
        """Return the PIT security status of *symbol* on *as_of*.

        Honours ``announced_at <= as_of`` to prevent look-ahead bias.
        Rows missing ``announced_at`` are still considered (using
        ``valid_from`` as the announcement proxy) and downgraded to
        ``confidence="medium"``.
        """
        symbol = normalize_a_share_symbol(symbol)
        # Select rows valid on as_of (valid_from <= as_of < valid_to).
        # Then filter by announced_at <= as_of, falling back to valid_from.
        stmt = (
            select(SecurityStatus)
            .where(
                SecurityStatus.symbol == symbol,
                SecurityStatus.valid_from <= as_of,
                or_(
                    SecurityStatus.valid_to.is_(None),
                    SecurityStatus.valid_to > as_of,
                ),
            )
            .order_by(SecurityStatus.valid_from.desc())
        )
        rows = list(self.session.scalars(stmt))
        for row in rows:
            announced = row.announced_at or row.valid_from
            if announced <= as_of:
                confidence = row.confidence if row.announced_at else "medium"
                return PitSecurityStatus(
                    symbol=row.symbol,
                    status=row.status,
                    valid_from=row.valid_from,
                    valid_to=row.valid_to,
                    announced_at=row.announced_at,
                    confidence=confidence,
                    source=row.source,
                    delist_reason=row.delist_reason,
                    degraded=(row.announced_at is None),
                )
        return None

    def name_as_of(self, symbol: str, as_of: date) -> PitNameRecord | None:
        """Return the PIT name of *symbol* on *as_of*.

        Picks the row with the greatest ``valid_from <= as_of`` whose
        ``[valid_from, valid_to)`` interval contains *as_of*.  Rows
        missing ``announced_at`` are downgraded to ``confidence="medium"``.
        """
        symbol = normalize_a_share_symbol(symbol)
        stmt = (
            select(SecurityName)
            .where(
                SecurityName.symbol == symbol,
                SecurityName.valid_from <= as_of,
                or_(
                    SecurityName.valid_to.is_(None),
                    SecurityName.valid_to > as_of,
                ),
            )
            .order_by(SecurityName.valid_from.desc())
        )
        rows = list(self.session.scalars(stmt))
        for row in rows:
            announced = row.announced_at or row.valid_from
            if announced <= as_of:
                # SecurityName has no explicit confidence column; derive
                # it from announced_at presence to match SecurityStatus
                # semantics (medium when the announcement date is missing).
                confidence = "high" if row.announced_at is not None else "medium"
                return PitNameRecord(
                    symbol=row.symbol,
                    name=row.name,
                    valid_from=row.valid_from,
                    valid_to=row.valid_to,
                    announced_at=row.announced_at,
                    confidence=confidence,
                    source=row.source,
                    degraded=(row.announced_at is None),
                )
        return None

    def index_constituents_as_of(
        self,
        index_symbol: str,
        as_of: date,
        *,
        with_weights: bool = False,
    ) -> list[IndexMember]:
        """Return the PIT constituent list of *index_symbol* on *as_of*.

        Members are sourced from :class:`IndexConstituent` intervals
        containing *as_of* (with ``announced_at <= as_of`` when
        available).  When ``with_weights=True`` each member is enriched
        with the most recent :class:`IndexWeightSnapshot` on or before
        *as_of* (forward-fill semantics).
        """
        index_symbol = normalize_a_share_symbol(index_symbol)
        stmt = (
            select(IndexConstituent)
            .where(
                IndexConstituent.index_symbol == index_symbol,
                IndexConstituent.valid_from <= as_of,
                or_(
                    IndexConstituent.valid_to.is_(None),
                    IndexConstituent.valid_to > as_of,
                ),
            )
            .order_by(IndexConstituent.symbol)
        )
        members: list[IndexMember] = []
        for row in self.session.scalars(stmt):
            announced = row.announced_at or row.valid_from
            if announced > as_of:
                # Skip rows whose announcement is in the future.
                continue
            confidence = "medium" if row.announced_at is None else "high"
            weight: float | None = None
            if with_weights:
                weight = self._latest_weight(index_symbol, row.symbol, as_of)
            name = self.name_as_of(row.symbol, as_of)
            members.append(
                IndexMember(
                    symbol=row.symbol,
                    name=name.name if name else None,
                    weight=weight,
                    valid_from=row.valid_from,
                    valid_to=row.valid_to,
                    announced_at=row.announced_at,
                    confidence=confidence,
                    source=row.source,
                )
            )
        return members

    def _latest_weight(
        self, index_symbol: str, symbol: str, as_of: date
    ) -> float | None:
        stmt = (
            select(IndexWeightSnapshot.weight)
            .where(
                IndexWeightSnapshot.index_symbol == index_symbol,
                IndexWeightSnapshot.symbol == symbol,
                IndexWeightSnapshot.trade_date <= as_of,
            )
            .order_by(IndexWeightSnapshot.trade_date.desc())
            .limit(1)
        )
        value = self.session.scalar(stmt)
        return float(value) if value is not None else None

    def trade_gap_as_of(
        self, symbol: str, as_of: date
    ) -> SecurityTradeGapRecord | None:
        """Return the trade-gap row for *symbol* on *as_of*, if present."""
        symbol = normalize_a_share_symbol(symbol)
        row = self.session.scalar(
            select(SecurityTradeGap).where(
                SecurityTradeGap.symbol == symbol,
                SecurityTradeGap.trade_date == as_of,
            )
        )
        return _trade_gap_record(row) if row is not None else None

    def trade_gaps_between(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[SecurityTradeGapRecord]:
        """Return trade-gap rows for *symbol* ordered by trade date."""
        symbol = normalize_a_share_symbol(symbol)
        stmt = (
            select(SecurityTradeGap)
            .where(
                SecurityTradeGap.symbol == symbol,
                SecurityTradeGap.trade_date >= start_date,
                SecurityTradeGap.trade_date <= end_date,
            )
            .order_by(SecurityTradeGap.trade_date)
        )
        return [_trade_gap_record(row) for row in self.session.scalars(stmt)]

    # ------------------------------------------------------------------
    # Universe build
    # ------------------------------------------------------------------

    def select_research_symbols_pit(
        self,
        as_of: date,
        start_date: date,
        end_date: date,
        *,
        exchanges: tuple[str, ...] = ("SH", "SZ", "BJ"),
        exclude_st: bool = True,
        index_symbol: str | None = None,
        min_trading_days: int | None = None,
        min_coverage_ratio: float = 0.8,
        limit: int = 300,
        st_policy: str = "exclude_known",
    ) -> PitUniverseResult:
        """Build a PIT research universe as of *as_of*.

        Args
        -----
        as_of
            The PIT date — status / name / index membership are read as
            they were on this date.
        start_date, end_date
            The bar-coverage window used to assess data quality.  Must
            satisfy ``start_date <= end_date``; usually equals the
            backtest window.
        exchanges
            Exchanges to consider when no ``index_symbol`` is given.
        exclude_st
            When True, ST-prefixed names are excluded per ``st_policy``.
            When False, ST names are always kept (overrides
            ``st_policy``).
        index_symbol
            When provided, the candidate set is restricted to PIT
            members of this index on *as_of* (ignores ``exchanges``).
        min_trading_days, min_coverage_ratio
            Bar-quality thresholds forwarded to the bar-count check.
        limit
            Maximum number of eligible symbols to return.
        st_policy
            ``exclude_known`` (default): exclude only symbols whose PIT
            name is known to carry an ST prefix; missing-data symbols
            are kept.  ``include_unknown``: never exclude by ST.
            ``strict``: when PIT name is missing, fall back to the
            current ``Stock.name`` snapshot for ST-prefix exclusion.

        Returns
        -------
        PitUniverseResult
            ``symbols`` is the eligible list (ordered by bar count
            desc).  ``meta`` carries the audit/diagnostic fields needed
            by the API layer (``pool_key``, ``pit_degraded`` ...).
        """
        spec = UniverseSpec(
            source="index" if index_symbol else "market",
            exchanges=exchanges,
            index_symbol=index_symbol,
            exclude_st=exclude_st,
            st_policy=st_policy,
            min_trading_days=min_trading_days,
            min_coverage_ratio=min_coverage_ratio,
            limit=limit,
        )
        pool_key = spec.pool_key(as_of)

        # Detect global degradation: PIT tables completely empty.
        pit_status_total = int(
            self.session.scalar(select(func.count(SecurityStatus.id))) or 0
        )
        pit_name_total = int(
            self.session.scalar(select(func.count(SecurityName.id))) or 0
        )
        globally_degraded = pit_status_total == 0 and pit_name_total == 0

        # Build candidate universe (symbol, name_snapshot) pairs.
        candidates: list[tuple[str, str | None]]
        if index_symbol:
            candidates = self._candidates_from_index(index_symbol, as_of)
        else:
            candidates = self._candidates_from_stocks(exchanges, as_of)

        total_candidates = len(candidates)

        # Compute expected trading-day count for the bar-quality check.
        expected_count = self._expected_trading_days(start_date, end_date)
        effective_min = self._effective_min_trading_days(
            start_date, end_date,
            expected_count,
            min_trading_days,
            min_coverage_ratio,
        )

        excluded: dict[str, int] = {}
        missing_status_rows = 0
        missing_name_rows = 0
        eligible: list[tuple[str, int]] = []  # (symbol, bar_count)
        # Track per-symbol degradation: any snapshot fallback flips the
        # global ``pit_degraded`` flag on so callers can surface a
        # warning, but does not abort the build.
        per_symbol_degraded = False

        for symbol, snapshot_name in candidates:
            status = self.status_as_of(symbol, as_of)
            name_rec = self.name_as_of(symbol, as_of)

            if status is None:
                missing_status_rows += 1
                per_symbol_degraded = True
            if name_rec is None:
                missing_name_rows += 1
                if exclude_st:
                    per_symbol_degraded = True

            # --- Exclusion: not yet listed / already delisted -----------
            # When a symbol has no PIT status row, fall back to the
            # Stock snapshot for that single symbol (rather than
            # excluding it outright).  This keeps a partially-populated
            # PIT table useful: symbols we have history for get the
            # correct PIT treatment, the rest inherit the snapshot
            # (which is correct for "today" and marked as degraded).
            if status is not None:
                if status.status == "delisted":
                    excluded["delisted"] = excluded.get("delisted", 0) + 1
                    continue
                if status.status == "suspended":
                    excluded["suspended"] = excluded.get("suspended", 0) + 1
                    continue
                if status.status not in _LISTED_STATUSES and status.status != "delisted":
                    # ST / SST / *ST are regulatory cages, not delisting.
                    # Keep them in the candidate pool — the ST-name filter
                    # below decides whether they are eligible.
                    pass
            else:
                # No PIT status row for this symbol — fall back to snapshot.
                per_symbol_degraded = True
                if not self._snapshot_is_active(symbol):
                    excluded["not_listed"] = excluded.get("not_listed", 0) + 1
                    continue

            # --- Exclusion: ST / *ST prefix on PIT name -----------------
            if exclude_st:
                name_to_check = name_rec.name if name_rec else None
                if name_to_check is None:
                    # No PIT name — act per st_policy.
                    if st_policy == "strict":
                        name_to_check = snapshot_name
                    elif st_policy == "include_unknown":
                        name_to_check = None
                    else:  # exclude_known
                        name_to_check = None
                if name_to_check and (
                    _st_prefix(name_to_check) or _has_delist_marker(name_to_check)
                ):
                    excluded["st"] = excluded.get("st", 0) + 1
                    continue

            # --- Exclusion: insufficient bars in [start, end] ----------
            bar_count = self._bar_count(symbol, start_date, end_date)
            if bar_count < effective_min:
                excluded["no_bars"] = excluded.get("no_bars", 0) + 1
                continue

            eligible.append((symbol, bar_count))

        # Order by bar count desc, then symbol for deterministic ties.
        eligible.sort(key=lambda item: (-item[1], item[0]))
        kept = [symbol for symbol, _ in eligible[: max(1, min(int(limit), 10_000))]]

        meta: dict[str, Any] = {
            "pool_key": pool_key,
            "as_of": as_of,
            "spec": {
                "exchanges": list(spec.exchanges),
                "index_symbol": spec.index_symbol,
                "exclude_st": spec.exclude_st,
                "st_policy": spec.st_policy,
                "min_trading_days": spec.min_trading_days,
                "min_coverage_ratio": spec.min_coverage_ratio,
                "limit": spec.limit,
            },
            "pit_degraded": globally_degraded or per_symbol_degraded,
            "st_policy": st_policy,
            "total_candidates": total_candidates,
            "eligible_count": len(kept),
            "excluded": excluded,
            "missing_status_rows": missing_status_rows,
            "missing_name_rows": missing_name_rows,
            "pit_status_rows_total": pit_status_total,
            "pit_name_rows_total": pit_name_total,
            "effective_min_trading_days": effective_min,
            "expected_trading_days": expected_count,
            "data_version": f"pit-v1-{as_of.isoformat()}",
        }
        return PitUniverseResult(symbols=kept, meta=meta)

    # ------------------------------------------------------------------
    # Materialisation
    # ------------------------------------------------------------------

    def materialize_research_pool(
        self,
        pool_key: str,
        as_of: date,
        members: Iterable[dict[str, Any]],
    ) -> int:
        """Persist the universe build result for auditability.

        ``members`` is an iterable of dicts with keys ``symbol``,
        ``eligible`` (bool), ``exclusion_reason`` (nullable),
        ``name_at`` (nullable), ``status_at`` (nullable).  Existing
        rows for ``(pool_key, as_of)`` are deleted first so re-running
        the same spec replaces atomically.
        """
        # Use ORM-aware delete so the identity map stays consistent
        # across repeated materialisations of the same (pool_key, as_of).
        for existing in list(
            self.session.scalars(
                select(ResearchPoolMember).where(
                    ResearchPoolMember.pool_key == pool_key,
                    ResearchPoolMember.as_of == as_of,
                )
            )
        ):
            self.session.delete(existing)
        self.session.flush()
        count = 0
        for row in members:
            symbol = normalize_a_share_symbol(row["symbol"])
            self.session.add(
                ResearchPoolMember(
                    pool_key=pool_key,
                    as_of=as_of,
                    symbol=symbol,
                    eligible=bool(row["eligible"]),
                    exclusion_reason=row.get("exclusion_reason"),
                    name_at=row.get("name_at"),
                    status_at=row.get("status_at"),
                )
            )
            count += 1
        self.session.commit()
        return count

    def list_research_pool(
        self, pool_key: str, as_of: date
    ) -> list[ResearchPoolMember]:
        stmt = (
            select(ResearchPoolMember)
            .where(
                ResearchPoolMember.pool_key == pool_key,
                ResearchPoolMember.as_of == as_of,
            )
            .order_by(ResearchPoolMember.symbol)
        )
        return list(self.session.scalars(stmt))

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def pit_coverage_report(self) -> dict[str, Any]:
        """Return row counts and degraded-ratio inputs for diagnostics."""
        status_rows = int(
            self.session.scalar(select(func.count(SecurityStatus.id))) or 0
        )
        name_rows = int(
            self.session.scalar(select(func.count(SecurityName.id))) or 0
        )
        status_missing_announced = int(
            self.session.scalar(
                select(func.count(SecurityStatus.id)).where(
                    SecurityStatus.announced_at.is_(None)
                )
            )
            or 0
        )
        name_missing_announced = int(
            self.session.scalar(
                select(func.count(SecurityName.id)).where(
                    SecurityName.announced_at.is_(None)
                )
            )
            or 0
        )
        index_const_rows = int(
            self.session.scalar(select(func.count(IndexConstituent.id))) or 0
        )
        weight_rows = int(
            self.session.scalar(select(func.count(IndexWeightSnapshot.id))) or 0
        )
        trade_gap_rows = int(
            self.session.scalar(select(func.count(SecurityTradeGap.id))) or 0
        )
        provider_gap_rows = int(
            self.session.scalar(
                select(func.count(SecurityTradeGap.id)).where(
                    SecurityTradeGap.gap_type == "provider_gap"
                )
            )
            or 0
        )
        return {
            "security_status_rows": status_rows,
            "security_name_rows": name_rows,
            "status_missing_announced_at": status_missing_announced,
            "name_missing_announced_at": name_missing_announced,
            "index_constituent_rows": index_const_rows,
            "index_weight_snapshot_rows": weight_rows,
            "security_trade_gap_rows": trade_gap_rows,
            "provider_gap_rows": provider_gap_rows,
            "pit_ready": status_rows > 0 or name_rows > 0,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _candidates_from_index(
        self, index_symbol: str, as_of: date
    ) -> list[tuple[str, str | None]]:
        members = self.index_constituents_as_of(index_symbol, as_of)
        if not members:
            # Index has no PIT members — fall back to current snapshot
            # via Stock table joined by symbol set is not feasible
            # because we have no symbol set.  Return empty: caller
            # sees total_candidates=0 and pit_degraded in meta.
            return []
        out: list[tuple[str, str | None]] = []
        for member in members:
            snapshot = self._snapshot_name(member.symbol)
            out.append((member.symbol, member.name or snapshot))
        return out

    def _candidates_from_stocks(
        self,
        exchanges: tuple[str, ...],
        as_of: date,
    ) -> list[tuple[str, str | None]]:
        # Lazy import to avoid a hard cycle at module load time.
        from app.models.entities import Stock

        stmt = (
            select(Stock.symbol, Stock.name)
            .where(Stock.exchange.in_(tuple(exchanges)))
            .order_by(Stock.symbol)
        )
        candidates = {
            (str(row.symbol), str(row.name) if row.name is not None else None)
            for row in self.session.execute(stmt).all()
        }
        historical_symbols = self.session.scalars(
            select(SecurityStatus.symbol)
            .where(SecurityStatus.valid_from <= as_of)
            .distinct()
        )
        current_symbols = {symbol for symbol, _ in candidates}
        for symbol in historical_symbols:
            normalized = normalize_a_share_symbol(symbol)
            if normalized in current_symbols:
                continue
            if _exchange_for_symbol(normalized) not in exchanges:
                continue
            candidates.add((normalized, None))
        return sorted(candidates, key=lambda item: item[0])

    def _snapshot_is_active(self, symbol: str) -> bool:
        from app.models.entities import Stock

        stock = self.session.get(Stock, symbol)
        return bool(stock is not None and stock.status == "active")

    def _snapshot_name(self, symbol: str) -> str | None:
        from app.models.entities import Stock

        stock = self.session.get(Stock, symbol)
        return stock.name if stock else None

    def _expected_trading_days(
        self, start_date: date, end_date: date
    ) -> int:
        if self._bar_reader is not None:
            try:
                days = self._bar_reader.trading_dates(start_date, end_date)
                if days:
                    return len(days)
            except Exception:
                pass
        # Fall back to a direct query against TradingCalendar when no
        # bar_reader was supplied (e.g. in tests that build a PitRepository
        # with only a Session).  If the calendar is empty we estimate from
        # the calendar span.
        try:
            from app.models.entities import TradingCalendar

            count = self.session.scalar(
                select(func.count(TradingCalendar.trade_date)).where(
                    TradingCalendar.is_open.is_(True),
                    TradingCalendar.trade_date >= start_date,
                    TradingCalendar.trade_date <= end_date,
                )
            )
            if count and int(count) > 0:
                return int(count)
        except Exception:
            pass
        span = max(1, (end_date - start_date).days + 1)
        return max(1, round(span * 5.0 / 7.0))

    def _effective_min_trading_days(
        self,
        start_date: date,
        end_date: date,
        expected_count: int,
        min_trading_days: int | None,
        min_coverage_ratio: float,
    ) -> int:
        from math import ceil

        if min_trading_days is not None:
            return max(1, min(int(min_trading_days), expected_count))
        return max(
            1,
            min(expected_count, max(1, ceil(expected_count * min_coverage_ratio))),
        )

    def _bar_count(
        self, symbol: str, start_date: date, end_date: date
    ) -> int:
        if self._bar_reader is not None:
            try:
                return int(
                    self._bar_reader.daily_bar_count(
                        symbol, start_date, end_date
                    )
                )
            except Exception:
                pass
        # Direct DB fallback when no bar_reader supplied.
        try:
            from app.models.entities import DailyBar

            count = self.session.scalar(
                select(func.count(DailyBar.id)).where(
                    DailyBar.symbol == symbol,
                    DailyBar.trade_date >= start_date,
                    DailyBar.trade_date <= end_date,
                )
            )
            return int(count or 0)
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# Date coercion helpers
# ---------------------------------------------------------------------------


def _coerce_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, pd.Timestamp):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value)
    if value is None:
        raise ValueError("date value is required")
    # Last-resort: pandas to_datetime
    return pd.to_datetime(value).date()


def _coerce_optional_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    return _coerce_date(value)


def _validate_interval(valid_from: date, valid_to: date | None) -> None:
    if valid_to is not None and valid_to <= valid_from:
        raise ValueError("valid_to must be later than valid_from")


def _exchange_for_symbol(symbol: str) -> str:
    if symbol.startswith("920"):
        return "BJ"
    if symbol.startswith(("6", "9")):
        return "SH"
    if symbol.startswith(("0", "2", "3")):
        return "SZ"
    if symbol.startswith(("4", "8")):
        return "BJ"
    return ""


def _deduplicate_rows(rows: Iterable[dict[str, Any]], key_builder) -> list[dict[str, Any]]:
    deduplicated: dict[object, dict[str, Any]] = {}
    for row in rows:
        deduplicated[key_builder(row)] = dict(row)
    return list(deduplicated.values())


def _trade_gap_record(row: SecurityTradeGap) -> SecurityTradeGapRecord:
    return SecurityTradeGapRecord(
        symbol=row.symbol,
        trade_date=row.trade_date,
        expected_open=row.expected_open,
        has_bar=row.has_bar,
        gap_type=row.gap_type,
        source=row.source,
        confidence=row.confidence,
    )
