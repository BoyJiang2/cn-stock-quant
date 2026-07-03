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
- 2026-06-30: Started the parallel factor/ML round requested by the user:
  - Codex owns 2026 verification, engineering integration, tests, and final result logging.
  - GLM-side agent owns public factor research and wrote `docs/agent-glm-factor-research.md`.
  - DeepSeek-side agent owns LightGBM and strategy-risk design and wrote `docs/agent-deepseek-ml-plan.md`.
  - Main handoff doc: `docs/factor-ml-parallel-plan.md`.
- 2026-06-30: Expanded built-in factor count from 24 to 34 with the first OHLCV-only research batch:
  `money_flow_proxy_20d`, `amount_volatility_20d`, `low_vol_reversal_20d`,
  `breakout_strength_20d`, `drawdown_recovery_20d`, `close_position_20d`,
  `price_efficiency_20d`, `intraday_momentum_20d`, `overnight_gap_20d`,
  and `tail_risk_20d`.
- 2026-06-30: Factor test suite passed after the expansion: `101 passed`.
- 2026-06-30: Small smoke factor experiment passed for 2025-01-01 to 2025-03-31,
  300 symbols, 34 factors, 17,100 factor rows. Best new factors in this small
  smoke sample were `low_vol_reversal_20d` (`rankic_mean=0.065046`,
  `long_short_return=0.005517`) and `amount_volatility_20d`
  (`rankic_mean=0.053649`, `long_short_return=0.003788`). This is only a
  plumbing/sanity check, not a promotion result.
- 2026-06-30: Full 2025 new-factor screen completed for 10 new factors,
  5,071 selected symbols and 1,232,253 factor rows. Best new factors:
  `amount_volatility_20d` (`rankic_mean=0.057144`, `rankic_ir=0.986665`,
  `long_short_return=0.003475`) and `low_vol_reversal_20d`
  (`rankic_mean=0.050335`, `rankic_ir=0.412821`,
  `long_short_return=0.002705`). Treat them as next-batch strategy/ML inputs,
  not live signals.
- 2026-06-30: Added `multi_factor_rank`, the first combined factor strategy
  using low amount volatility, low-vol reversal, amount stability, inverse
  momentum, and tail-risk. It keeps the existing target-weight contract and
  includes a hold-rank buffer for turnover control.
- 2026-06-30: Added `backend/build_ml_dataset.py`, a standalone first-stage
  ML dataset builder. It exports date/symbol factor features after per-date
  robust standardisation plus T+1-entry forward-return labels (`fwd_5d` and
  `fwd_10d` by default). This is the fixed boundary for later LightGBM work.
- 2026-06-30: `multi_factor_rank` 2025 full-market default-cost run,
  10-day rebalance, `top_n=30`, `hold_rank_multiplier=1.3`:
  `total_return=0.302000`, `max_drawdown=-0.052133`, `sharpe=2.443343`,
  turnover `30.918479`.
  - vs `000300`: benchmark `0.211901`, excess `0.090099`.
  - vs `000905`: benchmark `0.346163`, excess `-0.044163`.
  - vs `000852`: benchmark `0.310189`, excess `-0.008189`.
  Read: strong absolute return and drawdown control, but not yet a
  promotion-passing strategy because it does not beat the stronger 2025
  small/mid-cap benchmarks.
- 2026-06-30: Added `backend/train_lightgbm_ranker.py`, an optional-dependency
  LightGBM trainer for datasets produced by `build_ml_dataset.py`. It writes
  prediction scores, RankIC/top-bottom metrics, and an optional model artifact.
  `lightgbm>=4.3.0` is now recorded in backend requirements; existing tests do
  not import LightGBM unless the CLI is run.
- 2026-06-30: `multi_factor_rank` 2025 parameter grid against `000300`:
  - `base_top30`: return `0.302000`, excess `0.090099`, drawdown `-0.052133`,
    Sharpe `2.443343`, turnover `30.918479`.
  - `wider_top50`: return `0.279733`, excess `0.067832`, drawdown
    `-0.045923`, Sharpe `2.473733`, turnover `28.702071`.
  - `lower_turnover_top50`: return `0.276889`, excess `0.064989`, drawdown
    `-0.045391`, Sharpe `2.463848`, turnover `27.579726`.
  - `amount_stability_heavy`: return `0.269328`, excess `0.057427`,
    drawdown `-0.058846`, Sharpe `2.345957`, turnover `30.723220`.
  - `reversal_heavy`: return `0.228379`, excess `0.016478`, drawdown
    `-0.055697`, Sharpe `2.019039`, turnover `29.434601`.
  Read: `base_top30` is best on return/excess, while top50 variants are
  better drawdown/turnover compromises. Reversal-heavy weighting weakens the
  edge.
