from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from hashlib import sha256
import json
from typing import Literal

from app.data.symbols import normalize_a_share_symbols


UniverseSource = Literal["market", "index", "manual"]
StPolicy = Literal["exclude_known", "include_unknown", "strict"]


@dataclass(frozen=True)
class UniverseSpec:
    """Versionable definition of a research universe."""

    source: UniverseSource = "market"
    exchanges: tuple[str, ...] = ("SH", "SZ", "BJ")
    index_symbol: str | None = None
    manual_symbols: tuple[str, ...] = ()
    exclude_st: bool = True
    st_policy: StPolicy = "exclude_known"
    min_coverage_ratio: float = 0.8
    min_trading_days: int | None = None
    limit: int = 300

    def __post_init__(self) -> None:
        exchanges = tuple(dict.fromkeys(exchange.upper() for exchange in self.exchanges))
        if not exchanges or any(exchange not in {"SH", "SZ", "BJ"} for exchange in exchanges):
            raise ValueError("exchanges must contain SH, SZ, or BJ")
        if self.index_symbol and self.source == "market":
            object.__setattr__(self, "source", "index")
        if self.source == "index" and not self.index_symbol:
            raise ValueError("index_symbol is required for an index universe")
        if self.source == "manual" and not self.manual_symbols:
            raise ValueError("manual_symbols are required for a manual universe")
        if self.st_policy not in {"exclude_known", "include_unknown", "strict"}:
            raise ValueError("invalid st_policy")
        if not 0.0 < float(self.min_coverage_ratio) <= 1.0:
            raise ValueError("min_coverage_ratio must be in (0, 1]")
        if self.min_trading_days is not None and self.min_trading_days < 1:
            raise ValueError("min_trading_days must be positive")
        if not 1 <= int(self.limit) <= 6000:
            raise ValueError("limit must be between 1 and 6000")

        normalized_manual = tuple(normalize_a_share_symbols(list(self.manual_symbols)))
        object.__setattr__(self, "exchanges", exchanges)
        object.__setattr__(self, "manual_symbols", normalized_manual)

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()[:16]

    def pool_key(self, as_of_date: date) -> str:
        return f"{self.fingerprint}:{as_of_date.isoformat()}"


@dataclass(frozen=True)
class UniverseMember:
    symbol: str
    eligible: bool = True
    exclusion_reason: str | None = None
    name_at: str | None = None
    status_at: str | None = None
    weight: float | None = None


@dataclass(frozen=True)
class UniverseSnapshot:
    spec: UniverseSpec
    as_of_date: date
    members: tuple[UniverseMember, ...]
    data_version: str
    degraded: bool = False
    warnings: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def eligible_symbols(self) -> list[str]:
        return [member.symbol for member in self.members if member.eligible]

    @property
    def snapshot_key(self) -> str:
        member_payload = [
            {
                "symbol": member.symbol,
                "eligible": member.eligible,
                "reason": member.exclusion_reason,
                "weight": member.weight,
            }
            for member in sorted(self.members, key=lambda item: item.symbol)
        ]
        payload = json.dumps(
            {
                "spec": self.spec.fingerprint,
                "as_of_date": self.as_of_date.isoformat(),
                "data_version": self.data_version,
                "members": member_payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(payload.encode("utf-8")).hexdigest()[:20]


def current_market_snapshot(
    repository,
    spec: UniverseSpec,
    *,
    as_of_date: date,
    start_date: date,
    end_date: date,
    data_version: str,
) -> UniverseSnapshot:
    """Build a non-PIT snapshot through the current repository.

    This adapter keeps today's behavior explicit. It is a compatibility path,
    not a substitute for historical status and index membership data.
    """
    if spec.source == "manual":
        symbols = list(spec.manual_symbols)
    elif spec.source == "market":
        symbols = repository.select_research_symbols(
            start_date,
            end_date,
            limit=spec.limit,
            min_trading_days=spec.min_trading_days,
            min_coverage_ratio=spec.min_coverage_ratio,
            exchanges=spec.exchanges,
            exclude_risk_names=spec.exclude_st,
        )
    else:
        raise ValueError("index universes require the point-in-time repository")

    members = tuple(UniverseMember(symbol=symbol) for symbol in symbols)
    return UniverseSnapshot(
        spec=spec,
        as_of_date=as_of_date,
        members=members,
        data_version=data_version,
        degraded=True,
        warnings=(
            "Current-snapshot universe used; historical status and index membership were not applied.",
        ),
        metadata={"mode": "current_snapshot"},
    )
