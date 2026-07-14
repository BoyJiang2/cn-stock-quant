from datetime import date, datetime

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.data.repository import MarketDataRepository
from app.models.entities import Base


def make_repository() -> MarketDataRepository:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return MarketDataRepository(Session(engine))


def test_repository_normalizes_stock_symbols_and_searches_aliases():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": 1, "name": "Ping An Bank", "exchange": "SZ", "status": "active"},
            ]
        )
    )

    stocks = repository.list_stocks(keyword="SZ000001")

    assert len(stocks) == 1
    assert stocks[0].symbol == "000001"


def test_repository_resolves_stock_name_to_symbol():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "002156", "name": "通富微电", "exchange": "SZ", "status": "active"},
            ]
        )
    )

    assert repository.resolve_symbol("通富微电") == "002156"
    assert repository.resolve_symbol("SZ002156") == "002156"
    assert repository.resolve_symbols(["通富微电", "002156"]) == ["002156"]


def test_repository_keeps_partial_numeric_search_as_substring():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "600000", "name": "SPDB", "exchange": "SH", "status": "active"},
            ]
        )
    )

    assert [stock.symbol for stock in repository.list_stocks(keyword="600")] == ["600000"]
    assert [stock.symbol for stock in repository.list_stocks(keyword="  ")] == ["600000"]


def test_repository_normalizes_daily_bar_queries_and_reports_status():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "000001", "name": "Ping An Bank", "exchange": "SZ", "status": "active"},
            ]
        )
    )
    repository.replace_daily_bars(
        "000001.SZ",
        date(2024, 1, 2),
        date(2024, 1, 2),
        pd.DataFrame(
            [
                {
                    "symbol": "SZ000001",
                    "trade_date": date(2024, 1, 2),
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 1000.0,
                    "amount": 10200.0,
                    "adj": "qfq",
                }
            ]
        ),
    )

    bars = repository.daily_bars(["SZ000001"], date(2024, 1, 1), date(2024, 1, 3))
    status = repository.symbol_data_status("000001.SZ")

    assert bars["symbol"].tolist() == ["000001"]
    assert status == {
        "symbol": "000001",
        "stock_exists": True,
        "name": "Ping An Bank",
        "exchange": "SZ",
        "has_daily_bars": True,
        "start_date": date(2024, 1, 2),
        "end_date": date(2024, 1, 2),
        "bar_count": 1,
    }


def test_repository_rejects_daily_bars_for_a_different_symbol():
    repository = make_repository()
    bars = pd.DataFrame(
        [
            {
                "symbol": "600000",
                "trade_date": date(2024, 1, 2),
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "volume": 1000.0,
                "amount": 10200.0,
            }
        ]
    )

    try:
        repository.replace_daily_bars("000001", date(2024, 1, 2), date(2024, 1, 2), bars)
    except ValueError as exc:
        assert "target symbol is 000001" in str(exc)
    else:
        raise AssertionError("expected mismatched daily bar symbols to be rejected")


def test_repository_keeps_index_and_stock_bars_in_separate_namespaces():
    repository = make_repository()
    stock_bars = pd.DataFrame(
        [
            {
                "symbol": "000905",
                "trade_date": date(2024, 1, 2),
                "open": 7.0,
                "high": 8.0,
                "low": 6.5,
                "close": 7.5,
                "volume": 1000.0,
                "amount": 7500.0,
            }
        ]
    )
    index_bars = pd.DataFrame(
        [
            {
                "symbol": "000905",
                "trade_date": date(2024, 1, 2),
                "open": 5000.0,
                "high": 5100.0,
                "low": 4950.0,
                "close": 5050.0,
                "volume": 2000.0,
                "amount": 10_000_000.0,
            }
        ]
    )

    repository.replace_daily_bars("000905", date(2024, 1, 2), date(2024, 1, 2), stock_bars)
    repository.replace_index_daily_bars("000905", date(2024, 1, 2), date(2024, 1, 2), index_bars)

    assert repository.daily_bars(["000905"], date(2024, 1, 2), date(2024, 1, 2)).iloc[0]["close"] == 7.5
    assert repository.index_daily_bars("000905", date(2024, 1, 2), date(2024, 1, 2)).iloc[0]["close"] == 5050.0


