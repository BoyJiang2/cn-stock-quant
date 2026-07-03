# 2026 ML-score Strategy Backtest Audit

Date: 2026-07-03

Role: QA/backtest audit worker. This note is read-only with respect to strategy
code. It documents the current `ml_score_rank` implementation, the prediction
CSV shape, the required 2026 backtest matrix, and the acceptance gates for
judging whether ML score ranking is better than the existing
`multi_factor_rank` and `inverse_momentum` baselines.

## Current Implementation Read

Files reviewed:

- `backend/run_strategy_backtest.py`
- `backend/app/strategy/examples.py`
- `backend/artifacts/ml/lgbm-fwd5-static-2026-predictions.csv`
- `backend/run_2026_research_pipeline.py`
- `backend/research_runs/2026/summary_strategies_full.md`

`run_strategy_backtest.py` is a reproducible CLI runner. It loads a research
pool or manual symbol list, loads local daily bars, optionally loads one index
benchmark, runs one strategy or a JSON parameter grid, and writes JSON with:

- `metadata`: benchmark, costs, rebalance interval, selected symbol count, bar
  rows, timing, and degraded/non-PIT caveats.
- `runs`: sorted by `excess_return`, then `sharpe`, then `max_drawdown`.
- `metrics`: `total_return`, `annual_return`, `max_drawdown`, `sharpe`,
  `benchmark_return`, `excess_return`.
- `trade_stats`: trade count, buy/sell amount, turnover on initial cash,
  commission, stamp tax.

Important runner caveat: `metadata.point_in_time` is currently `false` and
`degraded` is `true`. Every result from this path must be labelled research
only/non-PIT until historical listing, delisting, ST state, suspensions, and
true PIT universe selection are consumed by the backtest path.

`ml_score_rank` currently:

- Requires `scores_path`.
- Reads an offline CSV through `_load_ml_scores(path, score_column)`.
- Requires `trade_date`, `symbol`, and the configured score column.
- Converts `trade_date` to Python dates.
- Normalizes numeric symbols such as `1` to `000001`.
- Sorts by `trade_date` and descending score.
- Drops duplicate `(trade_date, symbol)` rows, keeping the highest score after
  sorting.
- On each decision date, uses only rows where `score.trade_date ==
  context.current_date`.
- Filters by latest price, 20-day average amount, and `min_score`.
- Ranks eligible symbols by score descending.
- Retains existing positions inside `top_n * hold_rank_multiplier`.
- Adds new names only inside `top_n * entry_rank_multiplier`.
- Equal-weights selected names using
  `min(max_position_weight, max_total_weight / selected_count)`.

Current default ML parameters:

| Parameter | Default |
| --- | ---: |
| `top_n` | 30 |
| `max_position_weight` | 0.05 |
| `max_total_weight` | 0.8 |
| `min_score` | -1.0 |
| `min_avg_amount_20d` | 50,000,000 |
| `min_price` | 5.0 |
| `hold_rank_multiplier` | 1.3 |
| `entry_rank_multiplier` | 1.0 |

Prediction CSV format observed:

| Column | Meaning |
| --- | --- |
| `trade_date` | score date used by the strategy as same-date signal key |
| `symbol` | numeric or string stock code; current loader zero-pads numeric codes |
| `score` | model score consumed by `ml_score_rank` |
| `split` | offline split label; not consumed by the strategy |
| `fwd_5d` | offline forward return label; not consumed by the strategy |

Prediction CSV coverage observed:

| Item | Value |
| --- | ---: |
| rows | 788,874 |
| unique symbols | 4,968 |
| unique score dates | 164 |
| full date range | 2024-10-08 to 2026-06-10 |
| `valid` split | 291,872 rows, 2024-10-08 to 2024-12-31, 61 dates |
| `test` split | 497,002 rows, 2026-01-05 to 2026-06-10, 103 dates |
| score range | 0.0004559528 to 0.0131914733 |
| `fwd_5d` range | -0.5902439024 to 2.81 |

