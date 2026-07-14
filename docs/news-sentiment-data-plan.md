# News And Sentiment Data Plan

Date: 2026-07-02

Goal: add news, announcements, and sentiment as a risk-control layer first, then
as alpha factors after timing and data quality are proven.

## First Principle

Every item must store:

- `published_at`: when the source says the item became public;
- `fetched_at`: when this system first observed it.

Backtests must use the later of the two timestamps when deciding whether a
strategy could have known the item.

## Implemented Schema Boundary

Implemented in code:

- `backend/app/models/entities.py`
  - `NewsItem`
- `backend/app/data/news.py`
  - `NEWS_COLUMNS`
  - `NewsProvider` protocol
- `backend/app/data/repository.py`
  - `upsert_news_items`
  - `news_items`
- `backend/app/data/news_text.py`
  - `clean_news_text`
  - `clean_news_payload`
  - `has_mojibake`
- `backend/repair_news_text.py`
  - one-off scan/repair for persisted news text
- `backend/sync_news.py`
  - batch news sync and coverage report CLI

Canonical fields:

- `source`
- `source_id`
- `symbol`
- `title`
- `body`
- `url`
- `event_type`
- `sentiment_label`
- `sentiment_score`
- `relevance_score`
- `published_at`
- `fetched_at`
- `raw`

Uniqueness:

- `(source, source_id)` is unique.

## Candidate Public Sources

Use public/free sources only. Do not bypass logins, scrape paid content, or
work around anti-abuse controls.

| Source | Candidate Interface | Use | Status | Notes |
| --- | --- | --- | --- | --- |
| Eastmoney individual stock news | `stock_news_em` | stock-specific news | candidate | Good first source for news linked to one symbol |
| Eastmoney announcements | `stock_notice_report`, `stock_individual_notice_report` | company announcements | candidate | Useful for event-risk filters |
| CNInfo / Juchao disclosure | `stock_zh_a_disclosure_report_cninfo` | official disclosures | candidate | Strong source for announcement timing |
| Eastmoney popularity ranking | `stock_hot_rank_em`, `stock_hot_rank_detail_em` | attention/heat | candidate | More sentiment/attention than fundamental news |
| Eastmoney comment/ratings | `stock_comment_em` | market opinion proxy | candidate | Needs validation before trading use |
| Xueqiu/Guba/Taoguba | varies | social sentiment | research-only | Do not build until terms and stable access are clear |

References checked:

- AKShare stock data documentation: https://akshare.akfamily.xyz/data/stock/stock.html
- AKShare docs expose stock/news/announcement style interfaces under the stock
  data section; exact function availability should be verified in the local
  installed AKShare version before implementation.

## First Sync Scope

Do not build a broad crawler first. Start with one reliable command that syncs
one symbol or a small symbol list.

Suggested v1:

1. `AkShareNewsProvider.stock_news(symbol)`
2. `AkShareNewsProvider.announcements(symbol)`
3. `sync_news.py --symbols 000001,600000 --start-at ... --end-at ...`
4. Store raw records in `news_items`
5. No LLM call in the first sync

## First Risk Filter

Implement a conservative negative-news filter:

- identify event types:
  - regulatory penalty
  - investigation
  - lawsuit
  - major loss warning
  - large shareholder reduction
  - debt/default risk
  - trading abnormality
- if a stock has severe negative news in the last `N` calendar/trading days,
  set target weight to zero or lower its score.

This is preferred before using sentiment as alpha because it is easier to
validate: the goal is avoiding blowups, not predicting every small move.

## Leakage Risks

- Announcements often publish after market close. They must not affect same-day
  intraday or close decisions.
- Reposted news can duplicate the same event many times; deduplicate by source
  id and later by URL/title similarity.
- LLM labels are model-version dependent; store model name/version/prompt later.
- News matched to a symbol may be weakly relevant; store `relevance_score`.
- Current source timestamps may be local Beijing time; normalize consistently
  before backtests.

## Text Quality And Encoding

Implemented on 2026-07-07:

- New news text cleaner repairs common UTF-8-as-Latin-1/Windows-1252 mojibake,
  for example `å½å®¶` -> `国家`.
- `AkShareNewsProvider` cleans title/body/source/raw payload before returning
  records.
- `MarketDataRepository.upsert_news_items()` cleans title/body/sentiment fields
  and raw JSON before persistence.
- `MarketDataRepository.news_items()` cleans output as a read-side fallback so
  old dirty rows do not leak into API/front-end/model consumers.
- `backend/repair_news_text.py --dry-run` scans persisted rows before optional
  writeback.

Current local check:

- `python backend/repair_news_text.py --dry-run`
- Result on 2026-07-07: `scanned=10`, `updated=0`, `remaining_suspect=0`.
- The earlier `å...` display was mostly a PowerShell console encoding artifact;
  direct Python DB reads show stored titles are already normal Chinese. The
  cleaner remains in place as provider/storage/API defense.

## Next Tasks

