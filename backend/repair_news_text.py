from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime

from app.core.database import SessionLocal
from app.data.news_text import clean_news_text, has_mojibake
from app.models.entities import NewsItem


@dataclass
class RepairSummary:
    scanned: int = 0
    updated: int = 0
    remaining_suspect: int = 0
    dry_run: bool = False


def repair_news_text(*, dry_run: bool = False) -> RepairSummary:
    summary = RepairSummary(dry_run=dry_run)
    session = SessionLocal()
    try:
        rows = list(session.query(NewsItem).order_by(NewsItem.id))
        summary.scanned = len(rows)
        for row in rows:
            cleaned_title = clean_news_text(row.title)
            cleaned_body = clean_news_text(row.body)
            cleaned_event_type = clean_news_text(row.event_type)
            cleaned_sentiment_label = clean_news_text(row.sentiment_label)
            cleaned_raw = clean_news_text(row.raw)
            if any(has_mojibake(value) for value in (cleaned_title, cleaned_body, cleaned_raw)):
                summary.remaining_suspect += 1
            changed = (
                cleaned_title != (row.title or "")
                or cleaned_body != (row.body or "")
                or cleaned_event_type != (row.event_type or "")
                or cleaned_sentiment_label != (row.sentiment_label or "")
                or cleaned_raw != (row.raw or "")
            )
            if not changed:
                continue
            summary.updated += 1
            if not dry_run:
                row.title = cleaned_title
                row.body = cleaned_body
                row.event_type = cleaned_event_type
                row.sentiment_label = cleaned_sentiment_label
                row.raw = cleaned_raw
                row.updated_at = datetime.utcnow()
        if dry_run:
            session.rollback()
        else:
            session.commit()
        return summary
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair mojibake in persisted news text.")
    parser.add_argument("--dry-run", action="store_true", help="Scan without writing changes.")
    args = parser.parse_args()
    print(asdict(repair_news_text(dry_run=args.dry_run)))


if __name__ == "__main__":
    main()
