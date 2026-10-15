from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import create_sqlalchemy_engine, ensure_sqlite_parent_dir_for_url

# Import models so metadata is populated for Alembic.
from app.db import models as _models  # noqa: F401
from app.db import scene_asset_working_models as _scene_asset_working_models  # noqa: F401
from app.db import visual_reference_working_models as _visual_reference_working_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _configured_url() -> str:
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    return get_settings().database_url


def run_migrations_offline() -> None:
    url = _configured_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    database_url = _configured_url()
    if database_url.startswith("sqlite:///"):
        ensure_sqlite_parent_dir_for_url(database_url)

    connectable = create_sqlalchemy_engine(database_url)
    try:
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

            with context.begin_transaction():
                context.run_migrations()
    finally:
        # Alembic owns this engine.  Dispose it explicitly so SQLite WAL/SHM
        # handles and pooled connections never depend on garbage collection.
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
