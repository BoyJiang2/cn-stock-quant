import pandas as pd

from compare_ml_news_filter import (
    _comparison,
    _filtered_symbols_summary,
    _news_risk_summary,
    to_markdown,
)


def test_comparison_reports_metric_and_trade_deltas():
    baseline = {
        "metrics": {
            "total_return": 0.10,
            "annual_return": 0.12,
            "max_drawdown": -0.20,
            "sharpe": 1.0,
            "excess_return": 0.05,
        },
        "trade_stats": {"trade_count": 10},
        "traded_symbols": ["000001", "000002"],
    }
    news_filter = {
        "metrics": {
            "total_return": 0.08,
            "annual_return": 0.10,
            "max_drawdown": -0.12,
            "sharpe": 1.2,
            "excess_return": 0.04,
        },
        "trade_stats": {"trade_count": 6},
        "traded_symbols": ["000002", "000003"],
    }

    comparison = _comparison(baseline, news_filter)

    assert round(comparison["total_return_delta"], 6) == -0.02
    assert round(comparison["max_drawdown_delta"], 6) == 0.08
    assert round(comparison["sharpe_delta"], 6) == 0.2
    assert comparison["trade_count_delta"] == -4
    assert comparison["turnover_delta"] == 0
    assert comparison["winner"] == "db_news_filter"
    assert comparison["baseline_only_traded_symbols"] == ["000001"]
    assert comparison["news_filter_only_traded_symbols"] == ["000003"]


def test_news_risk_summary_counts_risk_symbols():
    frame = pd.DataFrame(
        [
            {"symbol": "000001", "event_type": "risk_news", "sentiment_label": "", "sentiment_score": None},
            {"symbol": "000002", "event_type": "stock_news", "sentiment_label": "negative", "sentiment_score": -0.1},
            {"symbol": "000003", "event_type": "stock_news", "sentiment_label": "", "sentiment_score": 0.2},
        ]
    )

    summary = _news_risk_summary(frame)

    assert summary["rows"] == 3
    assert summary["risk_rows"] == 2
    assert summary["risk_symbol_count"] == 2
    assert summary["risk_symbols"] == ["000001", "000002"]


def test_filtered_symbols_summary_groups_risk_news_by_symbol():
    frame = pd.DataFrame(
        [
            {
                "symbol": "002156",
                "known_at": "2026-05-20 10:00:00",
                "event_type": "risk_news",
                "sentiment_label": "risk",
                "sentiment_score": -0.4,
                "title": "risk one",
            },
            {
                "symbol": "002156",
                "known_at": "2026-05-21 10:00:00",
                "event_type": "negative_news",
                "sentiment_label": "negative",
                "sentiment_score": -0.8,
                "title": "risk two",
            },
            {
                "symbol": "000001",
                "known_at": "2026-05-20 10:00:00",
                "event_type": "stock_news",
                "sentiment_label": "",
                "sentiment_score": 0.1,
                "title": "normal",
            },
        ]
    )

    summary = _filtered_symbols_summary(frame)

    assert len(summary) == 1
    assert summary[0]["symbol"] == "002156"
    assert summary[0]["first_blocked_date"] == "2026-05-20"
    assert summary[0]["blocked_days"] == 2
    assert summary[0]["news_count"] == 2
    assert summary[0]["latest_title"] == "risk two"


def test_to_markdown_includes_variants_and_comparison():
    report = {
        "metadata": {
            "strategy": "ml_score_rank",
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
            "selected_symbol_count": 2,
        },
        "news_risk": {"risk_symbol_count": 1},
        "filtered_symbols": [
            {
                "symbol": "002156",
                "first_blocked_date": "2026-05-20",
                "blocked_days": 2,
                "news_count": 3,
                "latest_title": "risk",
                "event_type": "risk_news",
                "sentiment_score": -0.4,
            }
        ],
        "runs": {
            "baseline": {
                "metrics": {"total_return": 0.1, "max_drawdown": -0.2, "sharpe": 1.0},
                "trade_stats": {"trade_count": 10, "turnover_on_initial_cash": 1.5},
            },
            "db_news_filter": {
                "metrics": {"total_return": 0.08, "max_drawdown": -0.1, "sharpe": 1.2},
                "trade_stats": {"trade_count": 6, "turnover_on_initial_cash": 1.0},
            },
        },
        "comparison": {"total_return_delta": -0.02},
    }

    markdown = to_markdown(report)

    assert "# ML News Filter Comparison" in markdown
    assert "baseline" in markdown
    assert "db_news_filter" in markdown
    assert "total_return_delta" in markdown
    assert "## Filtered Symbols" in markdown
    assert "002156" in markdown
