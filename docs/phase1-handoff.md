# Phase 1 Handoff

Last updated: 2026-06-18

## Current State

Phase 1 is intended to turn the repository from a rough demo into a runnable local quant research prototype. The core engineering loop is now in place:

- FastAPI backend with local SQLite persistence.
- AkShare daily bar sync with a fallback path for unstable EastMoney requests.
- Strategy registry with built-in and user-file strategy loading.
- Daily backtest engine with A-share-oriented execution rules.
- Risk engine integrated before execution.
- React backtest UI with dynamic strategy parameters.
- JoinQuant strategy draft stored under `strategies/` for external platform testing.
- Regression tests for the backtest engine, strategy examples, and risk engine.

## Startup

Backend:

```powershell
cd D:\CursorProjects\cn-stock-quant\backend
D:\anaconda3\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Frontend:

```powershell
cd D:\CursorProjects\cn-stock-quant\frontend
npm run dev -- --host=127.0.0.1 --port=5173
```

Validation:

```powershell
cd D:\CursorProjects\cn-stock-quant\backend
D:\anaconda3\python.exe -m pytest tests
```

```powershell
cd D:\CursorProjects\cn-stock-quant\frontend
npm run build
```

## Completed In Phase 1

### Strategy Framework

- `Strategy` now exposes metadata and typed parameters.
- Built-in examples:
  - `moving_average`
  - `momentum_rank`
  - `mean_reversion`
- User strategies can be loaded from the top-level `strategies/` folder.

### Backtest Engine

- Signal generated on day T executes on the next trading day.
- Rebalance interval is configurable.
- Sell orders run before buy orders to release cash.
- Basic A-share constraints are modeled:
  - lot size,
  - T+1 sellability,
  - no trade on zero-volume bars,
  - limit-up blocks buys,
  - limit-down blocks sells,
  - commission, stamp tax, and slippage.
- Risk limits are applied to target weights before execution:
  - single-symbol weight cap,
  - total exposure cap,
  - max position count.

### Data Layer

- AkShare stock list and daily bar sync are available through API routes.
- `stock_zh_a_hist` failures fall back to `stock_zh_a_daily` for common stock symbols.
- Local cached data is reused when a resync fails but matching bars already exist.

### Frontend

- Strategy list is fetched from backend metadata.
- Backtest form renders strategy parameters dynamically.
- Risk and rebalance controls are available in the UI.
- Equity chart now supports strategy equity, benchmark equity, and drawdown.
- Backtest metrics include benchmark return and excess return for newly run backtests.

### Documentation

- `README.md` includes startup commands and common troubleshooting.
- `docs/roadmap.md` and `docs/development-log.md` record the architecture direction and completed work.
- `docs/joinquant-strategy-notes.md` records the JoinQuant strategy draft and caveats.

## Important Caveats

- Existing historical backtest records do not persist benchmark curves. New runs return benchmark metrics when local benchmark bars are present.
- `benchmark_symbol` defaults to `000300`, but the backend does not automatically fetch benchmark data during a backtest. Sync or insert benchmark daily bars first if benchmark comparison is required.
- The current JoinQuant script is a runnable experimental template, not a validated alpha strategy. Its live usefulness depends on fresh research, parameter sweeps, and real backtest evidence.
- Limit-up/down handling uses a simple 10% approximation. STAR, ChiNext, Beijing Exchange, ST, and IPO period rules need more precise daily limit logic in later phases.

## Suggested Next Agent Work

1. Add first-class index data sync for `000300`, `000905`, `000852`, and ETF symbols.
2. Persist benchmark fields if historical run detail needs benchmark replay from database.
3. Build a batch validation workflow:
   - sync 20-50 liquid symbols,
   - run built-in strategies,
   - export metrics,
   - compare against benchmark.
4. Start a research-driven strategy track:
   - dividend/quality/low-vol portfolio,
   - ETF or industry momentum rotation,
   - regime switching between momentum and mean reversion.
5. Replace the current JoinQuant draft with a strategy backed by report notes and parameter sensitivity tests.

## Files To Inspect First

- `backend/app/backtest/engine.py`
- `backend/app/strategy/base.py`
- `backend/app/strategy/examples.py`
- `backend/app/strategy/registry.py`
- `backend/app/risk/rules.py`
- `backend/app/data/akshare_provider.py`
- `frontend/src/pages/BacktestPage.tsx`
- `frontend/src/components/EquityChart.tsx`
- `strategies/joinquant_momentum_market_filter.py`

