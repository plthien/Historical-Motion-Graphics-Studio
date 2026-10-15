from __future__ import annotations

import hashlib
import json
from threading import Event

import httpx

from app.core.config import Settings
from app.db.session import get_session_factory
from app.modules.v4.elevenlabs_client import ElevenLabsHttpClient
from app.modules.v4.elevenlabs_credentials import ELEVENLABS_PROVIDER_KEY, VoiceProviderCredentialStore
from app.modules.v4.elevenlabs_provider_adapter import ElevenLabsProviderAdapter
from app.modules.v4.voice_provider_action_controller import VoiceProviderActionController
from app.modules.v4.voice_provider_contracts import VoiceProviderProfile
from app.modules.v4.voice_provider_execution_pool import VoiceProviderExecutionPool
from app.modules.v4.voice_provider_registry import build_voice_provider_registry
from app.repositories import SettingsRepository
from app.repositories.voice_provider_execution import VoiceProviderExecutionRepository
from tests.v4.test_voice_durable_provider_lifecycle import _seed_submitting_task, _wait_for_harvest
from tests.v4.test_voice_elevenlabs_adapter_execution import _pcm


KEY_A = "ele_action_account_A"
KEY_B = "ele_action_account_B"


def _store_key(value: str) -> None:
    with get_session_factory()() as session:
        VoiceProviderCredentialStore(session, SettingsRepository(session)).save_api_key(
            ELEVENLABS_PROVIDER_KEY,
            value,
        )


def _clear_key() -> None:
    with get_session_factory()() as session:
        VoiceProviderCredentialStore(session, SettingsRepository(session)).clear_api_key(
            ELEVENLABS_PROVIDER_KEY
        )


def _frozen_fingerprint(task_id: str) -> str:
    with get_session_factory()() as session:
        context = VoiceProviderExecutionRepository(session).get_provider_context(task_id)
    assert context is not None
    identity = context.provider_identity_json.get("execution_identity")
    assert isinstance(identity, dict)
    value = identity.get("credential_fingerprint_sha256")
    assert isinstance(value, str)
    return value


def test_durable_action_binds_one_credential_while_new_operation_hot_swaps(tmp_path) -> None:
    preflight_a_seen = Event()
    allow_a_to_continue = Event()
    post_b_seen = Event()
    requests: list[tuple[str, str, str]] = []
    raw_clients: list[httpx.Client] = []

    def client_factory(key: str, base: str, max_response: int, timeout: float) -> ElevenLabsHttpClient:
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((key, request.method, request.url.path))
            if request.method == "GET" and request.url.path == "/v2/voices":
                if key == KEY_A and not allow_a_to_continue.is_set():
                    preflight_a_seen.set()
                    assert allow_a_to_continue.wait(5), "test did not release credential-A preflight"
                return httpx.Response(
                    200,
                    json={
                        "voices": [
                            {
                                "voice_id": "voice_test",
                                "name": "Voice Test",
                                "category": "professional",
                                "fine_tuning": {"state": {"eleven_multilingual_v2": "fine_tuned"}},
                            }
                        ],
                        "has_more": False,
                    },
                )
            if request.method == "POST" and request.url.path == "/v1/text-to-speech/voice_test":
                if key == KEY_B:
                    post_b_seen.set()
                return httpx.Response(
                    200,
                    content=_pcm(),
                    headers={"request-id": f"req_{key[-1].lower()}"},
                )
            raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

        raw = httpx.Client(transport=httpx.MockTransport(handler), base_url=base)
        raw_clients.append(raw)
        return ElevenLabsHttpClient(key, base, max_response, timeout, client=raw)

    runtime = Settings(
        root_dir=tmp_path,
        elevenlabs_api_key="",
        elevenlabs_api_base_url="https://api.elevenlabs.io",
    )
    _store_key(KEY_A)
    adapter = build_voice_provider_registry(runtime).resolve(ELEVENLABS_PROVIDER_KEY)
    assert isinstance(adapter, ElevenLabsProviderAdapter)
    adapter._client_factory = client_factory

    old_task_id, _old_attempt = _seed_submitting_task(adapter)
    assert _frozen_fingerprint(old_task_id) == hashlib.sha256(KEY_A.encode()).hexdigest()

    registry = build_voice_provider_registry(runtime)
    registry._adapters[ELEVENLABS_PROVIDER_KEY] = adapter
    controller = VoiceProviderActionController(
        session_factory=get_session_factory(),
        registry=registry,
        pool=VoiceProviderExecutionPool(max_workers=2),
        settings=runtime,
        poll_interval_seconds=1,
    )

    try:
        assert controller.dispatch_submit(old_task_id).state == "running"
        assert preflight_a_seen.wait(5), "old action never reached ElevenLabs account preflight"

        # The generic durable runtime identity fence has already passed because
        # adapter.submit() is now executing. Replace the server-side credential
        # before the old action is allowed to reach the billable POST.
        _store_key(KEY_B)

        new_task_id, _new_attempt = _seed_submitting_task(adapter)
        expected_b = hashlib.sha256(KEY_B.encode()).hexdigest()
        assert adapter.execution_identity(
            VoiceProviderProfile(
                profile_id="elevenlabs_multilingual_v2",
                provider_key=ELEVENLABS_PROVIDER_KEY,
                enabled=True,
                display_name="ElevenLabs Multilingual v2",
                voice_id="voice_test",
                language="en",
                speaking_rate=1.0,
                requested_sample_rate_hz=24_000,
                requested_output_format="wav_pcm_s16le",
                provider_options={"model_id": "eleven_multilingual_v2", "narration_language": "en"},
            )
        )["credential_fingerprint_sha256"] == expected_b
        assert _frozen_fingerprint(new_task_id) == expected_b

        assert controller.dispatch_submit(new_task_id).state == "running"
        assert post_b_seen.wait(5), "new operation did not synthesize with replacement credential"
        new_progress = _wait_for_harvest(lambda: controller.harvest_submit(new_task_id))
        assert new_progress.state == "materializing"

        allow_a_to_continue.set()
        old_progress = _wait_for_harvest(lambda: controller.harvest_submit(old_task_id))
        assert old_progress.state == "materializing"

        a_requests = [item for item in requests if item[0] == KEY_A]
        b_requests = [item for item in requests if item[0] == KEY_B]
        assert (KEY_A, "GET", "/v2/voices") in a_requests
        assert any(key == KEY_A and method == "POST" for key, method, _path in requests)
        assert any(key == KEY_B and method == "GET" for key, method, _path in requests)
        assert any(key == KEY_B and method == "POST" for key, method, _path in requests)
        assert sum(1 for key, method, _path in requests if key == KEY_A and method == "POST") == 1
        assert sum(1 for key, method, _path in requests if key == KEY_B and method == "POST") == 1

        # The forbidden old-operation path would produce a second POST under B.
        assert [method for key, method, _path in requests if key == KEY_B].count("POST") == 1
    finally:
        allow_a_to_continue.set()
        controller.close()
        _clear_key()
        for raw in raw_clients:
            raw.close()