def test_quality_report_uses_trading_calendar_and_detects_internal_gap():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [{"symbol": "000001", "name": "Test", "exchange": "SZ", "status": "active"}]
        )
    )
    repository.upsert_trading_calendar(
        pd.DataFrame(
            {
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
                "is_open": [True, True, True],
            }
        )
    )
    repository.replace_daily_bars(
        "000001",
        date(2024, 1, 2),
        date(2024, 1, 4),
        pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "trade_date": trade_date,
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
                for trade_date in [date(2024, 1, 2), date(2024, 1, 4)]
            ]
        ),
    )

    report = repository.data_quality_report(
        date(2024, 1, 1),
        date(2024, 1, 4),
        limit=10,
    )

    assert report["expected_trading_days"] == 3
    assert report["symbols_fully_covered"] == 0
    assert report["items"][0]["missing_dates"] == [date(2024, 1, 3)]


def test_consecutive_sync_failures_persist_in_sync_jobs():
    repository = make_repository()
    for status in ["failed", "failed", "success", "failed", "failed", "failed"]:
        repository.create_sync_job(
            "daily",
            "000001",
            status,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

    assert repository.consecutive_sync_failures(
        "000001",
        date(2024, 1, 1),
        date(2024, 12, 31),
    ) == 3


def test_symbol_data_status_distinguishes_missing_stock_from_missing_bars():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "000001", "name": "Ping An Bank", "exchange": "SZ", "status": "active"},
            ]
        )
    )

    known = repository.symbol_data_status("000001")
    unknown = repository.symbol_data_status("600000")

    assert known["stock_exists"] is True
    assert known["has_daily_bars"] is False
    assert unknown["stock_exists"] is False
    assert unknown["has_daily_bars"] is False


def test_market_data_overview_reports_database_coverage():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "000001", "name": "Ping An Bank", "exchange": "SZ", "status": "active"},
                {"symbol": "600000", "name": "SPDB", "exchange": "SH", "status": "active"},
            ]
        )
    )
    repository.replace_daily_bars(
        "000001",
        date(2024, 1, 2),
        date(2024, 1, 2),
        pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "trade_date": date(2024, 1, 2),
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 1000.0,
                    "amount": 10200.0,
                    "adj": "qfq",
                }
            ]
        ),
    )

    assert repository.market_data_overview() == {
        "stock_count": 2,
        "bar_count": 1,
        "symbols_with_bars": 1,
        "start_date": date(2024, 1, 2),
        "end_date": date(2024, 1, 2),
    }


def test_repository_upserts_and_queries_news_items():
    repository = make_repository()
    repository.upsert_news_items(
        pd.DataFrame(
            [
                {
                    "source": "eastmoney_stock_news",
                    "source_id": "em-1",
                    "symbol": "SZ000001",
                    "title": "Ping An Bank news",
                    "body": "body",
                    "url": "https://example.com/news/1",
                    "event_type": "news",
                    "sentiment_label": "neutral",
                    "sentiment_score": 0.0,
                    "relevance_score": 0.9,
                    "published_at": datetime(2024, 1, 2, 9, 30),
                    "fetched_at": datetime(2024, 1, 2, 9, 35),
                    "raw": {"id": 1},
                }
            ]
        )
    )
    repository.upsert_news_items(
        pd.DataFrame(
            [
                {
                    "source": "eastmoney_stock_news",
                    "source_id": "em-1",
                    "symbol": "000001",
                    "title": "Updated title",
                    "published_at": datetime(2024, 1, 2, 9, 30),
                    "fetched_at": datetime(2024, 1, 2, 9, 40),
                },
                {
                    "source": "cninfo_announcement",
                    "source_id": "cn-1",
                    "symbol": "600000",
                    "title": "SPDB announcement",
                    "published_at": datetime(2024, 1, 3, 18, 0),
                    "fetched_at": datetime(2024, 1, 3, 18, 5),
                },
            ]
        )
    )

    pingan = repository.news_items(symbol="000001", limit=10)
    all_items = repository.news_items(
        start_at=datetime(2024, 1, 1),
        end_at=datetime(2024, 1, 4),
        limit=10,
    )

    assert len(pingan) == 1
    assert pingan.iloc[0]["source_id"] == "em-1"
    assert pingan.iloc[0]["symbol"] == "000001"
    assert pingan.iloc[0]["title"] == "Updated title"
    assert pingan.iloc[0]["fetched_at"] == datetime(2024, 1, 2, 9, 35)
    assert len(all_items) == 2


