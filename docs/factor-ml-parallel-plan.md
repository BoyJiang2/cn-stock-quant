# Factor and ML parallel execution plan

This is the handoff document for the next development rounds. The goal is to
turn the current factor lab into a profit-first research loop:

1. discover candidate factors from public research and platform examples;
2. implement only factors that can be computed without look-ahead;
3. validate them across 2024, 2025, and 2026;
4. feed surviving factors into LightGBM ranking;
5. promote only strategies that survive realistic costs and multiple
   benchmarks.

## Current Parallel Tracks

| Track | Owner | Status | Output |
| --- | --- | --- | --- |
| 2026 data and strategy verification | Codex | running | 2026 full-market sync, 000300/000905/000852 validation |
| Public factor research | GLM-side agent | running | `docs/agent-glm-factor-research.md` |
| ML and strategy review | DeepSeek-side agent | running | `docs/agent-deepseek-ml-plan.md` |
| Engineering implementation | Codex | running | factor code, tests, backtest/experiment results |

## Public Factor Sources To Learn From

Use only public, accessible material. Do not bypass logins, scrape paid data,
or copy proprietary datasets.

| Source | Useful Ideas | Engineering Notes |
| --- | --- | --- |
| Qlib Alpha158 / Alpha360 | OHLCV rolling features, normalized price ratios, volume transforms, rank-style features | Good first reference because it is open-source and mostly price/volume based |
| JoinQuant public community / Alpha101 references | Formula factors, correlations, rolling ranks, volume-price interaction | Implement simplified, PIT-safe variants first |
| Tonghuashun / iFinD public indicator ideas | money flow, popularity, industry strength, concept heat | Use as inspiration; many fields require licensed data |
| Public sell-side factor reports | reversal, momentum, low volatility, liquidity, quality, value, growth | Separate implementable OHLCV factors from financial-statement factors |
| Open-source A-share projects | strategy construction and benchmark methodology | Reuse ideas, not opaque results |

## First Engineering Batch

Added on 2026-06-30. These factors are all local OHLCV/amount factors, so they
can run before financial statement and sentiment data are integrated.

| Factor | Purpose | Required Data | Initial Direction |
| --- | --- | --- | --- |
| `money_flow_proxy_20d` | signed traded-amount imbalance | close, amount | positive |
| `amount_volatility_20d` | unstable trading-amount changes | amount | negative |
| `low_vol_reversal_20d` | reversal adjusted by realized volatility | close | positive |
| `breakout_strength_20d` | distance to trailing high | high, close | positive |
| `drawdown_recovery_20d` | recovery from trailing low | close | positive |
| `close_position_20d` | close position in high-low channel | high, low, close | positive |
| `price_efficiency_20d` | smoothness of trend path | close | positive |
| `intraday_momentum_20d` | close vs open pressure | open, close | positive |
| `overnight_gap_20d` | persistent overnight gap | open, close | positive |
| `tail_risk_20d` | left-tail daily-return risk | close | positive |

## First Validation Results

Smoke run:

- Period: 2025-01-01 to 2025-03-31
- Universe cap: 300 symbols
- Factors: 34
- Result: plumbing passed; this was only a sanity check.

Full 2025 new-factor screen:

- Period: 2025-01-01 to 2025-12-31
- Selected symbols: 5,071
- Bar rows: 2,112,845
- Factor panel rows: 1,232,253
- Factors tested: 10 new factors only

| Factor | RankIC Mean | RankIC IR | Long-Short Return | Read |
| --- | ---: | ---: | ---: | --- |
| `amount_volatility_20d` | 0.057144 | 0.986665 | 0.003475 | promote to next strategy/ML batch |
| `low_vol_reversal_20d` | 0.050335 | 0.412821 | 0.002705 | promote to next strategy/ML batch |
| `tail_risk_20d` | 0.031996 | 0.169009 | -0.002405 | risk filter candidate, not raw alpha yet |
| `overnight_gap_20d` | 0.015104 | 0.197258 | 0.001556 | weak watchlist |
| `price_efficiency_20d` | -0.000415 | -0.004825 | 0.000445 | neutral |
| `breakout_strength_20d` | -0.003707 | -0.023795 | -0.003225 | likely not useful directly |
| `close_position_20d` | -0.032818 | -0.313678 | -0.001706 | possible inverse/filter only |
| `money_flow_proxy_20d` | -0.034965 | -0.422946 | -0.001728 | possible inverse/crowding warning |
| `drawdown_recovery_20d` | -0.066915 | -0.555102 | -0.001503 | avoid direct long signal |
| `intraday_momentum_20d` | -0.076348 | -0.598190 | -0.005201 | avoid direct long signal; possible reversal signal |

Immediate factor read:

- `amount_volatility_20d` is the strongest new signal in 2025. Because the
  registered direction is negative, the positive adjusted RankIC means lower
  amount volatility is preferred.
