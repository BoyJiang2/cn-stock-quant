# Next Development Task Board

Date: 2026-07-02

This document is the task board for future agents. Update it after every
development round. The goal is not to collect ideas; it is to keep the work
aligned with the profit-first objective:

> Find strategies that can survive realistic costs, multiple benchmarks,
> out-of-sample years, and eventually paper trading.

## Current State

The project has moved from a demo backtester into a usable research workflow:

- full-market daily-bar sync is available and resumable;
- factor lab can compute 34 built-in OHLCV/amount factors;
- factor experiments can run on thousands of A-share symbols;
- reproducible strategy backtest CLI exists;
- initial strategy candidates exist;
- ML dataset and LightGBM trainer boundaries exist;
- agent handoff docs exist for GLM factor research and DeepSeek ML review.

Current 2026 data validation:

- state file: `backend/full-market-sync-2026.state.json`
- PID file: `backend/full-market-sync-2026.pid`
- latest checked progress on 2026-07-02: 99.98% complete
- the watcher/sync process is no longer running
- local bar overview: 5,515 symbols, 3,187,593 bars, date range
  2024-01-02 to 2026-06-18
- 2026 benchmark bars are available for `000300`, `000905`, and `000852`
  with 109 trading days from 2026-01-05 to 2026-06-18
- 2026 A-share coverage:
  - 5,514 symbols have at least one 2026 bar
  - 5,220 symbols have at least 60 2026 bars
  - 5,167 symbols have at least 100 2026 bars
  - 5,158 symbols have full 109-day coverage
- known issue: `000638` is the remaining circuit-breaker symbol and needs
  later retry
- caveat: missing bars can be valid suspensions/new listings/delistings or
  provider gaps. Historical listing/ST/index-membership point-in-time tables
  are still required before treating results as production-grade.

## What Has Been Done

### Data Infrastructure

- Fixed research-pool coverage problems that caused empty-stock-pool errors.
- Fixed missing benchmark behavior for `000300` and synced key index data.
- Synced 2025 `000905` and `000852` index bars so small/mid-cap benchmarks can
  be used.
- Added resumable full-market daily-bar sync:
  - `backend/sync_full_market.py`
  - state file updated after each batch
  - can resume from partial progress
- Added sync watcher:
  - `backend/watch_full_market_sync.py`
  - restarts `sync_full_market.py` after recoverable exits

### Factor Research

- Expanded built-in factor count from 24 to 34.
- Added first OHLCV-only public-factor-inspired batch:
  - `money_flow_proxy_20d`
  - `amount_volatility_20d`
  - `low_vol_reversal_20d`
  - `breakout_strength_20d`
  - `drawdown_recovery_20d`
  - `close_position_20d`
  - `price_efficiency_20d`
  - `intraday_momentum_20d`
  - `overnight_gap_20d`
  - `tail_risk_20d`
- Ran 2025 full-market screen on the 10 new factors.
- Best new factors in 2025:
  - `amount_volatility_20d`: RankIC `0.057144`, IR `0.986665`
  - `low_vol_reversal_20d`: RankIC `0.050335`, IR `0.412821`
- Useful interpretation:
  - lower amount volatility looks good;
  - reversal works better when volatility-scaled;
  - intraday momentum, drawdown recovery, and money-flow proxy looked more
    like overheating/crowding warnings in 2025.

### Strategy Research

Implemented and evaluated:

- `stable_reversal`
- `inverse_momentum`
- `multi_factor_rank`

Important current reads:

- `stable_reversal` is defensive but not strong enough as a main profit engine.
- `inverse_momentum` passed some 2024 checks but is cost-sensitive in 2025.
- `multi_factor_rank` is the strongest current combined rule strategy.

`multi_factor_rank` 2025 full-market result, default costs, 10-day rebalance:

