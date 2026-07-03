# 2026 Static ML Diagnosis Report Plan

Date: 2026-07-03

Role: QA result-analysis worker. This document is report-format and acceptance
criteria only. It does not change training, strategy, or backtest code.

## Scope And Source Artifacts

User-facing logical scope:

- `artifacts/ml/lgbm-fwd5-static-2025/2026 metrics`
- `research_runs/2026/fair_20260105_20260610/strategies`

Current repository paths observed:

- `backend/artifacts/ml/lgbm-fwd5-static-2025-metrics.json`
- `backend/artifacts/ml/lgbm-fwd5-static-2026-metrics.json`
- `backend/artifacts/ml/lgbm-fwd5-static-2026-diagnostics.json`
- `backend/artifacts/ml/lgbm-fwd5-static-2026-diagnostics.md`
- `backend/artifacts/ml/lgbm-fwd5-static-2026-predictions.csv`
- `backend/research_runs/2026/fair_20260105_20260610/strategies/*.json`

All strategy JSON files in the observed run are marked:

- `point_in_time=false`
- `degraded=true`
- research pool selected from current active-stock coverage
- historical listing, delisting, ST states, and qfq revision risk not fully
  applied

Therefore the diagnosis can judge research signal quality and relative
strategy behavior, but must not label any result paper/live ready.

## Current Baseline Facts

Static LightGBM setup:

- Label: `fwd_5d`
- Features: 40 technical/amount/liquidity factors
- Train: 2024-01-02 to 2024-09-30, 587,727 rows
- Valid: 2024-10-01 to 2024-12-31, 291,872 rows
- 2025 test: 2025-01-01 to 2025-12-31, 1,169,536 rows
- 2026 test: 2026-01-01 to 2026-06-18 in metrics, prediction coverage
  2026-01-05 to 2026-06-10
- `best_iteration=1`, which is itself a warning that the model may be too weak
  or early-stopped immediately

Static model quality:

| Window | RankIC mean | RankIC IR | Top return | Bottom return | Long-short |
| --- | ---: | ---: | ---: | ---: | ---: |
| Valid 2024Q4 | 0.055625 | 0.520983 | 0.007202 | 0.007150 | 0.000052 |
| Test 2025 | 0.037680 | 0.552200 | 0.010000 | 0.008042 | 0.001958 |
| Test 2026 | 0.001870 | 0.028125 | -0.001097 | 0.001024 | -0.002121 |

2026 score bucket diagnostic:

| Bucket | Avg names/date | Mean score | Mean fwd_5d return |
| ---: | ---: | ---: | ---: |
| 1 lowest | 964.69 | 0.004422 | 0.000900 |
| 2 | 965.04 | 0.004698 | 0.001772 |
| 3 | 965.01 | 0.004803 | -0.001095 |
| 4 | 965.04 | 0.004864 | -0.002462 |
| 5 highest | 965.49 | 0.005142 | -0.001154 |

Observed 2026 strategy baseline, default cost, same window
`2026-01-05` to `2026-06-10`:

| Strategy | Total return | Max drawdown | Sharpe | Turnover / initial cash | Trades |
| --- | ---: | ---: | ---: | ---: | ---: |
| `ml_score_rank` | -0.147360 | -0.163621 | -2.194948 | 11.475295 | 477 |
| `multi_factor_rank` | -0.069764 | -0.114147 | -1.094180 | 11.680169 | 311 |
| `inverse_momentum` | -0.100637 | -0.128037 | -2.396430 | 4.173801 | 179 |

Benchmark returns in default-cost ML JSON:

| Benchmark | Benchmark return | ML excess |
| --- | ---: | ---: |
| `000300` | 0.006539 | -0.153899 |
| `000905` | 0.053935 | -0.201295 |
| `000852` | 0.057370 | -0.204731 |

QA read: the static model is not just low-return in portfolio construction.
The raw score ordering is broken in 2026: RankIC is effectively zero, the
highest bucket loses money, and long-short is negative.

## Required Diagnosis Report Format

The final diagnosis report should be a single Markdown file plus optional CSV
tables. It should be reproducible from the artifacts listed above and include
the following sections in order.

### 1. Executive Verdict

Required fields:

