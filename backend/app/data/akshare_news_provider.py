from __future__ import annotations

import hashlib
from datetime import datetime

import pandas as pd

from app.data.news import NEWS_COLUMNS
from app.data.news_sentiment import classify_news_text
from app.data.news_text import clean_news_payload, clean_news_text
from app.data.symbols import normalize_a_share_symbol


class AkShareNewsProvider:
    """News provider backed by AkShare public endpoints."""

    def stock_news(
        self,
        symbol: str,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> pd.DataFrame:
        import akshare as ak

        normalized_symbol = normalize_a_share_symbol(symbol)
        raw = ak.stock_news_em(symbol=normalized_symbol)
        if raw is None or raw.empty:
            return pd.DataFrame(columns=NEWS_COLUMNS)

        fetched_at = datetime.utcnow()
        rows: list[dict] = []
        for item in raw.to_dict("records"):
            published_at = _parse_datetime(item.get("发布时间"))
            if published_at is None:
                continue
            if start_at is not None and published_at < start_at:
                continue
            if end_at is not None and published_at > end_at:
                continue

            title = clean_news_text(item.get("新闻标题"))
            body = clean_news_text(item.get("新闻内容"))
            url = str(item.get("新闻链接") or "").strip()
            source_name = clean_news_text(item.get("文章来源"))
            event_type, sentiment_label, sentiment_score = classify_news_text(title, body)
            rows.append(
                {
                    "source": "eastmoney_stock_news",
                    "source_id": _source_id(normalized_symbol, title, published_at, url),
                    "symbol": normalized_symbol,
                    "title": title,
                    "body": body,
                    "url": url,
                    "event_type": event_type,
                    "sentiment_label": sentiment_label,
                    "sentiment_score": sentiment_score,
                    "relevance_score": 1.0,
                    "published_at": published_at,
                    "fetched_at": fetched_at,
                    "raw": clean_news_payload({**item, "source_name": source_name}),
                }
            )
        return pd.DataFrame(rows, columns=NEWS_COLUMNS)


def _parse_datetime(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _source_id(symbol: str, title: str, published_at: datetime, url: str) -> str:
    if url:
        return url
    payload = f"{symbol}|{published_at.isoformat()}|{title}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()
