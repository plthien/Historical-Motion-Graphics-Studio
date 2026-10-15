from __future__ import annotations

import json
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


def test_windows_release_entries_fit_short_root_policy() -> None:
    manifest = json.loads((ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
    paths = [f"yas3/{record['path']}" for record in manifest["package_files"]]
    paths.append("yas3/MANIFEST.json")
    assert max(map(len, paths)) <= 180
