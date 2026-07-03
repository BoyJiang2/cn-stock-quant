# Static LightGBM Failure Analysis

Date: 2026-07-03

Scope: explain why the static `fwd_5d` LightGBM failed in 2026, using the local artifacts under `backend/artifacts/ml/`, the fair-window backtest notes in `docs/profit-first-execution-plan.md`, and the implementation of `backend/analyze_ml_predictions.py`. The user-facing artifact paths in the diagnostics are relative to `backend/`; the actual files are under `backend/artifacts/ml/`.

## Executive Verdict

Verdict: `reject_static`, `needs_walk_forward`.

The static LightGBM failed before portfolio construction. Its 2026 cross-sectional RankIC collapsed to `0.001870` with IR `0.028125`, the highest score bucket underperformed the lowest bucket by `-0.002055` over the 5-day label horizon, and the fair-window `ml_score_rank` top30 strategy lost `-14.7360%` from 2026-01-05 to 2026-06-10.

Data caveat: all current ML datasets/backtests are marked non-PIT/degraded. The dataset metadata states that the research pool is selected from today's active-stock coverage, the universe is non-PIT, and qfq OHLCV may be revised. That caveat limits promotion decisions, but it does not explain away the observed 2026 score inversion.

## Static vs Walk-Forward Snapshot

| Item | Static LightGBM | Walk-forward v45 embargo15 |
| --- | ---: | ---: |
| Dataset | `ml-factor-dataset-2024-2026.csv`, 40 factors | `ml-factor-dataset-2024-2026-v45.csv`, 45 factors |
| Train design | fixed 2024-01-02 to 2024-09-30 | rolling 12m train, 2m valid, 1m test |
| Validation | 2024-10-01 to 2024-12-31 | rolling validation with 15-calendar-day embargo |
| 2026 prediction window | 2026-01-05 to 2026-06-10 | same |
| Rows / dates / symbols | 497,002 / 103 / 4,965 | same |
| RankIC mean | `0.001870` | `0.032226` |
| RankIC IR | `0.028125` | `0.424468` |
| Positive IC day rate | `53.4%` | `64.1%` |
| IC t-stat | `0.29` | `4.31` |
| Bucket long-short | `-0.002055` | `0.001989` |
| Top30 mean `fwd_5d` | `-0.004445` | `0.000538` |
| Top30 minus universe | `-0.004037` | `0.000946` |
| Default-cost top30 strategy vs 000300 | `-14.7360%`, excess `-15.3899%` | `+1.1782%`, excess `+0.5244%` |

Walk-forward is materially better, but not promotion-ready. The best noted v45 variant with 20-day rebalance returned `+1.5474%` and beat `000300` by `+0.8936%`, but still lagged `000905` by `-3.8461%` and `000852` by `-4.1896%`, with max drawdown around `-13.9009%`.

## Ranked Failure Causes

### 1. The 2026 score has almost no ranking signal

Evidence:

| Window | RankIC mean | RankIC std | RankIC IR | N dates |
| --- | ---: | ---: | ---: | ---: |
| Static valid 2024Q4 | 0.055625 | 0.106770 | 0.520983 | 61 |
| Static 2025 OOS | 0.037680 | 0.068236 | 0.552200 | 243 |
| Static 2026 OOS | 0.001870 | 0.066506 | 0.028125 | 103 |
| WF v45 2026 | 0.032226 | 0.075921 | 0.424468 | 103 |

The key failure is not that the static model is mildly weaker in 2026; the signal is statistically indistinguishable from zero. The daily IC t-stat is only about `0.29`, and the positive IC day rate is only `53.4%`.

Monthly static RankIC also shows regime instability:

| Month | Static RankIC | WF v45 RankIC |
| --- | ---: | ---: |
| 2026-01 | 0.032162 | 0.103399 |
| 2026-02 | 0.002927 | 0.020100 |
| 2026-03 | 0.038738 | -0.000240 |
| 2026-04 | -0.045004 | 0.045922 |
| 2026-05 | -0.028984 | 0.013198 |
| 2026-06 | 0.015375 | -0.028341 |

Static fails especially in April and May, while walk-forward partly adapts because those months train on 2025 plus nearby 2026 validation context.

### 2. The bucket table is inverted, so top-N tuning cannot rescue this static score

Static 2026 per-date quintile buckets:

| Bucket | Mean score | Mean return |
| ---: | ---: | ---: |
| 1 lowest | 0.004422 | 0.000900 |
| 2 | 0.004698 | 0.001772 |
| 3 | 0.004803 | -0.001095 |
| 4 | 0.004864 | -0.002462 |
| 5 highest | 0.005142 | -0.001154 |

