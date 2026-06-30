# Profit-first execution plan

This document is the working checklist for turning `cn-stock-quant` from a
research demo into a profit-first quant workflow. Update it after each agent
round so future agents can continue without rediscovering context.

## P0: Prove Whether Current Strategy Can Make Money

| ID | Item | Goal | Output | Owner | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| P0-1 | Full-market `stable_reversal` backtest | Validate the first profit candidate | 2024/2025 report with return, drawdown, Sharpe, turnover | Codex | in progress | CLI added; full-market run still pending |
| P0-2 | Parameter grid search | Find robust parameters, not one lucky setting | `top_n`, reversal window, amount threshold, exposure comparison | GLM | pending | Use CLI output as input |
| P0-3 | Benchmark comparison | Check excess return vs 000300/000905/000852 | Excess return and relative drawdown table | Codex | pending | Start with local index bars |
| P0-4 | Cost/slippage stress test | Ensure paper alpha survives costs | Commission/slippage matrix | DeepSeek | pending | Reuse same CLI with cost overrides |

## P1: Make Backtests Trustworthy

| ID | Item | Goal | Output | Owner | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| P1-1 | Historical listing/delisting status | Reduce survivorship bias | `stock_status_history` table and sync task | DeepSeek | pending | Required before live confidence |
| P1-2 | Historical ST status | Exclude risky/untradable names as-of date | `stock_risk_status_history` | DeepSeek | pending | Use provider confidence flags |
| P1-3 | Historical index constituents | Real benchmark universes and CSI pools | `index_constituent_history` | GLM | pending | 000300/000905/000852 first |
| P1-4 | PIT universe selector | Return tradable universe as of any date | `get_tradeable_universe(as_of)` | Codex | pending | Integrate into backtest API |
| P1-5 | Gap classification | Separate suspension/listing/provider gaps | Upgraded data quality report | Codex | pending | Current report only counts missing bars |

## P2: Build A Strategy Portfolio

| ID | Item | Goal | Output | Owner | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| P2-1 | Enhance `stable_reversal` | Improve the current candidate | Low-vol weighting, crowding penalty, better defaults | Codex | pending | Use P0 grid results |
| P2-2 | Low-vol defensive rerun | Defensive return source | Full-market `low_vol_defensive` report | GLM | pending | Compare in bear/sideways regimes |
| P2-3 | Inverse momentum strategy | Exploit negative 2025 momentum IC | `inverse_momentum` strategy | GLM | in progress | First 60d variant is cost-adjusted excess positive in 2025 |
| P2-4 | Market regime filter | Switch strategies by market state | `market_regime_filter` | DeepSeek | pending | Avoid one-strategy exposure |
| P2-5 | Meta allocation | Allocate capital across strategies | Simple recent-performance allocator | Codex | pending | Not before P0 evidence |

## P3: Add Machine Learning Alpha

| ID | Item | Goal | Output | Owner | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| P3-1 | LightGBM trainer | Train on current factor panel | `lightgbm_trainer.py` | DeepSeek | pending | Time-series split only |
| P3-2 | ML prediction strategy | Convert prediction to target weights | `lightgbm_alpha` strategy | Codex | pending | Keep `dict[symbol -> weight]` |
| P3-3 | Out-of-sample validation | Fight overfitting | Train/valid/test report | GLM | pending | Compare with `stable_reversal` |
| P3-4 | Feature importance | Explain model edge | Importance report, optional SHAP later | DeepSeek | pending | Avoid black-box promotion |
| P3-5 | Rolling training | Adapt to drift | Monthly/quarterly rolling train CLI | Codex | pending | After static model proves useful |

## P4: Add News And Sentiment

| ID | Item | Goal | Output | Owner | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| P4-1 | `NewsProvider` protocol | Treat news as first-class data | Provider interface | Codex | pending | Keep source-agnostic |
| P4-2 | AkShare news/announcement sync | Start with free data | News/announcement tables | GLM | pending | Data quality first |
| P4-3 | Forum/social data feasibility | Find durable sentiment sources | Source feasibility report | GLM | pending | Snowball/Guba/Taoguba |
| P4-4 | Sentiment scoring model | Daily stock sentiment factor | `sentiment_score` table | DeepSeek | pending | Chinese finance model later |
| P4-5 | Sentiment factor backtest | Prove incremental value | Price vs price+sentiment comparison | Codex | pending | Avoid narrative-only signals |

## P5: Simulation And Live Readiness