| Benchmark | Strategy Return | Benchmark Return | Excess | Max Drawdown | Sharpe | Turnover |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `000300` | 0.302000 | 0.211901 | 0.090099 | -0.052133 | 2.443343 | 30.918479 |
| `000905` | 0.302000 | 0.346163 | -0.044163 | -0.052133 | 2.443343 | 30.918479 |
| `000852` | 0.302000 | 0.310189 | -0.008189 | -0.052133 | 2.443343 | 30.918479 |

Read:

- It beats `000300` clearly.
- It does not beat 2025 `000905` or `000852`.
- It has strong drawdown control.
- Turnover is still too high for paper/live promotion.

2026 full-market OOS result, 2026-01-01 to 2026-06-18:

| Strategy | Benchmark | Default Return | Benchmark Return | Excess | Max Drawdown | Sharpe | Turnover |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `multi_factor_rank` | `000300` | -0.055230 | 0.047449 | -0.102679 | -0.117898 | -0.779722 | 11.680169 |
| `multi_factor_rank` | `000905` | -0.055230 | 0.133560 | -0.188790 | -0.117898 | -0.779722 | 11.680169 |
| `multi_factor_rank` | `000852` | -0.055230 | 0.131179 | -0.186409 | -0.117898 | -0.779722 | 11.680169 |
| `inverse_momentum` | `000300` | -0.085279 | 0.047449 | -0.132728 | -0.128037 | -1.788838 | 4.173801 |
| `inverse_momentum` | `000905` | -0.085279 | 0.133560 | -0.218839 | -0.128037 | -1.788838 | 4.173801 |
| `inverse_momentum` | `000852` | -0.085279 | 0.131179 | -0.216458 | -0.128037 | -1.788838 | 4.173801 |

Read:

- Both current rule strategies failed 2026 OOS.
- `multi_factor_rank` remains useful as a research benchmark and factor
  blending baseline only.
- `inverse_momentum` remains lower-turnover, but its 2026 loss means it also
  cannot be promoted.
- Next profit search should shift to new 2026-confirmed amount/liquidity
  factors and LightGBM ranking instead of tuning these two old rules.

### ML Work

- Added dataset generator:
  - `backend/build_ml_dataset.py`
  - exports date/symbol factor features and T+1 forward-return labels
- Added LightGBM trainer boundary:
  - `backend/train_lightgbm_ranker.py`
  - consumes dataset CSV
  - uses date-only splits
  - outputs prediction scores and metrics
- Added `lightgbm>=4.3.0` to backend requirements.
- Installed LightGBM 4.6.0 in the active `pytorch` environment with
  `--no-deps` because `pip install -r requirements.txt` is currently blocked
  by a broken local `certifi` metadata file.
- Built the 2024-2026 full-market ML dataset:
  - output: `backend/artifacts/ml/ml-factor-dataset-2024-2026.csv`
  - rows: 2,546,137
  - symbols: 4,968
  - dates: 528
  - factors: 40
- First static LightGBM run:
  - train: 2024-01-02 to 2024-09-30
  - valid: 2024-10-01 to 2024-12-31
  - 2025 OOS: RankIC `0.037680`, ICIR `0.552200`,
    long-short `0.001958`
  - 2026 OOS: RankIC `0.001870`, ICIR `0.028125`,
    long-short `-0.002121`

Read:

- The first ML pipeline is now functional.
- The static 2024-trained LightGBM has weak positive 2025 signal but fails
  2026 OOS. It is not a promotion candidate.
- Next ML work should focus on walk-forward retraining, feature importance,
  and turning predictions into a backtestable target-weight strategy.
- Added `ml_score_rank`, a read-only prediction-score strategy that consumes
  offline CSV scores with exact same-date lookup:
  - prediction `trade_date` is treated as decision date;
  - execution remains the backtest engine's next-trading-day target-weight
    execution;
  - missing score dates do not forward fill;
  - current holdings are retained only if they remain inside the same-day hold
    rank buffer;
  - numeric symbols are normalized to 6-character A-share codes.
