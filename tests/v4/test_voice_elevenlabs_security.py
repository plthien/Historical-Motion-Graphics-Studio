from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.api.errors import AppError
from app.core.config import Settings
from app.modules.v4.voice_provider_registry import build_default_voice_settings
from tests.v4.test_voice_elevenlabs_adapter_execution import _adapter, _request, _pcm, _Control


def test_bootstrap_settings_do_not_store_api_key(tmp_path: Path) -> None:
    settings = Settings(root_dir=tmp_path, tts_provider_mode="elevenlabs", elevenlabs_api_key="secret-key")

    payload = build_default_voice_settings(settings)

    assert payload["active_profile_id"] == "elevenlabs_multilingual_v2"
    assert "secret-key" not in json.dumps(payload)


def test_success_metadata_and_identity_do_not_leak_api_key(tmp_path: Path) -> None:
    adapter, _ = _adapter(
        tmp_path,
        lambda request: (
            httpx.Response(200, json={"voices": [{"voice_id": "voice_a", "name": "Voice A"}], "has_more": False})
            if request.url.path == "/v2/voices"
            else httpx.Response(200, content=_pcm(), headers={"request-id": "req_1"})
        ),
    )

    result = adapter.submit(_request(tmp_path), _Control()).result
    assert result is not None
    rendered = json.dumps(result.sanitized_metadata) + json.dumps(adapter.execution_identity(adapter._profile_from_request(_request(tmp_path))))

    assert "test-eleven-key" not in rendered
    assert "xi-api-key" not in rendered


def test_error_details_do_not_leak_body_message_or_key(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/voices":
            return httpx.Response(200, json={"voices": [{"voice_id": "voice_a", "name": "Voice A"}], "has_more": False})
        return httpx.Response(
            401,
            json={"detail": {"type": "authentication_error", "code": "bad", "message": "secret-key leaked"}},
        )

    adapter, _ = _adapter(tmp_path, handler)

    with pytest.raises(AppError) as exc:
        adapter.submit(_request(tmp_path), _Control())

    rendered = json.dumps(exc.value.details)
    assert "test-eleven-key" not in rendered
    assert "secret-key leaked" not in rendered
    assert "xi-api-key" not in rendered
