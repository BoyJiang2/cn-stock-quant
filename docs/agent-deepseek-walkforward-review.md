# DeepSeek Walk-Forward ML Leakage Review

Date: 2026-07-03

Role: DeepSeek-side ML leakage reviewer. Scope is documentation only. Reviewed:

- `backend/build_ml_dataset.py`
- `backend/train_lightgbm_ranker.py`
- `backend/app/strategy/examples.py` `ml_score_rank`

## Executive Summary

The current static ML path is usable as a research prototype, but it is not yet a leak-proof walk-forward protocol. The highest-priority fixes are procedural and CLI-contract level:

1. Treat prediction `trade_date=t` as the decision date consumed by `ml_score_rank` on `context.current_date=t`; execution happens on the next engine trading date. Do not shift prediction dates.
2. Add an embargo between validation labels and the test prediction window. A `fwd_5d` validation label near the day before test start needs future bars from the test period, so using it for early stopping leaks test-period returns.
3. Require immutable `model_run_id` / `fold_id` on every prediction row and in sidecar manifests. One active model may score a given `(trade_date, symbol)` for strategy acceptance.
4. Freeze model design, feature list, portfolio params, and selection criteria before any 2026 metrics are used. If 2026 is inspected for tuning, it must be demoted to post-hoc diagnostics, not clean OOS.
5. Persist every walk-forward fold append-only with split windows, params, dataset metadata, artifact hashes, metrics, and prediction coverage. Aggregated strategy input should contain only fold test/live predictions.
6. Judge walk-forward improvement against the static model under identical dataset version, prediction date range, universe, costs, benchmarks, rebalance interval, and strategy params; use paired date/month performance, not cherry-picked best grid rows.

## Current Code Read

### Dataset Builder

`build_ml_dataset.py`:

- Computes factor features from local daily bars with warmup before `start_date`.
- Applies direction adjustment and per-date robust preprocessing.
- Builds forward-return labels such as `fwd_5d` and `fwd_10d`.
- Emits metadata with `label_timing`: `T+1 entry forward return from close(t+1) to close(t+1+h)`.
- Explicitly marks `point_in_time=false` and `degraded=true`.
- Uses `covered_research_symbols(...)`, which is current-coverage based and not a historical PIT universe.

Leakage implication: feature/date slicing is mostly compatible with research walk-forward, but the output must remain labelled non-PIT until historical universe, listing/ST/suspension, and corporate-action revision issues are solved.

### Static Trainer

`train_lightgbm_ranker.py`:

- Accepts explicit train, valid, and optional test date ranges.
- Trains one LightGBM model with early stopping on the validation split.
- Writes predictions for validation and test rows.
- Writes metrics for RankIC and top-bottom return.
- Does not currently require monotonic non-overlapping windows.
- Does not assign `model_run_id`, `fold_id`, dataset hash, feature hash, or frozen params hash.
- Writes labels into the prediction output; strategy ignores them, but production/live score files should not require label columns.

Leakage implication: the CLI can accidentally accept overlapping windows or validation labels that require future bars from the test window. This is the main blocker for walk-forward acceptance.

### `ml_score_rank`

`MLScoreRankStrategy`:

- Requires `scores_path`.
- Loads `trade_date`, `symbol`, and score column.
- Normalizes symbols and parses dates.
- Uses exact equality: `score.trade_date == context.current_date`.
- Ranks current-date scores only.
- Applies current-history filters such as latest price and 20-day amount.
- Retains positions only inside a current-day rank hold buffer.

This exact-date lookup is the right anti-leakage behavior. The strategy should continue to reject missing future/stale substitutions. For walk-forward, the score CSV contract must become stricter rather than making the strategy infer run identity.

## Walk-Forward Protocol

### Frozen Design Rule

Before looking at 2026 results, freeze:

- Feature list and feature transforms.
- Label horizon, normally `fwd_5d` for first pass.
- LightGBM objective and hyperparameter search space.
- Early-stopping rule.
- Train/valid/test window cadence.
- Portfolio conversion params for `ml_score_rank`.
- Static-vs-walk-forward acceptance metrics.

Allowed before freeze:

- Use 2024 train and 2024 validation for model and early stopping design.
- Use 2025 as development OOS only if explicitly declared; if 2025 informs choices, 2025 is no longer clean OOS.

Forbidden for clean acceptance:

- Choosing hyperparameters, fold cadence, `top_n`, hold buffers, liquidity floor, or rebalance interval because they improved 2026.
- Regenerating 2026 predictions after reading 2026 backtest metrics unless the new run is labelled tuned-on-2026.

### Window Semantics

Use chronological windows only. No random split, shuffled CV, or full-period scaler.

For each fold:

- `train`: historical fitting window.
- `valid`: historical early-stopping/model-selection window.
- `embargo`: at least `label_horizon + 1` trading days after `valid_end`.
- `test`: next prediction/score window consumed by strategy.

