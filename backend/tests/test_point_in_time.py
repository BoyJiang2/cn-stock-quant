"""Point-in-Time (PIT) data foundation tests.

Covers the acceptance matrix in
``backend/glm-point-in-time-plan.md`` section 7:

* 7.1 status / name PIT unit tests (delisted, ST, announced_at filter)
* 7.2 survivorship-bias regression
* 7.3 look-ahead-bias regression
* 7.4 index constituent PIT (different members per as_of + weight fwd-fill)
* 7.5 reproducibility (same pool_key+as_of yields same materialised rows)
  + integration smoke (HTTP) + global non-regression.

All tests run against an in-memory SQLite database seeded with
synthetic data — no network access.
"""

from __future__ import annotations

from datetime import date
import sys
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import pit as pit_module
from app.core.database import get_session
from app.data.akshare_pit_provider import AkSharePitProvider, _classify_st_name
from app.data.pit_repository import (
    PitRepository,
    UniverseSpec,
    _st_prefix,
)
from app.data.pit_sync import PitSyncConfig, PitSyncCoordinator
from app.main import create_app
from app.models import Base, DailyBar, Stock
from app.models.pit import (
    IndexConstituent,
    IndexWeightSnapshot,
    ResearchPoolMember,
    SecurityName,
    SecurityStatus,
    SecurityTradeGap,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _engine():
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


@pytest.fixture()
def session_factory():
    engine = _engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@pytest.fixture()
def session(session_factory):
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def pit(session) -> PitRepository:
    return PitRepository(session)


def _seed_stock(session, symbol, name, exchange="SZ", status="active"):
    session.merge(
        Stock(symbol=symbol, name=name, exchange=exchange, status=status)
    )
    session.commit()


def _seed_bars(session, symbol, start_date, end_date):
    d = start_date
    while d <= end_date:
        session.add(
            DailyBar(
                symbol=symbol,
                trade_date=d,
                open=10.0,
                high=10.5,
                low=9.8,
                close=10.2,
                volume=1000.0,
                amount=10200.0,
                adj="qfq",
            )
        )
        d = date.fromordinal(d.toordinal() + 1)
    session.commit()


# ---------------------------------------------------------------------------
# 7.1 — status / name PIT unit tests
# ---------------------------------------------------------------------------


def test_status_as_of_returns_listed_before_delist(pit, session):
    """Delisted stock: as_of=2020 → listed; as_of=2023 → delisted."""
    _seed_stock(session, "000001", "Ping An Bank", "SZ")
    pit.upsert_security_status(
        [
            {
                "symbol": "000001",
                "status": "listed",
                "valid_from": date(1991, 4, 3),
                "valid_to": date(2022, 6, 1),
                "announced_at": date(1991, 4, 3),
                "source": "test",
                "confidence": "high",
            },
            {
                "symbol": "000001",
                "status": "delisted",
                "valid_from": date(2022, 6, 1),
                "valid_to": None,
                "announced_at": date(2022, 6, 1),
                "delist_reason": "test delist",
                "source": "test",
                "confidence": "high",
            },
        ]
    )

    assert pit.status_as_of("000001", date(2020, 1, 1)).status == "listed"
    assert pit.status_as_of("000001", date(2023, 1, 1)).status == "delisted"


def test_status_as_of_filters_by_announced_at(pit, session):
    """Delist announced 2022-05-20 effective 2022-06-01.

    as_of=2022-05-25 → still listed (announcement not yet effective).
    as_of=2022-06-02 → delisted.
    """
    _seed_stock(session, "000002", "TestCo", "SH")
    pit.upsert_security_status(
        [
            {
                "symbol": "000002",
                "status": "listed",
                "valid_from": date(2000, 1, 1),
                "valid_to": date(2022, 6, 1),
                "announced_at": date(2000, 1, 1),
                "source": "test",
                "confidence": "high",
            },
            {
                "symbol": "000002",
                "status": "delisted",
                "valid_from": date(2022, 6, 1),
                "valid_to": None,
                "announced_at": date(2022, 5, 20),
                "source": "test",
                "confidence": "high",
            },
        ]
    )

    assert pit.status_as_of("000002", date(2022, 5, 25)).status == "listed"
    assert pit.status_as_of("000002", date(2022, 6, 2)).status == "delisted"


def test_status_as_of_downgrades_confidence_when_announced_at_missing(pit, session):
    """Rows missing announced_at should be downgraded to confidence=medium."""
    _seed_stock(session, "000003", "NoAnnounce", "SZ")
    pit.upsert_security_status(
        [
            {
                "symbol": "000003",
                "status": "listed",
                "valid_from": date(2010, 1, 1),
                "valid_to": None,
                "announced_at": None,  # missing
                "source": "test",
                "confidence": "high",  # source said high but we should downgrade
            }
        ]
    )
    result = pit.status_as_of("000003", date(2020, 6, 1))
    assert result is not None
    assert result.status == "listed"
    assert result.confidence == "medium"
    assert result.degraded is True


def test_name_as_of_picks_historical_st_name(pit, session):
    """ST name from 2023-06 onwards; 2022 query returns pre-ST name."""
    _seed_stock(session, "000004", "ST Foo", "SZ")
    pit.upsert_security_name(
        [
            {
                "symbol": "000004",
                "name": "Foo Industrial",
                "valid_from": date(2010, 1, 1),
                "valid_to": date(2023, 6, 1),
                "announced_at": date(2010, 1, 1),
                "source": "test",
            },
            {
                "symbol": "000004",
                "name": "ST Foo",
                "valid_from": date(2023, 6, 1),
                "valid_to": None,
                "announced_at": date(2023, 6, 1),
                "source": "test",
            },
        ]
    )

    assert pit.name_as_of("000004", date(2022, 6, 1)).name == "Foo Industrial"
    assert pit.name_as_of("000004", date(2024, 1, 1)).name == "ST Foo"


# ---------------------------------------------------------------------------
# 7.2 — survivorship-bias regression
# ---------------------------------------------------------------------------


def test_pit_universe_includes_delisted_stock_in_historical_window(pit, session):
    """PIT backtest 2020 must include a stock that delisted in 2022.

    With the legacy snapshot filter (status='active') the delisted stock
    would be excluded → survivorship bias.  PIT mode keeps it.
    """
    # 1 delisted + 3 active stocks.
    _seed_stock(session, "000001", "Ping An Bank", "SZ", status="active")
    _seed_stock(session, "000002", "Vanke", "SZ", status="active")
    _seed_stock(session, "000003", "Healthcare", "SH", status="active")
    _seed_stock(session, "000004", "DelistedCo", "SH", status="delisted")
    for sym in ("000001", "000002", "000003", "000004"):
        _seed_bars(session, sym, date(2020, 1, 2), date(2020, 1, 31))

    # PIT status: 000004 listed from 2000 → 2022, delisted from 2022.
    # The other three are listed throughout (so the universe build does
    # not fall back to the snapshot and pit_degraded stays False).
    pit.upsert_security_status(
        [
            {
                "symbol": "000004",
                "status": "listed",
                "valid_from": date(2000, 1, 1),
                "valid_to": date(2022, 6, 1),
                "announced_at": date(2000, 1, 1),
                "source": "test",
                "confidence": "high",
            },
            {
                "symbol": "000004",
                "status": "delisted",
                "valid_from": date(2022, 6, 1),
                "valid_to": None,
                "announced_at": date(2022, 6, 1),
                "source": "test",
                "confidence": "high",
            },
        ]
        + [
            {
                "symbol": sym,
                "status": "listed",
                "valid_from": date(2000, 1, 1),
                "valid_to": None,
                "announced_at": date(2000, 1, 1),
                "source": "test",
                "confidence": "high",
            }
            for sym in ("000001", "000002", "000003")
        ]
    )
    # Names (no ST prefix).
    pit.upsert_security_name(
        [
            {
                "symbol": sym,
                "name": f"Name{sym}",
                "valid_from": date(2000, 1, 1),
                "valid_to": None,
                "announced_at": date(2000, 1, 1),
                "source": "test",
            }
            for sym in ("000001", "000002", "000003", "000004")
        ]
    )
    pit.upsert_security_st_status(
        [
            {
                "symbol": sym,
                "st_status": "normal",
                "valid_from": date(2000, 1, 1),
                "valid_to": None,
                "announced_at": date(2000, 1, 1),
                "source": "test",
                "confidence": "high",
            }
            for sym in ("000001", "000002", "000003", "000004")
        ]
    )

    result = pit.select_research_symbols_pit(
        as_of=date(2020, 6, 1),
        start_date=date(2020, 1, 2),
        end_date=date(2020, 1, 31),
        exchanges=("SH", "SZ"),
        limit=10,
    )
    assert "000004" in result.symbols
    assert len(result.symbols) == 4
    assert result.meta["pit_degraded"] is False


def test_legacy_filter_would_have_excluded_delisted_stock(session):
    """Sanity check: the legacy snapshot filter DOES exclude the delisted
    stock — proving that the test above is actually exercising PIT logic
    and not a no-op.
    """
    from app.data.repository import MarketDataRepository

    _seed_stock(session, "000001", "X", "SZ", status="active")
    _seed_stock(session, "000004", "DelistedCo", "SH", status="delisted")
    _seed_bars(session, "000001", date(2020, 1, 2), date(2020, 1, 31))
    _seed_bars(session, "000004", date(2020, 1, 2), date(2020, 1, 31))
    repo = MarketDataRepository(session)
    # The legacy filter requires Stock.status == 'active'.
    selected = repo.select_research_symbols(
        date(2020, 1, 2), date(2020, 1, 31),
        exchanges=("SH", "SZ"), limit=10,
    )
    assert "000004" not in selected
    assert "000001" in selected


# ---------------------------------------------------------------------------
# 7.3 — look-ahead-bias regression
# ---------------------------------------------------------------------------


def test_pit_does_not_exclude_2024_st_for_2022_backtest(pit, session):
    """Stock Z: normal 2020, ST from 2023-06.

    PIT backtest 2022 → Z入选 (ST is a future event as of 2022).
    PIT backtest 2024 → Z excluded (ST is now known).
    """
    _seed_stock(session, "000005", "ST Z", "SZ", status="active")
    _seed_bars(session, "000005", date(2020, 1, 2), date(2024, 12, 31))

    # Name history: pre-ST name until 2023-06, ST name from 2023-06.
    pit.upsert_security_name(
        [
            {
                "symbol": "000005",
                "name": "Z Industrial",
                "valid_from": date(2010, 1, 1),
                "valid_to": date(2023, 6, 1),
                "announced_at": date(2010, 1, 1),
                "source": "test",
            },
            {
                "symbol": "000005",
                "name": "ST Z",
                "valid_from": date(2023, 6, 1),
                "valid_to": None,
                "announced_at": date(2023, 6, 1),
                "source": "test",
            },
        ]
    )
    # Status: listed throughout.
    pit.upsert_security_status(
        [
            {
                "symbol": "000005",
                "status": "listed",
                "valid_from": date(2010, 1, 1),
                "valid_to": None,
                "announced_at": date(2010, 1, 1),
                "source": "test",
                "confidence": "high",
            }
        ]
    )

    # 2022 backtest → ST name not yet known → Z入选
    res_2022 = pit.select_research_symbols_pit(
        as_of=date(2022, 6, 1),
        start_date=date(2022, 1, 2),
        end_date=date(2022, 1, 31),
        exchanges=("SZ",),
        exclude_st=True,
        st_policy="exclude_known",
        limit=10,
    )
    assert "000005" in res_2022.symbols
    assert res_2022.meta["excluded"].get("st", 0) == 0

    # 2024 backtest → ST name known → Z excluded
    res_2024 = pit.select_research_symbols_pit(
        as_of=date(2024, 6, 1),
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        exchanges=("SZ",),
        exclude_st=True,
        st_policy="exclude_known",
        limit=10,
    )
    assert "000005" not in res_2024.symbols
    assert res_2024.meta["excluded"].get("st", 0) >= 1


def test_pit_st_policy_strict_uses_snapshot_for_missing_data(pit, session):
    """With st_policy='strict' and no PIT name row, the legacy snapshot
    name (which carries an ST prefix) IS used to exclude — proving the
    policy actually changes behaviour.
    """
    _seed_stock(session, "000006", "ST FooBar", "SZ", status="active")
    _seed_bars(session, "000006", date(2020, 1, 2), date(2020, 1, 31))
    # Only status (listed), no name history → PIT name lookup misses.
    pit.upsert_security_status(
        [
            {
                "symbol": "000006",
                "status": "listed",
                "valid_from": date(2010, 1, 1),
                "valid_to": None,
                "announced_at": date(2010, 1, 1),
                "source": "test",
                "confidence": "high",
            }
        ]
    )

    # exclude_known: missing PIT name → kept
    res_known = pit.select_research_symbols_pit(
        as_of=date(2020, 6, 1),
        start_date=date(2020, 1, 2),
        end_date=date(2020, 1, 31),
        exchanges=("SZ",),
        st_policy="exclude_known",
        limit=10,
    )
    assert "000006" in res_known.symbols

    # strict: missing PIT name → fall back to Stock.name (ST FooBar) → excluded
    res_strict = pit.select_research_symbols_pit(
        as_of=date(2020, 6, 1),
        start_date=date(2020, 1, 2),
        end_date=date(2020, 1, 31),
        exchanges=("SZ",),
        st_policy="strict",
        limit=10,
    )
    assert "000006" not in res_strict.symbols
    assert res_strict.meta["excluded"].get("st", 0) >= 1


def test_pit_st_policy_include_unknown_keeps_when_name_missing(pit, session):
    """include_unknown: when PIT name is missing, keep the symbol.

    This is the "conservative on missing data" default codified in
    plan section 3.2.  Contrast with ``strict`` (which falls back to
    the snapshot name and would exclude an ST-prefixed snapshot).
    """
    _seed_stock(session, "000007", "ST Bar", "SZ", status="active")
    _seed_bars(session, "000007", date(2020, 1, 2), date(2020, 1, 31))
    # Status row exists but name history does not.
    pit.upsert_security_status(
        [
            {
                "symbol": "000007",
                "status": "listed",
                "valid_from": date(2010, 1, 1),
                "valid_to": None,
                "announced_at": date(2010, 1, 1),
                "source": "test",
                "confidence": "high",
            }
        ]
    )
    res = pit.select_research_symbols_pit(
        as_of=date(2020, 6, 1),
        start_date=date(2020, 1, 2),
        end_date=date(2020, 1, 31),
        exchanges=("SZ",),
        st_policy="include_unknown",
        exclude_st=True,
        limit=10,
    )
    assert "000007" in res.symbols


def test_pit_st_policy_exclude_known_excludes_when_st_name_known(pit, session):
    """exclude_known: when PIT name is known to carry ST, exclude."""
    _seed_stock(session, "000008", "ST Baz", "SZ", status="active")
    _seed_bars(session, "000008", date(2020, 1, 2), date(2020, 1, 31))
    pit.upsert_security_status(
        [
            {
                "symbol": "000008",
                "status": "listed",
                "valid_from": date(2010, 1, 1),
                "valid_to": None,
                "announced_at": date(2010, 1, 1),
                "source": "test",
                "confidence": "high",
            }
        ]
    )
    pit.upsert_security_name(
        [
            {
                "symbol": "000008",
                "name": "ST Baz",
                "valid_from": date(2010, 1, 1),
                "valid_to": None,
                "announced_at": date(2010, 1, 1),
                "source": "test",
            }
        ]
    )
    res = pit.select_research_symbols_pit(
        as_of=date(2020, 6, 1),
        start_date=date(2020, 1, 2),
        end_date=date(2020, 1, 31),
        exchanges=("SZ",),
        st_policy="exclude_known",
        exclude_st=True,
        limit=10,
    )
    assert "000008" not in res.symbols
    assert res.meta["excluded"].get("st", 0) >= 1


# ---------------------------------------------------------------------------
# 7.4 — index constituent PIT
# ---------------------------------------------------------------------------


def _seed_csi300_rebalance(pit, session):
    """Synthetic 沪深300 rebalance: 2020-06-01 has 000001/000002/000003;
    2024-06-01 swaps 000003 out for 000004.
    """
    _seed_stock(session, "000001", "BankA", "SZ")
    _seed_stock(session, "000002", "Vanke", "SZ")
    _seed_stock(session, "000003", "Healthcare", "SH")
    _seed_stock(session, "000004", "NewCo", "SH")
    pit.upsert_index_constituent(
        [
            # 2020-06-01 → 2024-06-01: 000001/000002/000003
            {
                "index_symbol": "000300",
                "symbol": "000001",
                "valid_from": date(2020, 6, 1),
                "valid_to": None,
                "announced_at": date(2020, 6, 1),
                "source": "test",
            },
            {
                "index_symbol": "000300",
                "symbol": "000002",
                "valid_from": date(2020, 6, 1),
                "valid_to": None,
                "announced_at": date(2020, 6, 1),
                "source": "test",
            },
            {
                "index_symbol": "000300",
                "symbol": "000003",
                "valid_from": date(2020, 6, 1),
                "valid_to": date(2024, 6, 1),
                "announced_at": date(2020, 6, 1),
                "source": "test",
            },
            # 2024-06-01 → ∞: 000004 added
            {
                "index_symbol": "000300",
                "symbol": "000004",
                "valid_from": date(2024, 6, 1),
                "valid_to": None,
                "announced_at": date(2024, 6, 1),
                "source": "test",
            },
        ]
    )
    pit.upsert_index_weight_snapshot(
        [
            {
                "index_symbol": "000300",
                "symbol": "000001",
                "trade_date": date(2020, 6, 1),
                "weight": 0.10,
                "source": "test",
            },
            {
                "index_symbol": "000300",
                "symbol": "000001",
                "trade_date": date(2024, 6, 1),
                "weight": 0.20,
                "source": "test",
            },
        ]
    )


def test_index_constituents_differ_by_as_of(pit, session):
    _seed_csi300_rebalance(pit, session)
    members_2020 = pit.index_constituents_as_of("000300", date(2020, 7, 1))
    members_2024 = pit.index_constituents_as_of("000300", date(2024, 7, 1))
    syms_2020 = {m.symbol for m in members_2020}
    syms_2024 = {m.symbol for m in members_2024}
    assert syms_2020 == {"000001", "000002", "000003"}
    assert syms_2024 == {"000001", "000002", "000004"}
    assert syms_2020 != syms_2024


def test_index_weight_forward_fill(pit, session):
    """as_of between two snapshots should return the earlier snapshot's weight."""
    _seed_csi300_rebalance(pit, session)
    # 2022-01-01 → between 2020-06-01 and 2024-06-01 → weight = 0.10
    members = pit.index_constituents_as_of(
        "000300", date(2022, 1, 1), with_weights=True
    )
    by_symbol = {m.symbol: m for m in members}
    assert by_symbol["000001"].weight == pytest.approx(0.10)
    # 2025-01-01 → after 2024-06-01 → weight = 0.20
    members = pit.index_constituents_as_of(
        "000300", date(2025, 1, 1), with_weights=True
    )
    by_symbol = {m.symbol: m for m in members}
    assert by_symbol["000001"].weight == pytest.approx(0.20)


def test_index_constituents_skips_unannounced_rebalance(pit, session):
    """A rebalance valid_from=2024-06-01 but announced_at=2024-12-01 must
    NOT be visible on as_of=2024-07-01 (announcement is still future).
    """
    _seed_stock(session, "000008", "LateRebalance", "SH")
    pit.upsert_index_constituent(
        [
            {
                "index_symbol": "000300",
                "symbol": "000008",
                "valid_from": date(2024, 6, 1),
                "valid_to": None,
                "announced_at": date(2024, 12, 1),  # future announce
                "source": "test",
            }
        ]
    )
    members = pit.index_constituents_as_of("000300", date(2024, 7, 1))
    assert "000008" not in {m.symbol for m in members}


# ---------------------------------------------------------------------------
# 7.5 — reproducibility + materialisation
# ---------------------------------------------------------------------------


def test_research_pool_materialization_is_reproducible(pit, session):
    """Same pool_key + as_of twice → identical ResearchPoolMember rows."""
    _seed_stock(session, "000001", "X", "SZ")
    _seed_bars(session, "000001", date(2020, 1, 2), date(2020, 1, 31))
    pit.upsert_security_status(
        [
            {
                "symbol": "000001",
                "status": "listed",
                "valid_from": date(2010, 1, 1),
                "valid_to": None,
                "announced_at": date(2010, 1, 1),
                "source": "test",
                "confidence": "high",
            }
        ]
    )
    pit.upsert_security_name(
        [
            {
                "symbol": "000001",
                "name": "X",
                "valid_from": date(2010, 1, 1),
                "valid_to": None,
                "announced_at": date(2010, 1, 1),
                "source": "test",
            }
        ]
    )
    spec = UniverseSpec(exchanges=("SZ",))
    pool_key = spec.pool_key(date(2020, 6, 1))

    members = [
        {
            "symbol": "000001",
            "eligible": True,
            "exclusion_reason": None,
            "name_at": "X",
            "status_at": "listed",
        }
    ]
    pit.materialize_research_pool(pool_key, date(2020, 6, 1), members)
    first = pit.list_research_pool(pool_key, date(2020, 6, 1))

    # Re-run with identical input — should replace, not duplicate.
    pit.materialize_research_pool(pool_key, date(2020, 6, 1), members)
    second = pit.list_research_pool(pool_key, date(2020, 6, 1))

    assert len(first) == len(second) == 1
    assert [r.symbol for r in first] == [r.symbol for r in second]
    assert first[0].eligible is True
    assert second[0].eligible is True


def test_pool_key_is_deterministic_for_same_spec():
    a = UniverseSpec(exchanges=("SH", "SZ"), index_symbol="000300")
    b = UniverseSpec(exchanges=("SH", "SZ"), index_symbol="000300")
    assert a.pool_key(date(2024, 1, 1)) == b.pool_key(date(2024, 1, 1))


def test_pool_key_differs_for_different_st_policy():
    a = UniverseSpec(exchanges=("SZ",), st_policy="exclude_known")
    b = UniverseSpec(exchanges=("SZ",), st_policy="strict")
    assert a.pool_key(date(2024, 1, 1)) != b.pool_key(date(2024, 1, 1))


def test_universe_spec_rejects_invalid_st_policy():
    with pytest.raises(ValueError):
        UniverseSpec(st_policy="bogus")


def test_universe_spec_rejects_invalid_exchanges():
    with pytest.raises(ValueError):
        UniverseSpec(exchanges=("",))


# ---------------------------------------------------------------------------
# Degraded-mode fallback
# ---------------------------------------------------------------------------


def test_pit_falls_back_to_snapshot_when_tables_empty(pit, session):
    """PIT tables empty → fall back to Stock.active and report degraded."""
    _seed_stock(session, "000001", "X", "SZ", status="active")
    _seed_bars(session, "000001", date(2020, 1, 2), date(2020, 1, 31))
    res = pit.select_research_symbols_pit(
        as_of=date(2020, 6, 1),
        start_date=date(2020, 1, 2),
        end_date=date(2020, 1, 31),
        exchanges=("SZ",),
        limit=10,
    )
    assert "000001" in res.symbols
    assert res.meta["pit_degraded"] is True


def test_pit_excludes_inactive_snapshot_when_degraded(pit, session):
    """In degraded mode a Stock.status != 'active' should be skipped
    with reason 'not_listed'.
    """
    _seed_stock(session, "000099", "OldCo", "SH", status="delisted")
    _seed_bars(session, "000099", date(2020, 1, 2), date(2020, 1, 31))
    res = pit.select_research_symbols_pit(
        as_of=date(2020, 6, 1),
        start_date=date(2020, 1, 2),
        end_date=date(2020, 1, 31),
        exchanges=("SH",),
        limit=10,
    )
    assert "000099" not in res.symbols
    assert res.meta["pit_degraded"] is True
    assert res.meta["excluded"].get("not_listed", 0) >= 1


# ---------------------------------------------------------------------------
# Coordinator (with a fake provider)
# ---------------------------------------------------------------------------


class _FakeProvider:
    def __init__(
        self,
        *,
        st_list: pd.DataFrame | None = None,
        listed: pd.DataFrame | None = None,
        sh_delist: pd.DataFrame | None = None,
        sz_delist: pd.DataFrame | None = None,
        sz_name_changes: pd.DataFrame | None = None,
        index_cons: pd.DataFrame | None = None,
        index_weights: pd.DataFrame | None = None,
    ):
        self._st = st_list if st_list is not None else pd.DataFrame(
            columns=["symbol", "name", "status", "source"]
        )
        self._listed = listed if listed is not None else pd.DataFrame(
            columns=["symbol", "name", "exchange", "list_date", "source"]
        )
        self._sh = sh_delist if sh_delist is not None else pd.DataFrame(
            columns=["symbol", "name", "list_date", "delist_date", "source"]
        )
        self._sz = sz_delist if sz_delist is not None else pd.DataFrame(
            columns=["symbol", "name", "list_date", "delist_date", "source"]
        )
        self._sz_name_changes = (
            sz_name_changes
            if sz_name_changes is not None
            else pd.DataFrame(
                columns=["symbol", "previous_name", "name", "change_date", "source"]
            )
        )
        self._index_cons = index_cons if index_cons is not None else pd.DataFrame(
            columns=["index_symbol", "symbol", "name", "snapshot_date", "source"]
        )
        self._index_weights = index_weights if index_weights is not None else pd.DataFrame(
            columns=["index_symbol", "symbol", "name", "trade_date", "weight", "source"]
        )

    def current_st_list(self):
        return self._st

    def stock_list_with_list_date(self):
        return self._listed

    def sh_delist(self):
        return self._sh

    def sz_delist(self):
        return self._sz

    def sz_name_changes(self):
        return self._sz_name_changes

    def index_constituents_current(self, index_symbol):
        df = self._index_cons.copy()
        if not df.empty:
            df["index_symbol"] = index_symbol
        return df

    def index_weights_current(self, index_symbol):
        df = self._index_weights.copy()
        if not df.empty:
            df["index_symbol"] = index_symbol
        return df


def test_coordinator_sync_security_status_writes_listed_and_st(pit, session):
    provider = _FakeProvider(
        listed=pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "name": "Bank",
                    "exchange": "SZ",
                    "list_date": date(1991, 4, 3),
                    "source": "test",
                }
            ]
        ),
        st_list=pd.DataFrame(
            [
                {
                    "symbol": "000002",
                    "name": "*ST Foo",
                    "status": "st_star",
                    "source": "test",
                }
            ]
        ),
    )
    coord = PitSyncCoordinator(
        pit, provider, PitSyncConfig(today=date(2024, 1, 1))
    )
    summary = coord.sync_security_status_current()
    assert summary.records == 2
    assert summary.extras["st"] == 1
    assert summary.extras["listed"] == 1
    # Verify the rows are queryable.
    assert pit.status_as_of("000001", date(2020, 1, 1)).status == "listed"
    assert pit.status_as_of("000002", date(2024, 6, 1)).status == "st_star"


