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
- [ ] Add `sync_news.py` CLI wrapper for batch jobs
- [x] Add negative-news rule classifier v1
- [x] Add a risk filter that consumes recent negative news
- [x] Add text cleaning and mojibake repair safeguards
- [ ] Backtest price-only strategy vs price-plus-news-risk-filter
- [ ] Add `sync_news.py` or batch job for research-pool news coverage
- [ ] Add duplicate clustering by URL/title similarity
- [ ] Add source coverage report by symbol/date/event type