- 2026-06-30: `multi_factor_rank` 2025 cost stress against `000300`,
  `top_n=30`, `hold_rank_multiplier=1.3`:

| Cost case | Commission | Slippage | Total return | Benchmark | Excess | Max drawdown | Sharpe | Turnover |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| zero | 0 | 0 | 0.346251 | 0.211901 | 0.134350 | -0.047858 | 2.760517 | 31.372807 |
| default | 0.0003 | 0.0005 | 0.302000 | 0.211901 | 0.090099 | -0.052133 | 2.443343 | 30.918479 |
| retail | 0.0005 | 0.0010 | 0.280530 | 0.211901 | 0.068629 | -0.056543 | 2.284107 | 30.637954 |
| stress | 0.0005 | 0.0020 | 0.248119 | 0.211901 | 0.036219 | -0.064113 | 2.037551 | 30.236223 |

  Read: the edge survives high costs versus `000300`, but turnover around 30x
  initial capital is too high for promotion. Next research should target lower
  rebalance frequency, wider hold buffers, and execution-aware selection.
- 2026-07-02: Added `backend/watch_full_market_sync.py`, a wrapper that
  relaunches `sync_full_market.py` with the same state file after recoverable
  exits. This is meant to replace manual PID restarts during long full-market
  syncs. It stops when state reports `completed` or remaining coverage reaches
  zero.
- 2026-07-02: Added `entry_rank_multiplier` to `multi_factor_rank` so new
  entries can be restricted to a tighter rank cutoff while existing holdings
  can remain inside the wider `hold_rank_multiplier` buffer. Smoke backtest
  passed for 2025Q1 with `top_n=20`, `momentum_window=20`,
  `reversal_window=10`, `hold_rank_multiplier=1.5`,
  `entry_rank_multiplier=1.2`: return `0.037544`, excess vs `000300`
  `0.020030`, drawdown `-0.028537`, turnover `4.329794`.
- 2026-07-02: Validated local 2026 data after the full-market sync watcher
  stopped. State progress is 99.98%: 5,515 total symbols, 5,514 symbols with
  2026 bars, 5,158 symbols with full 109-trading-day coverage, and 5,167
  symbols with at least 100 bars. `000300`, `000905`, and `000852` each have
  109 local 2026 bars from 2026-01-05 to 2026-06-18. Remaining blocked symbol:
  `000638`.
- 2026-07-02: Ran a 2026 plumbing smoke on 300 selected symbols:
  - factor smoke with 5 factors produced 85,400 bar rows and 32,700 factor
    panel rows.
  - `multi_factor_rank` smoke against `000300`, 2026-01-01 to 2026-06-18,
    `top_n=20`, `momentum_window=20`, `reversal_window=10`,
    `hold_rank_multiplier=1.5`, `entry_rank_multiplier=1.2`:
    `total_return=-0.122582`, benchmark `0.047449`, excess `-0.170031`,
    max drawdown `-0.157061`, Sharpe `-1.889656`, turnover `8.834497`.
  Read: the 2026 data pipeline is usable, but this reduced smoke strategy is
  not profitable. Do not promote it; use it only as evidence that the data,
  benchmark, factor, and strategy paths run end to end.
- 2026-07-02: Added `backend/run_2026_research_pipeline.py`, a thin
  orchestration script for reproducible 2026 factor and strategy batches.
  It writes JSON outputs under `backend/research_runs/2026/` and a Markdown
  summary for quick/full factor and strategy runs.
