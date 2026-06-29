"""Factor laboratory (module A).

A first-version, pure in-memory, vectorised factor lab.  Public API:

* :class:`FactorSpec`, :class:`FactorRegistry`, :class:`FactorInputs`
* :class:`FactorLab` -- compute factors into a MultiIndex DataFrame
* :func:`forward_returns` -- strict T+1-entry forward return labels
* :func:`winsorize_mad`, :func:`percentile_rank`, :func:`standardize_robust`,
  :func:`preprocess` -- per-trade-date cross-sectional preprocessing
* :func:`evaluate` -- IC / RankIC / quintile returns / long-short / turnover
* :func:`default_registry`, :data:`BUILTIN_FACTOR_NAMES`
"""

from app.factors.core import FactorLab
from app.factors.evaluation import evaluate
from app.factors.factors import BUILTIN_FACTOR_NAMES, FACTOR_DIRECTIONS, default_registry
from app.factors.preprocess import (
    percentile_rank,
    preprocess,
    standardize_robust,
    winsorize_mad,
)
from app.factors.returns import DEFAULT_HORIZONS, forward_returns
from app.factors.spec import FactorInputs, FactorFunc, FactorRegistry, FactorSpec

__all__ = [
    "FactorInputs",
    "FactorFunc",
    "FactorSpec",
    "FactorRegistry",
    "FactorLab",
    "default_registry",
    "BUILTIN_FACTOR_NAMES",
    "FACTOR_DIRECTIONS",
    "forward_returns",
    "DEFAULT_HORIZONS",
    "winsorize_mad",
    "percentile_rank",
    "standardize_robust",
    "preprocess",
    "evaluate",
]