The highest static score bucket loses money and underperforms the lowest bucket by `-0.002055`. Bucket 2 is the best bucket, not bucket 5. This is an economic failure of score ordering, not merely a backtest implementation issue.

Walk-forward v45 improves the bucket direction:

| Bucket | Mean score | Mean return |
| ---: | ---: | ---: |
| 1 lowest | 0.005920 | -0.001935 |
| 2 | 0.007340 | -0.001010 |
| 3 | 0.007984 | 0.000302 |
| 4 | 0.008672 | 0.000547 |
| 5 highest | 0.009785 | 0.000055 |

This is still not cleanly monotonic because bucket 5 trails bucket 4, but the long-short direction turns positive. That contrast strongly points to static-window decay rather than a universal LightGBM or strategy-adapter problem.

### 3. The static model over-learned stale price-state patterns

Static feature importance is concentrated in price-state, drawdown, and trend-position features:

| Feature | Split | Gain | Gain share |
| --- | ---: | ---: | ---: |
| `drawdown_recovery_20d` | 10 | 44.206070 | 41.1% |
| `max_drawdown_60d` | 3 | 14.773120 | 13.7% |
| `ma_gap_20d` | 3 | 14.123780 | 13.1% |
| `close_position_20d` | 3 | 9.898740 | 9.2% |
| `momentum_20d` | 2 | 4.478160 | 4.2% |

The top 3 features explain about `67.9%` of gain; the top 5 explain about `81.3%`. Only 13 of 40 features have nonzero split/gain, so 27 of 40 are effectively unused. The model's `best_iteration=1` reinforces the same read: the fitted surface is shallow and dominated by one narrow historical pattern.

Most top features are price state or recovery descriptors: `drawdown_recovery_20d`, `max_drawdown_60d`, `ma_gap_20d`, `close_position_20d`, `momentum_20d`, `price_efficiency_20d`, `reversal_10d`, `volatility_60d`, `momentum_60d`, `low_vol_reversal_20d`, `downside_volatility_20d`. Stable 2026 full-market factor evidence in `profit-first-execution-plan.md` instead favored amount stability/amount volatility and some overnight/trend-structure features. Static LightGBM largely failed to use those more stable liquidity/amount families.

### 4. The fixed 2024 training window is stale for 2026

Static training uses:

- train: 2024-01-02 to 2024-09-30, 587,727 rows
- valid: 2024-10-01 to 2024-12-31, 291,872 rows
- 2026 test: 2026-01-01 to 2026-06-18 in metrics, prediction coverage 2026-01-05 to 2026-06-10

That means the 2026 score is produced by a model whose training data ends more than 15 months before the 2026 test period starts, and whose early-stopping validation is all 2024Q4. The model looked acceptable on 2025 OOS, but by 2026 the learned 2024 price-state relationships no longer transfer.

Walk-forward v45 uses six rolling windows, each with recent 12-month training, 2-month validation, 1-month test, and 15-day embargo. Its per-window RankIC shows why rolling helps but does not solve everything:

| Window | Test period | Best iteration | RankIC | Long-short |
| ---: | --- | ---: | ---: | ---: |
| 1 | 2026-01-05 to 2026-01-31 | 1 | 0.103399 | 0.009381 |
| 2 | 2026-02-01 to 2026-02-28 | 4 | 0.020100 | -0.004866 |
| 3 | 2026-03-01 to 2026-03-31 | 19 | -0.000240 | 0.000924 |
| 4 | 2026-04-01 to 2026-04-30 | 15 | 0.045922 | 0.003858 |
| 5 | 2026-05-01 to 2026-05-31 | 82 | 0.013198 | 0.002795 |
| 6 | 2026-06-01 to 2026-06-10 | 1 | -0.028341 | -0.008121 |

The rolling model still has weak/negative months, but it avoids the static model's April-May inversion and produces a materially better overall 2026 RankIC.

### 5. Portfolio topN made the statistical failure economically worse

The `ml_score_rank` implementation reads same-date scores, applies eligibility filters, ranks descending by score, retains existing holdings only if they remain inside `top_n * hold_rank_multiplier`, and then equal-weights selected names subject to `max_total_weight` and `max_position_weight`. It does not forward-fill missing score dates.

The fair static configuration was:

- `top_n=30`
- `max_total_weight=0.8`
- `max_position_weight=0.05`
- `min_avg_amount_20d=50,000,000`
- `min_price=5`
- `hold_rank_multiplier=1.3`
- `entry_rank_multiplier=1.0`
- 10-day rebalance, default costs

Why topN was worse than the raw bucket read:

