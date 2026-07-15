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
- [ ] P1-3b-2d Allow a user-selected eligible OOS record to be attached to
  advisory context only after strategy, parameter, and as-of-date matching.
- [x] P1-4 Add an LLM prompt/result contract that constrains it to a
  risk-gated trade-plan explanation and non-binding risk rationale.
- [x] P1-5 Add streamed report events to the web API.

### P2: Portfolio Decision Workbench

- [ ] P2-1 Persist paper portfolio cash, positions, and valuation history.
- [ ] P2-2 Add concentration, cash, drawdown, and exposure diagnostics.
- [ ] P2-3 Show current holdings, risk-gated target weights, and trade-plan
  deltas in a single review screen.
- [ ] P2-4 Add explicit `draft`, `reviewed`, `expired`, and `rejected`
  advisory states. Do not add an `execute` state.

### P3: Multi-Agent Research Loop

- [ ] P3-1 Research agent: facts from local data/news with citations.
- [ ] P3-2 Strategy agent: compares validated factor/ML candidates.
- [ ] P3-3 Critic agent: looks for stale data, contradictory news, leakage,
  concentration, and unsupported conclusions.
- [ ] P3-4 Risk agent: produces deterministic gate results and a plain-language
  explanation of rejected exposures.
- [ ] P3-5 Store each agent's evidence and final synthesis for replay.

### P4: Notifications and Daily Workflow

- [x] P4-1 Add an outbound Enterprise WeChat group-webhook notifier.
- [x] P4-2 Send reviewed advisory summaries only after the advisory run is
  persisted.
- [x] P4-3 Add notification delivery audit and retry policy.
- [ ] P4-4 Consider official two-way Enterprise WeChat callbacks only after
  authentication, signature verification, and permission design are complete.

### P5: Evaluation Before Trust

- [ ] P5-1 Track each advisory's subsequent 1/5/20-day return, benchmark
  excess, drawdown, and adverse-news outcome.
- [ ] P5-2 Compare model-assisted reports against the same deterministic
  strategy without LLM text.
- [ ] P5-3 Require multi-period out-of-sample evidence before promoting any
  strategy/ML proposal to the paper portfolio.
- [ ] P5-4 Publish a factor/strategy cemetery for failed ideas.

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