- Hardened ML CSV handling in `build_ml_dataset.py` and
  `train_lightgbm_ranker.py` so symbols are normalized as strings at dataset
  and prediction boundaries, not only inside the strategy.
- Ran the fair primary ML-score strategy matrix over the prediction test
  coverage window, 2026-01-05 to 2026-06-10, with `rebalance_interval=10`.

Fair primary matrix, default costs:

| Strategy | Benchmark | Return | Benchmark Return | Excess | Max Drawdown | Sharpe | Turnover |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `ml_score_rank` | `000300` | -0.147360 | 0.006539 | -0.153899 | -0.163621 | -2.194948 | 11.475295 |
| `ml_score_rank` | `000905` | -0.147360 | 0.053935 | -0.201295 | -0.163621 | -2.194948 | 11.475295 |
| `ml_score_rank` | `000852` | -0.147360 | 0.057370 | -0.204731 | -0.163621 | -2.194948 | 11.475295 |
| `multi_factor_rank` | `000300` | -0.069764 | 0.006539 | -0.076303 | -0.114147 | -1.094180 | 11.680169 |
| `inverse_momentum` | `000300` | -0.100637 | 0.006539 | -0.107176 | -0.128037 | -2.396430 | 4.173801 |

Read:

- `ml_score_rank` fails the fair 2026 primary matrix.
- It is worse than both old baselines on return, drawdown, and excess return.
- Do not run sensitivity grids on this static model as a promotion path.
- Keep the strategy code because it is the correct reusable bridge from
  offline model scores to the target-weight backtest interface.
- Next ML work should be walk-forward retraining plus feature/model diagnosis,
  not parameter tuning of this failed static score file.
- Added `analyze_ml_predictions.py` for ML failure diagnosis:
  - score buckets;
  - RankIC;
  - date/symbol coverage;
  - LightGBM feature importance.
- Static 2026 diagnosis confirmed the model failure happened before portfolio
  construction: the highest score bucket underperformed the lowest bucket.
- Added `walk_forward_lightgbm.py` with rolling train/valid/test windows and a
  default 15-calendar-day label embargo between train, validation, and test.
- Ran 2026 embargo walk-forward experiments:
  - v40 factors: RankIC `0.033424`, ICIR `0.424144`,
    bucket long-short `0.003678`, but strategy return remained negative.
  - v45 factors: RankIC `0.032226`, ICIR `0.424468`,
    bucket long-short `0.001989`; strategy return turned positive against
    `000300` but still failed `000905`/`000852`.
- Best current ML research candidate:
  - `ml_score_rank_wf_v45_embargo15_top30`, 20-day rebalance,
    `hold_rank_multiplier=1.6`
  - 2026-01-05 to 2026-06-10 default cost return `0.015474`
  - excess vs `000300`: `0.008936`
  - excess vs `000905`: `-0.038461`
  - excess vs `000852`: `-0.041896`
  - max drawdown `-0.139009`
  - turnover `6.314782`

Read:

- Walk-forward is materially better than the static model.
- It is not promotion-ready because it only beats `000300`, not the
  stronger small/mid-cap benchmarks, and drawdown is still too high.
- Next ML work should reduce drawdown, compare monthly paired excess returns,
  and add PIT/trade-gap/news risk filters before any paper-trading discussion.
- Added `analyze_backtest_monthly.py` and reran the current best v45
  walk-forward candidate with `--include-curves`. Monthly excess versus
  `000300`:
  - positive excess months: 2
  - negative excess months: 4
  - mean monthly excess: `-0.001388`
  - worst monthly excess: `-0.039669`
  - best monthly excess: `0.063059`
  - worst month was 2026-03: strategy `-0.098590`, benchmark `-0.058921`,
    excess `-0.039669`, min drawdown `-0.139009`