Audit implication: use `2026-01-05` to `2026-06-10` as the clean primary ML
backtest window for this CSV. Running through `2026-06-18` is allowed only as a
diagnostic, because after `2026-06-10` the strategy has no same-date scores and
will return zero weights.

## Required Benchmarks And Costs

Use the same benchmark set as `backend/run_2026_research_pipeline.py`:

| Benchmark | Role |
| --- | --- |
| `000300` | large-cap CSI 300 comparison |
| `000905` | CSI 500 small/mid-cap comparison |
| `000852` | CSI 1000 small-cap comparison |

Use the same three cost cases, with the runner default stamp tax unchanged at
`0.001` unless a separate zero-tax diagnostic is explicitly requested:

| Cost case | Commission | Slippage | Stamp tax |
| --- | ---: | ---: | ---: |
| `default` | 0.0003 | 0.0005 | 0.001 |
| `retail` | 0.0005 | 0.0010 | 0.001 |
| `stress` | 0.0005 | 0.0020 | 0.001 |

Do not accept any ML result unless benchmark data is actually loaded. In each
output JSON, verify `metadata.benchmark_symbol` equals the requested benchmark;
if it is `null`, the run is invalid for benchmark comparison.

## Backtest Matrix

### Phase 0 Smoke

Purpose: prove the CSV path, symbol normalization, benchmark load, and cost
settings work before spending full-market runtime.

Run:

- Window: `2026-01-05` to `2026-06-10`.
- Pool: `--pool-max-symbols 300`.
- Benchmarks: `000300`, `000905`, `000852`.
- Costs: `default`.
- Strategy: one default ML run.

Smoke pass:

- Runner exits successfully for all three benchmarks.
- `metadata.selected_symbol_count > 0`.
- `metadata.benchmark_symbol` is not `null`.
- `runs[0].trade_stats.trade_count > 0`.
- `runs[0].trade_stats.turnover_on_initial_cash` is finite.
- No output has all-zero equity movement unless the pool genuinely has no
  overlap with the score file.

### Phase 1 Primary Acceptance Matrix

Purpose: decide whether the current static ML score strategy is better than the
old strategy baselines under identical conditions.

Run the following strategies over exactly the same window, universe, benchmark,
rebalance interval, and costs:

| Strategy | Required parameters | Rebalance |
| --- | --- | ---: |
| `ml_score_rank` | `scores_path=backend/artifacts/ml/lgbm-fwd5-static-2026-predictions.csv`, `top_n=30`, `min_avg_amount_20d=50000000`, `hold_rank_multiplier=1.3`, `entry_rank_multiplier=1.0` | 10 |
| `multi_factor_rank` | `top_n=20`, `momentum_window=20`, `reversal_window=10`, `hold_rank_multiplier=1.5`, `entry_rank_multiplier=1.2` | 10 |
| `inverse_momentum` | `lookback_window=60`, `top_n=30`, `hold_rank_multiplier=1.2` | 10 |

Run all combinations:

- 3 strategies.
- 3 benchmarks: `000300`, `000905`, `000852`.
- 3 costs: `default`, `retail`, `stress`.

This is 27 required primary runs. Existing
`backend/research_runs/2026/summary_strategies_full.md` is useful context but
is not a final comparator for this ML CSV because it used `2026-06-18`. The old
baselines must be rerun to `2026-06-10` for a fair primary matrix.

### Phase 2 Sensitivity Matrix

Purpose: test whether the ML strategy is robust to reasonable portfolio
construction choices. This is not the promotion matrix. It should only be run
after Phase 1 does not clearly fail.

Recommended ML sensitivity grid:

| Axis | Values | Reason |
| --- | --- | --- |
| `top_n` | 20, 30, 50, 80 | concentration vs score dilution |
| `hold_rank_multiplier` | 1.0, 1.3, 1.6, 2.0 | turnover control and stale-position risk |
| `entry_rank_multiplier` | 1.0, 1.2 | stricter vs slightly wider entry set |
| `min_avg_amount_20d` | 30,000,000; 50,000,000; 100,000,000 | capacity and liquidity sensitivity |
| `rebalance_interval` | 5, 10, 20 | fwd-5d label alignment vs cost drag |

