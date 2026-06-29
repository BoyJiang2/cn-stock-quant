"""Point-in-Time (PIT) data foundation models.

This module is owned by the GLM "Module A: PIT data foundation" boundary
(see ``backend/glm-point-in-time-plan.md`` section 9).  It introduces five
new tables that record *time-variant* security metadata so that historical
backtests can rebuild the tradable universe as of any past date without
importing current-snapshot survivorship or look-ahead bias.

Tables
------
* :class:`SecurityStatus`        -- listed / delisted / st / suspended intervals
* :class:`SecurityName`          -- security name history (with ST/*ST prefix)
* :class:`IndexConstituent`      -- index membership intervals (CSI semi-annual)
* :class:`IndexWeightSnapshot`   -- index weight snapshots (per rebalance date)
* :class:`ResearchPoolMember`    -- materialised PIT research universe for audit

All five tables are *additive* — the existing ``stocks`` / ``daily_bars`` /
``index_daily_bars`` / ``trading_calendar`` schemas in
:mod:`app.models.entities` are intentionally left untouched so the legacy
"current snapshot" path keeps working while the PIT path is being built.

The shared :class:`~app.models.entities.Base` declarative registry is
imported so ``Base.metadata.create_all`` will pick these tables up once
:mod:`app.models.pit` is imported at startup (see
:mod:`app.core.database`).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base

__all__ = [
    "SecurityStatus",
    "SecurityName",
    "IndexConstituent",
    "IndexWeightSnapshot",
    "ResearchPoolMember",
    "PIT_SECURITY_STATUSES",
    "PIT_CONFIDENCE_LEVELS",
    "PIT_ST_POLICIES",
]

# Canonical security-status vocabulary.  ``listed``/``normal`` are used
# interchangeably for currently-trading names; ``st``/``sst``/``st_star``
# distinguish the A-share ST / SST / *ST regulatory tiers; ``suspended``
# covers trading halts that are not delisting; ``delisted`` is terminal.
PIT_SECURITY_STATUSES: frozenset[str] = frozenset(
    {"listed", "normal", "st", "sst", "st_star", "suspended", "delisted"}
)

# Confidence bucket that travels with every PIT row.  ``high`` requires an
# explicit ``announced_at`` from the source; ``medium`` falls back to
# ``valid_from``; ``low`` is reserved for inferred / synthetic data.
PIT_CONFIDENCE_LEVELS: frozenset[str] = frozenset({"high", "medium", "low"})

# Allowed values for the ST policy parameter on PIT universe queries.
PIT_ST_POLICIES: frozenset[str] = frozenset(
    {"exclude_known", "include_unknown", "strict"}
)

# Sentinel used in queries to compare against ``valid_to`` NULLs without
# coaxing SQLite into a special NULL collation.  Chosen far enough in the
# future to outlive any realistic A-share backtest.
_FAR_FUTURE = date(9999, 12, 31)


class SecurityStatus(Base):
    """Time interval during which *symbol* had a given regulatory status.

    Each row is a half-open interval ``[valid_from, valid_to)``.  A NULL
    ``valid_to`` means the status still holds today.  Consecutive
    intervals for the same symbol are expected to chain (next
    ``valid_from`` == previous ``valid_to`` + 1 trading day), though the
    table does not enforce this at the DB level — the synchronisation
    layer is responsible for emitting contiguous segments.

    ``announced_at`` is the *market-visible* date — the day the
    information became public.  PIT queries must filter
    ``announced_at <= as_of`` (falling back to ``valid_from`` when
    ``announced_at`` is missing) to avoid look-ahead bias.
    """

    __tablename__ = "security_status"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "status", "valid_from", name="uq_sec_status_symbol_status_from"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    announced_at: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    delist_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[str] = mapped_column(String(8), nullable=False, default="high")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class SecurityName(Base):
    """Time interval during which *symbol* was named *name*.

    The ``name`` column preserves any ST / *ST / SST / S*ST / 退 prefix so
    PIT ST-filtering can be done by name prefix on the historical name
    rather than on the current snapshot (which is the source of look-ahead
    bias in the legacy ``Stock.name`` path).
    """

    __tablename__ = "security_name"
    __table_args__ = (
        UniqueConstraint("symbol", "valid_from", name="uq_sec_name_symbol_from"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    announced_at: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class IndexConstituent(Base):
    """Index membership interval for one (index_symbol, symbol) pair.

    CSI indices rebalance semi-annually, so the interval model is far
    cheaper than per-day snapshots.  ``announced_at`` is the date CSI
    published the rebalance list; ``valid_from`` is the effective date.
    The two differ by a few weeks, and that gap is exactly what PIT
    queries exploit to avoid look-ahead bias at the rebalance boundary.
    """

    __tablename__ = "index_constituent"
    __table_args__ = (
        UniqueConstraint(
            "index_symbol", "symbol", "valid_from", name="uq_idx_const_index_sym_from"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    index_symbol: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    announced_at: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class IndexWeightSnapshot(Base):
    """Free-float-adjusted weight snapshot for one index constituent.

    Unlike :class:`IndexConstituent`, weights are stored as snapshots
    (per ``trade_date``) because CSI publishes monthly weight files and
    intra-rebalance weights drift daily.  PIT queries forward-fill from
    the most recent snapshot ``<= as_of``.
    """

    __tablename__ = "index_weight_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "index_symbol", "symbol", "trade_date", name="uq_idx_wt_index_sym_date"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    index_symbol: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ResearchPoolMember(Base):
    """Materialised PIT research-universe row, written for auditability.

    A row is uniquely identified by ``(pool_key, as_of, symbol)``.
    ``pool_key`` is a deterministic fingerprint of the
    :class:`~app.data.pit_repository.UniverseSpec` parameters (exchanges,
    index, st policy, ...) so the same spec re-issued on the same day
    reproduces identical rows and is safe to deduplicate / cache.

    The table is *derived* — given the four history tables above plus
    ``daily_bars`` it can always be rebuilt.  It exists so that a
    backtest's ``selected_symbols`` can be inspected long after the
    source data has shifted, satisfying the "results must be
    reproducible" stop-condition in
    ``docs/next-stage-pit-qlib-rdagent-news-plan.md``.
    """

    __tablename__ = "research_pool_member"
    __table_args__ = (
        UniqueConstraint(
            "pool_key", "as_of", "symbol", name="uq_pool_key_date_sym"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pool_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    as_of: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    exclusion_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status_at: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
