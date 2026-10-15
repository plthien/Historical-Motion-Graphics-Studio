from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.version import APP_VERSION


def _read_dotenv_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    testing: bool = False
    test_root: str | None = None
    app_env: str = "local"
    app_version: str = APP_VERSION
    database_url: str = "sqlite:///./youtube_ai_studio_v3.db"
    artifact_root: str = "./local_artifacts"
    render_provider_mode: str = "ffmpeg_local"
    render_api_base_url: str = "http://127.0.0.1:8765"
    render_api_key: str = ""
    tts_provider_mode: str = "disabled"
    kokoro_base_path: str = "./kokoro"
    kokoro_command: str = "./.venv/Scripts/python.exe kokoro_cli.py"
    elevenlabs_api_key: str = ""
    elevenlabs_api_base_url: str = "https://api.elevenlabs.io"
    tts_provider_timeout_seconds: int = Field(default=120, gt=0, le=1800)
    tts_provider_max_output_bytes: int = Field(default=104_857_600, gt=0)
    tts_max_narration_characters: int = Field(default=1_000_000, gt=0)
    tts_max_segment_count: int = Field(default=10_000, gt=0)
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    render_timeout_seconds: int = Field(default=3600, gt=0, le=86_400)
    render_cancel_grace_seconds: int = Field(default=10, ge=1, le=120)
    render_max_output_bytes: int = Field(default=2_147_483_648, gt=0)
    render_progress_commit_interval_ms: int = Field(default=500, ge=100, le=10_000)
    render_stale_operation_seconds: int = Field(default=3600, ge=60, le=86_400)
    render_max_beat_count: int = Field(default=10_000, ge=1, le=100_000)
    render_max_input_count: int = Field(default=20_000, ge=1, le=100_000)
    sqlite_busy_timeout_ms: int = Field(default=15_000, ge=1_000, le=120_000)
    sqlite_synchronous: Literal["OFF", "NORMAL", "FULL", "EXTRA"] = "NORMAL"
    sqlite_wal_autocheckpoint_pages: int = Field(default=1_000, ge=1, le=100_000)
    sqlite_journal_size_limit_bytes: int = Field(default=67_108_864, ge=1_048_576, le=2_147_483_648)
    sqlite_cache_size_kib: int = Field(default=32_768, ge=1_024, le=1_048_576)
    api_base_path: str = "/api"
    auto_apply_migrations: bool = True
    media_provider_max_download_bytes: int = Field(default=26_214_400, gt=0)
    cors_allow_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    cors_allow_origin_regex: str = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    root_dir: Path

    @property
    def sqlite_file_path(self) -> Path:
        if not self.database_url.startswith("sqlite:///"):
            raise ValueError("Only sqlite:/// URLs are supported by the V3 local baseline.")

        relative = self.database_url.removeprefix("sqlite:///")
        return (self.root_dir / relative).resolve()

    def resolve_configured_path(self, value: str) -> Path:
        path = Path(value)
        return (path if path.is_absolute() else self.root_dir / path).resolve()

    @classmethod
    def from_environment(cls, root_dir: Path | None = None) -> "Settings":
        project_root = (root_dir or Path(__file__).resolve().parents[2]).resolve()
        testing = _env_truthy(os.environ.get("TESTING"))
        file_values = {} if testing else _read_dotenv_file(project_root / ".env")

        def read(key: str, default: Any) -> Any:
            return os.environ.get(key, file_values.get(key, default))

        def read_nonempty_text(key: str, default: str) -> str:
            value = read(key, default)
            if value is None:
                return default
            text = str(value).strip()
            return text or default

        settings = cls(
            testing=testing,
            test_root=read("TEST_ROOT", None),
            app_env=read("APP_ENV", "local"),
            app_version=APP_VERSION,
            database_url=read("DATABASE_URL", "sqlite:///./youtube_ai_studio_v3.db"),
            artifact_root=read("ARTIFACT_ROOT", "./local_artifacts"),
            render_provider_mode=read("RENDER_PROVIDER_MODE", "ffmpeg_local"),
            render_api_base_url=read("RENDER_API_BASE_URL", "http://127.0.0.1:8765"),
            render_api_key=read("RENDER_API_KEY", ""),
            tts_provider_mode=read("TTS_PROVIDER_MODE", "disabled"),
            kokoro_base_path=read("KOKORO_BASE_PATH", "./kokoro"),
            kokoro_command=read("KOKORO_COMMAND", "./.venv/Scripts/python.exe kokoro_cli.py"),
            elevenlabs_api_key=read("ELEVENLABS_API_KEY", ""),
            elevenlabs_api_base_url=read("ELEVENLABS_API_BASE_URL", "https://api.elevenlabs.io"),
            tts_provider_timeout_seconds=int(read("TTS_PROVIDER_TIMEOUT_SECONDS", 120)),
            tts_provider_max_output_bytes=int(read("TTS_PROVIDER_MAX_OUTPUT_BYTES", 104_857_600)),
            tts_max_narration_characters=int(read("TTS_MAX_NARRATION_CHARACTERS", 1_000_000)),
            tts_max_segment_count=int(read("TTS_MAX_SEGMENT_COUNT", 10_000)),
            ffmpeg_path=read("FFMPEG_PATH", "ffmpeg"),
            ffprobe_path=read("FFPROBE_PATH", "ffprobe"),
            render_timeout_seconds=int(read("RENDER_TIMEOUT_SECONDS", 3600)),
            render_cancel_grace_seconds=int(read("RENDER_CANCEL_GRACE_SECONDS", 10)),
            render_max_output_bytes=int(read("RENDER_MAX_OUTPUT_BYTES", 2_147_483_648)),
            render_progress_commit_interval_ms=int(read("RENDER_PROGRESS_COMMIT_INTERVAL_MS", 500)),
            render_stale_operation_seconds=int(read("RENDER_STALE_OPERATION_SECONDS", 3600)),
            render_max_beat_count=int(read("RENDER_MAX_BEAT_COUNT", 10_000)),
            render_max_input_count=int(read("RENDER_MAX_INPUT_COUNT", 20_000)),
            sqlite_busy_timeout_ms=int(read("SQLITE_BUSY_TIMEOUT_MS", 15_000)),
            sqlite_synchronous=str(read("SQLITE_SYNCHRONOUS", "NORMAL")).upper(),
            sqlite_wal_autocheckpoint_pages=int(read("SQLITE_WAL_AUTOCHECKPOINT_PAGES", 1_000)),
            sqlite_journal_size_limit_bytes=int(read("SQLITE_JOURNAL_SIZE_LIMIT_BYTES", 67_108_864)),
            sqlite_cache_size_kib=int(read("SQLITE_CACHE_SIZE_KIB", 32_768)),
            api_base_path=read("API_BASE_PATH", "/api"),
            auto_apply_migrations=read("AUTO_APPLY_MIGRATIONS", True),
            media_provider_max_download_bytes=int(read("MEDIA_PROVIDER_MAX_DOWNLOAD_BYTES", 26_214_400)),
            cors_allow_origins=tuple(
                value.strip()
                for value in read_nonempty_text(
                    "CORS_ALLOW_ORIGINS",
                    "http://localhost:5173,http://127.0.0.1:5173",
                ).split(",")
                if value.strip()
            ),
            cors_allow_origin_regex=read_nonempty_text(
                "CORS_ALLOW_ORIGIN_REGEX",
                r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
            ),
            root_dir=project_root,
        )
        validate_test_settings(settings)
        return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_environment()


