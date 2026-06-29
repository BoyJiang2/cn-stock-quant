"""Built-in vectorised factors and the default :class:`FactorRegistry`.

Every factor is implemented on the **wide** (trade_date x symbol) form and
uses only *trailing* rolling windows, which guarantees:

* **Cross-stock isolation** -- each symbol is an independent column; no
  information leaks between columns.
* **No look-ahead** -- a factor value at trade date *t* only depends on data
  with index <= *t*.

All rolling operations rely on pandas rolling, which is column-wise on a
DataFrame and therefore fully vectorised across symbols.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.factors.spec import FactorInputs, FactorRegistry

__all__ = ["default_registry", "BUILTIN_FACTOR_NAMES", "FACTOR_DIRECTIONS"]


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _simple_returns(close: pd.DataFrame) -> pd.DataFrame:
    """Daily simple returns: ``close.pct_change()``."""
    return close.pct_change(fill_method=None)


def _rolling_mean(series: pd.DataFrame, window: int) -> pd.DataFrame:
    return series.rolling(window).mean()


def _rolling_std(series: pd.DataFrame, window: int) -> pd.DataFrame:
    """Sample standard deviation (ddof=1), matching pandas / codebase default."""
    return series.rolling(window).std()


# ---------------------------------------------------------------------------
# Momentum family
# ---------------------------------------------------------------------------

def _momentum(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """N-day cumulative return: ``close / close.shift(N) - 1``."""
    return inputs.close.pct_change(window, fill_method=None)


def _momentum_skip(inputs: FactorInputs, window: int, skip: int) -> pd.DataFrame:
    """Return over ``window`` days ending ``skip`` days ago (no recent days)."""
    return inputs.close.shift(skip) / inputs.close.shift(skip + window) - 1.0


def _ma_gap(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Normalised gap between close and its moving average.

    ``(close - MA(close, N)) / MA(close, N)``.
    """
    ma = inputs.close.rolling(window).mean()
    return (inputs.close - ma) / ma


