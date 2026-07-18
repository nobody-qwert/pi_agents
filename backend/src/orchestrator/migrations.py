"""Explicit Alembic startup boundary for the API and runner containers."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def upgrade_database(database_url: str, config_path: Path) -> None:
    """Apply committed migrations before serving requests."""
    if not config_path.is_file():
        raise RuntimeError(f"Alembic configuration is missing: {config_path}")
    migrations = config_path.parent / "migrations"
    if not migrations.is_dir():
        raise RuntimeError(f"Alembic migrations are missing: {migrations}")
    config = Config(str(config_path))
    config.set_main_option("script_location", str(migrations))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
