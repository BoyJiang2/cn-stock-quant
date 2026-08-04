# A-share ValueCell Plan

## Product Definition

Build an A-share research and portfolio-decision workbench. It joins local
market data, point-in-time status, factor research, strategy results, news,
and a user portfolio into evidence-backed research reports and trade-plan
drafts.

It is not an autonomous live-trading system. A language model may explain,
challenge, and summarize a proposal, but it must not create orders or bypass
the deterministic strategy and risk layers.

## Non-Negotiable Boundaries

- Strategy output remains `dict[symbol, weight]`.
- `RiskEngine` remains the sole authority for weight, position-count, and
  blocked-symbol limits.
- `build_trade_plan` remains the sole component that turns approved target
  weights into round-lot buy/sell drafts.
- Current local bars are research-price inputs. Any broker/paper execution
  layer must use a separately verified execution-price source and enforce
  T+1, fees, suspensions, and limit-up/limit-down constraints.
- Every advisory run stores its input snapshot, data as-of time, risk decision,
  model/provider identity, generated text, and final status.
- Remote LLM use is disabled by default. Do not send portfolio data until the
  user has explicitly enabled a configured provider.
- The first WeCom integration is outbound notification only. It cannot accept
  trading commands and cannot submit orders.
- No broker execution endpoint is in scope. A human must review and confirm a
  paper-trading plan.

## Target Architecture

```text
market/news/PIT data + user portfolio
            |
            v
strategy target weights -> RiskEngine -> build_trade_plan
            |                  |              |
            +------------------+--------------+
                               v
                    immutable advisory snapshot
                               |
                               v
           LLM research / critic / explanation (optional, streamed)
                               |
                    web workbench + WeCom notification
```

The model receives structured, time-stamped evidence. It returns an
explanation, explicit uncertainty, opposing evidence, and a recommendation
that is constrained to the already risk-gated trade-plan draft.

## Delivery Checklist

### P0: Governance and Contracts

- [x] P0-1 Record product boundary and delivery plan in this document.
- [x] P0-2 Add advisory request/response schemas and input validation.
- [x] P0-3 Add persisted advisory-run audit records.
- [x] P0-4 Add a provider capability endpoint that exposes configuration state
  without exposing secrets.
- [x] P0-5 Add focused unit/API tests for disabled remote-model behavior.

### P1: A-share Research Copilot

- [x] P1-1 Build a reproducible current-date portfolio/market evidence
  snapshot from local data, including CSI 300 regime data and the user
  portfolio valuation basis.
- [x] P1-2 Generate strategy target weights from a selected registered
  strategy, then apply `RiskEngine` and `build_trade_plan`.
- [x] P1-3a Add market-regime and observed-news evidence with source/known
  timestamps to the advisory snapshot and LLM context. News is restricted to
  items known no later than the advisory date to prevent future leakage.
- [x] P1-3b-1 Add an as-of-date trailing factor snapshot for target/held
  symbols. It persists only observed price/volume transforms and explicitly
  excludes forward returns, IC, and historical-effectiveness claims.
- [x] P1-3b-2a Persist immutable provenance for each new backtest: request,
  selected symbols, PIT/universe metadata, benchmark metrics, and a content
  fingerprint. Legacy runs are explicitly marked as not recorded.
- [x] P1-3b-2b Add fixed-parameter rolling OOS diagnostics with prior-history
  warm-up, independent strategy instances, local benchmark checks, 1x/2x cost
  stress, immutable records, and explicit non-eligibility reasons.
- [x] P1-3b-2c Rebuild the PIT universe for every OOS window from the local
  trading calendar. Persist each window's PIT metadata, selected symbols, and
  market/benchmark/news input fingerprints; only fully covered, non-degraded
  windows may be eligible evidence.
- [x] P1-3b-2d Allow a user-selected eligible OOS record to be attached to
  advisory context only after exact strategy, parameter, and final OOS cutoff
  matching. Store its immutable evidence snapshot with the advisory draft.
- [x] P1-4 Add an LLM prompt/result contract that constrains it to a
  risk-gated trade-plan explanation and non-binding risk rationale.
- [x] P1-5 Add streamed report events to the web API.

### P2: Portfolio Decision Workbench

- [x] P2-1 Persist a default paper portfolio's cash, current positions, and
  daily research-close valuation history through explicit user snapshots.
- [x] P2-2 Add concentration, cash, drawdown, and exposure diagnostics from
  persisted paper-portfolio snapshots and valuation history.
- [x] P2-3 Show current holdings, risk-gated target weights, and trade-plan
  deltas in a single read-only review screen. Advisory drafts are rejected when
  persisted data is invalid and must be refreshed when the portfolio snapshot,
  positions, or equity differs from the draft.
- [x] P2-4 Add explicit `draft`, `reviewed`, `expired`, and `rejected`
  advisory states. Expiry is based on synchronized bars for the draft's own
  symbols after its next local trading date; no `execute` state exists.

### P3: Multi-Agent Research Loop

- [x] P3-1 Research agent: facts from local data/news with citations.
- [x] P3-2 Strategy agent: compares eligible rolling-OOS strategy candidates
  using a deterministic, published score across return, drawdown, Sharpe, and
  cost-stress Sharpe. Invalid metrics and inconsistent provenance are excluded.
