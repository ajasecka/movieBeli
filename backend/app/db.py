"""Database engine, session factory, and schema bootstrap."""
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from . import config

engine = create_engine(config.DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
Base = declarative_base()


@contextmanager
def session_scope():
    """Transactional session context manager."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create the pg_trgm extension, all tables, and search indexes."""
    from . import models  # noqa: F401 — ensure models are registered

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        # Trigram GIN index makes ILIKE '%query%' fast over ~1M titles.
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS movie_index_title_trgm "
                "ON movie_index USING gin (lower(original_title) gin_trgm_ops)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS movie_index_popularity "
                "ON movie_index (popularity DESC)"
            )
        )