Required invariant:

```text
max(train.trade_date) < min(valid.trade_date)
max(valid_label_end_date) < min(test.trade_date)
max(train_label_end_date) < min(test.trade_date)
test windows are monotonic and non-overlapping
```

`label_end_date` means the last future close needed by the label. For `fwd_5d` with T+1 entry, do not let a validation row whose forward return uses prices inside the test window participate in early stopping.

Recommended first cadence:

| Item | Value |
| --- | --- |
| Train window | trailing 12 to 24 months |
| Validation window | 1 quarter, or 1 month for faster rolls |
| Embargo | `label_horizon + 1` trading days minimum |
| Test window | next month or next quarter |
| Retrain | once per fold, before scoring that fold's test dates |

For sparse local data, prefer quarterly folds first to reduce runtime and noisy fold metrics. Monthly folds are acceptable after the manifest and validation checks are automated.

### Prediction `trade_date` Semantics

A prediction row with `trade_date=t` means:

- Features were computed using bars and eligible universe information available no later than `t`.
- The model used to score row `t` was trained before the fold's test window and did not use labels requiring prices from `t` or later.
- `ml_score_rank` reads the row on decision date `context.current_date == t`.
- The backtest engine schedules target weights for the next available trading date.

Do not:

- Shift scores to `t+1`.
- Use `latest score <= current_date`.
- Forward-fill missing score dates.
- Mix multiple model runs for the same score date without an explicit chosen `model_run_id`.

Missing score policy:

- Missing non-held symbol: cannot buy.
- Missing held symbol: target zero unless a separately documented stale-score grace rule exists.
- Entire missing date: target zero and count as prediction coverage failure.

## Model Run ID Contract

Every fold must create a stable `model_run_id`, for example:

```text
wf_lgbm_fwd5_20250102_20250331_<hash12>
```

The hash should cover:

- Dataset file hash and dataset metadata hash.
- Feature list.
- Label name and horizon.
- Train, valid, embargo, and test date ranges.
- LightGBM params and seed.
- Code version or git commit when available.
- PIT/degraded flags.

Prediction CSV required columns:

| Column | Requirement |
| --- | --- |
| `model_run_id` | immutable run id |
| `fold_id` | stable fold label, e.g. `2025Q1` |
| `trade_date` | decision date, ISO `YYYY-MM-DD` |
| `symbol` | normalized 6-character A-share code |
| `score` | finite numeric score; higher is better |
| `split` | `test` or `live` for strategy input; `valid` only for diagnostic files |

Recommended diagnostic columns:

- `score_asof`
- `train_start`, `train_end`
- `valid_start`, `valid_end`
- `test_start`, `test_end`
- `label`
- `feature_version`
- `dataset_version`
- `point_in_time`
- `degraded`

Uniqueness gate:

```text
(trade_date, symbol) is unique in the strategy input file
```

If multiple candidate model runs score the same date during research, keep them in separate files or require the strategy/backtest CLI to select one `model_run_id`.

## Rolling Artifact Layout

Recommended append-only layout:

```text
backend/artifacts/ml/walkforward/<experiment_id>/
  manifest.json
  dataset/
    dataset.csv
    dataset.metadata.json
    dataset.sha256
  folds/
    2025Q1/
      fold_manifest.json
      model.txt
      predictions.valid.csv
      predictions.test.csv
      metrics.json
      feature_importance.csv
    2025Q2/
      ...
  predictions/
    walkforward-test-predictions.csv
    walkforward-test-predictions.metadata.json
  acceptance/
    static-vs-walkforward-summary.json
    backtest-matrix/
```

Rules:

- Never overwrite a completed fold artifact; create a new `experiment_id` for changed params or data.
- Aggregate only `split=test` or `split=live` rows into the strategy score file.
- Keep validation predictions separate so they cannot accidentally feed `ml_score_rank`.
- Store fold-level coverage: dates scored, symbols scored per date, dropped NaN scores, duplicate rows rejected, missing dates.
- Store all degraded reasons from dataset metadata in every fold manifest.

## CLI Acceptance Requirements

### Dataset CLI Gate

`build_ml_dataset.py` output is acceptable for research only if:

- Metadata includes `label_timing`, `point_in_time`, `degraded`, and `degraded_reasons`.
- `point_in_time=false` is propagated into all downstream manifests.
- No feature column starts with `fwd_`.
- `trade_date` and `symbol` parse cleanly, and symbols preserve leading zeros.
- A feature invariance check passes: recomputing features after truncating bars at selected dates yields identical features for those dates.

### Trainer CLI Gate

`train_lightgbm_ranker.py` or a walk-forward wrapper must reject runs unless:

