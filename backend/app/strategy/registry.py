import importlib.util
from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.strategy.advanced import ADVANCED_STRATEGIES
from app.strategy.base import Strategy
from app.strategy.examples import BUILTIN_STRATEGIES


USER_STRATEGY_DIR = PROJECT_ROOT / "strategies"


def list_strategies() -> list[dict]:
    return [
        strategy_cls.metadata(source)
        for strategy_cls, source in _strategy_classes().values()
    ]


def get_strategy(name: str) -> Strategy:
    entry = _strategy_classes().get(name)
    if entry is None:
        raise ValueError(f"Unknown strategy: {name}")
    strategy_cls, _ = entry
    return strategy_cls()


def _strategy_classes() -> dict[str, tuple[type[Strategy], str]]:
    strategies: dict[str, tuple[type[Strategy], str]] = {
        name: (strategy_cls, "builtin")
        for name, strategy_cls in {**BUILTIN_STRATEGIES, **ADVANCED_STRATEGIES}.items()
    }
    strategies.update(_load_user_strategies(existing_names=set(strategies)))
    return strategies


def _load_user_strategies(existing_names: set[str]) -> dict[str, tuple[type[Strategy], str]]:
    loaded: dict[str, tuple[type[Strategy], str]] = {}
    if not USER_STRATEGY_DIR.exists():
        return loaded

    for path in sorted(USER_STRATEGY_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module = _load_module(path)
        if module is None:
            continue
        for strategy_cls in _candidate_strategy_classes(module):
            if strategy_cls.name in existing_names or strategy_cls.name in loaded:
                continue
            loaded[strategy_cls.name] = (strategy_cls, "user")
    return loaded


def _load_module(path: Path):
    module_name = f"cn_stock_quant_user_strategy_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


def _candidate_strategy_classes(module) -> list[type[Strategy]]:
    explicit = getattr(module, "STRATEGY_CLASS", None) or getattr(module, "Strategy", None)
    if isinstance(explicit, type) and issubclass(explicit, Strategy) and explicit is not Strategy:
        return [explicit]

    register = getattr(module, "register", None)
    if callable(register):
        registered = register()
        if registered is None:
            return []
        if isinstance(registered, type):
            registered = [registered]
        return [
            strategy_cls
            for strategy_cls in registered
            if isinstance(strategy_cls, type) and issubclass(strategy_cls, Strategy) and strategy_cls is not Strategy
        ]

    return []
