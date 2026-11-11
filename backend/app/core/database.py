"""
Database engine and session management (PostgreSQL via SQLAlchemy).

This is the ONLY place the SQLAlchemy engine is constructed. Every other
module obtains a session through get_db() (FastAPI dependency) so that
connection lifecycle is managed consistently and tests can override it.
"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,   # detects stale/dropped connections before using them
    future=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)


class Base(DeclarativeBase):
    """Declarative base class every ORM model inherits from."""
    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