Keep `max_total_weight=0.8`, `max_position_weight=0.05`, `min_price=5`, and
`min_score=-1` fixed for the first sensitivity pass. Do not tune `min_score`
until score distribution and hit-rate by score bucket are reviewed, because
absolute LightGBM score scale may not be stable across retrains.

To keep runtime bounded, use staged grids:

1. `top_n x hold_rank_multiplier` at default cost and `000300`.
2. Take the top 3 robust candidates, then run all three benchmarks and all
   three cost cases.
3. Only then test `min_avg_amount_20d` and `rebalance_interval` around the best
   stable candidate.

`min_avg_amount_20d=0` is diagnostic only. It can reveal whether the model edge
comes from illiquid names, but it should not be used for acceptance because the
current runner lacks full suspension, limit, and PIT tradability filters.

## Acceptance Gates

### Data And Fairness Gates

Before reading performance:

- Same date window for all compared strategies: primary window
  `2026-01-05` to `2026-06-10`.
- Same `symbol_source`, `pool_max_symbols`, costs, initial cash, and rebalance
  interval for ML and baseline strategies.
- Same benchmark requested and successfully loaded.
- All outputs remain labelled non-PIT/degraded.
- Score CSV must not be regenerated or edited between compared runs.
- If using grid output sorting, do not compare only `runs[0]` from a tuned grid
  against single-run baselines unless the grid was frozen before the run.

### Minimum Promotion Gate

The current static ML score strategy is a promotion candidate only if the
primary default-cost run satisfies all of the following:

- Beats both old strategy baselines on `total_return` under the same window.
- Beats both old strategy baselines on `sharpe`, or has a clearly lower
  drawdown that compensates for a small Sharpe gap.
- Has `max_drawdown` no worse than the worse old baseline by more than 2
  percentage points.
- Has positive `excess_return` versus at least two of `000300`, `000905`, and
  `000852`.
- Does not have turnover more than 1.5x the lower-turnover old baseline unless
  the extra return survives retail and stress costs.
- Has non-trivial trading activity and average holdings consistent with the
  intended `top_n`; a near-cash strategy must not pass merely by avoiding losses.

### Robustness Gate

After the minimum gate, retail and stress costs must confirm the edge:

- Under retail costs, ML still beats both old baselines on `total_return`.
- Under stress costs, ML should either still beat both old baselines or lose by
  less than 1 percentage point while retaining better drawdown.
- The best ML parameter neighbor should not be isolated. At least one adjacent
  setting in `top_n` or `hold_rank_multiplier` should have the same qualitative
  conclusion.

### Rejection Gate

Reject the static ML score strategy as an alpha candidate if any of the
following occurs:

- Default-cost ML fails to beat both `multi_factor_rank` and
  `inverse_momentum` on the same window.
- ML beats `000300` but fails both `000905` and `000852`; that is not enough
  for a 2026 small/mid-cap universe.
- Performance only appears after lowering liquidity below
  `min_avg_amount_20d=30,000,000`.
- Performance disappears under retail costs.
- The best result depends on one narrow `top_n` or hold-buffer setting and
  adjacent settings fail.
- The run has missing benchmark data, no trades, or score-date mismatch.

If rejected, stop tuning `ml_score_rank` buffers and move the ML effort to a
walk-forward retraining protocol. The current offline evidence already suggests
the static 2026 OOS score quality is weak, so strategy-level failure should not
be solved by excessive portfolio parameter search.

## Key Command Suggestions

Run commands from `backend/`.

Smoke ML run:

```powershell
python run_strategy_backtest.py `
  --strategy ml_score_rank `
  --start-date 2026-01-05 `
  --end-date 2026-06-10 `
  --pool-max-symbols 300 `
  --benchmark-symbol 000300 `
  --rebalance-interval 10 `
  --commission-rate 0.0003 `
  --slippage-rate 0.0005 `
  --param scores_path=artifacts/ml/lgbm-fwd5-static-2026-predictions.csv `
  --param top_n=30 `
  --param min_avg_amount_20d=50000000 `
  --param hold_rank_multiplier=1.3 `
  --param entry_rank_multiplier=1.0 `
  --output research_runs/2026/ml_smoke/ml_score_rank_000300_default.json
```

