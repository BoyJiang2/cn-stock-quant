import argparse
import json
from datetime import date
from pathlib import Path

import run_2026_research_pipeline as pipeline


def test_factor_command_uses_quick_pool_and_requested_factors(tmp_path: Path):
    args = argparse.Namespace(
        python="python",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 18),
        quick=True,
        pool_max_symbols=6000,
    )

    command = pipeline._factor_command(
        args,
        "batch",
        ["amount_stability_20d", "amount_volatility_20d"],
        tmp_path / "batch.json",
    )

    assert "--pool-max-symbols" in command
    assert command[command.index("--pool-max-symbols") + 1] == "300"
    assert command.count("--factor") == 2
    assert "amount_stability_20d" in command
    assert "amount_volatility_20d" in command


def test_write_summary_includes_factor_and_strategy_sections(tmp_path: Path):
    factor_dir = tmp_path / "full" / "factors"
    strategy_dir = tmp_path / "full" / "strategies"
    factor_dir.mkdir(parents=True)
    strategy_dir.mkdir(parents=True)

    factor_path = factor_dir / "all_builtin.json"
    factor_path.write_text(
        json.dumps(
            {
                "metadata": {
                    "selected_symbol_count": 5188,
                    "factor_count": 34,
                    "factor_panel_rows": 565492,
                },
                "summaries": [
                    {
                        "name": "amount_stability_20d",
                        "rankic_mean": 0.046843,
                        "rankic_ir": 0.709822,
                        "long_short_return": 0.004579,
                        "long_short_turnover": 0.109115,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    strategy_path = strategy_dir / "inverse_momentum_000300_default.json"
    strategy_path.write_text(
        json.dumps(
            {
                "metadata": {"benchmark_symbol": "000300"},
                "runs": [
                    {
                        "metrics": {
                            "total_return": -0.085279,
                            "benchmark_return": 0.047449,
                            "excess_return": -0.132728,
                            "max_drawdown": -0.128037,
                            "sharpe": -1.788838,
                        },
                        "trade_stats": {"turnover_on_initial_cash": 4.173801},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary_path = tmp_path / "summary.md"
    pipeline.write_summary([factor_path, strategy_path], summary_path)

    summary = summary_path.read_text(encoding="utf-8")
    assert "amount_stability_20d" in summary
    assert "inverse_momentum_000300_default.json" in summary
    assert "000300" in summary