def test_repository_queries_delayed_news_by_observed_time():
    repository = make_repository()
    repository.upsert_news_items(
        pd.DataFrame(
            [
                {
                    "source": "eastmoney_stock_news",
                    "source_id": "delayed-1",
                    "symbol": "000001",
                    "title": "Delayed feed item",
                    "published_at": datetime(2024, 1, 1, 9, 30),
                    "fetched_at": datetime(2024, 1, 5, 9, 30),
                }
            ]
        )
    )

    items = repository.news_items(
        known_start_at=datetime(2024, 1, 5),
        known_end_at=datetime(2024, 1, 5, 23, 59, 59),
    )

    assert items["source_id"].tolist() == ["delayed-1"]


def test_repository_rejects_news_items_without_required_timestamps():
    repository = make_repository()

    try:
        repository.upsert_news_items(
            pd.DataFrame(
                [
                    {
                        "source": "eastmoney_stock_news",
                        "source_id": "em-1",
                        "title": "missing fetched_at",
                        "published_at": datetime(2024, 1, 2, 9, 30),
                    }
                ]
            )
        )
    except ValueError as exc:
        assert "fetched_at" in str(exc)
    else:
        raise AssertionError("missing fetched_at should fail")


def test_repository_cleans_mojibake_news_text_on_upsert_and_query():
    repository = make_repository()

    repository.upsert_news_items(
        pd.DataFrame(
            [
                {
                    "source": "eastmoney_stock_news",
                    "source_id": "em-mojibake",
                    "symbol": "002156",
                    "title": "çµå­è¡ä¸ä»æ¥åæµåºèµé147.94äº¿å",
                    "body": "é¿çµç§æç­77è¡åæµåºèµéè¶äº¿å",
                    "event_type": "risk_news",
                    "sentiment_label": "risk",
                    "sentiment_score": -0.4,
                    "relevance_score": 1.0,
                    "published_at": datetime(2026, 6, 29, 16, 37),
                    "fetched_at": datetime(2026, 7, 3, 9, 0),
                    "raw": {"æ°é»æ é¢": "çµå­è¡ä¸ä»æ¥åæµåº"},
                }
            ]
        )
    )

    items = repository.news_items(symbol="002156")

    assert items.iloc[0]["title"] == "电子行业今日净流出资金147.94亿元"
    assert items.iloc[0]["body"] == "长电科技等77股净流出资金超亿元"
    assert "新闻标题" in items.iloc[0]["raw"]


def test_research_sync_candidates_exclude_risk_names_and_covered_symbols():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "000001", "name": "Ping An Bank", "exchange": "SZ", "status": "active"},
                {"symbol": "000002", "name": "*ST Example", "exchange": "SZ", "status": "active"},
                {"symbol": "600000", "name": "SPDB", "exchange": "SH", "status": "active"},
                {"symbol": "430001", "name": "BJ Example", "exchange": "BJ", "status": "active"},
            ]
        )
    )
    repository.replace_daily_bars(
        "000001",
        date(2024, 1, 1),
        date(2024, 12, 31),
        pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "trade_date": trade_date,
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.2,
                    "volume": 1000.0,
                    "amount": 10200.0,
                }
                for trade_date in (date(2024, 1, 1), date(2024, 12, 31))
            ]
        ),
    )

    symbols = repository.next_research_sync_symbols(
        date(2024, 1, 1),
        date(2024, 12, 31),
        batch_size=20,
    )
    progress = repository.research_sync_progress(date(2024, 1, 1), date(2024, 12, 31))

    assert symbols == ["600000"]
    assert progress == {"total": 2, "covered": 1, "remaining": 1, "percent": 50.0}


