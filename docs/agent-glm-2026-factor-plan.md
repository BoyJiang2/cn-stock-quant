# GLM 2026 Factor Screening Plan

Date: 2026-07-02

Scope: GLM-side factor research plan for P0/P1/P3/P4. This document is a
research and execution plan only. It does not change core factor or strategy
code.

## Current Read

The project now has 45 built-in OHLCV/amount factors in
`backend/app/factors/factors.py`. `backend/run_factor_experiment.py` can run
all built-ins by default or a named subset through repeated `--factor`
arguments. The 2026 data path is usable for research, with local bars through
2026-06-18 and benchmark bars for `000300`, `000905`, and `000852`, but runs
remain non-PIT and must be marked degraded until P3 is solved.

Use 2026 only as out-of-sample validation. Do not tune parameters on 2026 and
then call the same result OOS.

## 1. Batch Plan For The Current Built-In Factors

Run factors in small, interpretable batches first, then run the full 34-factor
screen once the batch runs finish. Use horizon 5 as the primary screen because
it is close to the current 5-10 day strategy cadence; rerun survivors on
horizon 10 before strategy use.

All commands below run from:

```powershell
cd D:\CursorProjects\cn-stock-quant\backend
```

### Batch A: Momentum And Reversal

Purpose: decide whether 2026 still favors laggards/reversal or returns to
trend-following.

Factors:

- `momentum_5d`
- `momentum_20d`
- `momentum_60d`
- `momentum_20d_skip_5d`
- `ma_gap_20d`
- `ma_gap_60d`
- `reversal_5d`
- `low_vol_reversal_20d`

Command:

```powershell
python run_factor_experiment.py --start-date 2026-01-01 --end-date 2026-06-18 --horizon 5 --pool-max-symbols 6000 --output factor-2026-batch-a-momentum-reversal.json --factor momentum_5d --factor momentum_20d --factor momentum_60d --factor momentum_20d_skip_5d --factor ma_gap_20d --factor ma_gap_60d --factor reversal_5d --factor low_vol_reversal_20d
```

Decision use:

- If `momentum_60d` remains negative but `low_vol_reversal_20d` stays positive,
  keep inverse-momentum and low-vol reversal as strategy/ML candidates.
- If short momentum turns positive while long momentum stays weak, avoid mixing
  them without regime controls.

### Batch B: Risk, Volatility, And Drawdown

Purpose: identify risk filters and low-volatility alpha that can help P1 lower
turnover and drawdown.

Factors:

- `volatility_20d`
- `volatility_60d`
- `downside_volatility_20d`
- `max_drawdown_20d`
- `max_drawdown_60d`
- `atr_pct_14d`
- `intraday_range_20d`
- `return_skew_20d`
- `tail_risk_20d`

Command:

```powershell
python run_factor_experiment.py --start-date 2026-01-01 --end-date 2026-06-18 --horizon 5 --pool-max-symbols 6000 --output factor-2026-batch-b-risk-vol.json --factor volatility_20d --factor volatility_60d --factor downside_volatility_20d --factor max_drawdown_20d --factor max_drawdown_60d --factor atr_pct_14d --factor intraday_range_20d --factor return_skew_20d --factor tail_risk_20d
```

Decision use:

- A risk factor can enter strategy even with modest RankIC if it reduces
  drawdown, turnover, or left-tail loss in backtests.
- `tail_risk_20d`, `downside_volatility_20d`, and `atr_pct_14d` should be
  tested as filters before being used as raw long alpha.

### Batch C: Liquidity, Amount, And Crowding

Purpose: validate whether 2025's low amount-volatility finding survives in
2026 and whether volume/amount signals are alpha or overheating warnings.

Factors:

- `log_amount_20d`
- `amount_ratio_5d_20d`
- `volume_ratio_5d_20d`
- `amount_stability_20d`
- `amihud_illiquidity_20d`
- `price_volume_corr_20d`
- `money_flow_proxy_20d`
- `amount_volatility_20d`
- `vwap_gap_20d`