- Added optional risk-filter inputs to `ml_score_rank`:
  - `trade_gap_path` and `exclude_gap_types` for PIT/trade-gap filtering;
  - `negative_news_path`, `negative_news_lookback_days`,
    `negative_news_min_relevance`, and `negative_news_max_sentiment` for
    negative-news risk filtering.

Read:

- Monthly evidence shows the candidate is unstable even against `000300`.
- The March drawdown is the immediate problem to diagnose before more tuning.
- PIT/news filters are now wired at the strategy boundary, but they need real
  populated data before measuring impact.

### Agent Outputs

- GLM factor research:
  - `docs/agent-glm-factor-research.md`
  - contains 46 candidate factors and first-batch engineering candidates
- DeepSeek ML review:
  - `docs/agent-deepseek-ml-plan.md`
  - covers dataset split, leakage gates, labels, metrics, and strategy risk
- Parallel plan:
  - `docs/factor-ml-parallel-plan.md`
- Detailed execution log:
  - `docs/profit-first-execution-plan.md`

## Current Problems

These are blockers before any serious paper/live discussion:

1. 2026 full-market data is not complete yet.
2. Current research universe is not fully point-in-time.
3. Historical ST/listing/delisting status is incomplete.
4. Some qfq OHLCV history may be revised after future corporate actions.
5. `multi_factor_rank` turnover is too high.
6. `multi_factor_rank` does not beat stronger 2025 small/mid-cap benchmarks.
7. LightGBM has a trainer boundary but not yet a proven trained model.
8. News/sentiment data is still not integrated.
9. Paper portfolio loop is not active yet.
10. Sync still has occasional provider/circuit-breaker issues.

## Promotion Gates

Do not promote a strategy to paper/live unless it passes:

- positive default-cost excess versus at least two of:
  - `000300`
  - `000905`
  - `000852`
- acceptable max drawdown versus benchmarks;
- positive excess under retail/stress costs;
- neighboring parameter sets also work;
- 2024, 2025, and 2026 checks do not collapse;
- known PIT limitations are clearly marked;
- trade count and turnover are realistic for an individual trader;
- no obvious data leakage or future data use.

## Task Board

Canonical checklist view:

### P0: 2026 Data Completion And OOS Validation

- [x] P0-1 Continue 2026 full-market sync
- [ ] P0-2 Retry failed/circuit-breaker symbols
- [x] P0-3 Confirm 2026 index bars
- [x] P0-4 Run 2026 full factor screen
- [x] P0-5 Run 2026 new-factor screen
- [x] P0-6 Run 2026 `multi_factor_rank` backtest
- [x] P0-7 Run 2026 `inverse_momentum` backtest
- [x] P0-8 Decide if `multi_factor_rank` remains active as research-only

### P1: Lower Turnover And Trading Costs

- [x] P1-1 Add `entry_rank_multiplier` to `multi_factor_rank`
- [ ] P1-2 Grid rebalance intervals
- [ ] P1-3 Grid hold/entry buffers
- [ ] P1-4 Grid `top_n` breadth
- [x] P1-5 Retail/stress cost matrix for current low-turnover variants
- [ ] P1-6 Document final low-turnover candidate

### P2: LightGBM ML Path

- [x] P2-1 Add ML dataset builder
- [x] P2-2 Add LightGBM trainer CLI
- [x] P2-3 Install LightGBM in active env
- [x] P2-4 Build 2024-2026 ML dataset
- [x] P2-5 Train first `fwd_5d` model
- [x] P2-6 Add prediction-backed strategy
- [x] P2-7 Backtest ML predictions as a target-weight strategy; current static model failed
- [x] P2-8 Feature-importance and score-bucket review
- [x] P2-9 2026 ML OOS check
- [x] P2-10 Add embargoed walk-forward LightGBM predictions
- [x] P2-11 Monthly paired excess review for walk-forward candidates
- [ ] P2-12 Drawdown-reduction filters for walk-forward candidates

