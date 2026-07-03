# DeepSeek 2026 ML/PIT Execution Plan

Date: 2026-07-02

Scope: P2 LightGBM ML path and P3 point-in-time data trustworthiness. This is an execution plan only. Do not change core strategy, factor, backtest, or API behavior in this pass.

## 1. Current Read

- `backend/build_ml_dataset.py` is already the right first dataset boundary: it pulls local daily bars, computes `FactorLab` factors, applies same-date cross-sectional robust preprocessing, and emits `fwd_5d` / `fwd_10d` labels with T+1 entry timing.
- `backend/train_lightgbm_ranker.py` is a standalone optional-dependency trainer. It uses date slices only, trains LightGBM regression, writes validation/test predictions, RankIC/top-bottom metrics, and an optional model artifact.
- `backend/app/data/pit_repository.py` and `backend/app/models/pit.py` already define the initial PIT surface: security status/name intervals, index constituent intervals, index weight snapshots, and materialized research pools.
- Current research remains degraded until PIT tables are actually populated and used by dataset/backtest universe selection. The dataset builder still uses `covered_research_symbols`, which is current-coverage based and therefore not production-grade PIT.
- The ML target should be to beat `multi_factor_rank` and `inverse_momentum` under identical universe, benchmarks, rebalance interval, and costs. Beating `000300` alone is insufficient because 2025 small/mid-cap benchmarks were stronger.

## 2. Required 2024/2025/2026 Splits

Use only chronological splits. No random split and no shuffled cross-validation.

### Baseline Static Split

| Split | Date Range | Purpose | Rules |
| --- | --- | --- | --- |
| Train | 2024-01-02 to 2024-09-30 | Fit model parameters | Features and labels only from this window |
| Valid | 2024-10-01 to 2024-12-31 | Early stopping and one-time hyperparameter choice | All parameter decisions must be frozen after this |
| Test/OOS 1 | 2025-01-01 to 2025-12-31 | First clean full-year OOS | Do not tune after seeing 2025 |
| Test/OOS 2 | 2026-01-01 to latest local date, currently 2026-06-18 in task board | Second OOS/regime check | Score with frozen setup unless explicitly running a walk-forward protocol |

If any tuning uses 2025 metrics, 2025 is no longer OOS. In that case, report 2025 as model-selection evidence and treat 2026 as the first clean OOS.

### Walk-Forward Split After Baseline

- Train window: trailing 12 months minimum, ending before validation/test.
- Validation window: next 1 month or quarter for early stopping.
- Test window: next month or quarter.
- Roll forward without reusing future test results for parameter changes.
- Save one `model_run_id` per window with split dates, feature list, label, params, dataset metadata, and degraded flags.

## 3. LightGBM Install, Dataset, Training, Validation Commands

Run from `backend/`.

```powershell
cd D:\CursorProjects\cn-stock-quant\backend
python -m pip install -r requirements.txt
python -c "import lightgbm as lgb; print(lgb.__version__)"
```

Build one dataset that covers train/valid/2025/2026 scoring rows. The builder already loads warmup history and extra label-end bars internally.

```powershell
python build_ml_dataset.py `
  --start-date 2024-01-02 `
  --end-date 2026-06-18 `
  --horizon 5 `
  --horizon 10 `
  --pool-max-symbols 6000 `
  --output artifacts/ml/ml-factor-dataset-2024-2026.csv `
  --metadata-output artifacts/ml/ml-factor-dataset-2024-2026.metadata.json
```

Train the baseline and score 2025 only:

```powershell
python train_lightgbm_ranker.py `
  --dataset artifacts/ml/ml-factor-dataset-2024-2026.csv `
  --label fwd_5d `
  --train-start 2024-01-02 `
  --train-end 2024-09-30 `
  --valid-start 2024-10-01 `
  --valid-end 2024-12-31 `
  --test-start 2025-01-01 `
  --test-end 2025-12-31 `
  --predictions-output artifacts/ml/lgbm-fwd5-static-2025-predictions.csv `
  --metrics-output artifacts/ml/lgbm-fwd5-static-2025-metrics.json `
  --model-output artifacts/ml/lgbm-fwd5-static-2024q1q3.txt
```

Score 2026 with the same frozen command shape. The current CLI retrains from the same train/valid windows, so use exactly the same params and split dates as the 2025 run:

```powershell
python train_lightgbm_ranker.py `
  --dataset artifacts/ml/ml-factor-dataset-2024-2026.csv `
  --label fwd_5d `
  --train-start 2024-01-02 `
  --train-end 2024-09-30 `
  --valid-start 2024-10-01 `
  --valid-end 2024-12-31 `
  --test-start 2026-01-01 `
  --test-end 2026-06-18 `
  --predictions-output artifacts/ml/lgbm-fwd5-static-2026-predictions.csv `
  --metrics-output artifacts/ml/lgbm-fwd5-static-2026-metrics.json `
  --model-output artifacts/ml/lgbm-fwd5-static-2024q1q3-rescore.txt
