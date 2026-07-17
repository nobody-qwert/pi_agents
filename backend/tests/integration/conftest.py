"""PostgreSQL-only fixtures for repository integration tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from orchestrator.persistence import PostgresUnitOfWork

BACKEND_DIR = Path(__file__).parents[2]


@pytest.fixture(scope="session")
def postgres_database_url() -> str:
    """Require an explicit disposable test database; never default a production URL."""
    database_url = os.environ.get("POSTGRES_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip(
            "POSTGRES_TEST_DATABASE_URL is required for PostgreSQL integration tests"
        )
    return database_url


@pytest.fixture(scope="session")
def migrated_postgres_database(postgres_database_url: str) -> Iterator[str]:
    """Recreate the explicitly designated test database at the migration head."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", postgres_database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield postgres_database_url
    command.downgrade(config, "base")


@pytest.fixture
def postgres_uow(migrated_postgres_database: str) -> Iterator[PostgresUnitOfWork]:
    """Create a transaction-scoped adapter against the migrated test database."""
    engine = create_engine(migrated_postgres_database)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE runs CASCADE"))
    unit_of_work = PostgresUnitOfWork(migrated_postgres_database)
    try:
        yield unit_of_work
    finally:
        unit_of_work.close()
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE runs CASCADE"))
        engine.dispose()
