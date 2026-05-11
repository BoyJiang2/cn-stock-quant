from app.strategy.base import Strategy
from app.strategy.examples import BUILTIN_STRATEGIES


def list_strategies() -> list[dict[str, str]]:
    return [
        {"name": strategy.name, "display_name": strategy.display_name}
        for strategy in BUILTIN_STRATEGIES.values()
    ]


def get_strategy(name: str) -> Strategy:
    strategy_cls = BUILTIN_STRATEGIES.get(name)
    if strategy_cls is None:
        raise ValueError(f"Unknown strategy: {name}")
    return strategy_cls()

