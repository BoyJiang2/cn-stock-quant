"""Factor evaluation: IC, RankIC, quintile returns, long-short, turnover.

Given a factor panel and a forward-return panel (both
``MultiIndex(trade_date, symbol)``), :func:`evaluate` produces a single-date
and an aggregated report:

* **IC / RankIC** -- cross-sectional Pearson / Spearman correlation between
  the factor and the forward return, computed per trade date and then
  summarised (mean, std, information ratio).
* **Quintile returns** -- equal-weighted forward return of each of ``n_groups``
  groups formed by ranking the factor cross-sectionally each date, averaged
  over dates.
* **Long-short return** -- top group minus bottom group, averaged over dates.
* **Turnover** -- average one-period turnover of each group and of the
  long-short portfolio, where turnover on a date is the sum of absolute
  changes in equal-weighted holdings between consecutive rebalance dates.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

__all__ = ["evaluate"]

_NAN = float("nan")


def _as_series(panel: pd.DataFrame | pd.Series, name: str = "factor") -> pd.Series:
    """Reduce a one-column DataFrame (or Series) to a named Series."""
    if isinstance(panel, pd.DataFrame):
        if panel.shape[1] != 1:
            raise ValueError(f"{name} must have exactly one column, got {panel.shape[1]}")
        panel = panel.iloc[:, 0]
    return panel.rename(name)


def _aligned_frame(factor: pd.Series, fwd: pd.Series) -> pd.DataFrame:
    """Inner-join the factor and forward return on their MultiIndex."""
    df = pd.concat([factor, fwd], axis=1, keys=["factor", "fwd"])
    return df.dropna()


def _daily_ic(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Per-date Pearson IC and Spearman RankIC."""
    ic = df.groupby(level="trade_date", sort=True).apply(
        lambda g: g["factor"].corr(g["fwd"]), include_groups=False
    )
    rankic = df.groupby(level="trade_date", sort=True).apply(
        lambda g: g["factor"].corr(g["fwd"], method="spearman"), include_groups=False
    )
    ic = ic.rename("ic")
    rankic = rankic.rename("rankic")
    return ic, rankic


def _group_assignments(df: pd.DataFrame, n_groups: int) -> pd.DataFrame:
    """Assign each (date, symbol) to a 1..n_groups bucket by factor rank.

    Group 1 = lowest factor value, group ``n_groups`` = highest.  Uses
    quantile buckets via rank to handle ties and uneven counts gracefully.
    """
    def _assign(group: pd.DataFrame) -> pd.Series:
        ranks = group["factor"].rank(method="first")
        # map ranks 1..n onto n_groups buckets
        buckets = np.ceil(ranks / len(group) * n_groups).clip(upper=n_groups, lower=1)
        return buckets.astype(int)

    df = df.copy()
    df["group"] = df.groupby(level="trade_date", group_keys=False, sort=False).apply(_assign)
    return df


def _group_weights(df: pd.DataFrame, n_groups: int) -> dict[int, pd.DataFrame]:
    """Equal-weighted holdings per group as wide (trade_date x symbol) frames.

    Weights are ``1 / group_size`` for members and 0 otherwise.
    """
    weights: dict[int, pd.DataFrame] = {}
    for g in range(1, n_groups + 1):
        mask = (df["group"] == g).astype(float)
        wide = mask.unstack("symbol").sort_index()
        size = wide.sum(axis=1).replace(0, np.nan)
        weights[g] = wide.div(size, axis=0).fillna(0.0)
    return weights


def _turnover(weights: pd.DataFrame) -> float:
    """Average one-period turnover = 0.5 * mean(sum|w_t - w_{t-1}|)."""
    if len(weights) < 2:
        return 0.0
    diff = 0.5 * weights.diff().abs().sum(axis=1).iloc[1:]
    if diff.empty:
        return 0.0
    return float(diff.mean())


def _group_returns(df: pd.DataFrame, n_groups: int) -> dict[int, float]:
    """Average equal-weighted forward return per group across dates."""
    out: dict[int, float] = {}
    for g in range(1, n_groups + 1):
        sub = df[df["group"] == g]
        daily = sub.groupby(level="trade_date", sort=True)["fwd"].mean()
        out[g] = float(daily.mean()) if not daily.empty else _NAN
    return out