| Field | Required content |
| --- | --- |
| `model_run` | artifact prefix and prediction CSV path |
| `periods` | train, valid, 2025 test, 2026 test dates |
| `verdict` | `reject_static`, `needs_walk_forward`, or `promotion_candidate` |
| `primary_reason` | one sentence tied to RankIC, bucket, and strategy evidence |
| `data_caveat` | must state non-PIT/degraded status if using current backtests |

Expected verdict for current artifacts: `reject_static` and
`needs_walk_forward`.

### 2. Data And Coverage Panel

Report table:

| Metric | 2025 test | 2026 test | Acceptance note |
| --- | ---: | ---: | --- |
| rows | from metrics | 497,002 | must match prediction diagnostics |
| date count | 243 | 103 | 2026 date count must cover backtest decisions |
| symbol count | optional if computed | 4,965 | no sudden unexplained collapse |
| min symbols/date | optional if computed | 4,772 | no severe sparse-score dates |
| median symbols/date | optional if computed | 4,837 | stable daily cross-section |
| max symbols/date | optional if computed | 4,857 | stable daily cross-section |
| missing score dates | computed vs trading calendar | required | any missing date must be listed |

Acceptance standard:

- Daily score coverage must be at least 95% of eligible research-pool names for
  every decision date, or the report must isolate the missing-date/name impact.
- The backtest window must not extend past the last score date unless the
  report explicitly explains unexecuted/missing signals.
- Any report that silently forward-fills scores fails QA.

### 3. RankIC Panel

Report table:

| Window | RankIC mean | RankIC std | RankIC IR | Positive IC day % | t-stat | N dates |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| valid | required | required | required | required | required | required |
| 2025 test | required | required | required | required | required | required |
| 2026 test | required | required | required | required | required | required |

Required charts:

- Daily RankIC line chart with 20-day rolling mean.
- Monthly RankIC bar chart for 2025 and 2026.
- RankIC distribution histogram for 2025 vs 2026.

Acceptance standard:

- Static model fails if 2026 RankIC mean is below 0.01 or RankIC IR is below
  0.20.
- Static model also fails if 2026 RankIC sign is unstable and positive IC day
  rate is not meaningfully above 50%.
- Current artifact fails: 2026 RankIC mean `0.001870`, IR `0.028125`.

### 4. Feature Importance Panel

Report tables:

| Feature | Split | Gain | Gain share | Cumulative gain share | Feature family |
| --- | ---: | ---: | ---: | ---: | --- |
| required | required | required | computed | computed | momentum/reversal/risk/liquidity/price-volume |

| Feature family | Gain share | Split share | 2025 IC | 2026 IC | Direction stable? |
| --- | ---: | ---: | ---: | ---: | --- |
| required | computed | computed | computed if available | computed if available | yes/no |

Required diagnostics:

- Top 10 gain features and cumulative gain concentration.
- Count and percentage of zero-importance features.
- Feature-family concentration, especially drawdown/reversal/momentum versus
  liquidity/amount features.
- Per-feature univariate 2025 vs 2026 RankIC if feasible from the dataset.
- For the current model, explicitly call out `drawdown_recovery_20d` as the
  dominant feature and count the zero-split features.

Acceptance standard:

- A promotion candidate should not depend on one dominant feature family unless
  that family has stable 2025 and 2026 univariate evidence.
- If top 3 features explain more than 60% of gain, require a concentration
  warning.
- If more than half the 40 input features have zero split/gain, require either
  feature pruning or model/training review before promotion.
- If 2026 failure lines up with reversal/drawdown feature inversion, mark
  `regime_dependency=true`.

### 5. Score Bucket Panel

Use per-date quantile buckets, not global quantiles. Each date should be split
into equal-count buckets so the report measures cross-sectional ranking rather
than time-varying market level.

Report table:

| Bucket | Rows | Dates | Avg names/date | Mean score | Mean fwd return | Median fwd return | Hit rate | Avg rank |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 lowest | required | required | required | required | required | required | required | required |
| ... | required | required | required | required | required | required | required | required |
| 5 highest | required | required | required | required | required | required | required | required |

Required extensions:

- Bucket long-short: highest minus lowest.
- Monotonicity score: count adjacent bucket improvements in mean return.
- Top bucket vs middle bucket and top bucket vs universe average.
- Same table by month.
- Same table by liquidity band if feasible.
- Same table by benchmark membership or size proxy if feasible.

How to read buckets:

- A good long-only ranking model should show higher realized return in higher
  score buckets, with the highest bucket above universe average.
