from __future__ import annotations

import inspect
from pathlib import Path

from app.modules.v4.durable_voice_service import DurableVoiceService


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


def test_legacy_voice_rq_modules_are_removed() -> None:
    assert not (ROOT / "app/modules/v4/voice_worker.py").exists()
    assert not (ROOT / "app/modules/v4/voice_recovery.py").exists()


def test_voice_services_have_no_rq_enqueue_dependency() -> None:
    durable_source = inspect.getsource(DurableVoiceService.submit_generation)
    base_source = (ROOT / "app/modules/v4/voice_service.py").read_text(encoding="utf-8")
    for forbidden in ("enqueue(", "create_queue", "WorkerJobPayload", "VOICE_QUEUE_UNAVAILABLE"):
        assert forbidden not in durable_source
        assert forbidden not in base_source
    assert "session.commit" in durable_source


def test_durable_voice_submission_freezes_provider_execution_identity_before_hashing() -> None:
    durable_source = inspect.getsource(DurableVoiceService.submit_generation)
    assert "registry.execution_identity" in durable_source
    assert '"execution_identity": execution_identity' in durable_source
    assert durable_source.index("execution_identity =") < durable_source.index("provider_identity =")
    assert durable_source.index('"execution_identity": execution_identity') < durable_source.index("provider_hash = canonical_hash(provider_identity)")


def test_production_voice_routes_use_durable_service() -> None:
    source = (ROOT / "app/api/routes/production_v4.py").read_text(encoding="utf-8")
    assert "from app.modules.v4.durable_voice_service import DurableVoiceService" in source
    assert "from app.modules.v4.voice_service import VoiceService" not in source
    assert "DurableVoiceService(session)" in source