def evaluate(
    factor: pd.DataFrame | pd.Series,
    forward_return: pd.DataFrame | pd.Series,
    *,
    n_groups: int = 5,
) -> dict[str, Any]:
    """Evaluate a single factor against a single forward-return column.

    Args:
        factor: ``MultiIndex(trade_date, symbol)`` factor values (one column).
        forward_return: ``MultiIndex(trade_date, symbol)`` forward returns
            (one column), e.g. ``fwd["fwd_1d"]``.
        n_groups: Number of quantile groups (default 5).

    Returns:
        A dict with keys::

            ic_mean, ic_std, ic_ir,
            rankic_mean, rankic_std, rankic_ir,
            group_returns          # dict {1..n_groups: avg_return}
            long_short_return      # top - bottom, averaged over dates
            long_short_daily       # Series of daily top-bottom returns
            turnover               # dict {1..n_groups: turnover}
            long_short_turnover    # turnover of the top-bottom portfolio
            daily_ic               # Series of per-date IC
            daily_rankic           # Series of per-date RankIC
            n_dates                # number of dates with a valid IC
    """
    if n_groups < 2:
        raise ValueError("n_groups must be >= 2")

    factor_s = _as_series(factor, "factor")
    fwd_s = _as_series(forward_return, "fwd")

    df = _aligned_frame(factor_s, fwd_s)
    if df.empty:
        return {
            "ic_mean": _NAN, "ic_std": _NAN, "ic_ir": _NAN,
            "rankic_mean": _NAN, "rankic_std": _NAN, "rankic_ir": _NAN,
            "group_returns": {g: _NAN for g in range(1, n_groups + 1)},
            "long_short_return": _NAN, "long_short_daily": pd.Series(dtype=float),
            "turnover": {g: _NAN for g in range(1, n_groups + 1)},
            "long_short_turnover": _NAN,
            "daily_ic": pd.Series(dtype=float), "daily_rankic": pd.Series(dtype=float),
            "n_dates": 0,
        }

    # Only score dates that can actually form ``n_groups`` buckets.  This keeps
    # the quintile statistics well-defined and avoids degenerate empty groups.
    date_counts = df.groupby(level="trade_date").size()
    valid_dates = date_counts[date_counts >= n_groups].index
    df = df[df.index.get_level_values("trade_date").isin(valid_dates)]
    if df.empty:
        return {
            "ic_mean": _NAN, "ic_std": _NAN, "ic_ir": _NAN,
            "rankic_mean": _NAN, "rankic_std": _NAN, "rankic_ir": _NAN,
            "group_returns": {g: _NAN for g in range(1, n_groups + 1)},
            "long_short_return": _NAN, "long_short_daily": pd.Series(dtype=float),
            "turnover": {g: _NAN for g in range(1, n_groups + 1)},
            "long_short_turnover": _NAN,
            "daily_ic": pd.Series(dtype=float), "daily_rankic": pd.Series(dtype=float),
            "n_dates": 0,
        }

    # --- IC / RankIC -------------------------------------------------------
    daily_ic, daily_rankic = _daily_ic(df)
    daily_ic = daily_ic.dropna()
    daily_rankic = daily_rankic.dropna()

    def _ir(s: pd.Series) -> float:
        if len(s) < 2 or s.std(ddof=1) == 0:
            return _NAN
        return float(s.mean() / s.std(ddof=1))

    # --- Quintile returns --------------------------------------------------
    grouped = _group_assignments(df, n_groups)
    group_returns = _group_returns(grouped, n_groups)
    long_short_return = group_returns.get(n_groups, _NAN) - group_returns.get(1, _NAN)

    # Daily long-short (top - bottom) for the time series.
    daily_top = grouped[grouped["group"] == n_groups].groupby(level="trade_date", sort=True)["fwd"].mean()
    daily_bottom = grouped[grouped["group"] == 1].groupby(level="trade_date", sort=True)["fwd"].mean()
    long_short_daily = (daily_top - daily_bottom).dropna()

    # --- Turnover ----------------------------------------------------------
    weights = _group_weights(grouped, n_groups)
    turnover = {g: _turnover(weights[g]) for g in range(1, n_groups + 1)}
    long_short_turnover = (turnover[n_groups] + turnover[1]) / 2.0

    return {
        "ic_mean": float(daily_ic.mean()) if not daily_ic.empty else _NAN,
        "ic_std": float(daily_ic.std(ddof=1)) if len(daily_ic) >= 2 else _NAN,
        "ic_ir": _ir(daily_ic),
        "rankic_mean": float(daily_rankic.mean()) if not daily_rankic.empty else _NAN,
        "rankic_std": float(daily_rankic.std(ddof=1)) if len(daily_rankic) >= 2 else _NAN,
        "rankic_ir": _ir(daily_rankic),
        "group_returns": group_returns,
        "long_short_return": float(long_short_return),
        "long_short_daily": long_short_daily,
        "turnover": turnover,
        "long_short_turnover": long_short_turnover,
        "daily_ic": daily_ic,
        "daily_rankic": daily_rankic,
        "n_dates": int(daily_ic.shape[0]),
    }