def test_coordinator_sync_security_delist_writes_intervals(pit, session):
    provider = _FakeProvider(
        sh_delist=pd.DataFrame(
            [
                {
                    "symbol": "600001",
                    "name": "DelistedSH",
                    "list_date": date(1998, 1, 22),
                    "delist_date": date(2009, 12, 29),
                    "source": "test",
                }
            ]
        ),
        sz_delist=pd.DataFrame(
            [
                {
                    "symbol": "000003",
                    "name": "DelistedSZ",
                    "list_date": date(1991, 1, 14),
                    "delist_date": date(2002, 6, 14),
                    "source": "test",
                }
            ]
        ),
    )
    coord = PitSyncCoordinator(pit, provider, PitSyncConfig())
    summary = coord.sync_security_delist()
    # 2 listed intervals + 2 delisted intervals = 4 rows.
    assert summary.records == 4
    assert summary.extras["sh"] == 1
    assert summary.extras["sz"] == 1
    assert pit.status_as_of("600001", date(2005, 1, 1)).status == "listed"
    assert pit.status_as_of("600001", date(2010, 1, 1)).status == "delisted"


def test_coordinator_sync_index_constituents_writes_intervals(pit, session):
    provider = _FakeProvider(
        index_cons=pd.DataFrame(
            [
                {
                    "index_symbol": "000300",
                    "symbol": "000001",
                    "name": "BankA",
                    "snapshot_date": date(2024, 6, 1),
                    "source": "test",
                }
            ]
        )
    )
    coord = PitSyncCoordinator(pit, provider, PitSyncConfig())
    summary = coord.sync_index_constituents("000300")
    assert summary.records == 1
    members = pit.index_constituents_as_of("000300", date(2024, 7, 1))
    assert "000001" in {m.symbol for m in members}


