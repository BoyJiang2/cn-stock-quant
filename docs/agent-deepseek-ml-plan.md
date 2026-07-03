# DeepSeek ML Plan v1

Date: 2026-06-30

Scope: research design only. Do not change core strategy, factor, backtest, or API code in this pass.

## Current Read

- The project already has a usable factor base in `backend/app/factors`: `FactorLab` builds a `MultiIndex(trade_date, symbol)` panel from OHLCV, all built-in factors use trailing rolling windows, and `preprocess` applies cross-sectional per-date robust transforms.
- `forward_returns` already defines labels with T+1 entry: signal date `t` maps to `close(t+1+h) / close(t+1) - 1`. This is the right starting point for a tradable LightGBM label.
- `backend/app/ai_research/qlib_adapter.py` already has the narrow adapter boundary: predictions for one date become `dict[symbol -> weight]`.
- Current research runs are explicitly degraded: universe selection is not point-in-time, ST/listing/delisting history is incomplete, and qfq OHLCV may be revised after future corporate actions. ML results must inherit these warnings until PIT data is wired in.
- `stable_reversal` has weak gross alpha and high turnover. `inverse_momentum` is currently the strongest simple-rule candidate but remains cost-sensitive under retail/stress assumptions.

## LightGBM Dataset v1

Use the existing local factor panel first, not full Qlib integration. The first dataset should be a versioned flat table exported from:

- `trade_date`
- `symbol`
- built-in factor columns from `FactorLab.compute_all`
- optional simple filters as columns, not hard-coded deletion: `close`, `amount_20d`, `is_tradable_proxy`
- label columns: `fwd_5d`, optionally `fwd_10d`
- metadata: `dataset_version`, `universe_version`, `factor_code_hash`, `created_at`, degraded flags

Recommended v1 feature set:

- Momentum/reversal: `momentum_5d`, `momentum_20d`, `momentum_60d`, `momentum_20d_skip_5d`, `ma_gap_20d`, `ma_gap_60d`, `reversal_5d`
- Risk: `volatility_20d`, `volatility_60d`, `downside_volatility_20d`, `max_drawdown_20d`, `max_drawdown_60d`, `atr_pct_14d`, `intraday_range_20d`
- Liquidity/crowding: `log_amount_20d`, `amount_ratio_5d_20d`, `volume_ratio_5d_20d`, `amount_stability_20d`, `amihud_illiquidity_20d`, `price_volume_corr_20d`, `up_day_ratio_20d`
- Distribution: `sharpe_20d`, `return_skew_20d`, `vwap_gap_20d`

Preprocessing:

- Apply winsorization and standardization per `trade_date`, never globally across time.
- Fit any imputation/scaling rule only on the train window if it has state. The existing per-date median/MAD transform is acceptable because it only uses the same-date cross-section available at signal time.
- Keep missing-factor rows out of v1 training unless missingness itself becomes an explicit feature later.

## Split Design

No random splits. Use date-based walk-forward only.

Primary static baseline:

- Train: 2024-01-02 to 2024-09-30
- Validation: 2024-10-01 to 2024-12-31
- Test/OOS 1: 2025-01-01 to 2025-12-31
- Test/OOS 2: 2026-01-01 to latest local date

Rolling validation after the baseline:

- Train window: trailing 12 months or all available history before validation, whichever is larger after warmup.
- Validation window: next 1 to 3 months for early stopping and parameter choice.
- Test window: next month or next quarter, then roll forward.
- Freeze model parameters before scoring each test window.

Do not tune on 2025 and then report 2025 as OOS. If 2025 is used for tuning, 2026 must become the first clean OOS period.

## Label Definition

Default label: `fwd_5d`, because current strategy work already uses 5 to 10 day rebalancing and the factor rerun used 5d.

Candidate labels:

- Raw return: `fwd_5d`
- Excess return: `fwd_5d - benchmark_fwd_5d` using a broad index such as `000300`, and later `000905` / `000852`
- Cross-sectional rank label: per-date percentile rank of `fwd_5d`
- Binary top-quantile label only as a secondary experiment, not the first version

Recommended v1 objective:

- Use LightGBM regression on cross-sectionally ranked or z-scored `fwd_5d`, then evaluate predictions by RankIC and TopN backtest. Ranking quality matters more than raw MSE.

Important label timing:

- Features at `trade_date=t` use only OHLCV up to and including `t`.
- Label starts at `close(t+1)`, matching current engine behavior where target weights are generated on `t` and executed on the next available trading day.
- Rows whose label needs prices beyond the available data must be dropped, not forward-filled.

## Leakage Gates

Hard blockers:

- No random train/test split.
- No features computed with future rows, centered windows, or full-sample statistics.
- No universe built from future survivorship when claiming live-readiness. Current research-pool runs must be marked degraded.
- No same-day close-to-close label if the strategy trades after seeing the same close.
- No benchmark, ST, listing, delisting, or index membership field may be backfilled from a future known state.
- No hyperparameter search on 2025 followed by calling 2025 out-of-sample.

Soft risks to disclose:

- qfq history may be revised after corporate actions.
- Missing bars are not yet classified into suspension, listing gap, delisting, or provider gap.
- BJ/illiquid names may inflate rank metrics unless liquidity filters are applied consistently in train and backtest.

## Evaluation Metrics

Model-level:

- Daily IC and RankIC mean/std/IR on validation and OOS.
- Top-minus-bottom quantile return, with turnover.
- Coverage: number of scored names per date and missing-feature drop rate.
- Feature importance stability across folds. A model dominated by one unstable factor should not graduate.