- Train, valid, embargo, and test windows are chronological.
- Windows are non-overlapping.
- No train/valid label end date reaches into the test window.
- Test windows across folds do not overlap.
- `--run-id` or deterministic run-id generation is present.
- Prediction output includes `model_run_id` and `fold_id`.
- Metrics output records full params, feature list, split rows, split dates, dataset hash, and code version.
- Prediction rows are finite and unique by `(model_run_id, trade_date, symbol)`.
- Strategy-input aggregation is unique by `(trade_date, symbol)`.

Suggested walk-forward wrapper interface:

```powershell
python run_walkforward_lgbm_ranker.py `
  --dataset artifacts/ml/ml-factor-dataset-2024-2026.csv `
  --dataset-metadata artifacts/ml/ml-factor-dataset-2024-2026.metadata.json `
  --label fwd_5d `
  --experiment-id wf-lgbm-fwd5-v1 `
  --train-window-months 24 `
  --valid-window-months 3 `
  --test-window-months 3 `
  --embargo-trading-days 6 `
  --first-test-start 2025-01-01 `
  --last-test-end 2026-06-30 `
  --frozen-params-json artifacts/ml/frozen-lgbm-fwd5-params.json `
  --output-dir artifacts/ml/walkforward/wf-lgbm-fwd5-v1
```

The wrapper should produce one manifest and one fold directory per fold, then one aggregated `walkforward-test-predictions.csv` for `ml_score_rank`.

### Strategy/Backtest CLI Gate

Before accepting `ml_score_rank` backtests:

- `scores_path` points to the aggregated walk-forward test/live prediction file, not validation predictions.
- Score file covers the requested decision window; missing dates are reported.
- `trade_date` is exact decision date and is not shifted.
- Benchmark is loaded and recorded for `000300`, `000905`, and `000852` comparisons.
- Same universe, costs, initial cash, rebalance interval, and strategy params are used for static and walk-forward comparisons.
- Outputs remain labelled non-PIT/degraded while dataset builder remains non-PIT.

## Static vs Walk-Forward Improvement Test

Compare against a frozen static model, not a moving target. Static baseline example:

- Train: `2024-01-02` to `2024-09-30`
- Valid: `2024-10-01` to `2024-12-31`
- Test/OOS: same dates as the walk-forward aggregated test rows
- Same feature set, label, LightGBM params unless the static-vs-WF experiment explicitly freezes a different static recipe before evaluation.

Primary model-level metrics:

- RankIC mean by test date.
- RankIC IR by test date.
- Top 20% minus bottom 20% forward return.
- Coverage per date.
- Fold-to-fold stability of RankIC and top-bottom return.

Primary strategy-level metrics:

- Excess return versus `000300`, `000905`, and `000852`.
- Sharpe.
- Max drawdown.
- Turnover and cost drag.
- Trade count and average holding period.
- Monthly paired excess returns versus static.

Promotion gates should be frozen before 2026 review. Recommended first gate:

- Walk-forward has higher median excess return than static across the 3 benchmarks x 3 cost cases.
- Walk-forward improves or matches Sharpe in at least 6 of 9 benchmark/cost cases.
- Walk-forward max drawdown is not worse than static by more than 3 percentage points in any primary case.
- Stress-cost result remains positive versus static or has a documented turnover reason.
- Model-level RankIC IR is higher than static, or strategy improvement is explained by lower turnover/drawdown without worse RankIC.
- Paired monthly excess return difference is positive in a majority of months; use a simple block bootstrap or paired month sign test as supporting evidence.

Do not accept:

- A single best grid row selected after seeing 2026.
- Improvement only under zero-tax or no-slippage diagnostics.
- Improvement caused by a different universe, date range, benchmark, score coverage, or rebalance interval.
- Walk-forward that wins only because validation predictions were included in strategy input.

## 2026 Tuning Rule

Clean 2026 acceptance requires an audit trail showing:

- The frozen params file existed before 2026 metrics were computed.
- Fold manifests for 2026 use the same protocol as 2025 folds.
- No 2026 backtest output was used to change hyperparameters, portfolio params, fold cadence, liquidity filters, or score thresholds.
- Any rerun after 2026 inspection has a new `experiment_id` and is labelled `tuned_on_2026=true`.

If this cannot be proven, report 2026 as exploratory only and reserve the next unseen period for clean OOS.

## Key Recommendations

1. Add a walk-forward wrapper instead of overloading the static trainer with manual repeated commands.
2. Add label embargo validation before early stopping and test scoring.
3. Add `model_run_id`, `fold_id`, manifest, and dataset/artifact hashes to the ML artifact contract.
4. Keep `ml_score_rank` exact-date and read-only; do not add stale-score fallback.
5. Keep validation predictions out of strategy score files.
6. Freeze all acceptance gates before reading 2026, then compare static and walk-forward on identical backtest matrices.