def test_research_sync_empty_job_marks_range_as_processed():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "600000", "name": "SPDB", "exchange": "SH", "status": "active"},
            ]
        )
    )
    repository.create_sync_job(
        "daily",
        "600000",
        "empty",
        start_date=date(2020, 1, 1),
        end_date=date(2024, 12, 31),
    )

    assert repository.next_research_sync_symbols(date(2021, 1, 1), date(2024, 1, 1)) == []
    assert repository.research_sync_progress(date(2021, 1, 1), date(2024, 1, 1)) == {
        "total": 1,
        "covered": 1,
        "remaining": 0,
        "percent": 100.0,
    }


def test_covered_research_symbols_returns_only_full_range_coverage():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "000001", "name": "Full", "exchange": "SZ", "status": "active"},
                {"symbol": "000002", "name": "Partial", "exchange": "SZ", "status": "active"},
                {"symbol": "000003", "name": "*ST Risk", "exchange": "SZ", "status": "active"},
            ]
        )
    )
    for symbol, dates in {
        "000001": (date(2024, 1, 1), date(2024, 12, 31)),
        "000002": (date(2024, 2, 1), date(2024, 12, 31)),
        "000003": (date(2024, 1, 1), date(2024, 12, 31)),
    }.items():
        repository.replace_daily_bars(
            symbol,
            dates[0],
            dates[1],
            pd.DataFrame(
                [
                    {
                        "symbol": symbol,
                        "trade_date": trade_date,
                        "open": 10.0,
                        "high": 10.0,
                        "low": 10.0,
                        "close": 10.0,
                        "volume": 1000.0,
                        "amount": 10000.0,
                    }
                    for trade_date in dates
                ]
            ),
        )

    assert repository.covered_research_symbols(
        date(2024, 1, 1),
        date(2024, 12, 31),
        limit=10,
    ) == ["000001"]


def test_covered_research_symbols_can_return_large_factor_universe():
    repository = make_repository()
    rows = [
        {
            "symbol": f"{i:06d}",
            "name": f"Name {i:06d}",
            "exchange": "BJ" if i % 10 == 0 else "SZ",
            "status": "active",
        }
        for i in range(1, 351)
    ]
    repository.upsert_stocks(pd.DataFrame(rows))
    for row in rows:
        symbol = row["symbol"]
        repository.replace_daily_bars(
            symbol,
            date(2024, 1, 1),
            date(2024, 12, 31),
            pd.DataFrame(
                [
                    {
                        "symbol": symbol,
                        "trade_date": trade_date,
                        "open": 10.0,
                        "high": 10.0,
                        "low": 10.0,
                        "close": 10.0,
                        "volume": 1000.0,
                        "amount": 10000.0,
                    }
                    for trade_date in (date(2024, 1, 1), date(2024, 12, 31))
                ]
            ),
        )

    symbols = repository.covered_research_symbols(
        date(2024, 1, 1),
        date(2024, 12, 31),
        limit=350,
    )

    assert len(symbols) == 350
    assert "000010" in symbols  # Beijing exchange is included by default.


def test_select_research_symbols_can_return_large_backtest_universe():
    repository = make_repository()
    repository.upsert_trading_calendar(
        pd.DataFrame(
            [
                {"trade_date": date(2024, 1, 1), "is_open": True},
                {"trade_date": date(2024, 1, 2), "is_open": True},
            ]
        )
    )
    rows = [
        {
            "symbol": f"{i:06d}",
            "name": f"Name {i:06d}",
            "exchange": "BJ" if i % 10 == 0 else "SZ",
            "status": "active",
        }
        for i in range(1, 351)
    ]
    repository.upsert_stocks(pd.DataFrame(rows))
    for row in rows:
        symbol = row["symbol"]
        repository.replace_daily_bars(
            symbol,
            date(2024, 1, 1),
            date(2024, 1, 2),
            pd.DataFrame(
                [
                    {
                        "symbol": symbol,
                        "trade_date": trade_date,
                        "open": 10.0,
                        "high": 10.0,
                        "low": 10.0,
                        "close": 10.0,
                        "volume": 1000.0,
                        "amount": 10000.0,
                    }
                    for trade_date in (date(2024, 1, 1), date(2024, 1, 2))
                ]
            ),
        )

    symbols = repository.select_research_symbols(
        date(2024, 1, 1),
        date(2024, 1, 2),
        limit=350,
    )

    assert len(symbols) == 350
    assert "000010" in symbols  # Beijing exchange is included by default.