- [x] P3-3 Critic agent: evaluates persisted evidence for stale data, future
  leakage, severe observed news, concentration, and validation-provenance
  mismatches. Its blockers prevent a research result from being described as
  ready for human review; they never create an execution path.
- [x] P3-4 Risk agent: exposes the persisted deterministic gate result, risk
  caps, retained exposure, and plain-language per-symbol rejection reasons.
- [x] P3-5 Store immutable creation-time research, strategy, critic, risk, and
  synthesis snapshots with ID- and agent-bound SHA-256 fingerprints; replay
  rejects incomplete, tampered, or cross-draft data. It intentionally does not
  treat later LLM prose as evidence, and legacy drafts are explicitly marked
  unavailable rather than reconstructed from new data.

### P4: Notifications and Daily Workflow

- [x] P4-1 Add an outbound Enterprise WeChat group-webhook notifier.
- [x] P4-2 Send reviewed advisory summaries only after the advisory run is
  persisted.
- [x] P4-3 Add notification delivery audit and retry policy.
- [ ] P4-4 Consider official two-way Enterprise WeChat callbacks only after
  authentication, signature verification, and permission design are complete.

### P5: Evaluation Before Trust

- [x] P5-1 Track each advisory's subsequent 1/5/20-trading-day return,
  benchmark excess, drawdown, and observed subsequent adverse-news outcome;
  windows require the local trading calendar, CSI 300, and full `qfq` price
  paths. Incomplete coverage remains explicitly pending; news is company-level
  observed evidence only, bounded by known-at timestamps.
- [x] P5-2 Compare the LLM explanation layer with its identical deterministic
  strategy control. The interface fingerprints targets and trade plans, reuses
  the same outcome data, and explicitly marks LLM performance attribution as
  not applicable because prose cannot change execution.
- [x] P5-3 Require an explicit human review plus at least three distinct,
  completed, eligible rolling OOS windows with matching strategy and provenance
  before an advisory can be promoted into the local paper portfolio.
- [x] P5-4 Publish a factor/strategy cemetery. Non-eligible walk-forward
  validations and factor experiments with weak or insufficient RankIC evidence
  are preserved with source fingerprints, metrics, and failure reasons. Factor
  experiments now persist their request, summary, implementation version, and
  actual-OHLCV snapshot fingerprint; both factor and strategy records have
  explicit idempotent backfill routes. Results created before factor-experiment
  persistence cannot be reconstructed because their raw experiment payloads
  were never stored.

### P6: Factor and Strategy Selection

- [x] P6-1 Add deterministic `rejected` / `watch` / `candidate` triage to
  factor experiments. Candidate means only that a factor merits the next
  point-in-time rolling-OOS research stage; it is never a trade approval.
- [x] P6-2 Expand only trailing, locally reproducible OHLCV factor families
  and record their implementation versions with each experiment.
- [x] P6-3a Require consecutive cross-period stability before a factor can
  remain a research candidate; factor experiments expose fold-level evidence.
- [ ] P6-3b Require turnover/cost stress and
  point-in-time rolling-OOS evidence before a factor can enter a composite or
  model feature set.
- [ ] P6-4 Compare eligible factor candidates across market regimes and
  retire factors whose live paper evidence materially diverges from OOS
  expectations.

## Provider and Secret Policy

- Runtime provider: OpenAI Responses API through a provider-neutral adapter.
  The app uses configurable model IDs and streaming; this Codex coding session
  is not a runtime inference endpoint.
- Required secret when enabled: `OPENAI_API_KEY`, supplied only through process
  environment or a local ignored secret manager.
- Required setting: `OPENAI_MODEL`; optional use requires
  `ALLOW_REMOTE_LLM=true`. Defaults must keep remote use off.
- WeCom secret: `WECOM_WEBHOOK_URL`, also environment-only and never returned
  by an API or committed to Git.
- A personal WeChat automation bot is out of scope. Use official Enterprise
  WeChat integration for reliable outbound notifications.

## Local Enablement

The deterministic advisory draft works without any model credentials. To
enable remote explanation after restarting the backend process, configure a
model that the account can use with the Responses API:

```powershell
$env:OPENAI_API_KEY = "..."
$env:OPENAI_MODEL = "your-responses-model-id"
$env:ALLOW_REMOTE_LLM = "true"
```

To enable reviewed-draft notifications, also configure an Enterprise WeChat
group-bot webhook. It is never returned by the API:

```powershell
$env:WECOM_WEBHOOK_URL = "https://qyapi.weixin.qq.com/..."
```

The current Codex development session is not a runtime inference endpoint.
The application uses the same OpenAI API ecosystem through an explicit,
environment-configured provider instead.

## Acceptance Criteria for the First Vertical Slice

1. A user can submit a cash/position snapshot and select a registered strategy.
2. The backend produces time-stamped target weights, risk decisions, and an
   A-share round-lot trade-plan draft from local data.
3. Without remote LLM configuration, the API clearly returns a disabled state
   and still retains the deterministic draft.
4. With an enabled provider, text streams to the client but the final persisted
   result is tied to the exact evidence snapshot.
5. A WeCom notification can send a summary of a persisted advisory run, but it
   cannot trigger a broker or paper execution.
