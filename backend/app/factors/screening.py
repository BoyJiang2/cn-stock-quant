"""Deterministic triage for single-factor research experiments.

The screen deliberately promotes a factor only to *further* rolling-OOS
research. It is not a forecast and never authorises a trade.
"""

from __future__ import annotations

from math import isfinite
from typing import Literal

FactorScreeningStatus = Literal["candidate", "watch", "rejected"]


def _finite_number(value: float | None) -> bool:
    return value is not None and isfinite(value)


def screen_factor_metrics(
    *,
    n_dates: int,
    rankic_mean: float | None,
    rankic_ir: float | None,
    long_short_return: float | None,
    long_short_turnover: float | None,
) -> tuple[FactorScreeningStatus, list[str]]:
    """Classify an in-sample factor result for the next research stage.

    ``candidate`` is intentionally stringent: it requires sufficient history,
    positive cross-sectional signal and positive spread return, plus manageable
    daily turnover. Passing this screen still requires point-in-time rolling
    OOS validation before the factor can support a strategy.
    """
    rejection_reasons: list[str] = []
    if n_dates < 60:
        rejection_reasons.append(f"only {n_dates} valid evaluation dates; need at least 60")
    if not _finite_number(rankic_mean) or rankic_mean <= 0.0:
        rejection_reasons.append("RankIC must be finite and positive")
    if not _finite_number(rankic_ir) or rankic_ir <= 0.0:
        rejection_reasons.append("RankIC IR must be finite and positive")
    if not _finite_number(long_short_return) or long_short_return <= 0.0:
        rejection_reasons.append("long-short return must be finite and positive")
    if rejection_reasons:
        return "rejected", rejection_reasons

    candidate_reasons: list[str] = []
    if n_dates < 120:
        candidate_reasons.append(f"only {n_dates} dates; need 120 for candidate status")
    if rankic_mean < 0.02:
        candidate_reasons.append(f"RankIC {rankic_mean:.4f} is below 0.0200")
    if rankic_ir < 0.15:
        candidate_reasons.append(f"RankIC IR {rankic_ir:.4f} is below 0.1500")
    if not _finite_number(long_short_turnover):
        candidate_reasons.append("long-short turnover is unavailable")
    elif long_short_turnover > 0.80:
        candidate_reasons.append(f"long-short turnover {long_short_turnover:.2f} exceeds 0.80")
    if candidate_reasons:
        return "watch", candidate_reasons
    return "candidate", ["passes the pre-OOS factor screen; rolling OOS validation is still required"]