def test_select_research_symbols_supports_single_trading_day():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [{"symbol": "000001", "name": "Single Day", "exchange": "SZ", "status": "active"}]
        )
    )
    repository.replace_daily_bars(
        "000001",
        date(2024, 1, 2),
        date(2024, 1, 2),
        pd.DataFrame(
            [{
                "symbol": "000001",
                "trade_date": date(2024, 1, 2),
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 1000.0,
                "amount": 10000.0,
            }]
        ),
    )

    assert repository.select_research_symbols(
        date(2024, 1, 2),
        date(2024, 1, 2),
    ) == ["000001"]


def test_select_research_symbols_excludes_chinese_st_prefix_without_space():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "000001", "name": "正常股份", "exchange": "SZ", "status": "active"},
                {"symbol": "000002", "name": "ST中珠", "exchange": "SZ", "status": "active"},
                {"symbol": "000003", "name": "*ST天创", "exchange": "SZ", "status": "active"},
            ]
        )
    )
    for symbol in ("000001", "000002", "000003"):
        repository.replace_daily_bars(
            symbol,
            date(2024, 1, 2),
            date(2024, 1, 2),
            pd.DataFrame(
                [{
                    "symbol": symbol,
                    "trade_date": date(2024, 1, 2),
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }]
            ),
        )

    assert repository.select_research_symbols(
        date(2024, 1, 2),
        date(2024, 1, 2),
    ) == ["000001"]


# ---------------------------------------------------------------------------
# select_research_symbols — trading-day-based coverage
# ---------------------------------------------------------------------------