Command:

```powershell
python run_factor_experiment.py --start-date 2026-01-01 --end-date 2026-06-18 --horizon 5 --pool-max-symbols 6000 --output factor-2026-batch-c-liquidity-crowding.json --factor log_amount_20d --factor amount_ratio_5d_20d --factor volume_ratio_5d_20d --factor amount_stability_20d --factor amihud_illiquidity_20d --factor price_volume_corr_20d --factor money_flow_proxy_20d --factor amount_volatility_20d --factor vwap_gap_20d
```

Decision use:

- `amount_volatility_20d` is the highest-priority confirmation target because
  it was the best 2025 new factor.
- If `money_flow_proxy_20d`, `amount_ratio_5d_20d`, or `vwap_gap_20d` show
  negative RankIC, treat them as crowding or overheat filters instead of
  discarding them.

### Batch D: Price Position, Path, And Intraday Structure

Purpose: test whether price-channel and intraday structure factors help with
entry timing or only identify crowded names.

Factors:

- `up_day_ratio_20d`
- `breakout_strength_20d`
- `drawdown_recovery_20d`
- `close_position_20d`
- `price_efficiency_20d`
- `intraday_momentum_20d`
- `overnight_gap_20d`

Command:

```powershell
python run_factor_experiment.py --start-date 2026-01-01 --end-date 2026-06-18 --horizon 5 --pool-max-symbols 6000 --output factor-2026-batch-d-price-structure.json --factor up_day_ratio_20d --factor breakout_strength_20d --factor drawdown_recovery_20d --factor close_position_20d --factor price_efficiency_20d --factor intraday_momentum_20d --factor overnight_gap_20d
```

Decision use:

- In 2025, `intraday_momentum_20d`, `drawdown_recovery_20d`, and
  `money_flow_proxy_20d` looked more like overheating warnings. If 2026 agrees,
  use inverse versions or risk filters, not direct long scores.
- `overnight_gap_20d` should not be promoted from weak RankIC alone because
  open information has strict timing constraints.

### Batch E: Full Built-In Factor Screen

Purpose: produce the canonical 2026 factor table after the focused runs.

Command:

```powershell
python run_factor_experiment.py --start-date 2026-01-01 --end-date 2026-06-18 --horizon 5 --pool-max-symbols 6000 --output factor-2026-all-builtins-h5.json
python run_factor_experiment.py --start-date 2026-01-01 --end-date 2026-06-18 --horizon 10 --pool-max-symbols 6000 --output factor-2026-all-builtins-h10.json
```

Minimum reporting table:

| Field | Use |
| --- | --- |
| `rankic_mean` | Primary cross-sectional direction check |
| `rankic_ir` | Stability across dates |
| `long_short_return` | Economic spread sanity check |
| `long_short_turnover` | P1 cost/tradability check |
| `n_dates` | Sample-size guard |
| 2025 rank side-by-side | Regime robustness check |

## 2. Next Implementable Factor Candidates

Only OHLCV/amount candidates should enter the next implementation batch. Keep
financial, industry, and news factors in planning until PIT timing and source
quality are solved.

### Recommended Batch F: OHLCV/Amount Candidates

