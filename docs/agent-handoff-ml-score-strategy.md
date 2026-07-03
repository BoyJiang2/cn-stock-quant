# ML-score Strategy Handoff Draft

Date: 2026-07-03

Scope: documentation closeout draft after the `ml_score_rank` strategy hookup.
This file is intentionally separate from `docs/next-development-plan.md` and
`docs/profit-first-execution-plan.md`; the main thread should decide what to
merge into the canonical plans.

## Current Read

The ML path now has three separate layers:

1. Offline dataset/training:
   - `backend/build_ml_dataset.py`
   - `backend/train_lightgbm_ranker.py`
   - local artifact dataset:
     `backend/artifacts/ml/ml-factor-dataset-2024-2026.csv`
2. Offline score output:
   - prediction CSVs with `trade_date`, `symbol`, and `score`
   - current static LightGBM score quality is weak in 2026
3. Runtime score consumer:
   - `ml_score_rank` strategy reads a score CSV and converts same-date scores
     into target weights

The key distinction for future agents: the strategy adapter is connected, but
the current model evidence is not promotion-worthy. Treat this as an integration
milestone, not as an alpha win.

## Completed

- Added a prediction-backed strategy boundary:
  - strategy name: `ml_score_rank`
  - input: offline CSV with `trade_date`, `symbol`, and configurable score
    column
  - output: existing `dict[symbol -> target_weight]` contract
- Registered `ml_score_rank` as a built-in strategy.
- The strategy does not train a model during backtest. It is a read-only score
  consumer, matching the DeepSeek plan.
- Same-date score selection is implemented: at decision date `t`, only rows
  whose `trade_date == t` are considered.
- Basic symbol normalization is implemented for numeric symbols such as `1`
  to `000001`.
- Basic tradability filters are present:
  - `min_price`
  - `min_avg_amount_20d`
  - `min_score`
- Position sizing is capped by:
  - `top_n`
  - `max_position_weight`
  - `max_total_weight`
- Turnover control hooks are present:
  - `hold_rank_multiplier`
  - `entry_rank_multiplier`
- Tests exist for the basic ML-score strategy behavior:
  - current-date score selection
  - symbol normalization
  - empty-current-date score handling
- The upstream ML infrastructure is functional:
  - LightGBM 4.6.0 was installed in the active environment with `--no-deps`
  - 2024-2026 dataset was built with 2,546,137 rows, 4,968 symbols, 528 dates,
    and 40 factors
  - static `fwd_5d` LightGBM baseline produced prediction scores and metrics
- Static LightGBM baseline evidence:
  - validation RankIC `0.055625`, ICIR `0.520983`
  - 2025 OOS RankIC `0.037680`, ICIR `0.552200`, long-short `0.001958`
  - 2026 OOS RankIC `0.001870`, ICIR `0.028125`, long-short `-0.002121`

## Still Needed

- Run a full backtest of `ml_score_rank` using the generated LightGBM
  prediction CSVs.
- Compare `ml_score_rank` against all required benchmarks:
  - `000300`
  - `000905`
  - `000852`
- Compare against current rule baselines:
  - `multi_factor_rank`
  - `inverse_momentum`
- Run default, retail, and stress cost matrices.
- Test parameter neighbors before any promotion discussion:
  - `top_n=30/50/80`
  - `hold_rank_multiplier=1.3/1.6/2.0`
  - `entry_rank_multiplier=1.0/1.2/1.5`
  - rebalance interval `5/10/15/20` or monthly
- Add or confirm tests for:
  - invalid `scores_path`
  - missing required score columns
  - custom `score_column`
  - duplicate `(trade_date, symbol)` rows
  - invalid parameter bounds
  - hold-buffer retention behavior
- Produce a feature-importance report for the static model before using it as a
  research baseline.
- Add walk-forward training before judging the ML route:
  - static 2024-trained model fails 2026
  - next serious test should use frozen walk-forward protocol, not ad hoc
    tuning on 2026