def _reversal(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Short-term reversal based on the inverse price ratio.

    ``close(t-N) / close(t) - 1`` is positive when the stock has fallen over
    the window. It is intentionally not identical to ``-momentum``.
    """
    return inputs.close.shift(window) / inputs.close - 1.0


# ---------------------------------------------------------------------------
# Volatility / drawdown family
# ---------------------------------------------------------------------------

def _volatility(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Standard deviation of daily simple returns over ``window`` days."""
    return _rolling_std(_simple_returns(inputs.close), window)


def _downside_volatility(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Downside deviation (RMS of non-positive returns) over ``window`` days.

    Negative returns are kept, non-negative returns are floored to zero, so
    the metric only punishes downside movements:

        downside = sqrt( mean( min(r, 0)^2 ) )
    """
    ret = _simple_returns(inputs.close)
    neg = ret.clip(upper=0.0)
    return np.sqrt((neg**2).rolling(window).mean())


def _max_drawdown(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Largest peak-to-trough decline observed within a trailing ``window``.

    Returns a non-positive number (0 = no drawdown).  For each trailing window
    of ``window`` closes the running peak is computed and the worst
    peak-to-trough decline within that window is returned, so the first
    non-NaN value appears after exactly ``window`` observations.
    """
    def _dd(values: np.ndarray) -> float:
        peak = np.maximum.accumulate(values)
        return float(np.min(values / peak - 1.0))

    return inputs.close.rolling(window).apply(_dd, raw=True)


def _atr_pct(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Average True Range as a fraction of close.

    True Range = max(high - low, |high - prev_close|, |low - prev_close|).
    ``atr_pct = ATR(window) / close``.
    """
    prev_close = inputs.close.shift(1)
    # Element-wise max of the three true-range components (wide frames share
    # the same index/columns so numpy maximum is safe and fully vectorised).
    tr = pd.DataFrame(
        np.maximum(
            np.maximum(
                (inputs.high - inputs.low).to_numpy(),
                (inputs.high - prev_close).abs().to_numpy(),
            ),
            (inputs.low - prev_close).abs().to_numpy(),
        ),
        index=inputs.close.index,
        columns=inputs.close.columns,
    )
    atr = tr.rolling(window).mean()
    return atr / inputs.close


def _intraday_range(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Average intraday range ``(high - low) / close`` over ``window`` days."""
    return ((inputs.high - inputs.low) / inputs.close).rolling(window).mean()


# ---------------------------------------------------------------------------
# Liquidity / volume family
# ---------------------------------------------------------------------------

def _log_amount(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Natural log of average amount over ``window`` days."""
    return np.log(inputs.amount.rolling(window).mean())


def _amount_ratio(inputs: FactorInputs, short: int, long: int) -> pd.DataFrame:
    """Ratio of short-term to long-term average amount."""
    return inputs.amount.rolling(short).mean() / inputs.amount.rolling(long).mean()


def _volume_ratio(inputs: FactorInputs, short: int, long: int) -> pd.DataFrame:
    """Ratio of short-term to long-term average volume."""
    return inputs.volume.rolling(short).mean() / inputs.volume.rolling(long).mean()


def _amount_stability(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Amount stability = mean / std over ``window`` (inverse coefficient of variation).

    Higher values mean more stable turnover.  Windows with zero dispersion
    yield ``inf``; these are replaced with ``NaN`` so callers can drop them.
    """
    mean = inputs.amount.rolling(window).mean()
    std = inputs.amount.rolling(window).std()
    stability = mean / std
    return stability.replace([np.inf, -np.inf], np.nan)


def _amihud_illiquidity(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Amihud illiquidity = average of ``|daily_return| / amount`` over ``window``.

    Measures price impact per unit of traded value.  Amount is in yuan, so the
    raw magnitude is tiny by construction -- only the cross-sectional ranking
    is meaningful, which is exactly what the downstream evaluation uses.
    """
    ret = _simple_returns(inputs.close).abs()
    return (ret / inputs.amount).rolling(window).mean()


def _price_volume_corr(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Rolling Pearson correlation between daily return and volume."""
    ret = _simple_returns(inputs.close)
    return ret.rolling(window).corr(inputs.volume)


def _up_day_ratio(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Fraction of up days (return > 0) over valid returns in ``window``."""
    ret = _simple_returns(inputs.close)
    up_count = (ret > 0).astype(float).rolling(window).sum()
    valid_count = ret.rolling(window).count()
    return up_count / valid_count


# ---------------------------------------------------------------------------
# Risk-adjusted / return-distribution family
# ---------------------------------------------------------------------------
#
# These factors summarise the *shape* of the trailing return distribution.
# They are deliberately complementary to the momentum / volatility families:
#
# * ``sharpe_20d``      -- mean / std of daily returns (risk-adjusted
#   momentum).  A classic long-only ranking signal: high Sharpe = steady
#   winner, low / negative Sharpe = either losing or choppy.
# * ``return_skew_20d`` -- sample skewness of daily returns.  Negative skew
#   indicates tail-risk (occasional large drops); positive skew is
#   preferable for a long-only holder.
# * ``vwap_gap_20d``    -- close vs the rolling volume-weighted average
#   price over ``window``.  ``vwap = sum(amount) / sum(volume)`` is the
#   standard rolling VWAP.  Closing persistently above VWAP indicates
#   sustained buying pressure (institutional absorption).
#
# All three are column-wise rolling computations on the wide frames, so the
# cross-stock isolation and no-look-ahead guarantees of the rest of the
# module carry over unchanged.

def _sharpe(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Rolling mean / std of daily simple returns (annualisation-agnostic).

    The ratio is invariant to any constant scaling of returns, so the
    ranking it produces is identical to a properly annualised Sharpe -- only
    the absolute level differs.  Zero-dispersion windows yield ``NaN`` so
    they are dropped downstream rather than treated as infinite Sharpe.
    """
    ret = _simple_returns(inputs.close)
    mean = ret.rolling(window).mean()
    std = ret.rolling(window).std()  # ddof=1, matching the codebase default
    return mean / std.replace(0.0, np.nan)


def _return_skew(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Rolling sample skewness of daily simple returns.

    Uses pandas' bias-corrected Fisher-Pearson skewness
    (``G1 = sqrt(n*(n-1))/(n-2) * g1``).  Negative values mark distributions
    with a fatter left tail -- the regime a long-only A-share holder wants
    to avoid.
    """
    ret = _simple_returns(inputs.close)
    return ret.rolling(window).skew()


def _vwap_gap(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Gap between close and the rolling volume-weighted average price.

    ``vwap = sum(amount) / sum(volume)`` over the trailing ``window``;
    the factor is ``(close - vwap) / vwap``.  Amount is in yuan and volume
    in shares, so ``amount / volume`` is the per-share average price.
    Windows with zero total volume yield ``NaN``.
    """
    vwap = (
        inputs.amount.rolling(window).sum()
        / inputs.volume.rolling(window).sum().replace(0.0, np.nan)
    )
    return (inputs.close - vwap) / vwap.replace(0.0, np.nan)


# ---------------------------------------------------------------------------
# Default registry
# ---------------------------------------------------------------------------

def default_registry() -> FactorRegistry:
    """Return a fresh registry populated with all built-in factors."""
    reg = FactorRegistry()

    # Momentum
    reg.register("momentum_5d", lambda inp, p: _momentum(inp, 5))
    reg.register("momentum_20d", lambda inp, p: _momentum(inp, 20))
    reg.register("momentum_60d", lambda inp, p: _momentum(inp, 60))
    reg.register("momentum_20d_skip_5d", lambda inp, p: _momentum_skip(inp, 20, 5))

    # Moving-average gap
    reg.register("ma_gap_20d", lambda inp, p: _ma_gap(inp, 20))
    reg.register("ma_gap_60d", lambda inp, p: _ma_gap(inp, 60))

    # Reversal
    reg.register("reversal_5d", lambda inp, p: _reversal(inp, 5))

    # Volatility
    reg.register("volatility_20d", lambda inp, p: _volatility(inp, 20))
    reg.register("volatility_60d", lambda inp, p: _volatility(inp, 60))
    reg.register("downside_volatility_20d", lambda inp, p: _downside_volatility(inp, 20))

    # Drawdown / range
    reg.register("max_drawdown_20d", lambda inp, p: _max_drawdown(inp, 20))
    reg.register("max_drawdown_60d", lambda inp, p: _max_drawdown(inp, 60))
    reg.register("atr_pct_14d", lambda inp, p: _atr_pct(inp, 14))
    reg.register("intraday_range_20d", lambda inp, p: _intraday_range(inp, 20))

    # Liquidity / volume
    reg.register("log_amount_20d", lambda inp, p: _log_amount(inp, 20))
    reg.register("amount_ratio_5d_20d", lambda inp, p: _amount_ratio(inp, 5, 20))
    reg.register("volume_ratio_5d_20d", lambda inp, p: _volume_ratio(inp, 5, 20))
    reg.register("amount_stability_20d", lambda inp, p: _amount_stability(inp, 20))
    reg.register("amihud_illiquidity_20d", lambda inp, p: _amihud_illiquidity(inp, 20))
    reg.register("price_volume_corr_20d", lambda inp, p: _price_volume_corr(inp, 20))
    reg.register("up_day_ratio_20d", lambda inp, p: _up_day_ratio(inp, 20))

    # Risk-adjusted / return-distribution
    reg.register("sharpe_20d", lambda inp, p: _sharpe(inp, 20))
    reg.register("return_skew_20d", lambda inp, p: _return_skew(inp, 20))
    reg.register("vwap_gap_20d", lambda inp, p: _vwap_gap(inp, 20))

    return reg


BUILTIN_FACTOR_NAMES: list[str] = [
    "momentum_5d",
    "momentum_20d",
    "momentum_60d",
    "momentum_20d_skip_5d",
    "ma_gap_20d",
    "ma_gap_60d",
    "reversal_5d",
    "volatility_20d",
    "volatility_60d",
    "downside_volatility_20d",
    "max_drawdown_20d",
    "max_drawdown_60d",
    "atr_pct_14d",
    "intraday_range_20d",
    "log_amount_20d",
    "amount_ratio_5d_20d",
    "volume_ratio_5d_20d",
    "amount_stability_20d",
    "amihud_illiquidity_20d",
    "price_volume_corr_20d",
    "up_day_ratio_20d",
    "sharpe_20d",
    "return_skew_20d",
    "vwap_gap_20d",
]

# Direction used to orient evaluation so a higher adjusted value always means
# "more desirable" for a long-only ranking experiment.
FACTOR_DIRECTIONS: dict[str, int] = {
    "momentum_5d": 1,
    "momentum_20d": 1,
    "momentum_60d": 1,
    "momentum_20d_skip_5d": 1,
    "ma_gap_20d": 1,
    "ma_gap_60d": 1,
    "reversal_5d": 1,
    "volatility_20d": -1,
    "volatility_60d": -1,
    "downside_volatility_20d": -1,
    "max_drawdown_20d": 1,
    "max_drawdown_60d": 1,
    "atr_pct_14d": -1,
    "intraday_range_20d": -1,
    "log_amount_20d": 1,
    "amount_ratio_5d_20d": 1,
    "volume_ratio_5d_20d": 1,
    "amount_stability_20d": 1,
    "amihud_illiquidity_20d": -1,
    "price_volume_corr_20d": 1,
    "up_day_ratio_20d": 1,
    # Risk-adjusted / return-distribution
    "sharpe_20d": 1,        # higher Sharpe = better risk-adjusted momentum
    "return_skew_20d": 1,   # positive skew preferable for long-only holders
    "vwap_gap_20d": 1,      # closing above VWAP = buying pressure
}