| Priority | Candidate | Formula | Fields | Initial Use |
| ---: | --- | --- | --- | --- |
| 1 | `upper_shadow_20d` | mean `(high - max(open, close)) / (high - low + eps)` over 20d | OHLC | overheat/supply filter |
| 2 | `lower_shadow_20d` | mean `(min(open, close) - low) / (high - low + eps)` over 20d | OHLC | reversal/absorption candidate |
| 3 | `close_location_20d` | mean `(2*close - high - low) / (high - low + eps)` over 20d | OHLC | close-strength signal |
| 4 | `rsv_20d` | `(close - min(low,20)) / (max(high,20) - min(low,20) + eps)` | OHLC | channel position, test both directions |
| 5 | `price_rank_20d` | rolling rank of latest close inside trailing 20 closes | close | smoother momentum/reversal input |
| 6 | `linear_slope_20d` | slope of close on time over 20d divided by close | close | trend strength |
| 7 | `trend_rsquare_20d` | rolling R2 of close on time over 20d | close | trend quality filter |
| 8 | `trend_residual_20d` | last residual from 20d trend regression divided by close | close | mean-reversion timing |
| 9 | `amount_shock_z_20d` | `(amount - mean(amount,20)) / std(amount,20)` | amount | crowding/event filter |
| 10 | `volume_return_divergence_20d` | corr of price return and log volume change over 20d | close, volume | price-volume confirmation/divergence |
| 11 | `reversal_10d` | `close.shift(10) / close - 1` | close | slower reversal, lower turnover candidate |
| 12 | `skip_momentum_60_5` | `close.shift(5) / close.shift(65) - 1` | close | classic skip-month momentum |

Implementation notes for Codex handoff:

- Use the existing wide-frame pattern in `backend/app/factors/factors.py`.
- Register explicit `FACTOR_DIRECTIONS`, but treat direction as provisional
  until 2024/2025/2026 screens agree.
- Add isolation, truncation, and small manual tests for each new factor.
- Do not add financial factors until announcement-date PIT fields exist.

Implementation status:

- 2026-07-02: Batch F first pass implemented for `upper_shadow_20d`,
  `lower_shadow_20d`, `close_location_20d`, `rsv_20d`,
  `amount_shock_z_20d`, and `reversal_10d`.
- 2026-07-03: Batch F second pass implemented for `linear_slope_20d`,
  `trend_rsquare_20d`, `trend_residual_20d`,
  `volume_return_divergence_20d`, and `price_rank_20d`.

### Deferred Factors

| Candidate Area | Reason To Defer |
| --- | --- |
| PE/PB/ROE/quality/growth | Requires PIT financial statements, announcement dates, TTM snapshots, and market-cap path |
| Industry-relative factors | Requires historical industry classification and preferably historical index constituents |
| Analyst revision | Requires historical consensus snapshots |
| News sentiment alpha | Requires P4 sync, deduplication, classifier versioning, and availability timing |

## 3. Promotion Rules For Strategy And ML

A factor can move from research to strategy or ML only after passing these
gates.

### Gate 1: Data And Timing

- Uses only data available at signal date `t`.
- Daily OHLCV factors are assumed usable only after the full `t` bar is known,
  so trading must begin no earlier than `t+1`.
- Runs must mark current universe as non-PIT until P3 provides listing,
  delisting, ST/risk, and historical constituent tables.
- Factors with revised data risk, such as qfq prices, must be noted in output.

### Gate 2: Cross-Year Evidence

Minimum evidence before strategy inclusion:

- 2025 full-market screen already exists or is rerun with the same CLI.
- 2026 screen has the same sign or a defensible inverse interpretation.
- 2024 check is run before promotion if local coverage is sufficient.
- RankIC should not depend on one small window or one benchmark regime.

Suggested thresholds:

- Strategy alpha candidate: `rankic_mean >= 0.02` and `rankic_ir > 0.2` in at
  least two years after direction adjustment.
- Strong candidate: `rankic_mean >= 0.04` or clearly positive long-short return
  in at least two years.
- Risk-filter candidate: can pass with lower RankIC if strategy backtests show
  lower drawdown, lower turnover, or better stress-cost excess.
- Cemetery candidate: unstable sign across years, very high turnover with weak
  spread, or no economic interpretation.

### Gate 3: Strategy Use

Before entering `multi_factor_rank`, `inverse_momentum`, or any new rule:

- Compare against `000300`, `000905`, and `000852`.
- Check default, retail, and stress cost cases.
- Confirm neighboring parameter sets also work.
- Measure turnover contribution. A high-IC factor that increases churn can be
  ML-only or filter-only rather than a direct strategy score.
- Prefer factors that improve 2025 and 2026 together; do not optimize only the
  latest half-year.

