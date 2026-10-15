from __future__ import annotations

from alembic import command
from alembic.config import Config

from app.core.config import Settings, get_settings
from app.db.session import ensure_sqlite_parent_dir


def build_alembic_config(settings: Settings | None = None) -> Config:
    active_settings = settings or get_settings()
    config = Config(str((active_settings.root_dir / "alembic.ini").resolve()))
    config.set_main_option("script_location", str((active_settings.root_dir / "alembic").resolve()))
    config.set_main_option("sqlalchemy.url", active_settings.database_url)
    return config


def upgrade_database(settings: Settings | None = None) -> None:
    active_settings = settings or get_settings()
    if active_settings.database_url.startswith("sqlite:///"):
        ensure_sqlite_parent_dir(active_settings)
    command.upgrade(build_alembic_config(active_settings), "head")
