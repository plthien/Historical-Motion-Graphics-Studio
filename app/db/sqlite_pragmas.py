from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class SQLitePragmaProfile:
    busy_timeout_ms: int
    synchronous: str
    wal_autocheckpoint_pages: int
    journal_size_limit_bytes: int
    cache_size_kib: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "SQLitePragmaProfile":
        return cls(
            busy_timeout_ms=settings.sqlite_busy_timeout_ms,
            synchronous=settings.sqlite_synchronous,
            wal_autocheckpoint_pages=settings.sqlite_wal_autocheckpoint_pages,
            journal_size_limit_bytes=settings.sqlite_journal_size_limit_bytes,
            cache_size_kib=settings.sqlite_cache_size_kib,
        )

    def statements(self) -> tuple[str, ...]:
        return (
            "PRAGMA journal_mode=WAL;",
            f"PRAGMA busy_timeout={self.busy_timeout_ms};",
            "PRAGMA foreign_keys=ON;",
            f"PRAGMA synchronous={self.synchronous};",
            f"PRAGMA wal_autocheckpoint={self.wal_autocheckpoint_pages};",
            f"PRAGMA journal_size_limit={self.journal_size_limit_bytes};",
            f"PRAGMA cache_size=-{self.cache_size_kib};",
        )


def apply_sqlite_pragmas(connection: Any, profile: SQLitePragmaProfile) -> None:
    cursor = connection.cursor()
    try:
        for statement in profile.statements():
            cursor.execute(statement)
    finally:
        cursor.close()


def read_sqlite_pragmas(connection: Any) -> dict[str, int | str]:
    cursor = connection.cursor()
    try:
        values: dict[str, int | str] = {}
        for name in (
            "journal_mode",
            "busy_timeout",
            "foreign_keys",
            "synchronous",
            "wal_autocheckpoint",
            "journal_size_limit",
            "cache_size",
        ):
            row = cursor.execute(f"PRAGMA {name};").fetchone()
            values[name] = row[0] if row else ""
        return values
    finally:
        cursor.close()