### Gate 4: ML Use

For `build_ml_dataset.py` / LightGBM:

- Features must use the direction-adjusted and per-date standardized factor
  values already produced by the dataset boundary.
- Remove or flag features with high missingness, unstable sign, or obvious
  timing ambiguity.
- Use 2024/2025 for train/validation and keep 2026 as untouched test when
  possible.
- A factor can enter ML before rule strategy if it is plausible and non-leaky,
  but feature importance must be reviewed. Reject models dominated by one
  unstable or future-risk feature.

## 4. Linkage With News And Sentiment Factors

News should connect to the factor workflow in two stages: risk control first,
alpha later.

### Stage P4-A: Data Availability And Timing

Use the implemented `NewsItem` and `NewsProvider` boundary. Every item must
keep:

- `published_at`
- `fetched_at`
- `source`
- `source_id`
- `symbol`
- `event_type`
- `sentiment_label`
- `sentiment_score`
- `relevance_score`

Backtests should use `known_at = max(published_at, fetched_at)`. If an item is
known after the market decision time, it can only affect the next trading day.

### Stage P4-B: Negative-News Risk Filter

Implement and test before sentiment alpha:

- severe negative event count over 3/5/20 trading days;
- weighted negative score: `sum(abs(sentiment_score) * relevance_score)`;
- event tags for investigation, regulatory penalty, lawsuit, loss warning,
  debt/default risk, shareholder reduction, and abnormal trading.

Promotion criterion: price-only strategy versus price-plus-news-filter should
show better drawdown or tail loss without destroying excess return. It does not
need high RankIC to be useful.

### Stage P4-C: Sentiment Factors

After sync and classifier v1 are stable, test:

| Factor | Definition | Use |
| --- | --- | --- |
| `news_sentiment_sum_5d` | 5d sum of `sentiment_score * relevance_score` | alpha candidate |
| `news_heat_change_5_20` | 5d news count / 20d average count - 1 | attention/crowding |
| `negative_news_shock_3d` | high-relevance negative count over 3d | risk filter |
| `sentiment_disagreement_20d` | 20d sentiment std or positive/negative split | uncertainty filter |
| `fundamental_news_sentiment_20d` | high-relevance fundamental-news sentiment | alpha candidate |

Join method:

1. Aggregate daily symbol-level news features using `known_at`.
2. Left-join to the factor panel by `trade_date` and `symbol`.
3. Use missing news as zero only for count/heat features; use NaN for sentiment
   averages when sample count is zero.
4. Run price-only factors and price-plus-news factors side by side.

### Interaction With Current OHLCV Factors

Prioritize these interactions:

- `amount_shock_z_20d` plus negative news: distinguish event-driven volume from
  normal crowding.
- `money_flow_proxy_20d` plus sentiment: test whether signed amount is useful
  only when news sentiment confirms it.
- `tail_risk_20d` plus negative news: risk-off filter for names with both poor
  left-tail behavior and bad events.
- `low_vol_reversal_20d` plus no-negative-news: avoid buying falling stocks
  with unresolved bad news.

## Immediate Recommendations

1. Run Batch A-D for 2026 first, then the full built-in horizon-5 and
   horizon-10 screens.
2. Promote only factors that agree with 2025 or have a clear inverse/filter
   interpretation.
3. Give highest implementation priority to `upper_shadow_20d`,
   `lower_shadow_20d`, `close_location_20d`, `rsv_20d`, `linear_slope_20d`,
   `trend_rsquare_20d`, `trend_residual_20d`, `amount_shock_z_20d`,
   `volume_return_divergence_20d`, and `reversal_10d`.
4. Keep `amount_volatility_20d` and `low_vol_reversal_20d` as the first
   confirmation targets for strategy/ML because they already worked in 2025.
5. Treat news as a drawdown and event-risk control layer before treating it as
   alpha.
6. Do not use financial or industry-relative factors in production strategy
   selection until P3/PIT data foundations exist.
