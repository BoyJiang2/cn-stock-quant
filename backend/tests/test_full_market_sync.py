"""Tests for the full-market sync orchestration layer.

All tests use lightweight in-memory fakes for the provider and repository
so the coordinator's orchestration logic — rate limiting, circuit breaker,
exchange filtering, batch cap, and resume — is verified without touching
the network or a real database.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from app.data.column_map import STANDARD_COLUMNS
from app.data.full_market import (
    DEFAULT_EXCHANGES,
    MAX_BATCH_SIZE,
    FullMarketSyncBatchSummary,
    FullMarketSyncConfig,
    FullMarketSyncCoordinator,
    FullMarketSyncItem,
    FullMarketSyncProvider,
    FullMarketSyncRepository,
)

START = date(2024, 1, 2)
END = date(2024, 1, 4)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _is_risk_name(name: str) -> bool:
    upper = name.upper()
    return "ST" in upper or "退" in name


def _bars_for(symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
    rows = []
    d = start_date
    while d <= end_date:
        rows.append(
            {
                "symbol": symbol,
                "trade_date": d,
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "volume": 1000.0,
                "amount": 10200.0,
                "adj": "qfq",
            }
        )
        d += timedelta(days=1)
    return pd.DataFrame(rows, columns=STANDARD_COLUMNS)


class _FakeProvider:
    """In-memory provider with per-symbol success/empty/fail behavior."""

    def __init__(
        self,
        fail_symbols: tuple[str, ...] = (),
        empty_symbols: tuple[str, ...] = (),
    ) -> None:
        self.fail_symbols = set(fail_symbols)
        self.empty_symbols = set(empty_symbols)
        self.calls: list[tuple[str, date, date, str]] = []

    def daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        self.calls.append((symbol, start_date, end_date, adjust))
        if symbol in self.fail_symbols:
            raise RuntimeError(f"fake failure for {symbol}")
        if symbol in self.empty_symbols:
            return pd.DataFrame(columns=STANDARD_COLUMNS)
        return _bars_for(symbol, start_date, end_date)


class _FakeRepository:
    """In-memory repository mirroring the resume semantics of the real one.

    A symbol is considered complete when a ``success``/``empty`` daily job
    exists whose date range covers the requested range.
    """

    def __init__(self, stocks: dict[str, dict]) -> None:
        # stocks: {symbol: {"exchange": str, "name": str}}
        self.stocks = dict(stocks)
        self.jobs: list[dict] = []
        self.bars_written: dict[str, pd.DataFrame] = {}
        self.replace_calls: list[str] = []

    def next_research_sync_symbols(
        self,
        start_date: date,
        end_date: date,
        batch_size: int = 20,
        exchanges: tuple[str, ...] = ("SH", "SZ"),
        exclude_risk_names: bool = True,
    ) -> list[str]:
        completed = {
            j["target"]
            for j in self.jobs
            if j["job_type"] == "daily"
            and j["status"] in ("success", "empty")
            and j["start_date"] <= start_date
            and j["end_date"] >= end_date
        }
        candidates = [
            sym
            for sym, info in self.stocks.items()
            if info["exchange"] in exchanges
            and (not exclude_risk_names or not _is_risk_name(info["name"]))
            and sym not in completed
        ]
        candidates.sort()
        return candidates[:batch_size]

    def replace_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        bars: pd.DataFrame,
    ) -> int:
        self.bars_written[symbol] = bars
        self.replace_calls.append(symbol)
        return len(bars)

    def create_sync_job(
        self,
        job_type: str,
        target: str,
        status: str,
        records: int = 0,
        message: str = "",
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict:
        job = {
            "job_type": job_type,
            "target": target,
            "status": status,
            "records": records,
            "message": message,
            "start_date": start_date,
            "end_date": end_date,
        }
        self.jobs.append(job)
        return job


def _stocks(*pairs: tuple[str, str, str]) -> dict[str, dict]:
    # pairs: (symbol, exchange, name)
    return {sym: {"exchange": exch, "name": name} for sym, exch, name in pairs}


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_fakes_satisfy_the_minimal_protocols():
    repo = _FakeRepository(_stocks(("000001", "SZ", "Bank")))
    provider = _FakeProvider()

    assert isinstance(repo, FullMarketSyncRepository)
    assert isinstance(provider, FullMarketSyncProvider)


def test_default_config_includes_beijing_exchange():
    config = FullMarketSyncConfig()

    assert config.exchanges == ("SH", "SZ", "BJ")
    assert DEFAULT_EXCHANGES == ("SH", "SZ", "BJ")
    assert MAX_BATCH_SIZE == 50


# ---------------------------------------------------------------------------
# Outcomes: success / empty / failed
# ---------------------------------------------------------------------------


def test_batch_records_success_empty_and_failed_outcomes():
    repo = _FakeRepository(
        _stocks(
            ("000001", "SZ", "OK"),
            ("000002", "SZ", "Empty"),
            ("000003", "SZ", "Fail"),
        )
    )
    provider = _FakeProvider(fail_symbols=("000003",), empty_symbols=("000002",))
    coordinator = FullMarketSyncCoordinator(repo, provider, FullMarketSyncConfig(batch_size=10))

    summary = coordinator.run_batch(START, END)

    assert summary.total == 3
    assert summary.processed == 3
    assert summary.success == 1
    assert summary.empty == 1
    assert summary.failed == 1
    assert summary.skipped == 0
    assert summary.completed is False

    by_symbol = {item.symbol: item for item in summary.items}
    assert by_symbol["000001"].status == "success"
    assert by_symbol["000001"].synced == 3  # 3 trading days in range
    assert by_symbol["000002"].status == "empty"
    assert by_symbol["000002"].message == "no data"
    assert by_symbol["000003"].status == "failed"
    assert "fake failure" in by_symbol["000003"].message

    # Only the successful symbol is persisted; jobs recorded for all three.
    assert set(repo.bars_written) == {"000001"}
    statuses = [j["status"] for j in repo.jobs]
    assert statuses == ["success", "empty", "failed"]


# ---------------------------------------------------------------------------
# Rate limiting between symbols
# ---------------------------------------------------------------------------


def test_rate_limiter_sleeps_between_symbols_only():
    repo = _FakeRepository(
        _stocks(("000001", "SZ", "A"), ("000002", "SZ", "B"), ("000003", "SZ", "C"))
    )
    provider = _FakeProvider()
    sleeps: list[float] = []
    coordinator = FullMarketSyncCoordinator(
        repo,
        provider,
        FullMarketSyncConfig(batch_size=10, min_request_interval=0.5, max_failures=5),
        sleeper=sleeps.append,
    )

    summary = coordinator.run_batch(START, END)

    assert summary.processed == 3
    # 3 symbols -> 2 inter-symbol sleeps; never before the first.
    assert sleeps == [0.5, 0.5]
    # Provider called once per symbol in order.
    assert [c[0] for c in provider.calls] == ["000001", "000002", "000003"]


def test_rate_limiter_disabled_when_interval_zero():
    repo = _FakeRepository(_stocks(("000001", "SZ", "A"), ("000002", "SZ", "B")))
    provider = _FakeProvider()
    sleeps: list[float] = []
    coordinator = FullMarketSyncCoordinator(
        repo,
        provider,
        FullMarketSyncConfig(batch_size=10, min_request_interval=0.0),
        sleeper=sleeps.append,
    )

    coordinator.run_batch(START, END)

    assert sleeps == []


def test_rate_limiter_does_not_sleep_around_skipped_symbols():
    repo = _FakeRepository(
        _stocks(("000001", "SZ", "Failing"), ("000002", "SZ", "OK"))
    )
    provider = _FakeProvider(fail_symbols=("000001",))
    sleeps: list[float] = []
    coordinator = FullMarketSyncCoordinator(
        repo,
        provider,
        FullMarketSyncConfig(batch_size=10, min_request_interval=0.1, max_failures=2),
        sleeper=sleeps.append,
    )

    # Burn through the failing symbol's tolerance.
    coordinator.run_batch(START, END)  # 000001 fails, 000002 succeeds -> 1 sleep
    coordinator.run_batch(START, END)  # 000001 fails again -> 0 sleeps (single symbol)
    skip_summary = coordinator.run_batch(START, END)  # 000001 skipped -> 0 sleeps

    assert skip_summary.skipped == 1
    assert skip_summary.processed == 0
    # Only the single inter-symbol sleep from the first batch.
    assert sleeps == [0.1]


# ---------------------------------------------------------------------------
# Circuit breaker (failure 熔断)
# ---------------------------------------------------------------------------


def test_circuit_breaker_skips_symbol_after_consecutive_threshold():
    repo = _FakeRepository(_stocks(("000001", "SZ", "Always Fails")))
    provider = _FakeProvider(fail_symbols=("000001",))
    coordinator = FullMarketSyncCoordinator(
        repo, provider, FullMarketSyncConfig(batch_size=10, max_failures=2)
    )

    first = coordinator.run_batch(START, END)
    assert first.failed == 1
    assert first.processed == 1
    assert first.completed is False
    assert coordinator.consecutive_failures("000001") == 1

    second = coordinator.run_batch(START, END)
    assert second.failed == 1
    assert second.processed == 1
    assert second.completed is False
    assert coordinator.consecutive_failures("000001") == 2

    third = coordinator.run_batch(START, END)
    assert third.skipped == 1
    assert third.processed == 0
    assert third.failed == 0
    assert third.completed is False
    assert third.blocked is True
    assert third.items[0].status == "skipped"
    assert third.items[0].message == "circuit breaker open"

    # Provider is not called for a skipped symbol.
    assert len(provider.calls) == 2


def test_circuit_breaker_resets_on_success():
    repo = _FakeRepository(_stocks(("000001", "SZ", "A"), ("000002", "SZ", "B")))
    provider = _FakeProvider()
    coordinator = FullMarketSyncCoordinator(
        repo, provider, FullMarketSyncConfig(batch_size=10, max_failures=2)
    )

    coordinator.run_batch(START, END)  # both succeed

    assert coordinator.consecutive_failures("000001") == 0
    assert coordinator.consecutive_failures("000002") == 0
    # Both symbols now have success jobs -> no candidates remain.
    final = coordinator.run_batch(START, END)
    assert final.total == 0
    assert final.completed is True
    assert final.blocked is False


# ---------------------------------------------------------------------------
# Beijing exchange inclusion
# ---------------------------------------------------------------------------


def test_beijing_exchange_symbols_are_included_by_default():
    repo = _FakeRepository(
        _stocks(
            ("000001", "SZ", "SZ Stock"),
            ("600000", "SH", "SH Stock"),
            ("430047", "BJ", "BJ Stock"),
        )
    )
    provider = _FakeProvider()
    coordinator = FullMarketSyncCoordinator(repo, provider, FullMarketSyncConfig(batch_size=10))

    summary = coordinator.run_batch(START, END)

    synced = {item.symbol for item in summary.items}
    assert "430047" in synced  # Beijing exchange present
    assert {"000001", "600000", "430047"} == synced
    assert summary.success == 3


def test_excluding_beijing_exchange_omits_bj_symbols():
    repo = _FakeRepository(
        _stocks(
            ("000001", "SZ", "SZ Stock"),
            ("430047", "BJ", "BJ Stock"),
        )
    )
    provider = _FakeProvider()
    coordinator = FullMarketSyncCoordinator(
        repo,
        provider,
        FullMarketSyncConfig(batch_size=10, exchanges=("SH", "SZ")),
    )

    summary = coordinator.run_batch(START, END)

    assert {item.symbol for item in summary.items} == {"000001"}
    assert "430047" not in {item.symbol for item in summary.items}


# ---------------------------------------------------------------------------
# Single-batch cap (<= 50)
# ---------------------------------------------------------------------------


def test_config_rejects_batch_size_above_cap():
    with pytest.raises(ValueError):
        FullMarketSyncConfig(batch_size=MAX_BATCH_SIZE + 1)
    with pytest.raises(ValueError):
        FullMarketSyncConfig(batch_size=0)


def test_config_accepts_boundary_batch_sizes():
    assert FullMarketSyncConfig(batch_size=1).batch_size == 1
    assert FullMarketSyncConfig(batch_size=MAX_BATCH_SIZE).batch_size == MAX_BATCH_SIZE


def test_batch_never_exceeds_cap_of_50():
    stocks = _stocks(*((f"0000{i:02d}", "SZ", f"S{i}") for i in range(1, 61)))
    repo = _FakeRepository(stocks)
    provider = _FakeProvider()
    coordinator = FullMarketSyncCoordinator(
        repo, provider, FullMarketSyncConfig(batch_size=MAX_BATCH_SIZE)
    )

    summary = coordinator.run_batch(START, END)

    assert summary.total == MAX_BATCH_SIZE
    assert summary.processed == MAX_BATCH_SIZE
    assert summary.success == MAX_BATCH_SIZE
    assert summary.completed is False


# ---------------------------------------------------------------------------
# Resume / checkpoint (断点续传)
# ---------------------------------------------------------------------------


def test_resume_skips_already_synced_symbols_across_batches():
    repo = _FakeRepository(
        _stocks(
            ("000001", "SZ", "SZ Bank"),
            ("430047", "BJ", "BJ Bio"),
            ("600000", "SH", "SH Bank"),
        )
    )
    provider = _FakeProvider()
    coordinator = FullMarketSyncCoordinator(
        repo, provider, FullMarketSyncConfig(batch_size=2)
    )

    # Sorted candidate order: 000001, 430047, 600000.
    first = coordinator.run_batch(START, END)
    assert [item.symbol for item in first.items] == ["000001", "430047"]
    assert first.success == 2
    assert first.completed is False

    second = coordinator.run_batch(START, END)
    assert [item.symbol for item in second.items] == ["600000"]
    assert second.success == 1
    assert second.completed is False

    # Everything is covered now -> no candidates, signal stop.
    third = coordinator.run_batch(START, END)
    assert third.total == 0
    assert third.processed == 0
    assert third.completed is True
    assert third.items == []

    # Provider never re-fetched a completed symbol.
    assert sorted({c[0] for c in provider.calls}) == ["000001", "430047", "600000"]
    assert len(provider.calls) == 3


def test_resume_treats_empty_result_as_completed():
    repo = _FakeRepository(
        _stocks(("000001", "SZ", "A"), ("000002", "SZ", "B"))
    )
    provider = _FakeProvider(empty_symbols=("000001",))
    coordinator = FullMarketSyncCoordinator(
        repo, provider, FullMarketSyncConfig(batch_size=10)
    )

    first = coordinator.run_batch(START, END)
    assert {item.symbol: item.status for item in first.items} == {
        "000001": "empty",
        "000002": "success",
    }

    # 000001's empty job covers the range, so it must not be re-selected.
    second = coordinator.run_batch(START, END)
    assert second.total == 0
    assert second.completed is True


# ---------------------------------------------------------------------------
# Summary shape
# ---------------------------------------------------------------------------


def test_empty_batch_summary_is_well_formed():
    repo = _FakeRepository(_stocks(("000001", "SZ", "A")))
    provider = _FakeProvider()
    coordinator = FullMarketSyncCoordinator(
        repo, provider, FullMarketSyncConfig(batch_size=10)
    )
    # Pre-complete the only symbol.
    repo.create_sync_job("daily", "000001", "success", start_date=START, end_date=END)

    summary = coordinator.run_batch(START, END)

    assert isinstance(summary, FullMarketSyncBatchSummary)
    assert summary.total == 0
    assert summary.processed == 0
    assert summary.success == 0
    assert summary.empty == 0
    assert summary.failed == 0
    assert summary.skipped == 0
    assert summary.completed is True
    assert summary.items == []


def test_skipped_items_use_full_market_sync_item_type():
    repo = _FakeRepository(_stocks(("000001", "SZ", "A")))
    provider = _FakeProvider(fail_symbols=("000001",))
    coordinator = FullMarketSyncCoordinator(
        repo, provider, FullMarketSyncConfig(batch_size=10, max_failures=1)
    )

    coordinator.run_batch(START, END)  # fail once
    summary = coordinator.run_batch(START, END)  # skipped

    assert all(isinstance(item, FullMarketSyncItem) for item in summary.items)
    assert summary.items[0].status == "skipped"
