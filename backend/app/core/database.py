from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import DATA_DIR, settings
from app.models import Base  # noqa: F401  (imports entities + pit tables)


DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    try:
        Base.metadata.create_all(bind=engine)
    except OperationalError as exc:
        # Multiple CLI processes may start at the same time after a new model is
        # added.  SQLite can race between the checkfirst inspection and CREATE
        # TABLE, yielding "table ... already exists" in the losing process.
        # Re-running create_all is safe and lets any remaining tables be
        # created without failing the whole command.
        if "already exists" not in str(exc).lower():
            raise
        Base.metadata.create_all(bind=engine)


def get_session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
