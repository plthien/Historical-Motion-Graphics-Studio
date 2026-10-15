from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_platform_rq_runtime_is_removed() -> None:
    for relative in (
        "app/worker.py",
        "app/infrastructure/redis.py",
        "app/modules/v4/render_worker.py",
        "app/modules/v4/render_recovery.py",
        "scripts/windows/start_worker.bat",
    ):
        assert not (ROOT / relative).exists(), relative
