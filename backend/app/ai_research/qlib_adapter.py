import pandas as pd


def to_qlib_frame(factor_panel: pd.DataFrame) -> pd.DataFrame:
    """Convert a factor panel to Qlib-style ``datetime/instrument`` columns."""
    if not isinstance(factor_panel.index, pd.MultiIndex):
        raise ValueError("factor_panel must use a MultiIndex")
    if list(factor_panel.index.names) != ["trade_date", "symbol"]:
        raise ValueError("factor_panel index must be named trade_date and symbol")
    frame = factor_panel.reset_index().rename(
        columns={"trade_date": "datetime", "symbol": "instrument"}
    )
    frame["datetime"] = pd.to_datetime(frame["datetime"])
    return frame.sort_values(["datetime", "instrument"]).reset_index(drop=True)
