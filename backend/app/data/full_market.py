"""Full-market daily sync orchestration.

This module is a *pure orchestration layer*: it selects the next batch of
incomplete symbols, drives a data provider symbol-by-symbol, rate-limits
requests between symbols, persists results via a repository, and applies a
per-symbol circuit breaker.  It has no FastAPI dependency and relies on two
minimal ``Protocol`` contracts so that routes can inject any
provider/repository implementation.

Only this module (and its tests) own the full-market sync contract;
``repository.py``, ``entities.py`` and the data schemas are intentionally
left untouched for other developers.

Resume / checkpoint semantics: completion is delegated to the repository's
``next_research_sync_symbols`` — symbols with a ``success`` or ``empty``
sync job (or full bar coverage) for the requested range are not re-selected.
Repeated ``run_batch`` calls therefore advance progress and naturally resume
after an interruption.  The per-symbol circuit breaker is in-process state
and does not persist across coordinator instances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from time import sleep as _default_sleep
from typing import Any, Callable, Protocol, runtime_checkable

import pandas as pd

from app.data.symbols import normalize_a_share_symbol

__all__ = [
    "DEFAULT_EXCHANGES",
    "MAX_BATCH_SIZE",
    "FullMarketSyncConfig",
    "FullMarketSyncItem",
    "FullMarketSyncBatchSummary",
    "FullMarketSyncCoordinator",
    "FullMarketSyncRepository",
    "FullMarketSyncProvider",
]

DEFAULT_EXCHANGES: tuple[str, ...] = ("SH", "SZ", "BJ")
MAX_BATCH_SIZE: int = 50


@runtime_checkable
class FullMarketSyncProvider(Protocol):
    """Minimal provider contract: just the daily-bars fetcher."""

    def daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        ...


@runtime_checkable
class FullMarketSyncRepository(Protocol):
    """Minimal repository contract for full-market sync.

    The concrete :class:`~app.data.repository.MarketDataRepository`
    satisfies this protocol structurally; it is declared here so the
    coordinator depends only on the operations it actually uses.
    """

    def next_research_sync_symbols(
        self,
        start_date: date,
        end_date: date,
        batch_size: int = 20,
        exchanges: tuple[str, ...] = ("SH", "SZ"),
        exclude_risk_names: bool = True,
    ) -> list[str]:
        ...

    def replace_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        bars: pd.DataFrame,
    ) -> int:
        ...

    def create_sync_job(
        self,
        job_type: str,
        target: str,
        status: str,
        records: int = 0,
        message: str = "",
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> Any:
        ...


@dataclass(frozen=True)
class FullMarketSyncConfig:
    """Configuration for :class:`FullMarketSyncCoordinator`.

    Attributes:
        batch_size: Symbols processed per batch; clamped to ``1..50``.
        exchanges: Exchanges to include; defaults to SH/SZ/BJ (full market).
        max_failures: Per-symbol consecutive failure threshold; once reached
            the symbol is skipped on subsequent batches.
        min_request_interval: Seconds to sleep between consecutive provider
            calls within a batch; ``0`` disables rate limiting.
        adjust: Adjustment type forwarded to the provider (``qfq``/``hfq``/````).
        exclude_risk_names: Forwarded to the repository query; ``False`` keeps
            ST/退 names for full-market semantics.
    """

    batch_size: int = 20
    exchanges: tuple[str, ...] = DEFAULT_EXCHANGES
    max_failures: int = 3
    min_request_interval: float = 0.0
    adjust: str = "qfq"
    exclude_risk_names: bool = False
    retry_failed: bool = False

    def __post_init__(self) -> None:
        if not (1 <= self.batch_size <= MAX_BATCH_SIZE):
            raise ValueError(
                f"batch_size must be between 1 and {MAX_BATCH_SIZE}, "
                f"got {self.batch_size}"
            )
        if self.max_failures < 1:
            raise ValueError(f"max_failures must be >= 1, got {self.max_failures}")
        if self.min_request_interval < 0:
            raise ValueError(
                f"min_request_interval must be >= 0, got {self.min_request_interval}"
            )
        if not self.exchanges:
            raise ValueError("exchanges must not be empty")
        normalized = tuple(str(e).strip().upper() for e in self.exchanges)
        if any(not e for e in normalized):
            raise ValueError("exchanges must not contain blank entries")
        object.__setattr__(self, "exchanges", normalized)


@dataclass
class FullMarketSyncItem:
    """Per-symbol result within a batch summary."""

    symbol: str
    status: str  # "success" | "empty" | "failed" | "skipped"
    synced: int = 0
    message: str = ""


@dataclass
class FullMarketSyncBatchSummary:
    """Summary of a single ``run_batch`` call.

    Attributes:
        total: Candidates returned by the repository for this batch.
        processed: Symbols actually attempted (``success`` + ``empty`` +
            ``failed``); circuit-broken symbols are not processed.
        success: Number of symbols written to the repository.
        empty: Number of symbols with no data in range.
        failed: Number of symbols whose fetch/persist raised.
        skipped: Number of symbols skipped by the circuit breaker.
        completed: ``True`` when no symbol was processed this round (no
            incomplete candidates remain, or all remaining are
            circuit-broken). Routes should stop auto-driving when set.
        items: Ordered per-symbol results.
    """

    total: int
    processed: int
    success: int
    empty: int
    failed: int
    skipped: int
    completed: bool
    blocked: bool
    items: list[FullMarketSyncItem] = field(default_factory=list)


class FullMarketSyncCoordinator:
    """Orchestrate full-market daily sync one batch at a time.

    The coordinator is deliberately framework-agnostic: a FastAPI route can
    construct it with a concrete :class:`MarketDataRepository` and
    :class:`AkShareProvider`, call :meth:`run_batch`, and return the summary.
    """

    def __init__(
        self,
        repository: FullMarketSyncRepository,
        provider: FullMarketSyncProvider,
        config: FullMarketSyncConfig | None = None,
        sleeper: Callable[[float], None] = _default_sleep,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._config = config if config is not None else FullMarketSyncConfig()
        self._sleeper = sleeper
        self._consecutive_failures: dict[str, int] = {}

    @property
    def config(self) -> FullMarketSyncConfig:
        return self._config

    def consecutive_failures(self, symbol: str) -> int:
        """Read-only view of a symbol's current consecutive failure count."""
        try:
            normalized = normalize_a_share_symbol(symbol)
        except ValueError:
            normalized = str(symbol).strip()
        return self._consecutive_failures.get(normalized, 0)

    def run_batch(self, start_date: date, end_date: date) -> FullMarketSyncBatchSummary:
        """Select and sync the next batch of incomplete symbols.

        Returns a :class:`FullMarketSyncBatchSummary`. Repeated calls resume
        from where the previous batch left off; symbols that hit
        ``max_failures`` consecutive failures are skipped.
        """
        candidates = self._repository.next_research_sync_symbols(
            start_date,
            end_date,
            batch_size=self._config.batch_size,
            exchanges=self._config.exchanges,
            exclude_risk_names=self._config.exclude_risk_names,
        )

        items: list[FullMarketSyncItem] = []
        success = empty = failed = skipped = 0
        processed = 0
        attempted = False

        for raw_symbol in candidates:
            try:
                symbol = normalize_a_share_symbol(raw_symbol)
            except ValueError:
                symbol = str(raw_symbol).strip()

            persistent_failure_reader = getattr(
                self._repository, "consecutive_sync_failures", None
            )
            persistent_failures = (
                int(
                    persistent_failure_reader(
                        symbol,
                        start_date,
                        end_date,
                        limit=self._config.max_failures,
                    )
                )
                if callable(persistent_failure_reader)
                else 0
            )
            failure_count = max(
                self._consecutive_failures.get(symbol, 0),
                persistent_failures,
            )
            if not self._config.retry_failed and failure_count >= self._config.max_failures:
                items.append(
                    FullMarketSyncItem(
                        symbol=symbol, status="skipped", message="circuit breaker open"
                    )
                )
                skipped += 1
                continue

            if attempted and self._config.min_request_interval > 0:
                self._sleeper(self._config.min_request_interval)
            attempted = True
            processed += 1

            try:
                bars = self._provider.daily_bars(
                    symbol, start_date, end_date, self._config.adjust
                )
            except Exception as exc:
                self._record_failure(symbol, exc, start_date, end_date)
                items.append(
                    FullMarketSyncItem(symbol=symbol, status="failed", message=str(exc))
                )
                failed += 1
                continue

            if bars.empty:
                self._consecutive_failures[symbol] = 0
                self._repository.create_sync_job(
                    "daily", symbol, "empty", start_date=start_date, end_date=end_date
                )
                items.append(
                    FullMarketSyncItem(symbol=symbol, status="empty", message="no data")
                )
                empty += 1
                continue

            try:
                count = self._repository.replace_daily_bars(
                    symbol, start_date, end_date, bars
                )
            except Exception as exc:
                self._record_failure(symbol, exc, start_date, end_date)
                items.append(
                    FullMarketSyncItem(symbol=symbol, status="failed", message=str(exc))
                )
                failed += 1
                continue

            self._consecutive_failures[symbol] = 0
            self._repository.create_sync_job(
                "daily",
                symbol,
                "success",
                records=count,
                start_date=start_date,
                end_date=end_date,
            )
            items.append(
                FullMarketSyncItem(symbol=symbol, status="success", synced=count)
            )
            success += 1

        blocked = processed == 0 and len(candidates) > 0
        return FullMarketSyncBatchSummary(
            total=len(candidates),
            processed=processed,
            success=success,
            empty=empty,
            failed=failed,
            skipped=skipped,
            completed=(len(candidates) == 0),
            blocked=blocked,
            items=items,
        )

    def _record_failure(
        self, symbol: str, exc: Exception, start_date: date, end_date: date
    ) -> None:
        self._consecutive_failures[symbol] = self._consecutive_failures.get(symbol, 0) + 1
        self._repository.create_sync_job(
            "daily",
            symbol,
            "failed",
            message=str(exc),
            start_date=start_date,
            end_date=end_date,
        )