```

Validation report must include:

- RankIC mean/std/IR by valid, 2025, and 2026.
- Top 20% minus bottom 20% return by split.
- Coverage by date: scored names, dropped names, missing feature rows.
- Feature importance and whether one factor dominates.
- Strategy-level comparison after converting predictions to TopN weights: `000300`, `000905`, `000852`, `multi_factor_rank`, `inverse_momentum`, default/retail/stress costs.

## 4. Leakage And Trustworthiness Checks

Hard blockers:

- Dataset split dates must be monotonic and non-overlapping.
- `trade_date=t` features must use bars up to and including `t` only.
- Label must remain T+1 entry: `close(t+1+h) / close(t+1) - 1`.
- Drop rows whose label requires unavailable future prices. Never forward-fill labels.
- No random split, shuffled CV, full-sample scaler, full-sample imputer, or centered rolling window.
- Same-date preprocessing may use only the `t` cross-section. Any stateful transform must be fitted on train only.
- Universe construction for live-readiness must use `as_of <= trade_date` PIT rows, not today's stock snapshot.
- `announced_at` must satisfy `announced_at <= as_of`; if missing, mark confidence no higher than medium and propagate degraded metadata.
- ST/listing/delisting/index membership cannot be backfilled from future effective membership or current names.
- 2025 tuning invalidates 2025 as OOS. 2026 becomes the first clean OOS if that happens.

Soft risks to disclose in every ML report until fixed:

- qfq OHLCV may be revised after future corporate actions.
- Missing daily bars are not yet separated into suspension, new listing, delisting, and provider gaps.
- The current dataset builder selects from present-day coverage and should be marked `point_in_time=false`.
- BJ and low-liquidity names can inflate rank metrics unless liquidity filters are applied consistently in dataset and backtest.

Minimum automated checks to add when implementation resumes:

- Assert `max(train.trade_date) < min(valid.trade_date) <= max(valid.trade_date) < min(test.trade_date)`.
- Assert all prediction rows have `trade_date` inside valid/test only.
- Assert metadata contains `label_timing`, `point_in_time`, `degraded`, and `degraded_reasons`.
- Assert no feature column starts with `fwd_`.
- For PIT queries, unit-test `announced_at > as_of` exclusion at an index rebalance boundary and at ST status changes.

## 5. Minimum PIT Schema Recommendations

The existing `app.models.pit` tables are close to the needed minimum. Keep schemas narrow and audit-friendly.

### Listing / Delisting / Regulatory Status

Table: `security_status`

Minimum columns:

- `symbol`
- `status`: `listed`, `normal`, `st`, `sst`, `st_star`, `suspended`, `delisted`
- `valid_from`
- `valid_to`
- `announced_at`
- `delist_reason`
- `source`
- `confidence`: `high`, `medium`, `low`
- `updated_at`

Minimum constraints:

- Unique key: `(symbol, status, valid_from)`
- PIT query condition: `valid_from <= as_of < coalesce(valid_to, far_future)` and `coalesce(announced_at, valid_from) <= as_of`
- If `announced_at` is null, downgrade confidence and count in degraded report.

### ST / Name History

Table: `security_name`

Minimum columns:

- `symbol`
- `name`
- `valid_from`
- `valid_to`
- `announced_at`
- `source`
- `updated_at`

Minimum constraints:

- Unique key: `(symbol, valid_from)`
- Preserve `ST`, `*ST`, `SST`, `S*ST`, and delisting markers in `name`.
- ST exclusion should prefer historical `security_name` over current `stocks.name`.

### Index Constituents

Table: `index_constituent`

Minimum columns:

- `index_symbol`: `000300`, `000905`, `000852`, etc.
- `symbol`
- `valid_from`
- `valid_to`
- `announced_at`
- `source`
- `updated_at`

Minimum constraints:

- Unique key: `(index_symbol, symbol, valid_from)`
- Query must use announcement timing, not only effective date.
- Store intervals rather than daily rows for membership.

Optional but useful table: `index_weight_snapshot`

- `index_symbol`
- `symbol`
- `trade_date`
- `weight`
- `source`
- `updated_at`
- Unique key: `(index_symbol, symbol, trade_date)`

### Suspension And Bar Gaps

New recommended table: `security_trade_gap`

Minimum columns:

- `symbol`
- `trade_date`
- `expected_open`: bool from trading calendar and listing status
- `has_bar`: bool
- `gap_type`: `normal`, `suspended`, `limit_halt`, `new_listing_gap`, `delisted_gap`, `provider_gap`, `unknown`
- `source`
- `confidence`
- `created_at`

Minimum constraints:

- Unique key: `(symbol, trade_date)`
- Gaps should be generated by comparing expected trading calendar, listing/status intervals, and actual daily bars.
- Provider gaps must block or degrade research metrics; valid suspensions should block trading but not be treated as provider data loss.

Implementation note, 2026-07-02:

- Added the additive ORM table `SecurityTradeGap` with the columns above and unique key `(symbol, trade_date)`.
- Added `PitRepository.upsert_security_trade_gap()`, `trade_gap_as_of()`, and `trade_gaps_between()` as the minimum write/read skeleton.
- Added `security_trade_gap_rows` and `provider_gap_rows` to `pit_coverage_report()`.
- No automatic gap generation is wired yet. The future generator should be a separate PIT sync/research job that compares the trading calendar, `security_status`, and `daily_bars`.
- No backtest, factor, strategy, or ML dataset behavior consumes this table yet. Until consumers join it, missing-bar quality remains partially degraded.

### Materialized Research Pool

The existing `research_pool_member` table should stay as the audit layer:

- `pool_key`
- `as_of`
- `symbol`
- `eligible`
- `exclusion_reason`
- `name_at`
- `status_at`
- `created_at`

Use it to reproduce ML dataset universe membership and backtest candidate sets.

## 6. Connecting Model Output To Strategy

Do not train inside the strategy. The strategy should be a read-only prediction consumer.

Recommended prediction file/table schema:

- `model_run_id`
- `trade_date`
- `symbol`
- `score`
- `label_horizon`
- `feature_version`
- `dataset_version`
- `created_at`

Boundary note, 2026-07-02:

- The prediction table/file remains a planned interface, not a runtime strategy dependency in this pass.
- Training stays offline in `train_lightgbm_ranker.py`; strategy code must only read stable predictions for the current decision date.
- A future database table should mirror the schema above with a unique key such as `(model_run_id, trade_date, symbol)`.
- Strategy integration should join predictions with PIT tradability at `trade_date`: `security_status`, `security_name`, and `security_trade_gap`. Rows with `gap_type in ("provider_gap", "unknown")` should degrade or block the run; `suspended` should block trading for that date without counting as provider data loss.

Execution path:

1. Train offline with `train_lightgbm_ranker.py`.
2. Save predictions for decision dates as `trade_date, symbol, score`.
3. At backtest decision date `t`, load only predictions where `trade_date == t`.
4. Join with PIT tradability filters as of `t`: listed/normal, not ST per configured policy, not suspended, sufficient recent liquidity, no provider gap.
5. Sort by score descending.
6. Apply hold buffer to reduce churn:
   - Entry universe: top `N`, first version `top_n=30` or `50`.
   - Existing holdings may remain until rank falls below `top_n * hold_rank_multiplier`, first version `1.3` to `1.6`.
7. Convert to weights:
   - v1: equal weight after caps.
   - `gross_exposure=0.8`
   - `max_position_weight=0.05`
   - no shorting.
8. Return `dict[symbol -> weight]` through the existing strategy boundary.

Promotion gate for ML strategy:

- Positive default-cost excess against at least two of `000300`, `000905`, `000852`.
- Does not underperform `multi_factor_rank` and `inverse_momentum` after costs without a clear drawdown/turnover benefit.
- Retail/stress costs do not erase all excess.
- 2024 valid, 2025 OOS, and 2026 OOS do not collapse.
- Turnover is lower than or comparable to current `multi_factor_rank`, or the excess return clearly pays for it.
- PIT degraded ratio is reported. If PIT tables are incomplete, mark results research-only.

## 7. Immediate P2/P3 Work Order

1. Install LightGBM and verify import in the active backend environment.
2. Build the 2024-2026 dataset once with `fwd_5d` and `fwd_10d`.
3. Train the static baseline: 2024 Q1-Q3 train, 2024 Q4 valid, 2025 test.
4. Rescore 2026 with the same frozen setup.
5. Review metrics and feature importance before any parameter search.
6. Add a prediction-backed strategy only after the first prediction file is stable.
7. Populate or sync PIT rows for listing/status/name/index membership.
8. Add gap classification before claiming missing-bar coverage as data quality.
9. Rebuild ML datasets using PIT universe selection once P3 tables have usable coverage.

## 8. Stop Conditions

Stop and report rather than optimizing further when:

- LightGBM cannot import after dependency install.
- Dataset row count or date count is unexpectedly tiny for any split.
- Validation RankIC is positive but 2025/2026 RankIC flips strongly negative.
- Feature importance is dominated by one suspect feature or a field tied to future availability.
- PIT coverage is mostly missing and the report would be presented as production-grade.
- Strategy backtest improves gross return but fails under default costs.
