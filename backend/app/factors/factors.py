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


def _money_flow_proxy(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Signed amount imbalance proxy over ``window`` days.

    A-share free data does not always expose reliable active buy/sell flow.
    This proxy signs daily traded amount by the close-to-close return and
    normalises by total amount:

        sum(sign(return) * amount) / sum(amount)
    """
    ret = _simple_returns(inputs.close)
    signed_amount = np.sign(ret).fillna(0.0) * inputs.amount
    denominator = inputs.amount.rolling(window).sum().replace(0.0, np.nan)
    return signed_amount.rolling(window).sum() / denominator


def _low_vol_reversal(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Reversal strength scaled by trailing volatility."""
    reversal = inputs.close.shift(window) / inputs.close - 1.0
    vol = _volatility(inputs, window).replace(0.0, np.nan)
    return reversal / vol


def _breakout_strength(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Close location relative to the trailing high.

    Values near zero indicate the close is near a trailing high; negative
    values indicate distance below that high.
    """
    high_max = inputs.high.rolling(window).max()
    return inputs.close / high_max.replace(0.0, np.nan) - 1.0


def _drawdown_recovery(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Recovery from the trailing low, normalised by current close."""
    low_min = inputs.close.rolling(window).min()
    return inputs.close / low_min.replace(0.0, np.nan) - 1.0


def _close_position(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Close position inside the trailing high-low channel, in [0, 1]."""
    high_max = inputs.high.rolling(window).max()
    low_min = inputs.low.rolling(window).min()
    spread = (high_max - low_min).replace(0.0, np.nan)
    return (inputs.close - low_min) / spread


def _price_efficiency(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Trend efficiency: net price displacement divided by path length."""
    net_move = (inputs.close - inputs.close.shift(window)).abs()
    path = inputs.close.diff().abs().rolling(window).sum()
    return net_move / path.replace(0.0, np.nan)


def _intraday_momentum(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Average close-to-open intraday return over ``window`` days."""
    intraday = inputs.close / inputs.open.replace(0.0, np.nan) - 1.0
    return intraday.rolling(window).mean()


def _overnight_gap(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Average open-to-previous-close gap over ``window`` days."""
    gap = inputs.open / inputs.close.shift(1).replace(0.0, np.nan) - 1.0
    return gap.rolling(window).mean()


def _tail_risk(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Trailing 5%% daily return quantile; lower values imply worse left tail."""
    return _simple_returns(inputs.close).rolling(window).quantile(0.05)


def _amount_volatility(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Volatility of log amount changes over ``window`` days."""
    log_amount = np.log(inputs.amount.replace(0.0, np.nan))
    return log_amount.diff().rolling(window).std()


def _upper_shadow(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Average upper candle shadow share over ``window`` days."""
    body_top = pd.DataFrame(
        np.maximum(inputs.open.to_numpy(), inputs.close.to_numpy()),
        index=inputs.close.index,
        columns=inputs.close.columns,
    )
    spread = (inputs.high - inputs.low).replace(0.0, np.nan)
    shadow = (inputs.high - body_top) / spread
    return shadow.rolling(window).mean()


def _lower_shadow(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Average lower candle shadow share over ``window`` days."""
    body_bottom = pd.DataFrame(
        np.minimum(inputs.open.to_numpy(), inputs.close.to_numpy()),
        index=inputs.close.index,
        columns=inputs.close.columns,
    )
    spread = (inputs.high - inputs.low).replace(0.0, np.nan)
    shadow = (body_bottom - inputs.low) / spread
    return shadow.rolling(window).mean()


def _close_location(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Average close location inside each day's high-low range."""
    spread = (inputs.high - inputs.low).replace(0.0, np.nan)
    location = (2.0 * inputs.close - inputs.high - inputs.low) / spread
    return location.rolling(window).mean()


def _rsv(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Raw stochastic value inside the trailing high-low channel."""
    low_min = inputs.low.rolling(window).min()
    high_max = inputs.high.rolling(window).max()
    spread = (high_max - low_min).replace(0.0, np.nan)
    return (inputs.close - low_min) / spread


def _amount_shock_z(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Current amount z-score against its trailing ``window`` history."""
    mean = inputs.amount.rolling(window).mean()
    std = inputs.amount.rolling(window).std().replace(0.0, np.nan)
    return (inputs.amount - mean) / std


def _rolling_trend_parts(
    close: pd.DataFrame, window: int
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Rolling OLS slope, intercept, and R2 for close ~ time.

    The time regressor is the relative position inside each trailing window
    (0 for the oldest row, ``window - 1`` for the current row).  Using the
    absolute row number and then shifting it back to relative coordinates
    keeps the computation vectorised across the wide symbol columns.
    """
    positions = pd.Series(np.arange(len(close), dtype=float), index=close.index)
    sum_y = close.rolling(window).sum()
    sum_y2 = (close**2).rolling(window).sum()
    weighted_abs = close.mul(positions, axis=0).rolling(window).sum()
    start_pos = positions - (window - 1)
    sum_xy = weighted_abs - sum_y.mul(start_pos, axis=0)

    x = np.arange(window, dtype=float)
    x_sum = float(x.sum())
    x_mean = x_sum / window
    x_denom = float(((x - x_mean) ** 2).sum())

    slope = (sum_xy - (x_sum / window) * sum_y) / x_denom
    intercept = (sum_y / window) - slope * x_mean

    total_ss = sum_y2 - (sum_y**2) / window
    r_square = (slope**2 * x_denom) / total_ss.where(total_ss > 0.0)
    return slope, intercept, r_square.clip(lower=0.0, upper=1.0)


def _linear_slope(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """OLS slope of close on time over ``window`` days, scaled by close."""
    slope, _, _ = _rolling_trend_parts(inputs.close, window)
    return slope / inputs.close.replace(0.0, np.nan)


def _trend_rsquare(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """R-squared of a trailing linear trend fit to close."""
    _, _, r_square = _rolling_trend_parts(inputs.close, window)
    return r_square


def _trend_residual(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Latest close residual from the trailing linear trend, scaled by close."""
    slope, intercept, _ = _rolling_trend_parts(inputs.close, window)
    fitted_last = intercept + slope * (window - 1)
    return (inputs.close - fitted_last) / inputs.close.replace(0.0, np.nan)


def _volume_return_divergence(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Rolling correlation between price return and log volume change."""
    ret = _simple_returns(inputs.close)
    log_volume_change = np.log(inputs.volume.replace(0.0, np.nan)).diff()
    return ret.rolling(window).corr(log_volume_change)


def _price_rank(inputs: FactorInputs, window: int) -> pd.DataFrame:
    """Percentile rank of the latest close inside the trailing close window.

    Returns 0 for a unique trailing low, 1 for a unique trailing high, and
    averages ties so a fully flat window lands at 0.5.
    """
    def _rank_latest(values: np.ndarray) -> float:
        latest = values[-1]
        if np.isnan(latest):
            return np.nan
        less = float(np.sum(values < latest))
        equal = float(np.sum(values == latest))
        avg_rank = less + (equal + 1.0) / 2.0
        return (avg_rank - 1.0) / (len(values) - 1.0)

    return inputs.close.rolling(window).apply(_rank_latest, raw=True)


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
    reg.register("reversal_10d", lambda inp, p: _reversal(inp, 10))

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
    reg.register("money_flow_proxy_20d", lambda inp, p: _money_flow_proxy(inp, 20))
    reg.register("amount_volatility_20d", lambda inp, p: _amount_volatility(inp, 20))

    # Risk-adjusted / return-distribution
    reg.register("sharpe_20d", lambda inp, p: _sharpe(inp, 20))
    reg.register("return_skew_20d", lambda inp, p: _return_skew(inp, 20))
    reg.register("vwap_gap_20d", lambda inp, p: _vwap_gap(inp, 20))

    # Public factor-library inspired price/volume transforms
    reg.register("low_vol_reversal_20d", lambda inp, p: _low_vol_reversal(inp, 20))
    reg.register("breakout_strength_20d", lambda inp, p: _breakout_strength(inp, 20))
    reg.register("drawdown_recovery_20d", lambda inp, p: _drawdown_recovery(inp, 20))
    reg.register("close_position_20d", lambda inp, p: _close_position(inp, 20))
    reg.register("price_efficiency_20d", lambda inp, p: _price_efficiency(inp, 20))
    reg.register("intraday_momentum_20d", lambda inp, p: _intraday_momentum(inp, 20))
    reg.register("overnight_gap_20d", lambda inp, p: _overnight_gap(inp, 20))
    reg.register("tail_risk_20d", lambda inp, p: _tail_risk(inp, 20))
    reg.register("upper_shadow_20d", lambda inp, p: _upper_shadow(inp, 20))
    reg.register("lower_shadow_20d", lambda inp, p: _lower_shadow(inp, 20))
    reg.register("close_location_20d", lambda inp, p: _close_location(inp, 20))
    reg.register("rsv_20d", lambda inp, p: _rsv(inp, 20))
    reg.register("amount_shock_z_20d", lambda inp, p: _amount_shock_z(inp, 20))
    reg.register("linear_slope_20d", lambda inp, p: _linear_slope(inp, 20))
    reg.register("trend_rsquare_20d", lambda inp, p: _trend_rsquare(inp, 20))
    reg.register("trend_residual_20d", lambda inp, p: _trend_residual(inp, 20))
    reg.register(
        "volume_return_divergence_20d",
        lambda inp, p: _volume_return_divergence(inp, 20),
    )
    reg.register("price_rank_20d", lambda inp, p: _price_rank(inp, 20))

    return reg


BUILTIN_FACTOR_NAMES: list[str] = [
    "momentum_5d",
    "momentum_20d",
    "momentum_60d",
    "momentum_20d_skip_5d",
    "ma_gap_20d",
    "ma_gap_60d",
    "reversal_5d",
    "reversal_10d",
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
    "money_flow_proxy_20d",
    "amount_volatility_20d",
    "sharpe_20d",
    "return_skew_20d",
    "vwap_gap_20d",
    "low_vol_reversal_20d",
    "breakout_strength_20d",
    "drawdown_recovery_20d",
    "close_position_20d",
    "price_efficiency_20d",
    "intraday_momentum_20d",
    "overnight_gap_20d",
    "tail_risk_20d",
    "upper_shadow_20d",
    "lower_shadow_20d",
    "close_location_20d",
    "rsv_20d",
    "amount_shock_z_20d",
    "linear_slope_20d",
    "trend_rsquare_20d",
    "trend_residual_20d",
    "volume_return_divergence_20d",
    "price_rank_20d",
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
    "reversal_10d": 1,
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
    "money_flow_proxy_20d": 1,
    "amount_volatility_20d": -1,
    # Risk-adjusted / return-distribution
    "sharpe_20d": 1,        # higher Sharpe = better risk-adjusted momentum
    "return_skew_20d": 1,   # positive skew preferable for long-only holders
    "vwap_gap_20d": 1,      # closing above VWAP = buying pressure
    # Price/volume transforms
    "low_vol_reversal_20d": 1,
    "breakout_strength_20d": 1,
    "drawdown_recovery_20d": 1,
    "close_position_20d": 1,
    "price_efficiency_20d": 1,
    "intraday_momentum_20d": 1,
    "overnight_gap_20d": 1,
    "tail_risk_20d": 1,
    "upper_shadow_20d": -1,
    "lower_shadow_20d": 1,
    "close_location_20d": 1,
    "rsv_20d": 1,
    "amount_shock_z_20d": -1,
    "linear_slope_20d": 1,
    "trend_rsquare_20d": 1,
    "trend_residual_20d": -1,
    "volume_return_divergence_20d": 1,
    "price_rank_20d": 1,
}