- A flat bucket table means the score scale may separate names numerically but
  not economically.
- An inverted bucket table means high scores are actively selecting worse
  future returns; the model should not be rescued by top-N tuning.
- Current artifact is inverted enough to reject the static model: bucket 5
  return is `-0.001154`, bucket 1 is `0.000900`, and bucket 2 is best.

Acceptance standard:

- Highest bucket mean return must exceed lowest bucket mean return by at least
  20 bps over the `fwd_5d` horizon, or have statistically clear monthly
  consistency.
- Highest bucket must beat universe average and middle bucket.
- Bucket monotonicity should have at least 3 of 4 adjacent improvements for
  five buckets, or the report must explain a stable non-linear selection rule.
- Current artifact fails: long-short bucket return is `-0.002055`.

### 6. Strategy Turnover And Coverage Panel

Report table by strategy, benchmark, and cost scenario:

| Strategy | Benchmark | Cost case | Total return | Excess | Max DD | Sharpe | Turnover | Trades | Cost drag | Degraded |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `ml_score_rank` | required | default/retail/stress | required | required | required | required | required | required | computed | required |
| `multi_factor_rank` | required | default/retail/stress | required | required | required | required | required | required | computed | required |
| `inverse_momentum` | required | default/retail/stress | required | required | required | required | required | required | computed | required |

Required ML-only coverage fields:

| Field | Definition |
| --- | --- |
| score coverage | scored names / research-pool eligible names per date |
| eligibility coverage | names after price/amount filters / scored names |
| selected count | actual holdings or target names per rebalance |
| missing-score held names | held names absent from current-day score table |
| turnover per rebalance | bought + sold notional / equity |
| cost drag | gross return minus net return, or total fees / initial cash |

Acceptance standard:

- ML cannot pass if it loses to both non-ML baselines under the same window and
  costs.
- Turnover is acceptable only if net returns survive retail/stress costs.
- If ML turnover is materially higher than the low-turnover baseline without
  better return or lower drawdown, classify the score as economically unusable.
- Current artifact fails strategy acceptance: default-cost `ml_score_rank`
  return `-14.736%` is worse than `multi_factor_rank` `-6.976%` and
  `inverse_momentum` `-10.064%`.

### 7. Failure Attribution

The report must distinguish these failure modes instead of saying only
"performance is bad":

| Failure mode | Evidence to check | Current read |
| --- | --- | --- |
| score decay | 2025 RankIC positive but 2026 near zero | present |
| bucket inversion | high score bucket underperforms low score bucket | present |
| feature concentration | gain dominated by a small feature set | likely; verify gain share |
| regime mismatch | 2024/2025 learned reversal/drawdown behavior no longer works in 2026 | plausible |
| cost/turnover drag | gross edge exists but costs erase it | not primary; raw bucket edge already negative |
| universe/PIT bias | current research pool not PIT | present caveat, not enough to explain inversion alone |
| execution mismatch | score date vs trade execution shifted wrongly | must be checked, but current strategy uses same-date signal for next-day execution per prior audit |
| liquidity filter distortion | high score names cluster near illiquid/risky names | must be analyzed by liquidity band |

## Why The Static Model Failed In 2026

Current evidence supports this diagnosis:

1. The model has little 2026 cross-sectional information. RankIC mean collapsed
   from `0.037680` in 2025 to `0.001870` in 2026.
2. The score is not merely weak; it is directionally harmful at the top. The
   highest 2026 bucket loses while lower buckets are flat to positive.
3. `best_iteration=1` and sparse feature usage suggest the trained model found
   only a very shallow pattern, not a robust multi-factor ranking surface.
4. The top importance list is dominated by drawdown/recovery and related price
   path factors. If 2026 changed from rebound-friendly behavior to persistent
   weakness or crowded reversal unwinds, high scores can select losers.
5. Strategy construction did not rescue the signal. Top-30 equal-weight
   portfolio lost more than both rule baselines and all benchmark comparisons.
6. Current results are non-PIT/degraded, so exact magnitudes need caution, but
   the raw prediction diagnostics are already enough to reject the static model
   as an alpha candidate.

## How To Interpret High-Score Losses

High-score group losses should be investigated in this order:

1. Direction error: high scores correspond to lower future returns in 2026.
   Confirm through bucket long-short and daily RankIC.
2. Regime dependency: top features may encode "drawdown recovery" or
   "mean-reversion after weakness"; those names may keep falling in 2026.