| ID | Item | Goal | Output | Owner | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| P5-1 | Paper portfolio loop | Daily plan and position update | Usable Portfolio page | Codex | pending | Use existing trade plan base |
| P5-2 | Trade plan realism | Handle A-share constraints | Lot, cash, limit-up/down aware plan | DeepSeek | pending | Match backtest assumptions |
| P5-3 | Risk rule upgrade | Prevent strategy blowups | Position, industry, drawdown, turnover limits | Codex | pending | Add alerts |
| P5-4 | vn.py adapter research | Prepare live boundary | Broker adapter design | GLM | pending | No live trading before paper |
| P5-5 | Paper observation period | Validate execution drift | Daily equity and fill report | Codex | pending | Minimum 1-3 months |

## Promotion Gates

Do not promote a strategy toward paper/live trading unless it passes all gates:

- Annual return beats at least two of `000300`, `000905`, `000852`.
- Maximum drawdown is lower than major benchmarks or return/drawdown is clearly better.
- Cost-adjusted return remains positive under realistic commission, stamp tax, and slippage.
- At least three neighboring parameter sets are profitable.
- Out-of-sample period does not collapse.
- Known non-PIT limitations are documented for the run.

## Development Log

- 2026-06-30: Plan created. P0-1 started. Current implemented candidate is `stable_reversal`.
- 2026-06-30: Added `backend/run_strategy_backtest.py` for reproducible strategy backtests and parameter grids.
- 2026-06-30: Unified backtest research-pool cap to 6000 and included BJ in `select_research_symbols`; kept normal research-sync defaults at SH/SZ.
- 2026-06-30: Fixed buy execution cash handling when the 5 yuan minimum commission would otherwise make cash negative.
- 2026-06-30: Smoke run passed: `stable_reversal`, 2025-01-01 to 2025-03-31, 100 symbols, 5-day rebalance, `total_return=0.005602`, `benchmark_return=0.017514`, `excess_return=-0.011913`. This validates the CLI only; it is not enough investment evidence.
- 2026-06-30: Full-market 2025 baseline, 5-day rebalance, default costs: `selected_symbol_count=5183`, `total_return=0.113974`, `benchmark_return=0.211901`, `excess_return=-0.097927`, `max_drawdown=-0.154943`, `sharpe=0.738009`, turnover on initial cash `71.218711`.
- 2026-06-30: Full-market 2025, 10-day rebalance, default costs: `total_return=0.066735`, `benchmark_return=0.211901`, `excess_return=-0.145166`, `max_drawdown=-0.131511`, turnover `36.453399`.
- 2026-06-30: Full-market 2025, 5-day rebalance, zero commission/stamp/slippage after fixing zero-cost commission handling: `total_return=0.218988`, `benchmark_return=0.211901`, `excess_return=0.007087`, `max_drawdown=-0.150144`, `sharpe=1.277301`, turnover `74.50572`.
- 2026-06-30: Added `stable_reversal` rank hysteresis parameters: `hold_rank_multiplier` retains existing positions that remain inside the hold buffer; `entry_rank_multiplier` controls how far down new entries may come from. This targets turnover near the selection boundary.
- 2026-06-30: GLM proposed low-turnover grids. Full-market 2025 with 10-day rebalance and default costs:
  - `G4_extreme_low_turnover`: `top_n=80`, `min_reversal=0.02`, `max_amount_ratio=1.6`, `low_vol_weight=0.3`, `hold_rank_multiplier=1.4`, `total_return=0.113111`, `excess_return=-0.09879`, `max_drawdown=-0.10509`, `sharpe=0.892717`, turnover `31.102737`.
  - `G1_wide_low_turnover`: `total_return=0.110767`, `excess_return=-0.101134`, `max_drawdown=-0.114946`, `sharpe=0.809594`, turnover `33.958062`.
  - `G2_tight_reversal`: `total_return=0.102014`, `excess_return=-0.109887`, `max_drawdown=-0.140175`, `sharpe=0.648505`, turnover `35.192906`.
