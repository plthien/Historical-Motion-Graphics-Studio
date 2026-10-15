from __future__ import annotations

import inspect
from pathlib import Path

from app.api.errors import AppError
from app.modules.v4.voice_service import VoiceService


ROOT = Path(__file__).resolve().parents[2]


def test_voice_service_core_has_no_provider_name_branching() -> None:
    source = (ROOT / "app/modules/v4/voice_service.py").read_text(encoding="utf-8")
    assert "kokoro_local" not in source
    assert "KokoroLocalProvider" not in source
    assert "DisabledTTSProvider" not in source


def test_legacy_provider_resolution_requires_explicit_injection() -> None:
    source = inspect.getsource(VoiceService._provider_for_profile)
    assert "self.provider is None" in source
    assert "VOICE_LEGACY_PROVIDER_NOT_INJECTED" in source
    assert "profile.get" not in source

    service = object.__new__(VoiceService)
    service.provider = None
    try:
        service._provider_for_profile({"provider": "any-provider"})
    except AppError as exc:
        assert exc.error_code == "VOICE_LEGACY_PROVIDER_NOT_INJECTED"
    else:
        raise AssertionError("Legacy Voice provider resolution must not create a provider implicitly.")
