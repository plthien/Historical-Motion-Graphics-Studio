from __future__ import annotations

import os
import tempfile
from pathlib import Path


TEST_ROOT = Path(tempfile.mkdtemp(prefix="youtube_ai_studio_v3_tests_")).resolve()
TEST_DATABASE = TEST_ROOT / "test.sqlite3"
TEST_ARTIFACT_ROOT = TEST_ROOT / "artifacts"
TEST_STORAGE_ROOT = TEST_ROOT / "storage"
TEST_UPLOAD_ROOT = TEST_ROOT / "uploads"
TEST_GENERATED_MEDIA_ROOT = TEST_ROOT / "generated"


def _install_isolated_environment() -> None:
    isolated_values = {
        "TESTING": "true",
        "APP_ENV": "test",
        "TEST_ROOT": str(TEST_ROOT),
        "DATABASE_URL": f"sqlite:///{TEST_DATABASE}",
        "ARTIFACT_ROOT": str(TEST_ARTIFACT_ROOT),
        "TEST_STORAGE_ROOT": str(TEST_STORAGE_ROOT),
        "TEST_UPLOAD_ROOT": str(TEST_UPLOAD_ROOT),
        "TEST_GENERATED_MEDIA_ROOT": str(TEST_GENERATED_MEDIA_ROOT),
        "AUTO_APPLY_MIGRATIONS": "true",
    }
    for key, value in isolated_values.items():
        os.environ[key] = value


_install_isolated_environment()

from app.core.config import get_settings, validate_test_settings  # noqa: E402
from app.db.migrations import upgrade_database  # noqa: E402
from app.db.session import get_engine, get_session_factory  # noqa: E402

get_settings.cache_clear()
get_engine.cache_clear()
get_session_factory.cache_clear()
validate_test_settings(get_settings())
upgrade_database(get_settings())


def pytest_report_header(config: object) -> list[str]:
    settings = get_settings()
    return [
        "Resolved isolated test environment:",
        f"  TESTING={settings.testing}",
        f"  APP_ENV={settings.app_env}",
        f"  TEST_ROOT={TEST_ROOT}",
        f"  DATABASE={settings.sqlite_file_path}",
        f"  ARTIFACT_ROOT={settings.resolve_configured_path(settings.artifact_root)}",
        f"  STORAGE_ROOT={TEST_STORAGE_ROOT}",
        f"  UPLOAD_ROOT={TEST_UPLOAD_ROOT}",
        f"  GENERATED_MEDIA_ROOT={TEST_GENERATED_MEDIA_ROOT}",
    ]