### P3: Point-In-Time And Data Trustworthiness

- [ ] P3-1 Historical listing/delisting table
- [ ] P3-2 Historical ST/risk table
- [ ] P3-3 Historical index constituents
- [ ] P3-4 PIT universe selector
- [x] P3-5 Gap classification schema/repository skeleton
- [x] P3-6 Add strategy-side trade-gap CSV filter hook
- [ ] P3-7 Populate trade-gap rows from calendar/bars
- [ ] P3-8 Make degraded warnings stricter

### P4: News, Announcements, And Sentiment

- [x] P4-1 News source feasibility
- [x] P4-2 `NewsProvider` protocol
- [x] P4-3 News/announcement DB schema
- [ ] P4-4 First news sync implementation
- [ ] P4-5 Sentiment/event classifier v1
- [x] P4-6 Negative-news risk filter hook
- [ ] P4-7 Sentiment factor experiment

### P5: Factor Expansion

- [x] P5-1 GLM next factor batch
- [ ] P5-2 DeepSeek factor triage
- [x] P5-3 Implement accepted OHLCV factors batch F1
- [x] P5-7 Implement OHLCV factors batch G1
- [x] P5-8 Run 2026 batch G1 factor screen
- [ ] P5-4 Financial factor data plan
- [ ] P5-5 Industry-relative factor plan
- [ ] P5-6 Factor cemetery

### P6: Paper Portfolio Loop

- [ ] P6-1 Paper portfolio storage
- [ ] P6-2 Daily paper run command
- [ ] P6-3 Realistic trade plan
- [ ] P6-4 Portfolio page wiring
- [ ] P6-5 Paper observation report

### P7: Dev Hygiene And Operations

- [x] P7-1 Clean temporary logs/results
- [ ] P7-2 Commit current stable work
- [ ] P7-3 Add watcher usage docs
- [x] P7-4 Keep task board updated
- [ ] P7-5 CI/test warning cleanup

Detailed table view follows for ownership, output, and acceptance criteria.

Status values:

- `todo`: not started
- `doing`: currently being implemented or running
- `blocked`: cannot continue without another task or external input
- `done`: implemented, tested, and documented
- `dropped`: intentionally abandoned

### P0: 2026 Data Completion And OOS Validation

Goal: turn 2026 into a real out-of-sample validation year.

| ID | Task | Owner | Status | Output | Acceptance Criteria | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| P0-1 | Continue 2026 full-market sync | Codex | doing | Updated local daily bars and state file | Coverage reaches 100% or all remaining failures are classified | Current progress was around 58% on 2026-07-02 |
| P0-2 | Retry failed/circuit-breaker symbols | Codex | todo | Retry run using `--retry-failed` | Failed symbols are retried and final failures are documented | Known example: `000638` |
| P0-3 | Confirm 2026 index bars | Codex | todo | Local `000300`, `000905`, `000852` bars | Each index has bars for 2026-01-01 to 2026-06-18 or valid trading-date span | Required before benchmark comparison |
| P0-4 | Run 2026 full factor screen | Codex | todo | Factor result summary in docs | All 34 factors evaluated with RankIC/IR/long-short | Do after P0-1 |
| P0-5 | Run 2026 new-factor screen | Codex | todo | New-factor result summary | The 10 new factors are evaluated separately | Compare against 2025 factor behavior |
| P0-6 | Run 2026 `multi_factor_rank` backtest | Codex | todo | 2026 metrics table | Compare vs `000300`, `000905`, `000852` with costs | Main OOS check |
| P0-7 | Run 2026 `inverse_momentum` backtest | Codex | todo | 2026 metrics table | Same benchmark/cost matrix as 2025 | Keep as simple-rule baseline |
| P0-8 | Decide if `multi_factor_rank` remains active | DeepSeek + Codex | todo | Keep/modify/drop decision | Decision uses 2024/2025/2026 evidence, not one year | If 2026 fails badly, move to regime/ML path |

