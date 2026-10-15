from __future__ import annotations

import sqlite3

from app.core.config import Settings
from app.db.session import ensure_sqlite_parent_dir
from app.db.sqlite_pragmas import SQLitePragmaProfile, apply_sqlite_pragmas


def connect_sqlite(settings: Settings) -> sqlite3.Connection:
    database_path = ensure_sqlite_parent_dir(settings)
    connection = sqlite3.connect(
        database_path,
        check_same_thread=False,
        timeout=settings.sqlite_busy_timeout_ms / 1000.0,
    )
    apply_sqlite_pragmas(connection, SQLitePragmaProfile.from_settings(settings))
    return connection
