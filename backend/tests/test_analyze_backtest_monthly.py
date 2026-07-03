from analyze_backtest_monthly import monthly_report


def test_monthly_report_computes_strategy_and_excess_returns():
    backtest = {
        "metadata": {"strategy": "demo", "benchmark_symbol": "000300"},
        "runs": [
            {
                "parameters": {},
                "metrics": {},
                "equity_curve": [
                    {"trade_date": "2026-01-05", "equity": 100.0, "drawdown": 0.0},
                    {"trade_date": "2026-01-31", "equity": 110.0, "drawdown": 0.0},
                    {"trade_date": "2026-02-02", "equity": 110.0, "drawdown": 0.0},
                    {"trade_date": "2026-02-28", "equity": 99.0, "drawdown": -0.1},
                ],
                "benchmark_curve": [
                    {"trade_date": "2026-01-05", "equity": 100.0},
                    {"trade_date": "2026-01-31", "equity": 105.0},
                    {"trade_date": "2026-02-02", "equity": 105.0},
                    {"trade_date": "2026-02-28", "equity": 100.0},
                ],
            }
        ],
    }

    report = monthly_report(backtest)

    assert report["summary"]["months"] == 2
    assert report["summary"]["positive_excess_months"] == 1
    assert report["summary"]["negative_excess_months"] == 1
    assert round(report["monthly"][0]["strategy_return"], 6) == 0.1
    assert round(report["monthly"][0]["excess_return"], 6) == 0.05