### P1: Lower Turnover And Trading Costs

Goal: make the current best rule strategy more tradable.

| ID | Task | Owner | Status | Output | Acceptance Criteria | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| P1-1 | Add `entry_rank_multiplier` to `multi_factor_rank` | Codex | done | Strategy parameter and tests | CLI accepts parameter; tests pass | Completed 2026-07-02 |
| P1-2 | Grid rebalance intervals | Codex | todo | 10/15/20/monthly comparison | Turnover and return table vs 3 benchmarks | Start after 2026 sync or use 2025 first |
| P1-3 | Grid hold/entry buffers | Codex | todo | Buffer sensitivity table | Identify lower-turnover setting with acceptable excess | Params: hold 1.3/1.6/2.0, entry 1.0/1.2/1.5 |
| P1-4 | Grid `top_n` breadth | Codex | todo | `top_n=30/50/80` comparison | Find return/drawdown/turnover tradeoff | Prior 2025 top50 reduced drawdown/turnover |
| P1-5 | Retail/stress cost matrix for best low-turnover variants | DeepSeek + Codex | todo | Cost matrix | Excess remains positive under retail/stress costs | Mandatory before promotion |
| P1-6 | Document final low-turnover candidate | Codex | todo | Candidate config in docs | Config is reproducible via CLI params | Do not promote without 2026 |

### P2: LightGBM ML Path

Goal: test whether ML improves ranking beyond hand-built factors.

| ID | Task | Owner | Status | Output | Acceptance Criteria | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| P2-1 | Add ML dataset builder | Codex | done | `backend/build_ml_dataset.py` | Dataset exports features and T+1 labels; tests pass | Completed |
| P2-2 | Add LightGBM trainer CLI | Codex | done | `backend/train_lightgbm_ranker.py` | Trainer helpers tested; optional dependency documented | Completed |
| P2-3 | Install LightGBM in active env | User/Codex | todo | Working `import lightgbm` | Trainer can run locally | Current env lacked LightGBM when checked |
| P2-4 | Build 2024/2025 ML dataset | Codex | todo | Dataset CSV + metadata | Includes stable feature set and `fwd_5d`/`fwd_10d` labels | Keep generated CSV out of git unless explicitly needed |
| P2-5 | Train first `fwd_5d` model | DeepSeek + Codex | todo | Metrics JSON + predictions CSV | Valid/test RankIC and top-bottom return reported | Use date splits only |
| P2-6 | Add prediction-backed strategy | Codex | todo | `lightgbm_alpha` or prediction consumer | Converts scores to `dict[symbol -> weight]` | Reuse existing adapter boundary |
| P2-7 | Backtest ML predictions | Codex | todo | ML strategy metrics table | Compare vs `multi_factor_rank` and `inverse_momentum` | Same costs and benchmarks |
| P2-8 | Feature-importance review | DeepSeek | todo | Review note | Reject model if dominated by unstable/leaky features | Use before further optimization |
| P2-9 | 2026 ML OOS check | Codex | blocked | 2026 ML metrics | Requires 2026 sync complete | Do not tune on 2026 |

### P3: Point-In-Time And Data Trustworthiness

Goal: reduce false confidence from survivorship and future-data assumptions.

| ID | Task | Owner | Status | Output | Acceptance Criteria | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| P3-1 | Historical listing/delisting table | DeepSeek + Codex | todo | DB model/table and sync | Backtest can know listed status by date | High priority before paper |
| P3-2 | Historical ST/risk table | DeepSeek + Codex | todo | DB model/table and sync | Strategy can exclude ST as-of date | Avoid using current name only |
| P3-3 | Historical index constituents | GLM + Codex | todo | Constituents for `000300/000905/000852` | Can build historical CSI universes | Needed for fair benchmarks/pools |
| P3-4 | PIT universe selector | Codex | todo | `get_tradeable_universe(as_of_date)` | Backtest can request tradable universe for each date | Integrate after P3-1/P3-2 |
| P3-5 | Gap classification | Codex | todo | Data quality report upgrade | Missing bars classified where possible | Suspension vs listing vs provider gap |
| P3-6 | Make degraded warnings stricter | Codex | todo | Backtest/report warnings | Non-PIT runs visibly marked | Already partially present |

