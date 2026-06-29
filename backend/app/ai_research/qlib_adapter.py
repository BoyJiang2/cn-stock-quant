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


def predictions_to_weights(
    predictions: pd.DataFrame,
    *,
    as_of_date,
    score_column: str = "score",
    top_n: int = 30,
    gross_exposure: float = 1.0,
    max_position_weight: float = 0.1,
    long_only_positive: bool = True,
) -> dict[str, float]:
    """Convert model prediction scores into target weights.

    The adapter is intentionally narrow: it consumes already-versioned model
    predictions for one decision date and returns the existing strategy
    contract, ``dict[symbol -> weight]``. It never creates orders.
    """
    if predictions.empty:
        return {}
    if score_column not in predictions.columns:
        raise ValueError(f"predictions missing score column: {score_column}")
    if top_n < 1:
        raise ValueError("top_n must be >= 1")
    if gross_exposure <= 0:
        raise ValueError("gross_exposure must be > 0")
    if max_position_weight <= 0:
        raise ValueError("max_position_weight must be > 0")

    frame = predictions.copy()
    date_column = "datetime" if "datetime" in frame.columns else "trade_date"
    symbol_column = "instrument" if "instrument" in frame.columns else "symbol"
    if date_column not in frame.columns or symbol_column not in frame.columns:
        raise ValueError("predictions must include datetime/instrument or trade_date/symbol")

    target_date = pd.Timestamp(as_of_date).normalize()
    frame[date_column] = pd.to_datetime(frame[date_column]).dt.normalize()
    frame = frame.loc[frame[date_column] == target_date, [symbol_column, score_column]]
    frame = frame.dropna(subset=[symbol_column, score_column])
    frame[score_column] = pd.to_numeric(frame[score_column], errors="coerce")
    frame = frame.dropna(subset=[score_column])
    if long_only_positive:
        frame = frame[frame[score_column] > 0]
    if frame.empty:
        return {}

    frame = (
        frame.sort_values([score_column, symbol_column], ascending=[False, True])
        .drop_duplicates(subset=[symbol_column], keep="first")
        .head(top_n)
    )
    if frame.empty:
        return {}

    per_name_cap = min(float(max_position_weight), float(gross_exposure) / len(frame))
    raw_weights = frame[score_column].clip(lower=0.0)
    if raw_weights.sum() <= 0:
        equal_weight = min(per_name_cap, float(gross_exposure) / len(frame))
        return {str(symbol): equal_weight for symbol in frame[symbol_column]}

    scaled = raw_weights / raw_weights.sum() * float(gross_exposure)
    capped = scaled.clip(upper=per_name_cap)
    return {
        str(symbol): float(weight)
        for symbol, weight in zip(frame[symbol_column], capped, strict=True)
        if weight > 0
    }
