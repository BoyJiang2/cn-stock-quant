"""Controlled AI research adapters.

LLMs may propose factor combinations through structured data, but they cannot
execute arbitrary code or bypass the factor evaluation pipeline.
"""

from app.ai_research.composite import build_composite_factor
from app.ai_research.market_regime import (
    MarketRegimeAnalyzer,
    RegimeResult,
    build_llm_market_context,
)
from app.ai_research.qlib_adapter import to_qlib_frame

__all__ = [
    "build_composite_factor",
    "to_qlib_frame",
    "MarketRegimeAnalyzer",
    "RegimeResult",
    "build_llm_market_context",
]