### P4: News, Announcements, And Sentiment

Goal: add event/sentiment data as a risk filter first, alpha later.

| ID | Task | Owner | Status | Output | Acceptance Criteria | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| P4-1 | News source feasibility | GLM | done | Source report | Public/free sources listed with constraints | See `docs/news-sentiment-data-plan.md` |
| P4-2 | `NewsProvider` protocol | Codex | done | Provider interface | Source-agnostic protocol exists | Implemented in `backend/app/data/news.py` |
| P4-3 | News/announcement DB schema | Codex | done | Tables/models | Stores `published_at` and `fetched_at` | Implemented as `NewsItem` |
| P4-4 | First news sync implementation | GLM + Codex | done | Sync API | Can sync/query sample stock news | Implemented via `AkShareNewsProvider` and `/api/data/sync/news` |
| P4-5 | Sentiment/event classifier v1 | DeepSeek + Codex | done | Rule scoring module | Outputs sentiment/event fields | Implemented in `backend/app/data/news_sentiment.py`; LLM version later |
| P4-6 | Negative-news risk filter | Codex | done | Strategy/risk filter | Backtest can exclude recent negative/risk news | `ml_score_rank` consumes DB news via `use_db_negative_news` |
| P4-7 | Sentiment factor experiment | Codex | todo | Factor RankIC/backtest | Requires broader news coverage | Compare price-only vs price+sentiment |
| P4-8 | News text quality repair | Codex | done | Cleaning module + repair script | Mojibake news text is cleaned before provider/storage/API use | `backend/app/data/news_text.py`, `backend/repair_news_text.py` |
| P4-9 | Batch news sync CLI and coverage report | Codex | done | `backend/sync_news.py` | Can batch sync manual/research-pool symbols and output JSON/Markdown coverage | Dry-run and single-symbol live sync verified |
| P4-10 | Research-pool news coverage expansion | GLM + Codex | done | Batch sync run artifacts | Research pool has enough news rows for 2026 backtests | 300 symbols: 1828 news rows, 364 risk rows, 0 failed |
| P4-11 | News filter validation grid | Codex + DeepSeek | done | Comparison reports | Price-only vs news-risk-filter compared on 2026 ML strategy | `published_at + lookback=3` improved return/DD on 300-symbol pool; not live evidence |
| P4-12 | News event classifier refinement | DeepSeek + Codex | done | Event taxonomy + historical reclassification | Separate severe events from broad market/industry flow news | Default now blocks only `severe_company_risk`; event-type comparison added |
| P4-13 | Entity-aware severe-news classifier | GLM + DeepSeek + Codex | todo | Company-subject and negation checks | Hard blocks require target-company relevance and explicit adverse event | Keyword v2 still needs stronger entity resolution |
| P4-14 | News policy OOS validation | Codex + DeepSeek | todo | Multi-period observed/published report | Retrospective benefit must repeat; observed mode remains non-leaky | Do not promote current small deltas |

### P5: Factor Expansion

Goal: keep expanding possible alpha sources, but only promote what tests well.