3. Tail contamination: top bucket may contain names with extreme prior
   drawdowns, high volatility, event risk, ST-like behavior, or liquidity
   shocks not fully filtered by the current research path.
4. Liquidity and tradability: if high scores over-select low-liquidity names,
   realized execution can be worse than label returns, especially with
   slippage/stamp tax.
5. Size/industry clustering: high scores may cluster in underperforming
   small-cap, micro-cap, or industry regimes; report bucket return by size and
   industry proxy where available.
6. Label/holding mismatch: `fwd_5d` may not align with a 10-day rebalance and
   hold-buffer strategy. This can worsen realized returns, but it does not
   explain negative raw bucket long-short by itself.
7. Data leakage or stale model selection: 2025 can no longer be treated as
   clean OOS if it informed model choices. 2026 is the real holdout and failed.

## Walk-Forward Success Criteria

Static training is rejected. The next serious ML test should be walk-forward,
with frozen protocol before looking at 2026 outcomes.

Minimum walk-forward design:

| Item | Required standard |
| --- | --- |
| training window | rolling or expanding, ending before each prediction segment |
| validation window | immediately before prediction segment, never overlapping test |
| prediction segment | monthly or quarterly 2026 slices |
| retrain cadence | monthly preferred, quarterly acceptable for first pass |
| model/run ID | immutable ID in prediction metadata or sidecar |
| feature list | frozen before each segment; no tuning on future segment |
| output | one stitched prediction CSV plus per-segment metrics |

Walk-forward report tables:

| Segment | Train end | Valid window | Test dates | RankIC | ICIR | Top bucket return | Bottom bucket return | Long-short |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| required | required | required | required | required | required | required | required | required |

| Strategy | Segment/group | Total return | Excess vs 000300 | Excess vs 000905 | Excess vs 000852 | Max DD | Sharpe | Turnover |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| required | required | required | required | required | required | required | required | required |

Walk-forward promotion gate:

- Stitched 2026 RankIC mean >= 0.015.
- Stitched 2026 RankIC IR >= 0.25.
- Positive IC day rate >= 53%.
- Highest bucket beats lowest bucket by >= 0.0015 over `fwd_5d`.
- Highest bucket beats universe average in at least two thirds of monthly
  segments.
- Default-cost ML strategy beats both `multi_factor_rank` and
  `inverse_momentum` on total return under the same 2026 window.
- Default-cost ML has positive excess return versus at least two of `000300`,
  `000905`, and `000852`.
- Retail-cost ML still beats both baselines, or any underperformance is less
  than 1 percentage point with materially lower drawdown.
- Stress-cost result does not flip from strong profit to deep loss solely due
  to turnover.
- No single segment contributes more than half of total strategy profit.
- Same conclusion holds for at least one adjacent `top_n` or hold-buffer
  setting.

Walk-forward rejection gate:

- RankIC remains below 0.01 or bucket long-short remains negative.
- Edge appears only after liquidity filters are loosened below realistic
  tradability thresholds.
- Strategy gains come from one tuned segment or one benchmark only.
- Retail/stress costs erase all alpha.
- Missing-score or non-PIT caveats dominate the result.

## QA Acceptance Checklist

A submitted diagnosis report is accepted only if it:

- cites exact artifact paths and generation dates where available
- separates raw prediction diagnostics from strategy backtest diagnostics
- includes RankIC, feature importance, score bucket, turnover, and coverage
  sections
- states whether buckets are per-date quantiles
- compares 2025 and 2026 instead of reading 2026 in isolation
- explains high-score losses with evidence, not only speculation
- labels all current strategy runs as non-PIT/degraded
- provides explicit pass/fail verdicts for static and walk-forward models
- rejects static ML if the current metrics are unchanged

## Key Recommendations

1. Do not tune `ml_score_rank` portfolio buffers further against current static
   scores. The raw 2026 ranking signal has failed.
2. Treat the current static LightGBM output as a diagnostic baseline, not a
   candidate alpha.
3. Use feature-family and bucket-by-month analysis to identify whether
   drawdown/reversal factors inverted in 2026.
4. Move ML effort to a frozen walk-forward protocol before another promotion
   discussion.
5. Keep all current 2026 strategy comparisons research-only until PIT universe,
   historical ST/listing state, suspension/gap classification, and tradability
   filters are consumed by the backtest path.