- [x] Define `NewsItem` schema
- [x] Define `NewsProvider` protocol
- [x] Add repository upsert/query methods
- [x] Add `AkShareNewsProvider`
- [x] Add HTTP sync/query endpoints for small symbol-list sync
- [x] Add tests with fake provider responses
- [x] Add `sync_news.py` CLI wrapper for batch jobs
- [x] Add negative-news rule classifier v1
- [x] Add a risk filter that consumes recent negative news
- [x] Add text cleaning and mojibake repair safeguards
- [ ] Backtest price-only strategy vs price-plus-news-risk-filter
- [x] Run research-pool batch sync for 2026 news coverage
- [x] Run first price-only vs price-plus-news-risk-filter comparison
- [ ] Add duplicate clustering by URL/title similarity
- [ ] Add source coverage report by symbol/date/event type
- [x] Split event taxonomy into severe company-specific, company-risk, industry-flow, and market-flow
- [x] Preserve first `fetched_at` and load observed-time windows without dropping delayed feeds
- [x] Add event-type-aware ML Score Rank blocking and comparison reporting
- [ ] Add company-subject/entity validation before treating keyword matches as production risk events

## Batch Sync Commands

Dry-run research-pool selection without network:

```powershell
python backend\sync_news.py --symbol-source research_pool --start-date 2026-01-01 --end-date 2026-07-02 --pool-max-symbols 20 --dry-run --json-output backend\artifacts\news\dry-run-news-sync-report.json --markdown-output backend\artifacts\news\dry-run-news-sync-report.md
```

Single-symbol live sync smoke test:

```powershell
python backend\sync_news.py --symbols 002156 --start-date 2026-06-01 --end-date 2026-07-03 --batch-size 1 --min-request-interval 0 --json-output backend\artifacts\news\002156-news-sync-report.json --markdown-output backend\artifacts\news\002156-news-sync-report.md
```

Recommended first research-pool batch:

```powershell
python backend\sync_news.py --symbol-source research_pool --start-date 2026-01-01 --end-date 2026-07-02 --pool-max-symbols 100 --batch-size 10 --min-request-interval 0.5 --json-output backend\artifacts\news\research-pool-100-news-sync-report.json --markdown-output backend\artifacts\news\research-pool-100-news-sync-report.md
```

## First 2026 Research Results

Run date: 2026-07-12.

Coverage:

- 100-symbol research pool: `success=92`, `empty=8`, `failed=0`,
  `news_rows=588`, `risk_rows=107`, `symbols_with_risk_news=48`.
- 300-symbol research pool: `success=275`, `empty=25`, `failed=0`,
  `news_rows=1828`, `risk_rows=364`, `symbols_with_risk_news=151`.

Important availability rule:

- `observed`: uses `known_at=max(published_at,fetched_at)`, safe for live-style
  backtests. Since historical news was fetched on 2026-07-12, it does not affect
  2026-01..06 historical trades.
- `published_at`: retrospective research mode. It approximates a system that
  had the public news feed at the time. Use only for signal research and label
  reports clearly.

ML Score Rank comparison on 300-symbol pool, 2026-01-05..2026-06-10,
`published_at`, `negative_news_lookback_days=3`:

- baseline total return: `-9.6254%`
- news-filter total return: `-9.0800%`
- total return delta: `+0.5454%`
- max drawdown: `-12.5667%` -> `-12.1178%`
- Sharpe: `-1.2500` -> `-1.1794`

Interpretation:

- News-risk filtering showed a small positive effect on the 300-symbol ML pool.
- The 100-symbol pool was unstable: same-day filtering helped slightly, longer
  lookbacks hurt. This suggests the classifier is too broad and should separate
  severe company-specific events from generic industry/market fund-flow news.

## Event Taxonomy v2 Results

Run date: 2026-07-14.

The initial `risk_news` label was too broad: it treated sector fund flows,
market moves, and weak company-risk mentions as direct reasons to avoid an
individual stock. The classifier now emits:

- `severe_company_risk`: investigation, confirmed penalty, financial fraud,
  debt default/overdue debt, insolvency/bankruptcy, or forced delisting.
- `company_risk`: operating loss, shareholder reduction, regulatory letter,
  or similar company-level concern. It is observation-only by default.
- `industry_market_flow`: sector/market capital-flow news. Never default-blocked.
- `general_market_sentiment`: market move or trading-abnormality news. Never
  default-blocked.
- `neutral`: no current risk classification.

The default `ml_score_rank` block list is now only
`severe_company_risk`. A caller may explicitly add `company_risk`; empty event
types retain the old sentiment-based compatibility behavior. Phrases such as
`预亏变预盈`, `亏损收窄`, `胜诉`, `退市新规`, `减持新规`, `异动拉升`,
and `涨停` are explicitly excluded from negative classification.

Historical reclassification command:

```powershell
python backend\reclassify_news_events.py --dry-run
python backend\reclassify_news_events.py
```

Local reclassification result over 1,346 persisted news rows:

- `severe_company_risk`: 9
- `company_risk`: 71
- `industry_market_flow`: 114
- `general_market_sentiment`: 18
- `neutral`: 1,134

300-symbol retrospective research comparison, 2026-01-05..2026-06-10,
`published_at`, 3-day lookback:

| Block policy | Return delta | Max drawdown delta | Sharpe delta | Conclusion |
| --- | ---: | ---: | ---: | --- |
| `severe_company_risk` | +0.0312% | 0.0000% | +0.0044 | Too small to promote; preserves a conservative default. |
| `severe_company_risk,company_risk` | +0.1078% | +0.0782% | +0.0137 | Mildly positive, but still too small and retrospective-only. |

These are not live-readiness results. They use `published_at` to study the
historical public feed. The default live-safe `observed` mode uses the first
time the system actually fetched the record and remains the only valid mode for
paper/live conclusions.