| ID | Task | Owner | Status | Output | Acceptance Criteria | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| P5-1 | GLM next factor batch | GLM | todo | 20 candidate factors | Formula, fields, leakage risk documented | Build from public sources |
| P5-2 | DeepSeek factor triage | DeepSeek | todo | Priority list | Reject weak/leaky/high-cost ideas | Focus on profitable practicality |
| P5-3 | Implement accepted OHLCV factors | Codex | todo | Factor code + tests | No look-ahead and cross-stock isolation tests pass | Keep batch size manageable |
| P5-4 | Financial factor data plan | GLM + Codex | todo | Data source/schema plan | PIT announcement-date handling defined | Do not implement naive future-leaky finance |
| P5-5 | Industry-relative factor plan | DeepSeek + Codex | todo | Plan or first implementation | Requires industry classification | Useful for neutralization |
| P5-6 | Factor cemetery | Codex | todo | Rejected/weak factor doc section | Weak factors documented with evidence | Avoid rediscovering failures |

### P6: Paper Portfolio Loop

Goal: move from research to observable paper trading only after validation.

| ID | Task | Owner | Status | Output | Acceptance Criteria | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| P6-1 | Paper portfolio storage | Codex | todo | Positions/equity tables | Can store daily paper state | Use existing trade-plan base |
| P6-2 | Daily paper run command | Codex | todo | CLI or scheduled task | Runs strategy and writes target weights | No auto-live trading |
| P6-3 | Realistic trade plan | DeepSeek + Codex | todo | Lot/cash/cost aware plan | 100-share lots, commission, stamp tax | Limit-up/down later |
| P6-4 | Portfolio page wiring | Codex | todo | Frontend view | Shows holdings, equity, pending trades | Current page exists but needs data |
| P6-5 | Paper observation report | Codex | blocked | Daily/weekly report | Requires running paper loop | Minimum 1-3 months before live talk |

### P7: Dev Hygiene And Operations

Goal: keep long-running work maintainable.

| ID | Task | Owner | Status | Output | Acceptance Criteria | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| P7-1 | Clean temporary logs/results | Codex | doing | Clean workspace | No unnecessary JSON/CSV/log files in git | Keep state files local/ignored |
| P7-2 | Commit current stable work | Codex | todo | Git commit | Tests pass before commit | User previously asked to push latest code |
| P7-3 | Add watcher usage docs | Codex | todo | README/docs snippet | User can run watcher directly | After current manual sync finishes |
| P7-4 | Keep task board updated | All agents | doing | This document | Every round updates status/notes | Required for handoff |
| P7-5 | CI/test warning cleanup | Codex | todo | Reduced warnings | Pydantic/FastAPI deprecation warnings addressed | Not urgent for alpha |

## Suggested Multi-Agent Split

Use the same collaborative pattern:

| Agent | Role | Next Ownership |
| --- | --- | --- |
| Codex | integration, tests, docs, backtest reliability | watcher, PIT selector, ML adapter, final validation |
| GLM | factor discovery and data source research | next 20 factors, news source feasibility, public formula translation |
| DeepSeek | strategy/ML critique | LightGBM validation design, leakage review, feature importance review |

Cross-review:

1. GLM proposes factor/data candidates.
2. DeepSeek rejects weak/leaky ideas and ranks priorities.
3. Codex implements the accepted subset.
4. Codex runs tests and backtests.
5. DeepSeek reviews results for overfitting.
6. GLM proposes the next factor batch based on failures.

## Recommended Immediate Sequence

| Order | Task IDs | Why |
| ---: | --- | --- |
| 1 | P0-1, P0-2, P0-3 | 2026 OOS data is the biggest validation gap |
| 2 | P2-3, P2-4, P2-5 | First ML model can run while 2026 sync continues |
| 3 | P1-2, P1-3, P1-4 | Turnover reduction determines whether rule strategy is practical |
| 4 | P4-1, P4-2, P4-3 | News schema can be designed without waiting for all data |
| 5 | P3-1, P3-2, P3-4 | PIT fixes are required before serious paper/live confidence |

My recommended next working set:

1. Keep 2026 sync running in the background.
2. Install LightGBM and run the first 2024/2025 ML experiment.
3. Start news schema/source feasibility in parallel.
4. When 2026 completes, immediately run P0-4 through P0-8.
