from math import isfinite

import pandas as pd

from app.factors import BUILTIN_FACTOR_NAMES, FACTOR_DIRECTIONS, preprocess


def build_composite_factor(
    factor_panel: pd.DataFrame,
    components: dict[str, float],
) -> pd.Series:
    """Build a direction-adjusted composite from registered factors.

    Each component is robustly standardized by trade date, oriented so a
    higher value is preferable, weighted, and normalized by total absolute
    weight. This protocol is safe for LLM-generated JSON proposals because it
    never evaluates model-generated code.
    """
    if not components:
        raise ValueError("components must not be empty")
    unknown = sorted(set(components) - set(BUILTIN_FACTOR_NAMES))
    if unknown:
        raise ValueError(f"unknown factor components: {', '.join(unknown)}")
    if any(not isfinite(float(weight)) for weight in components.values()):
        raise ValueError("component weights must be finite")
    total_abs_weight = sum(abs(float(weight)) for weight in components.values())
    if total_abs_weight <= 0:
        raise ValueError("at least one component weight must be non-zero")

    composite = pd.Series(0.0, index=factor_panel.index, name="composite")
    valid = pd.Series(True, index=factor_panel.index)
    for name, weight in components.items():
        standardized = preprocess(factor_panel[[name]])["standardized"][name]
        oriented = standardized * FACTOR_DIRECTIONS[name]
        valid &= oriented.notna()
        composite = composite.add(oriented.fillna(0.0) * float(weight), fill_value=0.0)
    composite = composite / total_abs_weight
    return composite.where(valid)