def _env_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def assert_path_within_test_root(target: Path, test_root: Path) -> Path:
    resolved_target = target.resolve()
    resolved_root = test_root.resolve()
    if resolved_target.anchor == str(resolved_target):
        raise RuntimeError(f"Unsafe test path is a filesystem root: {resolved_target}")
    if resolved_target == Path.home().resolve():
        raise RuntimeError(f"Unsafe test path is the user home directory: {resolved_target}")
    if resolved_target == resolved_root:
        return resolved_target
    if resolved_root not in resolved_target.parents:
        raise RuntimeError(f"Unsafe test path outside test root: {resolved_target}")
    return resolved_target


def validate_test_settings(settings: Settings) -> None:
    if not settings.testing:
        return
    if not settings.test_root:
        raise RuntimeError("TESTING=true requires TEST_ROOT.")
    test_root = Path(settings.test_root).resolve()
    if not test_root.is_absolute():
        raise RuntimeError("TEST_ROOT must resolve to an absolute path.")
    if not settings.database_url.startswith("sqlite:///"):
        raise RuntimeError("TESTING=true requires a sqlite:/// DATABASE_URL.")
    database_raw = settings.database_url.removeprefix("sqlite:///")
    if not database_raw.strip() or database_raw.strip() in {".", ".."}:
        raise RuntimeError("TESTING=true requires a concrete SQLite database file path.")
    database_path = Path(database_raw)
    resolved_database = database_path.resolve() if database_path.is_absolute() else (settings.root_dir / database_path).resolve()
    assert_path_within_test_root(resolved_database, test_root)

    for label, configured in {
        "ARTIFACT_ROOT": settings.artifact_root,
    }.items():
        if not configured.strip() or configured.strip() in {".", ".."}:
            raise RuntimeError(f"TESTING=true requires a concrete {label}.")
        assert_path_within_test_root(settings.resolve_configured_path(configured), test_root)
