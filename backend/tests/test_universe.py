from datetime import date

import pytest

from app.data.universe import (
    UniverseMember,
    UniverseSnapshot,
    UniverseSpec,
    current_market_snapshot,
)


class StubRepository:
    def __init__(self):
        self.kwargs = None

    def select_research_symbols(self, start_date, end_date, **kwargs):
        self.kwargs = kwargs
        return ["000001", "600000"]


def test_universe_spec_normalizes_values_and_has_stable_fingerprint():
    first = UniverseSpec(
        exchanges=("sz", "SH", "sz"),
        manual_symbols=("SZ000001", "600000.SH"),
    )
    second = UniverseSpec(
        exchanges=("SZ", "SH"),
        manual_symbols=("000001", "600000"),
    )

    assert first.exchanges == ("SZ", "SH")
    assert first.manual_symbols == ("000001", "600000")
    assert first.fingerprint == second.fingerprint


def test_universe_spec_rejects_incomplete_source_configuration():
    with pytest.raises(ValueError, match="index_symbol"):
        UniverseSpec(source="index")
    with pytest.raises(ValueError, match="manual_symbols"):
        UniverseSpec(source="manual")


def test_snapshot_key_is_independent_of_member_order():
    spec = UniverseSpec()
    first = UniverseSnapshot(
        spec=spec,
        as_of_date=date(2024, 1, 2),
        members=(UniverseMember("000001"), UniverseMember("600000")),
        data_version="bars-v1",
    )
    second = UniverseSnapshot(
        spec=spec,
        as_of_date=date(2024, 1, 2),
        members=(UniverseMember("600000"), UniverseMember("000001")),
        data_version="bars-v1",
    )

    assert first.snapshot_key == second.snapshot_key


def test_current_market_snapshot_marks_compatibility_path_as_degraded():
    repository = StubRepository()
    spec = UniverseSpec(limit=50, min_coverage_ratio=0.75)

    snapshot = current_market_snapshot(
        repository,
        spec,
        as_of_date=date(2024, 12, 31),
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        data_version="local-db",
    )

    assert snapshot.eligible_symbols == ["000001", "600000"]
    assert snapshot.degraded is True
    assert snapshot.metadata["mode"] == "current_snapshot"
    assert repository.kwargs["limit"] == 50
    assert repository.kwargs["min_coverage_ratio"] == 0.75


def test_manual_snapshot_preserves_normalized_symbols():
    snapshot = current_market_snapshot(
        StubRepository(),
        UniverseSpec(source="manual", manual_symbols=("SZ000001", "600000.SH")),
        as_of_date=date(2024, 1, 2),
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 2),
        data_version="manual",
    )

    assert snapshot.eligible_symbols == ["000001", "600000"]
