from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from app.core.config import Settings, assert_path_within_test_root, get_settings
from conftest import (
    TEST_ARTIFACT_ROOT,
    TEST_DATABASE,
    TEST_GENERATED_MEDIA_ROOT,
    TEST_ROOT,
    TEST_STORAGE_ROOT,
    TEST_UPLOAD_ROOT,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_test_database_is_isolated() -> None:
    settings = get_settings()
    assert os.environ["TESTING"] == "true"
    assert settings.testing is True
    assert settings.database_url.startswith("sqlite:///")
    assert settings.sqlite_file_path == TEST_DATABASE
    assert settings.sqlite_file_path.name != "youtube_ai_studio_v3.db"
    assert TEST_ROOT not in REPO_ROOT.parents
    assert REPO_ROOT not in settings.sqlite_file_path.parents
    assert_path_within_test_root(settings.sqlite_file_path, TEST_ROOT)


def test_persistent_roots_are_isolated() -> None:
    settings = get_settings()
    paths = [
        settings.resolve_configured_path(settings.artifact_root),
        Path(os.environ["TEST_STORAGE_ROOT"]).resolve(),
        Path(os.environ["TEST_UPLOAD_ROOT"]).resolve(),
        Path(os.environ["TEST_GENERATED_MEDIA_ROOT"]).resolve(),
    ]
    assert paths == [
        TEST_ARTIFACT_ROOT,
        TEST_STORAGE_ROOT,
        TEST_UPLOAD_ROOT,
        TEST_GENERATED_MEDIA_ROOT,
        ]
    for path in paths:
        assert_path_within_test_root(path, TEST_ROOT)
        assert REPO_ROOT not in path.parents


def test_development_env_values_do_not_win_in_test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTING", "true")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("TEST_ROOT", str(TEST_ROOT))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./youtube_ai_studio_v3.db")
    monkeypatch.setenv("ARTIFACT_ROOT", "./local_artifacts")
    with pytest.raises(RuntimeError):
        Settings.from_environment()


def test_unsafe_database_path_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTING", "true")
    monkeypatch.setenv("TEST_ROOT", str(TEST_ROOT))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./youtube_ai_studio_v3.db")
    monkeypatch.setenv("ARTIFACT_ROOT", str(TEST_ARTIFACT_ROOT))
    with pytest.raises(RuntimeError, match="outside test root"):
        Settings.from_environment()


def test_unsafe_artifact_root_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTING", "true")
    monkeypatch.setenv("TEST_ROOT", str(TEST_ROOT))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{TEST_DATABASE}")
    monkeypatch.setenv("ARTIFACT_ROOT", "./local_artifacts")
    with pytest.raises(RuntimeError, match="outside test root"):
        Settings.from_environment()


@pytest.mark.parametrize(
    "unsafe",
    [
        REPO_ROOT,
        REPO_ROOT / "youtube_ai_studio_v3.db",
        REPO_ROOT / "local_artifacts",
        Path.home(),
        Path(Path.home().anchor),
        TEST_ROOT.parent,
    ],
)
def test_destructive_cleanup_targets_outside_test_root_are_rejected(unsafe: Path) -> None:
    with pytest.raises(RuntimeError):
        assert_path_within_test_root(unsafe, TEST_ROOT)


def test_safe_temporary_paths_are_accepted() -> None:
    for safe in [TEST_ROOT, TEST_DATABASE, TEST_ARTIFACT_ROOT / "nested" / "file.json"]:
        assert assert_path_within_test_root(safe, TEST_ROOT) == safe.resolve()


def test_development_sentinel_remains_unchanged(tmp_path: Path) -> None:
    simulated_dev_db = tmp_path / "youtube_ai_studio_v3.db"
    simulated_dev_artifact = tmp_path / "local_artifacts" / "sentinel.txt"
    simulated_dev_artifact.parent.mkdir(parents=True)
    simulated_dev_db.write_text("simulated development database", encoding="utf-8")
    simulated_dev_artifact.write_text("simulated development artifact", encoding="utf-8")

    before = {
        simulated_dev_db: (simulated_dev_db.stat().st_size, sha256(simulated_dev_db)),
        simulated_dev_artifact: (simulated_dev_artifact.stat().st_size, sha256(simulated_dev_artifact)),
    }

    TEST_DATABASE.parent.mkdir(parents=True, exist_ok=True)
    TEST_ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    (TEST_ARTIFACT_ROOT / "test-write.txt").write_text("isolated write", encoding="utf-8")

    after = {
        simulated_dev_db: (simulated_dev_db.stat().st_size, sha256(simulated_dev_db)),
        simulated_dev_artifact: (simulated_dev_artifact.stat().st_size, sha256(simulated_dev_artifact)),
    }
    assert after == before
