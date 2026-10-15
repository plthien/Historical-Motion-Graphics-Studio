from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _import_main_from(cwd: Path, *, app_version: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    if app_version is None:
        env.pop("APP_VERSION", None)
    else:
        env["APP_VERSION"] = app_version
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.main; print(app.main.app.version)",
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_app_main_import_ignores_missing_and_malformed_working_directory_manifest(tmp_path: Path) -> None:
    missing = _import_main_from(tmp_path)
    assert missing.returncode == 0, missing.stderr
    assert missing.stdout.strip() == "3.0.0-external-authoring.18-rc1"

    (tmp_path / "MANIFEST.json").write_text("{not-json", encoding="utf-8")
    malformed = _import_main_from(tmp_path)
    assert malformed.returncode == 0, malformed.stderr
    assert malformed.stdout.strip() == "3.0.0-external-authoring.18-rc1"


def test_app_version_environment_override_is_respected(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("APP_VERSION", "3.8d-runtime")
    import app.core.version as version_module

    reloaded = importlib.reload(version_module)
    assert reloaded.APP_VERSION == "3.8d-runtime"


def test_app_version_default_and_fastapi_version_are_deterministic(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("APP_VERSION", raising=False)
    import app.core.version as version_module

    reloaded = importlib.reload(version_module)
    assert reloaded.resolve_app_version() == "3.0.0-external-authoring.18-rc1"
    assert reloaded.APP_VERSION == "3.0.0-external-authoring.18-rc1"

    import app.main as main_module

    reloaded_main = importlib.reload(main_module)
    assert reloaded_main.app.version == "3.0.0-external-authoring.18-rc1"
