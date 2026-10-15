from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.sqlite_pragmas import SQLitePragmaProfile, apply_sqlite_pragmas


def create_sqlalchemy_engine(
    database_url: str,
    *,
    settings: Settings | None = None,
) -> Engine:
    engine_kwargs: dict[str, object] = {"future": True, "pool_pre_ping": True}
    active_settings = settings or get_settings()
    if database_url.startswith("sqlite:///"):
        engine_kwargs["connect_args"] = {
            "check_same_thread": False,
            "timeout": active_settings.sqlite_busy_timeout_ms / 1000.0,
        }
    engine = create_engine(database_url, **engine_kwargs)
    if database_url.startswith("sqlite:///"):
        profile = SQLitePragmaProfile.from_settings(active_settings)
        event.listen(engine, "connect", lambda connection, _record: apply_sqlite_pragmas(connection, profile))
    return engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    return create_sqlalchemy_engine(settings.database_url, settings=settings)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False)


def build_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    active_settings = settings or get_settings()
    return sessionmaker(
        bind=create_sqlalchemy_engine(active_settings.database_url, settings=active_settings),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def ensure_sqlite_parent_dir(settings: Settings | None = None) -> Path:
    active_settings = settings or get_settings()
    database_path = active_settings.sqlite_file_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return database_path


def ensure_sqlite_parent_dir_for_url(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite:///"):
        return None
    database_path = Path(database_url.removeprefix("sqlite:///")).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return database_path