Primary ML loop:

```powershell
$benchmarks = @("000300", "000905", "000852")
$costs = @{
  default = @{ commission = "0.0003"; slippage = "0.0005" }
  retail  = @{ commission = "0.0005"; slippage = "0.0010" }
  stress  = @{ commission = "0.0005"; slippage = "0.0020" }
}
foreach ($benchmark in $benchmarks) {
  foreach ($costName in $costs.Keys) {
    $cost = $costs[$costName]
    python run_strategy_backtest.py `
      --strategy ml_score_rank `
      --start-date 2026-01-05 `
      --end-date 2026-06-10 `
      --pool-max-symbols 6000 `
      --benchmark-symbol $benchmark `
      --rebalance-interval 10 `
      --commission-rate $cost.commission `
      --slippage-rate $cost.slippage `
      --param scores_path=artifacts/ml/lgbm-fwd5-static-2026-predictions.csv `
      --param top_n=30 `
      --param min_avg_amount_20d=50000000 `
      --param hold_rank_multiplier=1.3 `
      --param entry_rank_multiplier=1.0 `
      --output "research_runs/2026/ml_primary/ml_score_rank_${benchmark}_${costName}.json"
  }
}
```

Primary baseline reruns must use the same date window. Example baseline command:

```powershell
python run_strategy_backtest.py `
  --strategy inverse_momentum `
  --start-date 2026-01-05 `
  --end-date 2026-06-10 `
  --pool-max-symbols 6000 `
  --benchmark-symbol 000300 `
  --rebalance-interval 10 `
  --commission-rate 0.0003 `
  --slippage-rate 0.0005 `
  --param lookback_window=60 `
  --param top_n=30 `
  --param hold_rank_multiplier=1.2 `
  --output research_runs/2026/ml_primary_baselines/inverse_momentum_000300_default.json
```

Use the same pattern for `multi_factor_rank`:

```powershell
python run_strategy_backtest.py `
  --strategy multi_factor_rank `
  --start-date 2026-01-05 `
  --end-date 2026-06-10 `
  --pool-max-symbols 6000 `
  --benchmark-symbol 000300 `
  --rebalance-interval 10 `
  --commission-rate 0.0003 `
  --slippage-rate 0.0005 `
  --param top_n=20 `
  --param momentum_window=20 `
  --param reversal_window=10 `
  --param hold_rank_multiplier=1.5 `
  --param entry_rank_multiplier=1.2 `
  --output research_runs/2026/ml_primary_baselines/multi_factor_rank_000300_default.json
```

Quick result extraction after runs:

```powershell
Get-ChildItem research_runs/2026/ml_primary -Filter *.json |
  ForEach-Object {
    $j = Get-Content $_.FullName -Raw | ConvertFrom-Json
    $r = $j.runs[0]
    [pscustomobject]@{
      file = $_.Name
      benchmark = $j.metadata.benchmark_symbol
      total_return = $r.metrics.total_return
      benchmark_return = $r.metrics.benchmark_return
      excess_return = $r.metrics.excess_return
      max_drawdown = $r.metrics.max_drawdown
      sharpe = $r.metrics.sharpe
      turnover = $r.trade_stats.turnover_on_initial_cash
      trades = $r.trade_stats.trade_count
    }
  } | Sort-Object file | Format-Table -AutoSize
```

## Final QA Position

The required evidence is not "ML beats one index". It is:

1. ML beats `multi_factor_rank` and `inverse_momentum` under the same 2026
   window, universe, benchmark load, rebalance interval, and costs.
2. ML has positive default-cost excess against at least two of `000300`,
   `000905`, and `000852`.
3. The result survives realistic retail/stress costs and nearby `top_n` /
   hold-buffer choices.
4. The result is still reported as non-PIT/degraded until the data path is
   fixed.

Until those gates pass, `ml_score_rank` should be treated as a working adapter
for offline scores, not as a validated 2026 alpha strategy.
