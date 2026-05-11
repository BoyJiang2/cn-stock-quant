from datetime import date

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.entities import DailyBar, Stock, SyncJob


class MarketDataRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_stocks(self, stocks: pd.DataFrame) -> int:
        count = 0
        for row in stocks.to_dict("records"):
            stock = self.session.get(Stock, row["symbol"]) or Stock(symbol=row["symbol"], name=row["name"])
            stock.name = row["name"]
            stock.exchange = row.get("exchange", "")
            stock.status = row.get("status", "active")
            self.session.merge(stock)
            count += 1
        self.session.commit()
        return count

    def replace_daily_bars(self, symbol: str, start_date: date, end_date: date, bars: pd.DataFrame) -> int:
        self.session.execute(
            delete(DailyBar).where(
                DailyBar.symbol == symbol,
                DailyBar.trade_date >= start_date,
                DailyBar.trade_date <= end_date,
            )
        )
        records = [
            DailyBar(
                symbol=row["symbol"],
                trade_date=row["trade_date"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0.0)),
                amount=float(row.get("amount", 0.0)),
                adj=row.get("adj", "qfq"),
            )
            for row in bars.to_dict("records")
        ]
        self.session.add_all(records)
        self.session.commit()
        return len(records)

    def list_stocks(self, limit: int = 100, keyword: str | None = None) -> list[Stock]:
        stmt = select(Stock).order_by(Stock.symbol).limit(limit)
        if keyword:
            stmt = select(Stock).where(Stock.symbol.contains(keyword) | Stock.name.contains(keyword)).order_by(Stock.symbol).limit(limit)
        return list(self.session.scalars(stmt))

    def daily_bars(self, symbols: list[str], start_date: date, end_date: date) -> pd.DataFrame:
        stmt = (
            select(DailyBar)
            .where(DailyBar.symbol.in_(symbols), DailyBar.trade_date >= start_date, DailyBar.trade_date <= end_date)
            .order_by(DailyBar.trade_date, DailyBar.symbol)
        )
        rows = list(self.session.scalars(stmt))
        return pd.DataFrame(
            [
                {
                    "symbol": row.symbol,
                    "trade_date": row.trade_date,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                    "amount": row.amount,
                }
                for row in rows
            ]
        )

    def daily_bar_count(self, symbol: str, start_date: date, end_date: date) -> int:
        stmt = select(func.count(DailyBar.id)).where(
            DailyBar.symbol == symbol,
            DailyBar.trade_date >= start_date,
            DailyBar.trade_date <= end_date,
        )
        return int(self.session.scalar(stmt) or 0)

    def create_sync_job(
        self,
        job_type: str,
        target: str,
        status: str,
        records: int = 0,
        message: str = "",
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> SyncJob:
        job = SyncJob(
            job_type=job_type,
            target=target,
            status=status,
            records=records,
            message=message[:500],
            start_date=start_date,
            end_date=end_date,
        )
        self.session.add(job)
        self.session.commit()
        return job

    def list_sync_jobs(self, limit: int = 100) -> list[SyncJob]:
        stmt = select(SyncJob).order_by(SyncJob.created_at.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def daily_status(self, limit: int = 200) -> list[dict]:
        stmt = (
            select(
                DailyBar.symbol,
                func.min(DailyBar.trade_date).label("start_date"),
                func.max(DailyBar.trade_date).label("end_date"),
                func.count(DailyBar.id).label("bar_count"),
            )
            .group_by(DailyBar.symbol)
            .order_by(DailyBar.symbol)
            .limit(limit)
        )
        return [
            {
                "symbol": row.symbol,
                "start_date": row.start_date,
                "end_date": row.end_date,
                "bar_count": row.bar_count,
            }
            for row in self.session.execute(stmt)
        ]
