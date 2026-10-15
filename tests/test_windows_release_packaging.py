from __future__ import annotations

import zipfile

from tools.package_windows_release import build_release, _collect_release_paths
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_start_launcher_uses_project_virtualenv_and_starts_all_runtime_processes() -> None:
    launcher = (ROOT / "start.bat").read_text(encoding="ascii")
    assert ".venv\\Scripts\\python.exe" in launcher
    assert "BOOTSTRAP_PYTHON=py -3.12" in launcher
    assert '-m venv ".venv"' in launcher
    assert "pip install --disable-pip-version-check -r" in launcher
    assert "npm.cmd ci --no-audit --no-fund" in launcher
    assert ".yas3_requirements.sha256" in launcher
    assert ".yas3_package_lock.sha256" in launcher
    assert "scripts\\windows\\start_backend.bat" in launcher
    assert "scripts\\windows\\start_durable_media_workflow.bat" in launcher
    assert "scripts\\windows\\start_durable_render.bat" in launcher
    assert "scripts\\windows\\start_worker.bat" not in launcher
    assert "Redis must be available" not in launcher
    assert "import fastapi, uvicorn, sqlalchemy, redis, rq" not in launcher
    assert "http://127.0.0.1:5173" in launcher


def test_start_launcher_migrates_database_before_spawning_runtime_hosts() -> None:
    launcher = (ROOT / "start.bat").read_text(encoding="ascii")
    migration_call = "call :upgrade_database"
    backend_start = 'start "YAS3 Backend API"'
    durable_render_start = 'start "YAS3 Durable Render"'

    assert migration_call in launcher
    assert "from app.db.migrations import upgrade_database; upgrade_database()" in launcher
    assert "Database migration failed. Runtime processes were not started." in launcher
    assert launcher.index(migration_call) < launcher.index(backend_start)
    assert launcher.index(migration_call) < launcher.index(durable_render_start)


def test_durable_render_launcher_preflights_ffmpeg_and_starts_host() -> None:
    launcher = (ROOT / "scripts" / "windows" / "start_durable_render.bat").read_text(encoding="ascii")
    assert "FFMPEG_PATH" in launcher
    assert "FFPROBE_PATH" in launcher
    assert "-m app.durable_render_runner" in launcher
    assert "redis" not in launcher.lower()
    assert "rq" not in launcher.lower()


def test_windows_release_does_not_depend_on_or_include_root_manifest(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("Example\n", encoding="utf-8")
    (tmp_path / "MANIFEST.json").write_text('{"obsolete":true}\n', encoding="utf-8")
    (tmp_path / "sample.db").write_bytes(b"private data")
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    files = _collect_release_paths(tmp_path)
    assert files == ["README.md", "app/main.py"]
    zip_path = tmp_path.parent / "history-release-test.zip"
    report = build_release(tmp_path, zip_path, "history", 180)
    assert report["entry_count"] == 2
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.namelist() == ["history/README.md", "history/app/main.py"]
