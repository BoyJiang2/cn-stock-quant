# DeepSeek ML Strategy Review

Date: 2026-07-03

Scope: review only. No code changes. Focus is connecting `train_lightgbm_ranker.py` prediction CSV output into a target-weight strategy under the existing `Strategy.generate_target_weights()` and `DailyBacktestEngine` contract.

## Files Reviewed

- `backend/train_lightgbm_ranker.py`
- `backend/app/strategy/base.py`
- `backend/app/backtest/engine.py`
- `backend/run_strategy_backtest.py`

Additional context observed but not changed:

- `backend/app/strategy/examples.py` already contains `MLScoreRankStrategy`, which reads a scores CSV and maps it to equal target weights.
- `backend/app/risk/rules.py` applies generic long-only caps after strategy output.

## Current Interface Read

- `train_lightgbm_ranker.py` writes predictions with `trade_date`, `symbol`, `score`, `split`, and label column. It does not currently add `model_run_id`, dataset metadata, feature version, label horizon, or PIT/degraded flags to the CSV.
- `Strategy.generate_target_weights(context, history)` is the correct boundary. Strategy code receives `context.current_date`, current positions, params, and historical bars already filtered to `trade_date <= current_date`.
- `DailyBacktestEngine` already has T+1-style order timing: on date `t`, it executes any pending target weights from the previous decision date, then calls `generate_target_weights()` and stores the new target weights in `pending_target_weights` for the next trading date.
- Execution uses same-day close of the execution date, skips zero-volume symbols, blocks buy at limit-up and sell at limit-down, applies lot rounding, commission, stamp tax, slippage, and T+1 sellability through lot `available_date`.
- `run_strategy_backtest.py` selects a universe and loads bars only for `[start_date, end_date]`, passes CLI params through `BacktestConfig.params`, and marks current runs as non-PIT/degraded.

## Highest-Risk Issues

1. Symbol dtype and leading zeros are a hard blocker for CSV ingestion.

   `train_lightgbm_ranker.py` reads the dataset without `dtype={"symbol": "string"}` and writes `symbol` as-is. If the dataset CSV stores `000001` and pandas infers it as integer, predictions will contain `1`, not `000001`. The existing `MLScoreRankStrategy` context normalizes score symbols with `zfill(6)`, which mitigates this at strategy read time, but the training output itself is not contract-safe. Recommendation: prediction CSV reader and writer should both treat `symbol` as string, normalize once, and reject symbols that cannot be normalized.

2. Date meaning must be explicitly documented as decision date, not execution date.

   The prediction CSV `trade_date=t` is currently produced from feature rows at `t`. Under the engine, those scores should be consumed by the strategy on decision date `t` and executed on the next available trading date. Do not shift the CSV date forward before strategy ingestion. A shifted file would turn T+1 into T+2 or create ambiguous same-day execution assumptions.

3. Future prediction leakage is possible unless the strategy enforces exact-date lookup.

   The strategy must load only `score.trade_date == context.current_date`. It must not use "latest score <= current_date" unless the score file is a PIT append-only table with a separate `created_at/as_of` and the lookup is explicitly constrained. With a flat validation/test CSV, `<= current_date` can accidentally read stale scores, future-generated model runs, or scores produced after tuning on later periods.

4. Missing scores should force cash or hold-buffer behavior, not implicit forward fill.

   If a symbol has no score for `context.current_date`, it should not enter new buys. Existing holdings can only be retained if the holding symbol appears in the current day's ranked score universe and remains inside the hold cutoff. If the whole date has no score rows, the safest backtest behavior is target zero weights for all current-history symbols and record coverage failure; silently carrying prior weights hides prediction outages.

5. Hold buffer must be rank-based on the same current-date score cross-section.

   The buffer should keep current holdings only when their current-day rank is within `top_n * hold_rank_multiplier`. It should not keep holdings whose current-day score is missing, non-finite, below the score floor, or outside eligibility filters. Otherwise the buffer becomes a stale-position forward-fill path.

6. RiskEngine caps do not preserve model ranking if raw strategy weights are equal.

   Generic `risk_max_positions` sorts by weight, not model score. If the ML strategy returns equal weights for more names than allowed, RiskEngine tie ordering can dominate selection. The strategy should already return at most `top_n` selected names, and CLI `risk_max_positions` should be unset or aligned with `top_n`.

## Recommended Prediction CSV Contract

Minimum required columns:

- `trade_date`: decision date, ISO `YYYY-MM-DD`, matching the feature date and strategy `context.current_date`.
- `symbol`: normalized 6-character A-share symbol string where numeric codes preserve leading zeros.
- `score`: numeric finite model score. Higher means better long candidate.
- `model_run_id`: immutable identifier for the exact model/training split/params.
- `split`: `valid`, `test`, `live`, or walk-forward segment name.

Strongly recommended metadata columns or sidecar JSON:

- `label_horizon`, for example `fwd_5d`.
- `label_timing`, explicitly `close_t_plus_1_to_close_t_plus_1_plus_h`.
- `feature_version` and `dataset_version`.
- `train_start`, `train_end`, `valid_start`, `valid_end`, `test_start`, `test_end`.
- `created_at`.
- `point_in_time`, `degraded`, and `degraded_reasons`.

File-level validation before a backtest:

- Required columns exist.
- `trade_date` parses without nulls.
- `symbol` remains string after load and normalizes to the same value as bars.
- `(model_run_id, trade_date, symbol)` is unique, or duplicates are deterministically rejected.
- `score` is finite after numeric coercion.
- Prediction date range covers the requested backtest decision dates.
- No prediction row with `trade_date > context.current_date` is ever visible to the strategy lookup.

## Strategy Conversion Recommendation

Use a read-only prediction strategy. It should not train, mutate the CSV, infer missing scores, or query future rows.

Suggested decision flow for `context.current_date=t`:

1. Load/cache predictions with `pd.read_csv(..., dtype={"symbol": "string"})`.
2. Normalize symbols in both scores and bar history to the same 6-digit contract.
3. Select exactly `trade_date == t`.
4. Drop rows with missing/non-finite `score`.
5. Intersect with symbols present in `history` as of `t`.
6. Apply tradability filters using only `history <= t`: latest close above floor, 20d amount floor, and any PIT status/trade-gap filters once available.
7. Rank by `score` descending.
8. Retain existing holdings only if currently eligible and rank <= `top_n * hold_rank_multiplier`.
9. Fill remaining slots from ranks <= `top_n * entry_rank_multiplier`.
10. Return equal target weights capped by `max_position_weight` and `max_total_weight`, with zero weights for all other current-history symbols.

Recommended defaults:

- `top_n=30` or `50`
- `max_position_weight=0.05`
- `max_total_weight=0.8`
- `hold_rank_multiplier=1.3` to `1.6`
- `entry_rank_multiplier=1.0`
- no shorting and no negative weights

## Date Alignment And T+1 Notes

- Do not consume predictions dated earlier than the first backtest decision date unless explicitly warming an existing portfolio. Starting from cash, the first decision date produces no same-day trade; it schedules target weights for the next available trading date.
- The last decision date in the backtest may never execute if there is no subsequent trading date in `bars`. For clean attribution, evaluation windows should include one extra trading day after the final decision date or report that the final signal is unexecuted.
- `train_lightgbm_ranker.py` predictions are valid/test rows from the dataset. If the same command is rerun after tuning on test results, the CSV must get a different `model_run_id` and the affected test period must no longer be called clean OOS.

## Missing Score Policy

Recommended policy:

- Missing score for a non-held symbol: do not buy.
- Missing score for a held symbol: target zero unless a separate, explicit stale-score grace rule is implemented and reported.
- Entire missing date: target zero and record a degraded reason such as `missing_prediction_date`.
- Duplicate score rows: fail fast unless duplicate values are identical and the loader has a documented deterministic rule.
- NaN/inf score: drop and count in coverage metrics.

## Tests To Add When Implementation Resumes

- CSV symbol test: input symbols `1`, `000001`, `600000`, and `300750.0` normalize to expected bar symbols without losing leading zeros.
- Exact-date test: on `context.current_date=t`, a row for `t+1` is ignored even if it has a better score.
- T+1 execution test: a signal on `t` creates the first buy on the next engine trading date, not on `t`.
- Hold-buffer test: a current holding inside hold cutoff is retained; one missing from today's score cross-section is not retained.
- Missing-date test: no prediction rows for `t` returns zero targets and does not reuse `t-1`.
- Duplicate-date-symbol test: duplicate rows fail validation or keep a deterministic first row after sorting by an explicit rule.
- Risk cap test: the ML strategy itself returns no more than `top_n`; `risk_max_positions` is not relied on for score ranking.

## Bottom Line

The existing backtest engine is already compatible with a T+1 target-weight ML strategy if prediction `trade_date` is treated as the decision date and consumed by exact equality. The most important interface hardening is around CSV symbol string handling, score/date validation, and making missing-score behavior explicit. Hold buffer should reduce churn only within the current-day ranked universe; it must not become a hidden forward-fill of stale model predictions.