- 2026-07-02: Ran 2026 full-market factor screens with 5-day labels:
  - all 34 built-ins: 5,188 selected symbols, 1,468,109 bar rows, 565,492
    factor panel rows.
  - 10-factor new batch: same selected universe and 565,492 factor panel rows.
  Top full-market 2026 factors:
  - `amount_stability_20d`: RankIC `0.046843`, ICIR `0.709822`,
    long-short `0.004579`.
  - `amount_volatility_20d`: RankIC `0.044621`, ICIR `0.708614`,
    long-short `0.004904`.
  - `overnight_gap_20d`: RankIC `0.035523`, ICIR `0.492833`,
    long-short `0.005971`.
  Read: amount stability/amount volatility survived from 2025 into 2026 and
  should be first-class features for the next strategy/ML round. Volatility
  family factors have positive RankIC but negative long-short spreads, so they
  should be handled as risk filters until direction is audited.
- 2026-07-02: Ran 2026 full-market strategy matrix for current low-turnover
  variants across `000300`, `000905`, and `000852`, with default/retail/stress
  costs. Default-cost results:
  - `multi_factor_rank`, 5,249 selected symbols: return `-0.055230`, max
    drawdown `-0.117898`, Sharpe `-0.779722`, turnover `11.680169`.
    Excess was `-0.102679` vs `000300`, `-0.188790` vs `000905`, and
    `-0.186409` vs `000852`.
  - `inverse_momentum`, 5,249 selected symbols: return `-0.085279`, max
    drawdown `-0.128037`, Sharpe `-1.788838`, turnover `4.173801`.
    Excess was `-0.132728` vs `000300`, `-0.218839` vs `000905`, and
    `-0.216458` vs `000852`.
  Read: both current rule strategies fail 2026 OOS. Keep them as research
  baselines only. The next profit-directed path is new factor implementation
  plus LightGBM ranking, not further promotion of these parameter sets.
- 2026-07-02: GLM-side worker implemented six OHLCV/amount factors:
  `upper_shadow_20d`, `lower_shadow_20d`, `close_location_20d`, `rsv_20d`,
  `amount_shock_z_20d`, and `reversal_10d`. Factor tests reported by the
  worker: targeted factor tests `33 passed`; full factor test folder
  `105 passed`.
- 2026-07-02: Ran 2026 full-market screen for the six new Batch F factors,
  5,188 selected symbols and 565,492 factor panel rows:
  - `lower_shadow_20d`: RankIC `0.024427`, ICIR `0.263740`,
    long-short `-0.001805`.
  - `amount_shock_z_20d`: RankIC `0.020580`, ICIR `0.194871`,
    long-short `0.000958`, turnover `0.470583`.
  - `reversal_10d`: RankIC `0.018182`, ICIR `0.150068`,
    long-short `-0.002688`.
  - `close_location_20d`: RankIC `0.017003`, ICIR `0.171885`,
    long-short `0.004716`.
  Read: these are not standalone alpha winners. Keep `lower_shadow_20d`,
  `close_location_20d`, and possibly `amount_shock_z_20d` as ML/strategy
  features, but do not promote them directly. `amount_shock_z_20d` is
  especially turnover-heavy.
- 2026-07-02: DeepSeek-side worker added the PIT trade-gap foundation:
  `SecurityTradeGap`, `PIT_TRADE_GAP_TYPES`, repository upsert/query helpers,
  and coverage reporting for `security_trade_gap_rows` and `provider_gap_rows`.
  Worker test result: `49 passed` for `backend/tests/test_point_in_time.py`.
- 2026-07-02: Installed LightGBM 4.6.0 in the active environment using
  `python -m pip install lightgbm==4.6.0 --no-deps`. Full requirements install
  is currently blocked by a broken local `certifi` METADATA file, so the
  no-deps install was used because numpy/scipy were already present.
- 2026-07-02: Built the full 2024-2026 ML dataset:
  `backend/artifacts/ml/ml-factor-dataset-2024-2026.csv`, 2,546,137 rows,
  4,968 symbols, 528 dates, and 40 factors. This file is intentionally local
  artifact data and is ignored by Git.
- 2026-07-02: Trained first static `fwd_5d` LightGBM model with train
  2024-01-02 to 2024-09-30 and validation 2024-10-01 to 2024-12-31:
  - validation: RankIC `0.055625`, ICIR `0.520983`, long-short `0.000052`.
  - 2025 OOS: RankIC `0.037680`, ICIR `0.552200`, long-short `0.001958`.
  - 2026 OOS: RankIC `0.001870`, ICIR `0.028125`, long-short `-0.002121`.
  Read: the ML pipe is functional, but the first static model fails 2026.
  It should not be treated as a strategy candidate until prediction scores are
  converted into target weights and pass walk-forward/cost/benchmark tests.
