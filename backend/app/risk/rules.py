from dataclasses import dataclass, field


@dataclass
class RiskDecision:
    accepted: dict[str, float]
    rejected: dict[str, str] = field(default_factory=dict)


@dataclass
class RiskConfig:
    max_symbol_weight: float = 0.3
    max_total_weight: float = 0.95
    max_positions: int | None = None
    blocked_symbols: set[str] = field(default_factory=set)


class RiskEngine:
    def evaluate(self, target_weights: dict[str, float], config: RiskConfig) -> RiskDecision:
        accepted: dict[str, float] = {}
        rejected: dict[str, str] = {}

        for symbol, weight in target_weights.items():
            if symbol in config.blocked_symbols:
                rejected[symbol] = "blocked symbol"
                continue
            if weight < 0:
                rejected[symbol] = "negative weight"
                continue
            accepted[symbol] = min(weight, config.max_symbol_weight)

        if config.max_positions is not None and config.max_positions >= 0:
            ranked = sorted(accepted.items(), key=lambda item: item[1], reverse=True)
            accepted = dict(ranked[: config.max_positions])
            for symbol, _ in ranked[config.max_positions :]:
                rejected[symbol] = "max positions exceeded"

        total_weight = sum(accepted.values())
        if total_weight > config.max_total_weight and total_weight > 0:
            scale = config.max_total_weight / total_weight
            accepted = {symbol: round(weight * scale, 6) for symbol, weight in accepted.items()}

        return RiskDecision(accepted=accepted, rejected=rejected)