Portfolio-level:

- Total return, annual return, max drawdown, Sharpe.
- Excess return versus `000300`, `000905`, and `000852`.
- Turnover on initial cash and annualized turnover proxy.
- Trade count, buy/sell amount, commission, stamp tax.
- Cost matrix: zero, ideal, default, retail, stress.
- Neighbor robustness: `top_n`, exposure cap, rebalance interval, hold buffer.

Promotion gate:

- Positive default-cost excess return in at least 2024 and 2025.
- 2026 walk-forward does not collapse.
- Retail/stress costs should not erase all excess unless the model is explicitly marked research-only.
- Drawdown must be acceptable versus benchmarks and simple rules.

## TopN Strategy Conversion

Use the existing adapter boundary:

1. Save predictions as `trade_date/symbol/score/model_run_id`.
2. On each decision date, select predictions for `context.current_date`.
3. Filter out invalid scores, suspended/no-volume names, very low price names, and names below liquidity floor.
4. Rank by score descending.
5. Keep existing holdings inside a hold buffer before adding new names. Suggested v1:
   - `top_n=30`
   - `entry_rank_multiplier=1.0`
   - `hold_rank_multiplier=1.3` to `1.6`
   - `max_position_weight=0.05`
   - `gross_exposure=0.8`
6. Weighting options:
   - v1: equal weight after caps for stability and easier attribution.
   - v1.1: score-proportional positive weights, capped by `max_position_weight`.
   - Avoid shorting in v1.

The strategy class should remain a read-only prediction consumer: no model training, no database mutation, no order generation.

## 2024/2025/2026 Validation

Minimum validation sequence:

1. Baseline simple factors:
   - Re-run factor evaluation separately for 2024, 2025, and 2026-to-date.
   - Confirm whether 2025 negative momentum persists in 2024/2026 or was regime-specific.
2. Static LightGBM:
   - Train on 2024 Q1-Q3, validate on 2024 Q4.
   - Score 2025 only once with chosen parameters.
   - If 2025 passes, score 2026-to-date without retuning.
3. Rolling LightGBM:
   - Monthly or quarterly retrain.
   - Each test month/quarter must only use models trained before that period.
4. Strategy backtest:
   - Compare ML TopN versus `inverse_momentum`, `stable_reversal`, and low-vol defensive rules using identical universe, costs, rebalance interval, and benchmark.
5. Stress validation:
   - Run cost matrix and longer rebalance intervals.
   - Measure whether hold buffer lowers turnover without destroying RankIC-to-PnL conversion.

Do not promote based on one combined 2024-2026 run. Each year/regime must be reported separately.

## Risk Review: inverse_momentum

Strengths:

- Current 60d variant has the best simple-rule evidence so far: positive default-cost excess in 2025 and positive 2024 OOS against `000300`.
- Turnover is materially lower than early `stable_reversal` runs.
- Uses liquidity, price, drawdown, amount-ratio, hold-buffer, and benchmark-momentum gates.

Risks:

- It may be exploiting a 2024/2025 regime where laggards mean-reverted strongly. The 20d variants were weak, so the edge may be horizon-specific and fragile.
- It buys weak trailing names. Without stronger quality/risk filters it can concentrate in structural losers, post-crash bounces, or names with hidden event risk.
- It is still cost-sensitive: retail/stress costs turned 2025 excess negative in current logs.
- Benchmark gate currently uses one broad momentum threshold and can be too blunt; it does not distinguish CSI 300/500/1000/BJ regimes.
- Current universe is non-PIT, so survivorship and ST/delisting exclusion risks remain.

Recommendation:

- Keep `IM_60d_top30` as the simple-rule benchmark for ML. ML must beat it after costs, not just beat `stable_reversal`.
- Add regime-sliced reports before promotion: bull, sideways, drawdown, high-vol, low-vol.
- Treat 2026-to-date as the next clean forward check if no tuning uses it.

## Risk Review: stable_reversal

Strengths:

- Uses intuitive factors that looked good in 2025 factor rerun: amount stability and 5d reversal.
- Hysteresis parameters now directly target boundary churn.
- Defensive behavior improved with wider `top_n`, stricter reversal/crowding filters, and lower drawdown.

Risks:

- Gross edge is weak: 2025 zero-cost `G4_extreme_low_turnover` still lagged `000300`.
- Turnover remains high even after improvements, and costs erase much of the edge.
- It may be selecting short-term oversold/liquid names without enough medium-term quality control.
- `amount_stability_20d` can favor stale or mechanically stable traded amount; it needs suspension/gap classification and stronger tradability filters.
- Equal weight avoids score overfitting but may allocate too much to marginal candidates near the cutoff.

Recommendation:

- Treat `stable_reversal` as a defensive sleeve or feature source, not the primary profit engine.
- Feed its component factors into LightGBM, but require ML to prove incremental value over the hand-built rule.
- Do not spend more grid budget on `stable_reversal` until PIT and multi-benchmark validation are in place.

## First Implementation Boundary

When implementation is assigned, keep ownership narrow:

- Add a standalone training CLI or worker entry, not FastAPI runtime imports of heavy ML dependencies.
- Input: versioned factor/label parquet or CSV generated from existing `FactorLab`.
- Output: model artifact, prediction table/file, evaluation JSON.
- Strategy integration: a read-only prediction strategy that returns `dict[symbol -> weight]`.
- Tests: label timing, date split integrity, no random split, prediction-to-weight caps, and no mutation in prediction strategy.

