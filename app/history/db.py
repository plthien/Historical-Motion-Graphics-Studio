"""Fresh History SQLite ONLY. An existing foreign DB is inspected read-only, never upgraded."""
from __future__ import annotations
import json
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from urllib.parse import quote

from alembic import command
from alembic.config import Config
from .errors import HistoryError

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_SCHEMA = "historical-motion-graphics-studio"
STAGE_KEYS = ("topic","script_analysis","voice","assets","visual_director","motion_timeline","final_video")


def default_db_path() -> Path:
    return Path(os.environ.get("HISTORY_DB_PATH") or str(ROOT / "history_data" / "history_app.db")).expanduser().absolute()


class HistoryDB:
    def __init__(self, db_path: Path | str | None = None):
        self.path = Path(db_path).expanduser().absolute() if db_path else default_db_path()
        # Never intentionally create/open files at known old project/database names.
        if any(token in str(self.path).lower() for token in ("youtube_ai_studio", "yas3", "ytb-ai-studio")):
            raise HistoryError("HISTORY_LEGACY_STORAGE_UNSUPPORTED", 409)

    def _check_existing_read_only(self) -> None:
        # URI mode=ro avoids -wal creation, writing journal, or schema upgrades.
        uri = "file:" + quote(self.path.as_posix(), safe="/:") + "?mode=ro&immutable=1"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=2)
            try:
                row = conn.execute("SELECT schema_key, schema_version FROM history_meta WHERE id=1").fetchone()
                tables = {x[0] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                required = {"history_meta","channels","projects","stage_heads","artifacts","authoring_reservations","alembic_version"}
                if row != (EXPECTED_SCHEMA, 1) or not required.issubset(tables):
                    raise ValueError("History schema marker or required table missing")
                version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
                if version != ("001_history_initial",):
                    raise ValueError("Unsupported History migration head")
            finally:
                conn.close()
        except (sqlite3.Error, ValueError, OSError) as e:
            raise HistoryError("HISTORY_LEGACY_STORAGE_UNSUPPORTED", 409) from e

    def bootstrap(self) -> None:
        if self.path.exists():
            if not self.path.is_file():
                raise HistoryError("HISTORY_LEGACY_STORAGE_UNSUPPORTED", 409)
            self._check_existing_read_only()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "alembic"))
        config.set_main_option("sqlalchemy.url", "sqlite:///" + self.path.as_posix())
        command.upgrade(config, "head")
        self._check_existing_read_only()

    @contextmanager
    def connect(self):
        self._check_existing_read_only()
        conn = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            if conn.in_transaction:
                conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()


def strict_json(text: str, max_bytes: int = 1024 * 1024):
    if not isinstance(text, str) or len(text.encode("utf-8")) > max_bytes:
        raise HistoryError("HISTORY_JSON_INVALID", 422)
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise HistoryError("HISTORY_JSON_DUPLICATE_KEY", 422, f"Duplicate key: {key}")
            result[key] = value
        return result
    def invalid_constant(_value):
        raise ValueError("Non-finite values are forbidden")
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except HistoryError:
        raise
    except (ValueError, UnicodeError) as e:
        raise HistoryError("HISTORY_JSON_INVALID", 422) from e