def test_select_research_symbols_estimates_when_calendar_empty():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "000001", "name": "Full", "exchange": "SZ", "status": "active"},
                {"symbol": "000002", "name": "Short", "exchange": "SZ", "status": "active"},
            ]
        )
    )
    # 000001: 31 bars (every day of Jan)
    repository.replace_daily_bars(
        "000001",
        date(2024, 1, 1),
        date(2024, 1, 31),
        pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "trade_date": trade_date,
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
                for trade_date in [
                    date(2024, 1, d) for d in range(1, 32)
                ]
            ]
        ),
    )
    # 000002: only 2 bars
    repository.replace_daily_bars(
        "000002",
        date(2024, 1, 1),
        date(2024, 1, 2),
        pd.DataFrame(
            [
                {
                    "symbol": "000002",
                    "trade_date": trade_date,
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
                for trade_date in [date(2024, 1, 1), date(2024, 1, 2)]
            ]
        ),
    )

    # With no trading calendar the effective min ≈ max(2, int(22*0.3)) = 6
    # 000001 has 31 bars → included; 000002 has 2 bars → excluded
    # Default 80% coverage requires at least 4 of the 5 trading days.
    symbols = repository.select_research_symbols(
        date(2024, 1, 1),
        date(2024, 1, 31),
        limit=10,
    )

    assert symbols == ["000001"]


def test_select_research_symbols_uses_trading_calendar():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "000001", "name": "Covered", "exchange": "SZ", "status": "active"},
                {"symbol": "600000", "name": "Gappy", "exchange": "SH", "status": "active"},
            ]
        )
    )
    # 5 trading days in calendar
    repository.upsert_trading_calendar(
        pd.DataFrame(
            {
                "trade_date": [
                    date(2024, 1, 2),
                    date(2024, 1, 3),
                    date(2024, 1, 4),
                    date(2024, 1, 5),
                    date(2024, 1, 8),
                ],
                "is_open": [True] * 5,
            }
        )
    )
    # 000001: bars on all 5 trading days
    repository.replace_daily_bars(
        "000001",
        date(2024, 1, 2),
        date(2024, 1, 8),
        pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "trade_date": trade_date,
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
                for trade_date in [
                    date(2024, 1, 2),
                    date(2024, 1, 3),
                    date(2024, 1, 4),
                    date(2024, 1, 5),
                    date(2024, 1, 8),
                ]
            ]
        ),
    )
    # 600000: only 2 bars
    repository.replace_daily_bars(
        "600000",
        date(2024, 1, 2),
        date(2024, 1, 3),
        pd.DataFrame(
            [
                {
                    "symbol": "600000",
                    "trade_date": trade_date,
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
                for trade_date in [date(2024, 1, 2), date(2024, 1, 3)]
            ]
        ),
    )

    # 5 expected days × 0.3 = 1.5 → max(5, 1) = 5 effective_min
    # 000001 has 5 → included; 600000 has 2 → excluded
    symbols = repository.select_research_symbols(
        date(2024, 1, 1),
        date(2024, 1, 8),
        limit=10,
    )

    assert symbols == ["000001"]


def test_select_research_symbols_orders_by_bar_count_desc():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "000001", "name": "Best", "exchange": "SZ", "status": "active"},
                {"symbol": "600000", "name": "Good", "exchange": "SH", "status": "active"},
            ]
        )
    )
    # 000001: 30 bars, 600000: 15 bars — both pass a low min but 000001
    # should come first because it has better coverage.
    repository.replace_daily_bars(
        "000001",
        date(2024, 1, 1),
        date(2024, 1, 30),
        pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "trade_date": date(2024, 1, d),
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
                for d in range(1, 31)
            ]
        ),
    )
    repository.replace_daily_bars(
        "600000",
        date(2024, 1, 16),
        date(2024, 1, 30),
        pd.DataFrame(
            [
                {
                    "symbol": "600000",
                    "trade_date": date(2024, 1, d),
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
                for d in range(16, 31)
            ]
        ),
    )

    symbols = repository.select_research_symbols(
        date(2024, 1, 1),
        date(2024, 1, 31),
        min_trading_days=5,
        limit=10,
    )

    # 000001 has 30 bars (best coverage first), then 600000 with 15
    assert symbols == ["000001", "600000"]


def test_select_research_symbols_end_date_beyond_data():
    """Symbols whose latest bar falls before end_date still qualify."""
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "000001", "name": "Test", "exchange": "SZ", "status": "active"},
            ]
        )
    )
    # Bars only through Jan-15 but the request extends to Jan-31
    repository.replace_daily_bars(
        "000001",
        date(2024, 1, 1),
        date(2024, 1, 15),
        pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "trade_date": date(2024, 1, d),
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
                for d in range(1, 16)
            ]
        ),
    )

    # 15 bars should pass the effective_min threshold
    symbols = repository.select_research_symbols(
        date(2024, 1, 1),
        date(2024, 1, 31),
        limit=10,
    )

    assert symbols == ["000001"]


def test_select_research_symbols_start_date_before_data():
    """Symbols whose first bar starts after start_date still qualify."""
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "000001", "name": "NewListing", "exchange": "SZ", "status": "active"},
            ]
        )
    )
    # Bars start Jan-15 but request begins Jan-01
    repository.replace_daily_bars(
        "000001",
        date(2024, 1, 15),
        date(2024, 1, 31),
        pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "trade_date": date(2024, 1, d),
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
                for d in range(15, 32)
            ]
        ),
    )

    symbols = repository.select_research_symbols(
        date(2024, 1, 1),
        date(2024, 1, 31),
        limit=10,
    )

    assert symbols == ["000001"]