- 2026-07-03: Added `ml_score_rank`, a read-only score-consumer strategy that
  maps offline prediction CSV rows (`trade_date`, `symbol`, `score`) into
  equal target weights using exact decision-date lookup. It does not forward
  fill missing score dates. It keeps holdings only if they remain inside the
  same-day hold-rank buffer, and it normalizes numeric stock symbols to
  6-character A-share codes.
- 2026-07-03: Hardened ML symbol handling at the source: `build_ml_dataset.py`
  now normalizes dataset symbols, and `train_lightgbm_ranker.py` reads/writes
  symbols as strings and normalizes prediction output. This fixes the
  `000001` -> `1` CSV inference risk called out by the DeepSeek-side review.
- 2026-07-03: Ran an initial diagnostic ML-score backtest through 2026-06-18.
  It failed badly (`ml_score_rank`, top30, 5-day rebalance, `000300`,
  default cost): return `-0.104774`, benchmark `0.047449`, excess
  `-0.152223`, drawdown `-0.171754`, turnover `20.941958`. QA then correctly
  flagged that the prediction test split only covers 2026-01-05 to
  2026-06-10, so this was kept as diagnostic only.
- 2026-07-03: Ran the fair primary 2026 ML-score matrix over
  2026-01-05 to 2026-06-10, with the same 10-day rebalance window for
  `ml_score_rank`, `multi_factor_rank`, and `inverse_momentum`, across
  `000300`, `000905`, `000852`, and default/retail/stress costs. Default-cost
  reads:
  - `ml_score_rank`: return `-0.147360`, drawdown `-0.163621`, Sharpe
    `-2.194948`, turnover `11.475295`. Excess was `-0.153899` vs `000300`,
    `-0.201295` vs `000905`, and `-0.204731` vs `000852`.
  - `multi_factor_rank`: return `-0.069764`, drawdown `-0.114147`, Sharpe
    `-1.094180`, turnover `11.680169`. Excess was `-0.076303` vs `000300`.
  - `inverse_momentum`: return `-0.100637`, drawdown `-0.128037`, Sharpe
    `-2.396430`, turnover `4.173801`. Excess was `-0.107176` vs `000300`.
  Read: the static LightGBM score strategy fails the fair 2026 matrix and is
  worse than the old baselines. Keep `ml_score_rank` as reusable infrastructure,
  but do not tune or promote this static model. Next ML work should be
  walk-forward retraining, feature importance review, and regime-specific
  factor/model diagnosis.
- 2026-07-03: Added `backend/analyze_ml_predictions.py`, an offline diagnostic
  report for prediction CSVs. Static 2026 LightGBM diagnosis:
  - coverage: 497,002 rows, 4,965 symbols, 103 dates from 2026-01-05 to
    2026-06-10.
  - RankIC `0.001870`, ICIR `0.028125`.
  - 5-bucket long-short return `-0.002055`.
  - highest score bucket mean return `-0.001154`; lowest score bucket
    `0.000900`.
  - top feature importance was concentrated in `drawdown_recovery_20d`,
    `max_drawdown_60d`, `ma_gap_20d`, and `close_position_20d`.
  Read: the static model failed before portfolio construction. It was not a
  top-N/hold-buffer issue.
- 2026-07-03: Added `backend/walk_forward_lightgbm.py`, a rolling LightGBM
  prediction generator. DeepSeek review caught an important leakage issue:
  `fwd_5d` validation labels use future prices, so adjacent valid/test windows
  can leak through early stopping. The script now defaults to a 15-calendar-day
  embargo between train, validation, and test windows. Any pre-embargo
  walk-forward output is diagnostic only and must not be used for acceptance.
- 2026-07-03: Ran embargoed 2026 walk-forward LightGBM:
  - v40 factor set: RankIC `0.033424`, ICIR `0.424144`, bucket long-short
    `0.003678`, but target-weight strategy still lost money.
  - v45 factor set after GLM batch G1: RankIC `0.032226`, ICIR `0.424468`,
    bucket long-short `0.001989`.