1. The top bucket was already bad. Top quintile return was `-0.001154`, so selecting from the extreme high-score tail starts from negative expected return.
2. The extreme top30 tail was worse than the top quintile. Static top30 mean `fwd_5d` was `-0.004445`; top30 minus universe was `-0.004037`; top30 minus bottom30 was `-0.007120`.
3. The score scale was compressed. Static daily score standard deviation averaged only about `0.000353`, so top30 selection among roughly 4,800 names was based on tiny score differences from a shallow `best_iteration=1` model.
4. The portfolio is long-only and concentrated. A negative ranking tail compounds through 30-name exposure instead of being diluted across 965 names in a quintile bucket.
5. Turnover and costs add drag but are not the primary cause. Static default-cost turnover was `11.475295` times initial cash with 477 trades, but the raw top30 forward-return diagnostic was already negative before strategy costs.

Fair-window strategy evidence:

| Strategy | Benchmark | Return | Benchmark | Excess | Max DD | Sharpe | Turnover | Trades |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| static top30 | 000300 | -0.147360 | 0.006539 | -0.153899 | -0.163621 | -2.194948 | 11.475295 | 477 |
| static top30 | 000905 | -0.147360 | 0.053935 | -0.201295 | -0.163621 | -2.194948 | 11.475295 | 477 |
| static top30 | 000852 | -0.147360 | 0.057370 | -0.204731 | -0.163621 | -2.194948 | 11.475295 | 477 |
| WF v45 top30, reb10 | 000300 | 0.011782 | 0.006539 | 0.005244 | -0.140875 | 0.246119 | 10.161311 | 420 |
| WF v45 top30, reb20 | 000300 | 0.015474 | 0.006539 | 0.008936 | -0.139009 | 0.289357 | 6.314782 | 253 |

The static topN strategy is worse than `multi_factor_rank` and `inverse_momentum` in the same fair window according to `profit-first-execution-plan.md`: static `ml_score_rank` returned `-14.7360%`, while `multi_factor_rank` returned `-6.9764%` and `inverse_momentum` returned `-10.0637%`.

## What Was Not the Primary Failure

- Not a score coverage collapse: both static and walk-forward diagnostics cover 497,002 rows, 103 dates, 4,965 symbols, with median 4,837 names per date.
- Not primarily a missing-score forward-fill bug: `ml_score_rank` returns zero weights when today's scores are absent and uses exact same-date lookup.
- Not primarily cost drag: costs worsen the result, but static buckets and static top30 forward returns are already negative before portfolio costs.
- Not proof that LightGBM is unusable: walk-forward v45 materially improves RankIC and turns default-cost return positive versus `000300`.

## Recommended Next Steps

1. Stop tuning static `ml_score_rank` buffers against this score file. The raw ranking is inverted, so `top_n`, hold buffer, and cost grids are optimizing noise.
2. Make walk-forward the minimum ML protocol. Keep 15-day label embargo, monthly test windows, and report per-window RankIC, bucket spread, and strategy return.
3. Add a pre-portfolio acceptance gate: require 2026 RankIC mean above `0.01`, RankIC IR above `0.20`, positive top-minus-bottom bucket spread, and top30 forward return above universe average before running full strategy grids.
4. Prune or constrain feature families. The static run should be rejected if top 3 gain share exceeds 60% and the dominant family lacks current-year univariate support. Re-test without the dominant drawdown/recovery cluster and compare to a liquidity/amount/trend-structure feature subset.
5. Produce feature-family stability tables for 2025 vs 2026. Required families: price-state/drawdown, momentum/reversal, volatility/risk, liquidity/amount, price-volume, trend-structure.
6. Diagnose topN conversion separately from model quality. For every prediction file, compute top10/top30/top50 same-day rank portfolios on labels before costs, then compare with backtest net PnL to isolate ranking decay from execution/cost drag.
7. Add liquidity, size, and benchmark-membership bucket diagnostics. The static model may be selecting a bad tail of price-state names after liquidity filters; verify by amount band and index/size proxy before adding filters.
8. Keep strategy code, reject the static model artifact. `ml_score_rank` is the correct adapter; the failed artifact is `lgbm-fwd5-static-2026-predictions.csv`, not the portfolio bridge.
9. Do not promote the current walk-forward candidate yet. It beats `000300` but still lags `000905`/`000852` and has high drawdown. Next work should reduce drawdown and validate monthly paired excess returns under retail/stress costs.

## Bottom Line

The static LightGBM failed because it learned a narrow 2024 price-state/drawdown pattern that decayed by 2026. The 2026 score is nearly uninformative by RankIC and actively harmful in the high-score tail. Portfolio topN then concentrated that bad tail into a long-only, turnover-heavy book, making the loss much larger than the bucket table alone suggests. Walk-forward retraining fixes enough of the drift to become a research candidate, but it still needs drawdown, benchmark, PIT, and cost validation before promotion.