- Wire PIT tradability when P3 tables have usable coverage:
  - listing/delisting
  - historical ST/name status
  - suspension/provider gap classification
  - index membership if using CSI universes
- Mark all current ML-score backtests as degraded/non-PIT until the PIT
  universe selector is actually consumed.

## Risks

- Current static model fails the 2026 OOS check. A strategy backtest may still
  look acceptable after sizing or filtering, but the raw score evidence is weak.
- If 2025 metrics are used to tune model parameters or strategy parameters,
  2025 becomes model-selection evidence rather than OOS. In that case, 2026 is
  the first clean OOS window.
- `ml_score_rank` currently depends on an offline CSV path. This is acceptable
  for research but brittle for paper/live unless model runs and predictions are
  stored with stable IDs and metadata.
- The current universe is still present-coverage based, not true PIT. Results
  may include survivorship and future-availability bias.
- qfq OHLCV data may be revised by future corporate actions.
- Missing bars are not yet fully separated into valid suspension/new-listing
  gaps versus provider gaps.
- The current score consumer filters by recent amount and price only. It does
  not yet enforce historical ST, suspended, limit-up/down, or provider-gap
  exclusions.
- Equal-weight TopN avoids score-scale overfitting, but may over-allocate to
  marginal names near the cutoff. Score-proportional sizing should only be
  tested after the equal-weight baseline is measured.
- CSV loading is cached. This is useful during backtests but can surprise
  interactive research if the score file is overwritten under the same path.

## Suggested Next Sub-Agent Split

| Agent | Ownership | Output |
| --- | --- | --- |
| Codex integration worker | Backtest `ml_score_rank` with existing prediction CSVs, add focused strategy tests, keep changes scoped | Metrics tables, test results, and a small strategy behavior note |
| DeepSeek ML reviewer | Review LightGBM feature importance, leakage gates, split protocol, and static-vs-walk-forward design | Reject/accept note for current model and walk-forward spec |
| GLM factor worker | Triage which 2026-confirmed factors should remain in the ML feature set, especially amount/liquidity factors | Feature keep/drop list with 2025/2026 evidence |
| PIT/data worker | Continue P3 foundations and define the exact join contract for `ml_score_rank` tradability filters | PIT coverage report and consumer contract |
| Main thread | Merge selected facts into `next-development-plan.md` and `profit-first-execution-plan.md` | Canonical task-board update |

## Recommended Immediate Sequence

1. Run `ml_score_rank` backtests using the static 2025 and 2026 prediction CSVs
   without changing model or strategy parameters.
2. Record whether ML-score backtest beats `multi_factor_rank`,
   `inverse_momentum`, and at least two of `000300`/`000905`/`000852` under
   default costs.
3. Run retail/stress costs only if default-cost results are not clearly dead.
4. Review feature importance before any model tuning.
5. If static ML fails at strategy level too, switch to walk-forward retraining
   before doing parameter grids on `ml_score_rank`.
6. Keep all reports labeled research-only/non-PIT until PIT universe selection
   and gap classification are consumed by the backtest path.

## Draft Canonical Plan Update For Main Thread

Suggested `next-development-plan.md` deltas, for the main thread to integrate
manually:

- Mark P2-6 as completed only if the main thread accepts `ml_score_rank` as the
  prediction-backed strategy boundary.
- Keep P2-7 open until actual backtest metrics exist.
- Add a note that the current static model is not a promotion candidate because
  2026 OOS RankIC is near zero and long-short is negative.
- Add a new P2 task for walk-forward LightGBM scoring.
- Keep P3/PIT caveats attached to every ML result.

Suggested `profit-first-execution-plan.md` deltas:

- Add a development log entry that `ml_score_rank` now consumes offline
  prediction CSVs and returns capped equal target weights.
- Do not mark the ML prediction strategy as profitable or paper-ready.
- Add a stop condition: if static `ml_score_rank` fails 2026 default-cost
  benchmark comparison, do not tune strategy buffers before trying a
  walk-forward model.