- 2026-07-03: GLM-side worker implemented batch G1 OHLCV factors:
  `linear_slope_20d`, `trend_rsquare_20d`, `trend_residual_20d`,
  `volume_return_divergence_20d`, and `price_rank_20d`, bringing the built-in
  factor count to 45. Worker-reported tests: targeted factor tests
  `39 passed`; full factor tests `111 passed`.
- 2026-07-03: Ran 2026 batch G1 factor screen:
  - `trend_rsquare_20d`: RankIC `0.029669`, ICIR `0.409160`,
    long-short `0.004071`.
  - `trend_residual_20d`: RankIC `0.016687`, ICIR `0.126297`,
    long-short `0.003873`.
  - `linear_slope_20d`: RankIC `0.002735`, ICIR `0.020569`,
    long-short `0.008228`.
  - `volume_return_divergence_20d`: RankIC `-0.029203`, ICIR `-0.295432`.
  Read: `trend_rsquare_20d` is the cleanest G1 candidate. The negative
  `volume_return_divergence_20d` direction needs factor-cemetery or inverse
  treatment before reuse.
- 2026-07-03: Built v45 ML dataset:
  `backend/artifacts/ml/ml-factor-dataset-2024-2026-v45.csv`, 2,546,137 rows,
  4,968 symbols, 528 dates, 45 factors. This is a local artifact ignored by
  Git.
- 2026-07-03: Walk-forward strategy results, fair window 2026-01-05 to
  2026-06-10:
  - v40, 10-day rebalance, default cost: return `-0.073752`, excess vs
    `000300` `-0.080290`, drawdown `-0.148320`, turnover `9.620410`.
  - v45, 10-day rebalance, default cost: return `0.011782`, excess vs
    `000300` `0.005244`, drawdown `-0.140875`, turnover `10.161311`.
  - v45, 20-day rebalance with `hold_rank_multiplier=1.6`, default cost:
    return `0.015474`, excess vs `000300` `0.008936`, excess vs `000905`
    `-0.038461`, excess vs `000852` `-0.041896`, drawdown `-0.139009`,
    turnover `6.314782`.
  Read: this is the first ML research candidate with positive 2026 return and
  positive excess over `000300`, but it still fails the promotion gate because
  it does not beat `000905`/`000852`, drawdown is high, and all runs remain
  non-PIT/degraded.
- 2026-07-03: Static LightGBM failure analysis completed in
  `docs/agent-static-lightgbm-failure-analysis.md`. Root cause ranking:
  - failure happened at the model-ranking layer, not only in portfolio
    construction: 2026 RankIC `0.001870`, IR `0.028125`, top bucket minus
    bottom bucket `-0.002055`.
  - model over-relied on stale price-state features from 2024:
    `drawdown_recovery_20d` contributed roughly `41.1%` of gain, top three
    features about `67.9%`, and only 13 of 40 features had non-zero
    importance.
  - fixed train/valid windows were too old for 2026; embargoed walk-forward
    improved RankIC to about `0.032`.
  - portfolio top-N concentrated the bad high-score tail: static top30 average
    `fwd_5d` was `-0.004445`, about `-0.004037` below the broad universe.
  Read: reject the static artifact permanently. Keep `ml_score_rank` as the
  score-to-portfolio adapter, but require walk-forward + embargo + bucket
  prechecks before any strategy grid.
- 2026-07-03: Added `backend/analyze_backtest_monthly.py` and reran the best
  current v45 walk-forward candidate with `--include-curves`. Monthly excess
  versus `000300`:
  - positive excess months: 2
  - negative excess months: 4
  - mean monthly excess `-0.001388`
  - worst monthly excess `-0.039669`
  - best monthly excess `0.063059`
  - worst month was 2026-03: strategy `-0.098590`, benchmark `-0.058921`,
    excess `-0.039669`, min drawdown `-0.139009`
  Read: the current ML candidate's headline positive return hides unstable
  monthly behavior. March 2026 is the next failure slice to investigate.
- 2026-07-03: Added optional strategy-side risk-filter hooks to
  `ml_score_rank`:
  - `trade_gap_path` and `exclude_gap_types` can block symbols with same-date
    suspension/provider-gap/unknown trade-gap rows.
  - `negative_news_path`, `negative_news_lookback_days`,
    `negative_news_min_relevance`, and `negative_news_max_sentiment` can block
    symbols with recent known negative news.
  These are disabled by default and need populated CSV inputs before measuring
  actual effect.
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
