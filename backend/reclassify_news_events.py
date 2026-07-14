from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime

from app.core.database import SessionLocal
from app.data.news_sentiment import classify_news_text
from app.models.entities import NewsItem


@dataclass
class ReclassifySummary:
    scanned: int = 0
    updated: int = 0
    dry_run: bool = False
    event_type_counts: dict[str, int] = field(default_factory=dict)


def reclassify_news_events(*, dry_run: bool = False) -> ReclassifySummary:
    summary = ReclassifySummary(dry_run=dry_run)
    event_type_counts: Counter[str] = Counter()
    session = SessionLocal()
    try:
        rows = list(session.query(NewsItem).order_by(NewsItem.id))
        summary.scanned = len(rows)
        for row in rows:
            event_type, sentiment_label, sentiment_score = classify_news_text(
                row.title or "", row.body or ""
            )
            event_type_counts[event_type] += 1
            changed = (
                row.event_type != event_type
                or row.sentiment_label != sentiment_label
                or row.sentiment_score != sentiment_score
            )
            if not changed:
                continue
            summary.updated += 1
            if not dry_run:
                row.event_type = event_type
                row.sentiment_label = sentiment_label
                row.sentiment_score = sentiment_score
                row.updated_at = datetime.utcnow()
        summary.event_type_counts = dict(sorted(event_type_counts.items()))
        if dry_run:
            session.rollback()
        else:
            session.commit()
        return summary
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reclassify persisted news with the current event taxonomy."
    )
    parser.add_argument("--dry-run", action="store_true", help="Scan without writing changes.")
    args = parser.parse_args()
    print(asdict(reclassify_news_events(dry_run=args.dry_run)))


if __name__ == "__main__":
    main()