- `low_vol_reversal_20d` confirms the current project direction: reversal
  works better when scaled by risk.
- `intraday_momentum_20d`, `drawdown_recovery_20d`, and `money_flow_proxy_20d`
  look like crowding/overheating signals in 2025 rather than direct long
  alpha.
- None of these are promoted to paper/live until 2024 and 2026 checks pass.

## First Combined Strategy Result

`multi_factor_rank` was added as the first hand-built bridge between the factor
screen and ML ranking:

- low `amount_volatility_20d`
- high `low_vol_reversal_20d`
- high `amount_stability_20d`
- inverse `momentum_60d`
- high `tail_risk_20d`

2025 full-market default-cost run:

| Benchmark | Strategy Return | Benchmark Return | Excess | Max Drawdown | Sharpe | Turnover |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `000300` | 0.302000 | 0.211901 | 0.090099 | -0.052133 | 2.443343 | 30.918479 |
| `000905` | 0.302000 | 0.346163 | -0.044163 | -0.052133 | 2.443343 | 30.918479 |
| `000852` | 0.302000 | 0.310189 | -0.008189 | -0.052133 | 2.443343 | 30.918479 |

Read:

- The strategy is materially better than `stable_reversal` on 2025 absolute
  return and drawdown.
- It beats `000300`, but not the stronger 2025 `000905` and `000852`
  benchmarks.
- It is a good ML baseline and factor-combination candidate, but not a
  paper/live candidate yet.

Promotion rules:

- A factor can enter a strategy only after 2024/2025/2026 RankIC checks.
- A factor with strong negative RankIC is not discarded automatically; evaluate
  inverse use.
- Risk filters can be promoted without high long-short return if they reduce
  drawdown and turnover.
- Any factor with unclear as-of availability is kept out of live/paper paths.

## LightGBM First Version

Target implementation:

- Build feature matrix from the factor panel.
- Label with future 5-day and 10-day returns.
- Use time splits only:
  - train: 2024 or earlier available data;
  - validation: 2025;
  - test: 2026 when sync is complete.
- Produce per-date prediction scores.
- Convert scores into `dict[symbol -> weight]` using Top N selection.
- Compare against `inverse_momentum` and `stable_reversal`.

Engineering status:

- `backend/build_ml_dataset.py` now implements the first dataset boundary.
- It writes a CSV dataset plus JSON metadata.
- Features are adjusted by the registered factor direction before per-date
  robust standardisation.
- Labels use the existing T+1-entry forward return implementation.
- `backend/train_lightgbm_ranker.py` now implements the first optional
  LightGBM training boundary. It consumes the dataset CSV, uses date-only
  splits, writes validation/test prediction scores, reports daily RankIC and
  top-bottom return, and can save a model artifact.
- `lightgbm>=4.3.0` is recorded in backend requirements. The dependency is only
  needed when running the trainer CLI.

Smoke result:

- Command generated `16,896` rows for 2025Q1, 300 symbols, five selected
  factors, and `fwd_5d`/`fwd_10d` labels.

Trainer CLI shape:

```powershell
python build_ml_dataset.py --start-date 2024-01-01 --end-date 2026-06-18 --pool-max-symbols 6000 --output ml-factor-dataset.csv
python train_lightgbm_ranker.py --dataset ml-factor-dataset.csv --label fwd_5d --train-start 2024-01-01 --train-end 2024-09-30 --valid-start 2024-10-01 --valid-end 2024-12-31 --test-start 2025-01-01 --test-end 2025-12-31 --predictions-output lightgbm-predictions.csv --metrics-output lightgbm-metrics.json
```

Leakage controls:

- Features at date `t` must only use data up to `t`.
- Labels must be shifted forward and never included in features.
- Universe membership and ST/listing filters must eventually be point-in-time.
- Do not tune on 2026 and then claim 2026 as out-of-sample.

## Next Validation Commands

Run a small smoke experiment first:

```powershell
cd D:\CursorProjects\cn-stock-quant\backend
python run_factor_experiment.py --start-date 2025-01-01 --end-date 2025-03-31 --pool-max-symbols 300 --output factor-smoke-new-batch.json
```

After the smoke run passes, run the full 2025 factor screen:

```powershell
python run_factor_experiment.py --start-date 2025-01-01 --end-date 2025-12-31 --pool-max-symbols 6000 --output factor-2025-expanded.json
```

When the 2026 sync completes, rerun the same experiment for 2026 and compare
against 2025 before promoting any new factor.

## 2026 Sync Watch

The 2026 full-market sync is still running from:

```powershell
python sync_full_market.py --start-date 2026-01-01 --end-date 2026-06-18 --batch-size 20 --interval 0.2 --state-file full-market-sync-2026.state.json
```

Check progress with:

```powershell
Get-Content D:\CursorProjects\cn-stock-quant\backend\full-market-sync-2026.state.json
```
