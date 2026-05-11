from datetime import date
from time import sleep

import pandas as pd

from app.data.symbols import normalize_a_share_symbol


class AkShareProvider:
    def stock_list(self) -> pd.DataFrame:
        import akshare as ak

        raw = ak.stock_info_a_code_name()
        frame = raw.rename(columns={"code": "symbol", "name": "name"})
        frame["exchange"] = frame["symbol"].map(_guess_exchange)
        frame["status"] = "active"
        return frame[["symbol", "name", "exchange", "status"]]

    def daily_bars(self, symbol: str, start_date: date, end_date: date, adjust: str = "qfq") -> pd.DataFrame:
        import akshare as ak

        normalized_symbol = normalize_a_share_symbol(symbol)
        raw = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                raw = ak.stock_zh_a_hist(
                    symbol=normalized_symbol,
                    period="daily",
                    start_date=start_date.strftime("%Y%m%d"),
                    end_date=end_date.strftime("%Y%m%d"),
                    adjust=adjust,
                )
                break
            except Exception as exc:
                last_error = exc
                sleep(0.8 * (attempt + 1))

        if raw is None:
            raise RuntimeError(f"AkShare daily bars request failed for {normalized_symbol}") from last_error

        if raw.empty:
            return pd.DataFrame(columns=["trade_date", "open", "high", "low", "close", "volume", "amount"])

        frame = raw.rename(
            columns={
                "日期": "trade_date",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
            }
        )
        frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
        frame["symbol"] = normalized_symbol
        frame["adj"] = adjust
        return frame[["symbol", "trade_date", "open", "high", "low", "close", "volume", "amount", "adj"]]


def _guess_exchange(symbol: str) -> str:
    if symbol.startswith(("6", "9")):
        return "SH"
    if symbol.startswith(("0", "2", "3")):
        return "SZ"
    if symbol.startswith(("4", "8")):
        return "BJ"
    return ""
