# Agent Handoff - 2026-06-29

## Current Baseline

- Latest pushed commit before this handoff: `414b2dc Build PIT data and factor research foundation`.
- Backend full test suite before the next sprint: `400 passed`.
- PIT universe, full-market sync, factor lab, AI research adapters, and frontend data/backtest wiring are already in place.
- Full-market daily sync is resumable, but the previous background process was no longer running. Last observed log progress was `924/5515` symbols, about `16.75%`.
- Runtime logs and pid files are ignored by Git.

## Three-Agent Coordination

- Codex: integration owner. Keep final merges, tests, docs, API consistency, and data quality gates under Codex control.
- OpenCode GLM 5.2: strategy/factor expansion owner. It should work in `backend/app/strategy`, `backend/app/factors`, and related tests unless Codex assigns a different slice.
- Claude Code DeepSeek V4 Pro: Qlib LightGBM and RD-Agent review owner. It should first review interfaces and leakage risks, then propose worker boundaries before touching production code.

Every phase should follow this loop:

1. Each agent reads the current repo and proposes a narrow plan.
2. Codex compares plans and assigns disjoint file ownership.
3. Agents implement in separate slices.
4. GLM reviews Codex interfaces, Codex reviews DeepSeek interfaces, DeepSeek reviews GLM interfaces.
5. Codex runs focused tests, then full backend tests before pushing.

## Work Completed After `414b2dc`

- `backend/sync_full_market.py` now supports `--state-file`.
- The sync runner writes a UTF-8 JSON heartbeat after every batch with:
  - `pid`
  - `updated_at`
  - date range
  - progress
  - last batch summary
  - `exit_reason`
- Added `backend/tests/test_sync_full_market_runner.py`.
- Added `predictions_to_weights()` in `backend/app/ai_research/qlib_adapter.py`.
- Exported the adapter from `backend/app/ai_research/__init__.py`.
- Added tests for converting Qlib/project prediction frames into `dict[symbol -> weight]`.

Focused validation:

```text
python -m pytest backend/tests/factors/test_ai_research.py backend/tests/test_sync_full_market_runner.py backend/tests/test_full_market_sync.py -q
26 passed

python -m pytest backend/tests/test_sync_full_market_runner.py backend/tests/test_full_market_sync.py backend/tests/test_data_routes.py -q
67 passed
```

## DeepSeek Review Summary

DeepSeek recommended the shortest safe Qlib path:

1. Keep Qlib in an isolated worker process. Do not import heavy Qlib dependencies into FastAPI runtime.
2. Export PIT universe, calendar, OHLCV, and labels into versioned worker inputs.
3. Run Alpha158 + LightGBM with strict walk-forward splits.
4. Store model runs, artifacts, predictions, and evaluations.
5. Convert predictions into target weights through a small adapter.
6. Register a `QlibLightGBMStrategy` that only returns `dict[symbol -> weight]`.

Hard leakage gates:

- No random time-series splits.
- Labels must start from T+1 execution assumptions.
- PIT degraded results must not be treated as live-trading evidence.
- News and announcements must use tradable-time alignment, not natural-date backfill.

## Immediate Next Steps

1. Resume full-market sync with a state file:

```powershell
cd D:\CursorProjects\cn-stock-quant\backend
python sync_full_market.py --start-date 2024-01-01 --end-date 2025-12-31 --batch-size 20 --interval 0.35 --state-file full-market-sync.state.json
```

2. Add a model registry foundation:
   - `backend/app/models/qlib.py`
   - `model_runs`
   - `model_artifacts`
   - `model_predictions`
   - `model_evaluations`

3. Add `QlibLightGBMStrategy` as a read-only prediction consumer:
   - load predictions for `context.current_date`
   - call `predictions_to_weights()`
   - return target weights only

4. Expand the strategy/factor candidate pool:
   - low-volatility dividend proxy
   - small-cap momentum with liquidity filter
   - volatility contraction breakout
   - trend-filtered mean reversion
   - ETF rotation
   - beta-neutral residual momentum
   - downside-risk reversal

5. Start news/sentiment schema after the data sync and model registry are stable:
   - `news_items`
   - `news_entities`
   - `sentiment_scores`
   - `sentiment_daily_features`

## Non-Negotiable Rule

Profit-first does not mean curve-fitting-first. A strategy candidate only graduates if it is:

- point-in-time safe,
- cost-aware,
- tradable in A-share constraints,
- stable across multiple market regimes,
- explainable enough to debug,
- still useful after realistic slippage and position caps.