def test_select_research_symbols_min_coverage_ratio_respected():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "000001", "name": "High", "exchange": "SZ", "status": "active"},
                {"symbol": "000002", "name": "Low", "exchange": "SZ", "status": "active"},
            ]
        )
    )
    repository.upsert_trading_calendar(
        pd.DataFrame(
            {"trade_date": [date(2024, 1, d) for d in range(2, 32) if d % 7 not in (0, 6)],
             "is_open": [True] * 22}
        )
    )
    # ~22 trading days in Jan 2024
    repository.replace_daily_bars(
        "000001",
        date(2024, 1, 2),
        date(2024, 1, 31),
        pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "trade_date": trade_date,
                    "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
                    "volume": 1000.0, "amount": 10000.0,
                }
                for trade_date in [date(2024, 1, d) for d in range(2, 32) if d % 7 not in (0, 6)]
            ]
        ),
    )
    # 000002 has only 2 bars
    repository.replace_daily_bars(
        "000002",
        date(2024, 1, 2),
        date(2024, 1, 3),
        pd.DataFrame(
            [
                {
                    "symbol": "000002",
                    "trade_date": trade_date,
                    "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
                    "volume": 1000.0, "amount": 10000.0,
                }
                for trade_date in [date(2024, 1, 2), date(2024, 1, 3)]
            ]
        ),
    )

    # Require 90% coverage (~20 bars) → only 000001 qualifies
    symbols = repository.select_research_symbols(
        date(2024, 1, 1),
        date(2024, 1, 31),
        min_coverage_ratio=0.9,
        limit=10,
    )

    assert symbols == ["000001"]


def test_select_research_symbols_excludes_risk_names():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "000001", "name": "Normal", "exchange": "SZ", "status": "active"},
                {"symbol": "000002", "name": "*ST Risk", "exchange": "SZ", "status": "active"},
            ]
        )
    )
    for symbol, d1, d2 in [
        ("000001", date(2024, 1, 1), date(2024, 1, 31)),
        ("000002", date(2024, 1, 1), date(2024, 1, 31)),
    ]:
        repository.replace_daily_bars(
            symbol,
            d1,
            d2,
            pd.DataFrame(
                [
                    {
                        "symbol": symbol,
                        "trade_date": date(2024, 1, d),
                        "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
                        "volume": 1000.0, "amount": 10000.0,
                    }
                    for d in range(1, 32)
                ]
            ),
        )

    symbols = repository.select_research_symbols(
        date(2024, 1, 1),
        date(2024, 1, 31),
        limit=10,
        exclude_risk_names=True,
    )

    assert symbols == ["000001"]


def test_select_research_symbols_empty_when_no_symbols_pass():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "000001", "name": "TooFewBars", "exchange": "SZ", "status": "active"},
            ]
        )
    )
    # Only 1 bar for a 31-day range → won't pass even the lenient default
    repository.replace_daily_bars(
        "000001",
        date(2024, 1, 15),
        date(2024, 1, 15),
        pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "trade_date": date(2024, 1, 15),
                    "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
                    "volume": 1000.0, "amount": 10000.0,
                }
            ]
        ),
    )

    symbols = repository.select_research_symbols(
        date(2024, 1, 1),
        date(2024, 1, 31),
        min_trading_days=10,
        limit=10,
    )

    assert symbols == []


# ---------------------------------------------------------------------------
# research_pool_diagnostics
# ---------------------------------------------------------------------------


def test_research_pool_diagnostics_reports_coverage_gaps():
    repository = make_repository()
    repository.upsert_stocks(
        pd.DataFrame(
            [
                {"symbol": "000001", "name": "Test", "exchange": "SZ", "status": "active"},
                {"symbol": "600000", "name": "Other", "exchange": "SH", "status": "active"},
            ]
        )
    )
    # Only 000001 has bars, and only a few
    repository.replace_daily_bars(
        "000001",
        date(2024, 1, 2),
        date(2024, 1, 5),
        pd.DataFrame(
            [
                {
                    "symbol": "000001",
                    "trade_date": trade_date,
                    "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
                    "volume": 1000.0, "amount": 10000.0,
                }
                for trade_date in [date(2024, 1, d) for d in range(2, 6)]
            ]
        ),
    )

    diag = repository.research_pool_diagnostics(
        date(2024, 1, 1),
        date(2024, 12, 31),
    )

    assert diag["eligible_stocks"] == 2
    assert diag["db_symbols_with_bars"] == 1
    assert diag["db_min_bar_date"] == date(2024, 1, 2)
    assert diag["db_max_bar_date"] == date(2024, 1, 5)
    assert len(diag["top_symbols_in_range"]) == 1
    assert diag["top_symbols_in_range"][0]["symbol"] == "000001"
    assert "hint" in diag