def test_coordinator_sync_index_weights_writes_snapshot(pit, session):
    provider = _FakeProvider(
        index_weights=pd.DataFrame(
            [
                {
                    "index_symbol": "000300",
                    "symbol": "000001",
                    "name": "BankA",
                    "trade_date": date(2024, 6, 1),
                    "weight": 0.10,
                    "source": "test",
                }
            ]
        )
    )
    coord = PitSyncCoordinator(pit, provider, PitSyncConfig())
    summary = coord.sync_index_weights("000300")
    assert summary.records == 1
    weight = pit._latest_weight("000300", "000001", date(2024, 7, 1))
    assert weight == pytest.approx(0.10)


def test_s_star_st_maps_to_supported_status():
    assert _classify_st_name("S*ST示例") == "st_star"


def test_current_st_list_falls_back_to_stock_names(monkeypatch):
    def fail_st():
        raise ConnectionError("eastmoney unavailable")

    fake_akshare = SimpleNamespace(
        stock_zh_a_st_em=fail_st,
        stock_info_a_code_name=lambda: pd.DataFrame(
            [
                {"code": "000001", "name": "正常股份"},
                {"code": "000002", "name": "*ST示例"},
                {"code": "000003", "name": "STAR Technology"},
            ]
        ),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    result = AkSharePitProvider().current_st_list()

    assert result["symbol"].tolist() == ["000002"]
    assert result.iloc[0]["status"] == "st_star"
    assert result.iloc[0]["source"].endswith("_fallback")


def test_stock_list_falls_back_to_generic_a_share_list(monkeypatch):
    fake_akshare = SimpleNamespace(
        stock_info_a_code_name=lambda: pd.DataFrame(
            [
                {"code": "000001", "name": "平安银行"},
                {"code": "600000", "name": "浦发银行"},
            ]
        )
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    result = AkSharePitProvider().stock_list_with_list_date()

    assert result["symbol"].tolist() == ["000001", "600000"]
    assert result["exchange"].tolist() == ["SZ", "SH"]


def test_stock_list_normalizes_listing_dates_from_provider_strings(monkeypatch):
    sh_rows = pd.DataFrame(
        [{"证券代码": "600000", "证券简称": "Example", "上市日期": "1999-11-10"}]
    )
    fake_akshare = SimpleNamespace(
        stock_info_sh_name_code=lambda symbol: sh_rows if symbol == "主板A股" else pd.DataFrame(),
        stock_info_sz_name_code=lambda symbol: pd.DataFrame(),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    result = AkSharePitProvider().stock_list_with_list_date()

    assert result.iloc[0]["list_date"] == date(1999, 11, 10)


def test_st_prefix_does_not_match_unrelated_english_name():
    assert _st_prefix("STAR Technology") is False
    assert _st_prefix("ST中珠") is True
    assert _st_prefix("*ST天创") is True


def test_current_status_sync_skips_future_listing(pit):
    provider = _FakeProvider(
        listed=pd.DataFrame(
            [{
                "symbol": "001999",
                "name": "Future",
                "exchange": "SZ",
                "list_date": date(2024, 2, 1),
                "source": "test",
            }]
        )
    )
    summary = PitSyncCoordinator(
        pit,
        provider,
        PitSyncConfig(today=date(2024, 1, 1)),
    ).sync_security_status_current()

    assert summary.records == 0
    assert pit.status_as_of("001999", date(2024, 1, 15)) is None


def test_real_listing_date_replaces_later_snapshot_placeholder(pit):
    pit.upsert_security_status(
        [
            {
                "symbol": "000001",
                "status": "listed",
                "valid_from": date(2026, 6, 20),
                "valid_to": None,
                "announced_at": None,
                "source": "snapshot",
                "confidence": "medium",
            }
        ]
    )
    pit.upsert_security_status(
        [
            {
                "symbol": "000001",
                "status": "listed",
                "valid_from": date(1991, 4, 3),
                "valid_to": None,
                "announced_at": date(1991, 4, 3),
                "source": "provider",
                "confidence": "high",
            }
        ]
    )

    status = pit.status_as_of("000001", date(2026, 7, 1))

    assert status is not None
    assert status.valid_from == date(1991, 4, 3)
    assert status.confidence == "high"


def test_current_name_snapshot_is_not_backdated_to_listing_date(pit):
    provider = _FakeProvider(
        listed=pd.DataFrame(
            [{
                "symbol": "000001",
                "name": "*ST Today",
                "exchange": "SZ",
                "list_date": date(1991, 4, 3),
                "source": "test",
            }]
        )
    )
    PitSyncCoordinator(
        pit,
        provider,
        PitSyncConfig(today=date(2024, 1, 2)),
    ).sync_security_names()

    assert pit.name_as_of("000001", date(2020, 1, 1)) is None
    assert pit.name_as_of("000001", date(2024, 1, 2)).name == "*ST Today"


def test_repeated_st_sync_closes_removed_st_interval(pit):
    listed = pd.DataFrame(
        [{
            "symbol": "000002",
            "name": "Example",
            "exchange": "SZ",
            "list_date": date(2020, 1, 1),
            "source": "test",
        }]
    )
    first_provider = _FakeProvider(
        listed=listed,
        st_list=pd.DataFrame(
            [{
                "symbol": "000002",
                "name": "*ST Example",
                "status": "st_star",
                "source": "test",
            }]
        ),
    )
    PitSyncCoordinator(
        pit,
        first_provider,
        PitSyncConfig(today=date(2024, 1, 2)),
    ).sync_security_status_current()
    PitSyncCoordinator(
        pit,
        _FakeProvider(listed=listed),
        PitSyncConfig(today=date(2024, 1, 3)),
    ).sync_security_status_current()

    assert pit.status_as_of("000002", date(2024, 1, 2)).status == "st_star"
    assert pit.status_as_of("000002", date(2024, 1, 3)).status == "listed"


def test_repeated_name_sync_closes_previous_name(pit):
    def listed(name):
        return pd.DataFrame(
            [{
                "symbol": "000001",
                "name": name,
                "exchange": "SZ",
                "list_date": date(1991, 4, 3),
                "source": "test",
            }]
        )

    PitSyncCoordinator(
        pit,
        _FakeProvider(listed=listed("Old Name")),
        PitSyncConfig(today=date(2024, 1, 2)),
    ).sync_security_names()
    PitSyncCoordinator(
        pit,
        _FakeProvider(listed=listed("New Name")),
        PitSyncConfig(today=date(2024, 1, 3)),
    ).sync_security_names()

    assert pit.name_as_of("000001", date(2024, 1, 2)).name == "Old Name"
    assert pit.name_as_of("000001", date(2024, 1, 3)).name == "New Name"


def test_repeated_index_sync_closes_removed_constituent(pit):
    first = pd.DataFrame(
        [
            {
                "index_symbol": "000300",
                "symbol": symbol,
                "name": symbol,
                "snapshot_date": date(2024, 6, 1),
                "source": "test",
            }
            for symbol in ("000001", "000002")
        ]
    )
    second = pd.DataFrame(
        [{
            "index_symbol": "000300",
            "symbol": "000001",
            "name": "000001",
            "snapshot_date": date(2024, 12, 1),
            "source": "test",
        }]
    )
    PitSyncCoordinator(
        pit,
        _FakeProvider(index_cons=first),
        PitSyncConfig(today=date(2024, 6, 1)),
    ).sync_index_constituents("000300")
    PitSyncCoordinator(
        pit,
        _FakeProvider(index_cons=second),
        PitSyncConfig(today=date(2024, 12, 1)),
    ).sync_index_constituents("000300")

    assert {m.symbol for m in pit.index_constituents_as_of("000300", date(2024, 7, 1))} == {
        "000001",
        "000002",
    }
    assert {m.symbol for m in pit.index_constituents_as_of("000300", date(2024, 12, 1))} == {
        "000001"
    }


def test_upsert_rejects_invalid_interval(pit):
    with pytest.raises(ValueError, match="valid_to"):
        pit.upsert_security_status(
            [{
                "symbol": "000001",
                "status": "listed",
                "valid_from": date(2024, 1, 2),
                "valid_to": date(2024, 1, 2),
                "announced_at": date(2024, 1, 2),
                "source": "test",
                "confidence": "high",
            }]
        )


def test_pit_universe_can_include_delisted_symbol_absent_from_current_stock_table(
    pit,
    session,
):
    _seed_bars(session, "600999", date(2020, 1, 2), date(2020, 1, 31))
    pit.upsert_security_status(
        [
            {
                "symbol": "600999",
                "status": "listed",
                "valid_from": date(2000, 1, 1),
                "valid_to": date(2022, 1, 1),
                "announced_at": date(2000, 1, 1),
                "source": "test",
                "confidence": "high",
            },
            {
                "symbol": "600999",
                "status": "delisted",
                "valid_from": date(2022, 1, 1),
                "valid_to": None,
                "announced_at": date(2022, 1, 1),
                "source": "test",
                "confidence": "high",
            },
        ]
    )

    result = pit.select_research_symbols_pit(
        as_of=date(2020, 1, 2),
        start_date=date(2020, 1, 2),
        end_date=date(2020, 1, 31),
        exchanges=("SH",),
        limit=10,
    )

    assert "600999" in result.symbols


def test_upsert_security_status_deduplicates_provider_batch(pit):
    row = {
        "symbol": "600190",
        "status": "delisted",
        "valid_from": date(2025, 7, 28),
        "valid_to": None,
        "announced_at": date(2025, 7, 28),
        "source": "test",
        "confidence": "high",
    }

    assert pit.upsert_security_status([row, dict(row)]) == 1
    assert pit.status_as_of("600190", date(2025, 7, 28)).status == "delisted"


# ---------------------------------------------------------------------------
# Trade-gap audit rows
# ---------------------------------------------------------------------------


def test_upsert_security_trade_gap_deduplicates_and_queries(pit, session):
    rows = [
        {
            "symbol": "000001",
            "trade_date": date(2024, 1, 2),
            "expected_open": True,
            "has_bar": False,
            "gap_type": "provider_gap",
            "source": "test",
            "confidence": "high",
        },
        {
            "symbol": "000001",
            "trade_date": date(2024, 1, 2),
            "expected_open": True,
            "has_bar": True,
            "gap_type": "normal",
            "source": "test-resync",
            "confidence": "medium",
        },
        {
            "symbol": "000001",
            "trade_date": date(2024, 1, 3),
            "expected_open": True,
            "has_bar": False,
            "gap_type": "suspended",
            "source": "test",
            "confidence": "high",
        },
    ]

    assert pit.upsert_security_trade_gap(rows) == 2

    one_day = pit.trade_gap_as_of("000001", date(2024, 1, 2))
    assert one_day is not None
    assert one_day.gap_type == "normal"
    assert one_day.has_bar is True
    assert one_day.confidence == "medium"

    gaps = pit.trade_gaps_between(
        "000001", date(2024, 1, 1), date(2024, 1, 31)
    )
    assert [gap.trade_date for gap in gaps] == [
        date(2024, 1, 2),
        date(2024, 1, 3),
    ]
    assert [gap.gap_type for gap in gaps] == ["normal", "suspended"]
    assert session.query(SecurityTradeGap).count() == 2


def test_upsert_security_trade_gap_rejects_unknown_gap_type(pit):
    with pytest.raises(ValueError, match="trade gap type"):
        pit.upsert_security_trade_gap(
            [
                {
                    "symbol": "000001",
                    "trade_date": date(2024, 1, 2),
                    "expected_open": True,
                    "has_bar": False,
                    "gap_type": "mystery",
                    "source": "test",
                    "confidence": "high",
                }
            ]
        )


# ---------------------------------------------------------------------------
# Dual-axis ST state
# ---------------------------------------------------------------------------


def test_availability_and_st_axes_overlap_without_losing_listed_state(pit):
    pit.upsert_security_status(
        [{
            "symbol": "000001",
            "status": "listed",
            "valid_from": date(2010, 1, 1),
            "valid_to": None,
            "announced_at": date(2010, 1, 1),
            "source": "test",
            "confidence": "high",
        }]
    )
    pit.upsert_security_st_status(
        [{
            "symbol": "000001",
            "st_status": "st_star",
            "valid_from": date(2024, 1, 2),
            "valid_to": None,
            "announced_at": None,
            "source": "test",
            "confidence": "medium",
        }]
    )

    availability = pit.availability_as_of("000001", date(2024, 6, 1))
    st_status = pit.st_status_as_of("000001", date(2024, 6, 1))

    assert availability is not None and availability.status == "listed"
    assert st_status is not None and st_status.st_status == "st_star"
    assert st_status.degraded is True


def test_explicit_st_excludes_without_historical_name(pit, session):
    _seed_stock(session, "000001", "Current Normal Name", "SZ")
    _seed_bars(session, "000001", date(2024, 1, 2), date(2024, 1, 31))
    pit.upsert_security_status(
        [{
            "symbol": "000001",
            "status": "listed",
            "valid_from": date(2010, 1, 1),
            "valid_to": None,
            "announced_at": date(2010, 1, 1),
            "source": "test",
            "confidence": "high",
        }]
    )
    pit.upsert_security_st_status(
        [{
            "symbol": "000001",
            "st_status": "st",
            "valid_from": date(2024, 1, 1),
            "valid_to": None,
            "announced_at": None,
            "source": "test",
            "confidence": "medium",
        }]
    )

    result = pit.select_research_symbols_pit(
        as_of=date(2024, 1, 15),
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 31),
        exchanges=("SZ",),
        limit=10,
    )

    assert result.symbols == []
    assert result.meta["excluded"]["st"] == 1


def test_security_name_history_sync_writes_independent_st_intervals(pit):
    provider = _FakeProvider(
        sz_name_changes=pd.DataFrame(
            [
                {"symbol": "000001", "previous_name": "Example", "name": "ST Example", "change_date": date(2021, 1, 4), "source": "test"},
                {"symbol": "000001", "previous_name": "ST Example", "name": "Example", "change_date": date(2022, 1, 4), "source": "test"},
            ]
        )
    )

    summary = PitSyncCoordinator(pit, provider).sync_security_name_history()

    assert summary.extras == {"name_changes": 2, "st_intervals": 2}
    assert pit.st_status_as_of("000001", date(2021, 6, 1)).st_status == "st"
    normal = pit.st_status_as_of("000001", date(2022, 6, 1))
    assert normal is not None and normal.st_status == "normal"


# ---------------------------------------------------------------------------
# Coverage report
# ---------------------------------------------------------------------------


def test_pit_coverage_report_counts_rows(pit, session):
    pit.upsert_security_status(
        [
            {
                "symbol": "000001",
                "status": "listed",
                "valid_from": date(2010, 1, 1),
                "valid_to": None,
                "announced_at": None,
                "source": "test",
                "confidence": "high",
            }
        ]
    )
    report = pit.pit_coverage_report()
    assert report["security_status_rows"] == 1
    assert report["status_missing_announced_at"] == 1
    assert report["security_trade_gap_rows"] == 0
    assert report["pit_ready"] is True


def test_pit_coverage_report_counts_trade_gaps(pit):
    pit.upsert_security_trade_gap(
        [
            {
                "symbol": "000001",
                "trade_date": date(2024, 1, 2),
                "expected_open": True,
                "has_bar": False,
                "gap_type": "provider_gap",
                "source": "test",
                "confidence": "high",
            },
            {
                "symbol": "000001",
                "trade_date": date(2024, 1, 3),
                "expected_open": True,
                "has_bar": False,
                "gap_type": "suspended",
                "source": "test",
                "confidence": "high",
            },
        ]
    )

    report = pit.pit_coverage_report()
    assert report["security_trade_gap_rows"] == 2
    assert report["provider_gap_rows"] == 1


def test_pit_coverage_report_when_empty():
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        pit = PitRepository(s)
        report = pit.pit_coverage_report()
        assert report["security_status_rows"] == 0
        assert report["pit_ready"] is False


# ---------------------------------------------------------------------------
# HTTP integration smoke
# ---------------------------------------------------------------------------


def _client_with(session_factory) -> TestClient:
    app = create_app()

    def override_get_session() -> Session:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)


def test_pit_security_status_endpoint_returns_404_when_missing(session_factory):
    client = _client_with(session_factory)
    resp = client.get(
        "/api/data/pit/security-status",
        params={"symbol": "000001", "as_of": "2024-01-01"},
    )
    assert resp.status_code == 404


def test_pit_coverage_endpoint_returns_empty_report(session_factory):
    client = _client_with(session_factory)
    resp = client.get("/api/data/pit/coverage")
    assert resp.status_code == 200
    body = resp.json()
    assert body["security_status_rows"] == 0
    assert body["pit_ready"] is False


def test_pit_research_pool_endpoint_returns_degraded_when_empty(session_factory):
    """With no PIT data, /research-pool should still work and report degraded."""
    s = session_factory()
    try:
        _seed_stock(s, "000001", "X", "SZ")
        _seed_bars(s, "000001", date(2020, 1, 2), date(2020, 1, 31))
    finally:
        s.close()
    client = _client_with(session_factory)
    resp = client.post(
        "/api/data/pit/research-pool",
        json={
            "as_of": "2020-06-01",
            "start_date": "2020-01-02",
            "end_date": "2020-01-31",
            "exchanges": ["SZ"],
            "limit": 10,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbols"] == ["000001"]
    assert body["meta"]["pit_degraded"] is True
    assert body["pool_key"].endswith(":2020-06-01")
    assert len(body["pool_key"].split(":", 1)[0]) == 16


def test_pit_security_status_endpoint_returns_status(session_factory):
    """Seed PIT rows directly, query through HTTP."""
    s = session_factory()
    try:
        pit = PitRepository(s)
        pit.upsert_security_status(
            [
                {
                    "symbol": "000001",
                    "status": "listed",
                    "valid_from": date(1991, 4, 3),
                    "valid_to": None,
                    "announced_at": date(1991, 4, 3),
                    "source": "test",
                    "confidence": "high",
                }
            ]
        )
        s.commit()
    finally:
        s.close()
    client = _client_with(session_factory)
    resp = client.get(
        "/api/data/pit/security-status",
        params={"symbol": "000001", "as_of": "2020-06-01"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "listed"
    assert body["confidence"] == "high"
    assert body["degraded"] is False


def test_pit_security_status_endpoint_exposes_two_axes(session_factory):
    s = session_factory()
    try:
        pit = PitRepository(s)
        pit.upsert_security_status(
            [{
                "symbol": "000001",
                "status": "listed",
                "valid_from": date(1991, 4, 3),
                "valid_to": None,
                "announced_at": date(1991, 4, 3),
                "source": "test",
                "confidence": "high",
            }]
        )
        pit.upsert_security_st_status(
            [{
                "symbol": "000001",
                "st_status": "st_star",
                "valid_from": date(2024, 1, 2),
                "valid_to": None,
                "announced_at": None,
                "source": "test",
                "confidence": "medium",
            }]
        )
    finally:
        s.close()

    response = _client_with(session_factory).get(
        "/api/data/pit/security-status",
        params={"symbol": "000001", "as_of": "2024-06-01"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "listed"
    assert body["availability_status"] == "listed"
    assert body["st_status"] == "st_star"


def test_pit_index_constituents_endpoint_404_when_empty(session_factory):
    client = _client_with(session_factory)
    resp = client.get(
        "/api/data/pit/index-constituents",
        params={"index_symbol": "000300", "as_of": "2024-01-01"},
    )
    assert resp.status_code == 404


def test_pit_security_status_rejects_invalid_symbol(session_factory):
    client = _client_with(session_factory)
    resp = client.get(
        "/api/data/pit/security-status",
        params={"symbol": "BOGUS", "as_of": "2024-01-01"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Negative validation
# ---------------------------------------------------------------------------


def test_upsert_security_status_rejects_unknown_status(pit):
    with pytest.raises(ValueError):
        pit.upsert_security_status(
            [
                {
                    "symbol": "000001",
                    "status": "weird",
                    "valid_from": date(2020, 1, 1),
                    "valid_to": None,
                    "source": "test",
                    "confidence": "high",
                }
            ]
        )


def test_upsert_security_status_rejects_unknown_confidence(pit):
    with pytest.raises(ValueError):
        pit.upsert_security_status(
            [
                {
                    "symbol": "000001",
                    "status": "listed",
                    "valid_from": date(2020, 1, 1),
                    "valid_to": None,
                    "source": "test",
                    "confidence": "ultra",
                }
            ]
        )
