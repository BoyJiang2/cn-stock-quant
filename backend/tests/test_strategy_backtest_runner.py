import argparse

from run_strategy_backtest import _grid_params


def test_grid_params_accepts_utf8_bom_json(tmp_path):
    path = tmp_path / "grid.json"
    path.write_bytes(
        b'\xef\xbb\xbf[{"label":"bom","top_n":30}]'
    )
    args = argparse.Namespace(
        param=["min_price=1", "hold_rank_multiplier=1.5"],
        grid_json=path,
    )

    assert _grid_params(args) == [
        {
            "label": "bom",
            "top_n": 30,
            "min_price": 1,
            "hold_rank_multiplier": 1.5,
        }
    ]
