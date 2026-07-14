"""Point-in-Time data synchronisation coordinator.

Owned by GLM "Module A".  Mirrors the structure of
:mod:`app.data.full_market`: a ``Protocol``-based coordinator that
drives a :class:`~app.data.akshare_pit_provider.PitProvider` and writes
through :class:`~app.data.pit_repository.PitRepository`, recording each
run as a :class:`~app.models.entities.SyncJob` for audit.

Public surface
--------------

* :class:`PitSyncConfig`        -- per-run configuration
* :class:`PitSyncSummary`       -- result of one sync method
* :class:`PitSyncCoordinator`   -- the coordinator itself

Job types
---------

* ``security_status_current``  -- refresh current ST + listed snapshot
* ``security_delist``          -- backfill delisted intervals (SH/SZ)
* ``security_names``           -- write current name intervals
* ``security_name_history``    -- backfill Shenzhen historical abbreviations
* ``index_constituents``       -- write current index constituent intervals
* ``index_weights``            -- write index weight snapshots
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Protocol, runtime_checkable

import pandas as pd

from app.data.akshare_pit_provider import (
    PitProvider,
    _classify_st_name,
    _is_st_name,
)
from app.data.pit_repository import PitRepository
from app.data.symbols import normalize_a_share_symbol
from app.models.entities import SyncJob

__all__ = [
    "PitSyncConfig",
    "PitSyncSummary",
    "PitSyncCoordinator",
    "PitSyncRepositoryLike",
    "PitSyncProviderLike",
]


@runtime_checkable
class PitSyncRepositoryLike(Protocol):
    """Subset of :class:`PitRepository` used by the coordinator."""

    def upsert_security_status(self, rows: Iterable[dict[str, Any]]) -> int: ...
    def upsert_security_st_status(self, rows: Iterable[dict[str, Any]]) -> int: ...
    def reconcile_current_st_status(
        self, rows: Iterable[dict[str, Any]], as_of: date
    ) -> int: ...
    def upsert_security_name(self, rows: Iterable[dict[str, Any]]) -> int: ...
    def upsert_index_constituent(self, rows: Iterable[dict[str, Any]]) -> int: ...
    def upsert_index_weight_snapshot(self, rows: Iterable[dict[str, Any]]) -> int: ...
    def pit_coverage_report(self) -> dict[str, Any]: ...


@runtime_checkable
class PitSyncProviderLike(Protocol):
    """Subset of :class:`AkSharePitProvider` used by the coordinator."""

    def current_st_list(self) -> pd.DataFrame: ...
    def sh_delist(self) -> pd.DataFrame: ...
    def sz_delist(self) -> pd.DataFrame: ...
    def sz_name_changes(self) -> pd.DataFrame: ...
    def stock_list_with_list_date(self) -> pd.DataFrame: ...
    def index_constituents_current(self, index_symbol: str) -> pd.DataFrame: ...
    def index_weights_current(self, index_symbol: str) -> pd.DataFrame: ...


@dataclass(frozen=True)
class PitSyncConfig:
    """Configuration for :class:`PitSyncCoordinator`.

    Attributes:
        default_source: Source tag stamped on rows that don't carry
            their own ``source`` (e.g. rows back-filled from the
            ``stocks`` snapshot).
        today: Override for "today" — useful for deterministic tests.
            ``None`` means use :func:`datetime.utcnow`'s date.
        default_confidence: Confidence stamped on rows without an
            explicit ``announced_at``.
    """

    default_source: str = "akshare"
    today: date | None = None
    default_confidence: str = "medium"

    def effective_today(self) -> date:
        return self.today if self.today is not None else datetime.utcnow().date()


@dataclass
class PitSyncSummary:
    """Result of one coordinator sync method.

    ``job_type`` matches the ``SyncJob.job_type`` column so the API
    layer can return a consistent payload.  ``records`` is the number of
    rows actually written through the repository.
    """

    job_type: str
    target: str
    records: int = 0
    status: str = "success"
    message: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class PitSyncCoordinator:
    """Drive the PIT provider and persist results through the repository.

    Each public method (``sync_security_status_current`` etc.) is
    idempotent: re-running with the same input replaces the affected
    interval rows rather than appending duplicates.  Failures are
    recorded as ``SyncJob`` rows with ``status="failed"`` and re-raised
    so the API layer can map them to HTTP errors.
    """

    def __init__(
        self,
        repository: PitRepository | PitSyncRepositoryLike,
        provider: PitProvider | PitSyncProviderLike,
        config: PitSyncConfig | None = None,
        *,
        job_recorder: Any | None = None,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._config = config if config is not None else PitSyncConfig()
        # Optional callable matching MarketDataRepository.create_sync_job
        # signature; when None we skip SyncJob persistence (useful for
        # tests that don't have a MarketDataRepository on hand).
        self._job_recorder = job_recorder

    @property
    def config(self) -> PitSyncConfig:
        return self._config

    # ------------------------------------------------------------------
    # Public sync methods
    # ------------------------------------------------------------------

    def sync_security_status_current(self) -> PitSyncSummary:
        """Refresh the *current* ST + listed snapshot.

        Writes one ``listed``/``normal`` interval per currently-listed
        symbol (from the listing-date-enriched stock list) and one
        ``st`` / ``st_star`` / ``sst`` interval per current ST name.
        All intervals start at ``today`` (or the listing date when
        known to be in the past) with ``valid_to=None``.
        """
        today = self._config.effective_today()
        try:
            listed = self._provider.stock_list_with_list_date()
            st_list = self._provider.current_st_list()
        except Exception as exc:
            self._record_job(
                "security_status_current", "all", "failed", message=str(exc)
            )
            raise

        listed_rows: list[dict[str, Any]] = []
        for row in listed.to_dict("records"):
            list_date = row.get("list_date")
            if isinstance(list_date, date) and list_date > today:
                continue
            valid_from = list_date if isinstance(list_date, date) else today
            listed_rows.append(
                {
                    "symbol": row["symbol"],
                    "status": "listed",
                    "valid_from": valid_from,
                    "valid_to": None,
                    "announced_at": list_date if isinstance(list_date, date) else None,
                    "source": row.get("source", self._config.default_source),
                    "confidence": "high"
                    if isinstance(list_date, date)
                    else self._config.default_confidence,
                }
            )

        st_rows: list[dict[str, Any]] = []
        st_by_symbol = {
            normalize_a_share_symbol(row["symbol"]): str(row["status"])
            for row in st_list.to_dict("records")
        }
        for row in st_list.to_dict("records"):
            st_rows.append(
                {
                    "symbol": row["symbol"],
                    "status": row["status"],
                    "valid_from": today,
                    "valid_to": None,
                    "announced_at": None,
                    "source": row.get("source", self._config.default_source),
                    "confidence": self._config.default_confidence,
                }
            )
        st_axis_rows = [
            {
                "symbol": row["symbol"],
                "st_status": st_by_symbol.get(
                    normalize_a_share_symbol(row["symbol"]), "normal"
                ),
                "valid_from": today,
                "valid_to": None,
                "announced_at": None,
                "source": row.get("source", self._config.default_source),
                "confidence": self._config.default_confidence,
            }
            for row in listed.to_dict("records")
            if not isinstance(row.get("list_date"), date)
            or row["list_date"] <= today
        ]
        listed_written = self._repository.upsert_security_status(listed_rows)
        st_written = self._repository.reconcile_current_st_snapshot(st_rows, today)
        st_axis_written = self._repository.reconcile_current_st_status(
            st_axis_rows, today
        )
        written = listed_written + st_written
        st_count = len(st_rows)
        summary = PitSyncSummary(
            job_type="security_status_current",
            target="all",
            records=written,
            status="success",
            extras={
                "listed": listed_written,
                "st": st_count,
                "st_axis": st_axis_written,
            },
        )
        self._record_job(
            summary.job_type,
            summary.target,
            summary.status,
            records=summary.records,
            message=f"listed={listed_written}, st={st_count}",
        )
        return summary

    def sync_security_delist(self) -> PitSyncSummary:
        """Back-fill delisted intervals from SH + SZ delist endpoints."""
        try:
            sh = self._provider.sh_delist()
            sz = self._provider.sz_delist()
        except Exception as exc:
            self._record_job("security_delist", "all", "failed", message=str(exc))
            raise

        rows: list[dict[str, Any]] = []
        for row in pd.concat([sh, sz], ignore_index=True).to_dict("records"):
            delist_date = row.get("delist_date")
            list_date = row.get("list_date")
            if not isinstance(delist_date, date):
                # Without a delist date we cannot build a valid interval.
                continue
            if isinstance(list_date, date) and list_date < delist_date:
                # Also record the original listing interval so
                # ``status_as_of`` before the delist returns 'listed'.
                rows.append(
                    {
                        "symbol": row["symbol"],
                        "status": "listed",
                        "valid_from": list_date,
                        "valid_to": delist_date,
                        "announced_at": list_date,
                        "source": row.get("source", self._config.default_source),
                        "confidence": "high",
                    }
                )
            rows.append(
                {
                    "symbol": row["symbol"],
                    "status": "delisted",
                    "valid_from": delist_date,
                    "valid_to": None,
                    "announced_at": delist_date,
                    "delist_reason": row.get("name"),
                    "source": row.get("source", self._config.default_source),
                    "confidence": "high",
                }
            )

        written = self._repository.upsert_security_status(rows)
        summary = PitSyncSummary(
            job_type="security_delist",
            target="all",
            records=written,
            status="success",
            extras={"sh": len(sh), "sz": len(sz)},
        )
        self._record_job(
            summary.job_type,
            summary.target,
            summary.status,
            records=summary.records,
            message=f"sh={len(sh)}, sz={len(sz)}",
        )
        return summary

    def sync_security_names(self) -> PitSyncSummary:
        """Write one ``security_name`` interval per known current name.

        AkShare does not expose a full name-change history without a
        Tushare token, so this first version writes the *current* name
        with ``valid_from = earliest known date`` (listing date when
        available, otherwise ``today``) and ``confidence = medium``.
        The historical ST-interval gap is documented in the plan.
        """
        today = self._config.effective_today()
        try:
            listed = self._provider.stock_list_with_list_date()
            st_list = self._provider.current_st_list()
        except Exception as exc:
            self._record_job("security_names", "all", "failed", message=str(exc))
            raise

        rows: list[dict[str, Any]] = []
        # Use the most up-to-date name from the ST list when available
        # (it carries the ST prefix); otherwise use the listed name.
        st_names = {
            str(row["symbol"]): str(row["name"])
            for row in st_list.to_dict("records")
        }
        for row in listed.to_dict("records"):
            symbol = str(row["symbol"])
            list_date = row.get("list_date")
            # This endpoint is a current snapshot, not a name-history source.
            # Backdating today's name to list_date would leak later renames
            # and ST prefixes into historical universes.
            valid_from = today
            name = st_names.get(symbol) or str(row.get("name") or "")
            if not name:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "valid_from": valid_from,
                    "valid_to": None,
                    "announced_at": today,
                    "source": row.get("source", self._config.default_source),
                    "confidence": self._config.default_confidence,
                }
            )
        written = self._repository.reconcile_current_names(rows, today)
        summary = PitSyncSummary(
            job_type="security_names",
            target="all",
            records=written,
            status="success",
            extras={"st_names_overridden": len(st_names)},
        )
        self._record_job(
            summary.job_type,
            summary.target,
            summary.status,
            records=summary.records,
            message=f"st_overrides={len(st_names)}",
        )
        return summary

    def sync_security_name_history(self) -> PitSyncSummary:
        """Backfill Shenzhen historical names and ST-state intervals.

        The SZSE abbreviation-change feed supplies effective dates but no
        publication time.  Therefore all generated intervals retain
        ``announced_at=None`` and ST rows use ``confidence=medium``.  This
        deliberately avoids treating a later data refresh as information
        known on the historical change date.
        """
        try:
            changes = self._provider.sz_name_changes()
        except Exception as exc:
            self._record_job("security_name_history", "SZ", "failed", message=str(exc))
            raise

        name_rows = _historical_name_rows(changes, self._config.default_source)
        st_rows = _historical_st_rows(changes, self._config.default_source)
        names_written = self._repository.upsert_security_name(name_rows)
        st_written = self._repository.upsert_security_st_status(st_rows)
        summary = PitSyncSummary(
            job_type="security_name_history",
            target="SZ",
            records=names_written + st_written,
            status="success",
            extras={
                "name_changes": names_written,
                "st_intervals": st_written,
            },
        )
        self._record_job(
            summary.job_type,
            summary.target,
            summary.status,
            records=summary.records,
            message=f"names={names_written}, st_intervals={st_written}",
        )
        return summary

    def sync_index_constituents(self, index_symbol: str) -> PitSyncSummary:
        """Write the *current* constituent intervals for *index_symbol*."""
        target = normalize_a_share_symbol(index_symbol)
        try:
            raw = self._provider.index_constituents_current(target)
        except Exception as exc:
            self._record_job("index_constituents", target, "failed", message=str(exc))
            raise
        if raw.empty:
            summary = PitSyncSummary(
                job_type="index_constituents",
                target=target,
                records=0,
                status="empty",
                message="provider returned no constituents",
            )
            self._record_job(summary.job_type, summary.target, summary.status, message=summary.message)
            return summary

        snapshot_date = raw["snapshot_date"].iloc[0] if "snapshot_date" in raw.columns else None
        valid_from = snapshot_date if isinstance(snapshot_date, date) else self._config.effective_today()
        rows = [
            {
                "index_symbol": target,
                "symbol": str(row["symbol"]),
                "valid_from": valid_from,
                "valid_to": None,
                "announced_at": valid_from,
                "source": str(row.get("source", self._config.default_source)),
            }
            for row in raw.to_dict("records")
        ]
        written = self._repository.reconcile_index_constituent_snapshot(
            target,
            rows,
            valid_from,
        )
        summary = PitSyncSummary(
            job_type="index_constituents",
            target=target,
            records=written,
            status="success",
            extras={"snapshot_date": valid_from.isoformat()},
        )
        self._record_job(
            summary.job_type,
            summary.target,
            summary.status,
            records=summary.records,
            message=f"snapshot={valid_from.isoformat()}",
        )
        return summary

    def sync_index_weights(self, index_symbol: str) -> PitSyncSummary:
        """Write the *current* weight snapshot for *index_symbol*."""
        target = normalize_a_share_symbol(index_symbol)
        try:
            raw = self._provider.index_weights_current(target)
        except Exception as exc:
            self._record_job("index_weights", target, "failed", message=str(exc))
            raise
        if raw.empty:
            summary = PitSyncSummary(
                job_type="index_weights",
                target=target,
                records=0,
                status="empty",
                message="provider returned no weights",
            )
            self._record_job(summary.job_type, summary.target, summary.status, message=summary.message)
            return summary

        trade_date = raw["trade_date"].iloc[0] if "trade_date" in raw.columns else None
        if not isinstance(trade_date, date):
            trade_date = self._config.effective_today()
        rows = [
            {
                "index_symbol": target,
                "symbol": str(row["symbol"]),
                "trade_date": trade_date,
                "weight": row.get("weight"),
                "source": str(row.get("source", self._config.default_source)),
            }
            for row in raw.to_dict("records")
        ]
        written = self._repository.upsert_index_weight_snapshot(rows)
        summary = PitSyncSummary(
            job_type="index_weights",
            target=target,
            records=written,
            status="success",
            extras={"trade_date": trade_date.isoformat()},
        )
        self._record_job(
            summary.job_type,
            summary.target,
            summary.status,
            records=summary.records,
            message=f"trade_date={trade_date.isoformat()}",
        )
        return summary

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _record_job(
        self,
        job_type: str,
        target: str,
        status: str,
        *,
        records: int = 0,
        message: str = "",
    ) -> SyncJob | None:
        if self._job_recorder is None:
            return None
        try:
            return self._job_recorder(
                job_type=job_type,
                target=target,
                status=status,
                records=records,
                message=message,
            )
        except Exception:
            # Failing to record the job must not abort the sync.
            return None


def _historical_name_rows(
    changes: pd.DataFrame,
    default_source: str,
) -> list[dict[str, Any]]:
    """Convert post-change names to half-open historical intervals."""
    rows: list[dict[str, Any]] = []
    if changes.empty:
        return rows
    for _, group in changes.groupby("symbol", sort=False):
        events = group.sort_values("change_date").to_dict("records")
        for index, event in enumerate(events):
            next_date = (
                events[index + 1]["change_date"]
                if index + 1 < len(events)
                else None
            )
            rows.append(
                {
                    "symbol": event["symbol"],
                    "name": event["name"],
                    "valid_from": event["change_date"],
                    "valid_to": next_date,
                    "announced_at": None,
                    "source": event.get("source", default_source),
                }
            )
    return rows


def _historical_st_rows(
    changes: pd.DataFrame,
    default_source: str,
) -> list[dict[str, Any]]:
    """Extract ST-state transitions from the SZSE name-change history.

    We intentionally do not infer a ``normal`` interval before the first
    observed ST transition.  The source only tells us when a changed name
    appears, not the complete naming history before its earliest record.
    """
    rows: list[dict[str, Any]] = []
    if changes.empty:
        return rows
    for _, group in changes.groupby("symbol", sort=False):
        active: dict[str, Any] | None = None
        for event in group.sort_values("change_date").to_dict("records"):
            status = _historical_st_status(event["name"])
            if active is None:
                if status == "normal":
                    continue
                active = {
                    "symbol": event["symbol"],
                    "st_status": status,
                    "valid_from": event["change_date"],
                    "valid_to": None,
                    "announced_at": None,
                    "source": event.get("source", default_source),
                    "confidence": "medium",
                }
                rows.append(active)
                continue
            if active["st_status"] == status:
                continue
            active["valid_to"] = event["change_date"]
            active = {
                "symbol": event["symbol"],
                "st_status": status,
                "valid_from": event["change_date"],
                "valid_to": None,
                "announced_at": None,
                "source": event.get("source", default_source),
                "confidence": "medium",
            }
            rows.append(active)
    return rows


def _historical_st_status(name: str) -> str:
    """Classify an SZSE historical abbreviation into the ST vocabulary."""
    normalized = str(name or "").strip()
    if _is_st_name(normalized):
        return _classify_st_name(normalized)
    if "\u9000\u5e02" in normalized or "\u9000" in normalized:
        return "st_star"
    return "normal"