- 2026-06-30: `G4_extreme_low_turnover` zero-cost run: `total_return=0.173592`, `benchmark_return=0.211901`, `excess_return=-0.038309`, `max_drawdown=-0.103647`, `sharpe=1.296044`, turnover `31.984806`.
- 2026-06-30: Added builtin `inverse_momentum`, designed to exploit negative 2025 momentum IC by buying liquid laggards with drawdown, crowding, and optional benchmark-momentum gates.
- 2026-06-30: Full-market 2025 `inverse_momentum`, default parameters, 10-day rebalance, default costs: `total_return=0.210259`, `benchmark_return=0.211901`, `excess_return=-0.001642`, `max_drawdown=-0.09678`, `sharpe=1.397148`, turnover `23.631958`.
- 2026-06-30: Full-market 2025 `inverse_momentum`, default parameters, 10-day rebalance, zero costs: `total_return=0.239089`, `benchmark_return=0.211901`, `excess_return=0.027188`, `max_drawdown=-0.089612`, `sharpe=1.566754`, turnover `23.868972`.
- 2026-06-30: Full-market 2025 inverse-momentum grid, 10-day rebalance, default costs:
  - `IM_60d_top30`: `lookback_window=60`, `top_n=30`, `hold_rank_multiplier=1.2`, `total_return=0.21699`, `excess_return=0.005089`, `max_drawdown=-0.099372`, `sharpe=1.441734`, turnover `22.723315`.
  - `IM_60d_top50`: `total_return=0.185018`, `excess_return=-0.026883`, `max_drawdown=-0.086038`, `sharpe=1.372861`, turnover `20.319901`.
  - `IM_20d_strict`: `total_return=0.116793`, `excess_return=-0.095108`, `max_drawdown=-0.175942`, `sharpe=0.684917`, turnover `29.669327`.
  - `IM_20d_top30`: `total_return=0.112428`, `excess_return=-0.099473`, `max_drawdown=-0.185211`, `sharpe=0.651254`, turnover `28.970167`.
- 2026-06-30: `IM_60d_top30` 2024 OOS validation passed:
  - Default costs: `total_return=0.21141`, `benchmark_return=0.161991`, `excess_return=0.049419`, `max_drawdown=-0.182329`, `sharpe=0.914687`, turnover `17.874486`.
  - Zero costs: `total_return=0.233385`, `benchmark_return=0.161991`, `excess_return=0.071394`, `max_drawdown=-0.171926`, `sharpe=0.990564`, turnover `18.006957`.
- 2026-06-30: `IM_60d_top30` cost stress matrix:

| Year | Cost case | Commission | Slippage | Total return | Benchmark | Excess | Max drawdown | Sharpe | Turnover |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2025 | zero | 0 | 0 | 0.248017 | 0.211901 | 0.036116 | -0.090952 | 1.617692 | 23.088173 |
| 2025 | ideal | 0.0001 | 0.0003 | 0.223670 | 0.211901 | 0.011769 | -0.097440 | 1.481739 | 22.808936 |
| 2025 | default | 0.0003 | 0.0005 | 0.216990 | 0.211901 | 0.005089 | -0.099372 | 1.441734 | 22.723315 |
| 2025 | retail | 0.0005 | 0.0010 | 0.200837 | 0.211901 | -0.011064 | -0.103855 | 1.344706 | 22.568798 |
| 2025 | stress | 0.0005 | 0.0020 | 0.178434 | 0.211901 | -0.033467 | -0.110253 | 1.208521 | 22.415773 |
| 2024 | zero | 0 | 0 | 0.233385 | 0.161991 | 0.071394 | -0.171926 | 0.990564 | 18.006957 |
| 2024 | ideal | 0.0001 | 0.0003 | 0.216489 | 0.161991 | 0.054498 | -0.179979 | 0.932726 | 17.913608 |
| 2024 | default | 0.0003 | 0.0005 | 0.211410 | 0.161991 | 0.049419 | -0.182329 | 0.914687 | 17.874486 |
| 2024 | retail | 0.0005 | 0.0010 | 0.192560 | 0.161991 | 0.030568 | -0.187858 | 0.852389 | 17.733874 |
| 2024 | stress | 0.0005 | 0.0020 | 0.170400 | 0.161991 | 0.008409 | -0.195494 | 0.777426 | 17.544484 |

## Current P0 Read

- `stable_reversal` has weak gross alpha in 2025: it barely beats 000300 before costs.
- Default real-world costs erase the edge because turnover is extremely high.
- The next optimization target is not simply higher return; it is lower turnover with similar gross return.
- Prioritize grid dimensions that reduce churn: higher `top_n`, higher `min_reversal`, stricter `max_amount_ratio`, longer rebalance interval with hysteresis, and a future hold-buffer rule.
- The first hysteresis/grid round improved drawdown and cut turnover by more than half, but did not restore benchmark outperformance. Treat `stable_reversal` as a defensive sleeve candidate, not the main profit engine.
- Next profit search should shift toward inverse momentum, low-vol defensive, and LightGBM factor blending instead of overfitting this one rule.
- `inverse_momentum` is now the strongest simple rule candidate: 60-day laggard selection produced the first default-cost positive excess result in 2025, while 20-day variants were weak. Next verify 2024 and run cost stress before promoting.
- `IM_60d_top30` passed 2024 OOS and 2024 cost stress, but 2025 retail/stress costs turn excess negative. This is a viable research candidate, not yet a paper/live candidate. Next reduce execution drag or add a stronger alpha layer.
- Promotion gate still incomplete: benchmark comparison is only against `000300`; must compare against `000905` and `000852`, and PIT universe limitations remain unresolved.
