"""
Shared pytest fixtures.

Tests run against a REAL PostgreSQL database (a separate, disposable
database from dev, created fresh for the test session and torn down
after), per the project's "no SQLite substitute" requirement. This proves
migrations, constraints, and queries behave the same in tests as they will
in production.

Privilege separation (important):
  CREATE DATABASE / DROP DATABASE are administrative operations. The
  application's own role (POSTGRES_USER, e.g. `invoice_app`) is a
  least-privilege role and must NOT be granted CREATEDB or SUPERUSER just
  to make testing convenient. Instead, a separate admin/bootstrap
  credential (POSTGRES_ADMIN_USER / POSTGRES_ADMIN_PASSWORD -- defaults to
  the standard `postgres` superuser role that every fresh PostgreSQL
  install already has) is used ONLY here, ONLY to create and drop the
  disposable test database. Once created, the admin role immediately hands
  ownership to POSTGRES_USER, so every actual test query still runs as the
  same low-privilege application role used in production -- privileges are
  never silently escalated for the app itself.
"""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

os.environ["POSTGRES_DB"] = "invoice_compliance_test_db"

from app.core.config import get_settings  # noqa: E402
from app.core.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

get_settings.cache_clear()
settings = get_settings()


def _admin_engine():
    """
    Connects as the admin/bootstrap role to Postgres's default 'postgres'
    maintenance database. CREATE DATABASE / DROP DATABASE cannot run inside
    a transaction block, hence AUTOCOMMIT.
    """
    return create_engine(settings.ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT", future=True)


@pytest.fixture(scope="session", autouse=True)
def test_database():
    admin_engine = _admin_engine()
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{settings.POSTGRES_DB}"'))
        # OWNER is set to the application role (invoice_app) so that role
        # -- which has no CREATEDB privilege of its own -- fully owns the
        # resulting database and can create tables/indexes in it (Postgres
        # 15+ no longer grants CREATE on the public schema to non-owners
        # by default).
        conn.execute(
            text(f'CREATE DATABASE "{settings.POSTGRES_DB}" OWNER "{settings.POSTGRES_USER}"')
        )
    admin_engine.dispose()

    # All actual schema creation and every test query below run as the
    # normal application role (POSTGRES_USER), exactly as in production.
    engine = create_engine(settings.DATABASE_URL, future=True)
    Base.metadata.create_all(bind=engine)

    yield engine

    engine.dispose()

    cleanup_engine = _admin_engine()
    try:
        with cleanup_engine.connect() as conn:
            # Terminate any lingering connections so DROP DATABASE doesn't
            # fail with "database is being accessed by other users".
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :dbname AND pid <> pg_backend_pid()"
                ),
                {"dbname": settings.POSTGRES_DB},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{settings.POSTGRES_DB}"'))
    finally:
        cleanup_engine.dispose()


@pytest.fixture()
def db_session(test_database):
    TestSessionLocal = sessionmaker(bind=test_database, autoflush=False, autocommit=False, future=True)
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean_tables(test_database):
    """
    Ensures every test starts with empty tables.

    db_session commits are real, committed PostgreSQL writes (not wrapped in
    a transaction that gets rolled back), so without this, data written by
    one test (e.g. an uploaded invoice with a particular file_hash) would
    still be visible to a later test that happens to write the same content
    -- which would be silently misinterpreted as a duplicate-file scenario.
    Deletes in reverse table-dependency order to respect foreign keys.
    """
    with test_database.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    yield


@pytest.fixture()
def client(db_session):
    from fastapi.testclient import TestClient

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
