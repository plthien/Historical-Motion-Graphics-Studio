from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from app.api.errors import AppError
from app.modules.v4.elevenlabs_client import ElevenLabsAmbiguousSubmission, ElevenLabsHttpClient


class _FailingBody(httpx.SyncByteStream):
    def __init__(self, error: Exception) -> None:
        self.error = error

    def __iter__(self) -> Iterator[bytes]:
        raise self.error


def _client_for_response(status: int, body_error: Exception) -> ElevenLabsHttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"request-id": "req_known_status"}, stream=_FailingBody(body_error))

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.elevenlabs.io")
    return ElevenLabsHttpClient("ele_secret_MUST_NOT_LEAK_123", client=client)


@pytest.mark.parametrize(
    ("status", "body_error", "expected", "retryable"),
    [
        (429, httpx.ReadTimeout("read timeout"), "ELEVENLABS_RATE_LIMITED", True),
        (429, httpx.ReadError("read error"), "ELEVENLABS_RATE_LIMITED", True),
        (500, httpx.ReadTimeout("read timeout"), "ELEVENLABS_INTERNAL_ERROR", True),
        (503, httpx.ReadError("read error"), "ELEVENLABS_UNAVAILABLE", True),
        (400, httpx.ReadTimeout("read timeout"), "ELEVENLABS_REQUEST_INVALID", False),
        (401, httpx.ReadError("read error"), "ELEVENLABS_AUTH_INVALID", False),
    ],
)
def test_known_http_status_survives_error_body_stream_failure(status: int, body_error: Exception, expected: str, retryable: bool) -> None:
    client = _client_for_response(status, body_error)
    with pytest.raises(AppError) as exc:
        client.synthesize(voice_id="voice_a", output_format="pcm_24000", body={"text": "Hello.", "model_id": "eleven_multilingual_v2"})
    assert exc.value.error_code == expected
    assert exc.value.retryable is retryable
    assert exc.value.details["http_status"] == status
    assert exc.value.details["provider_error_body_unavailable"] in {"ReadTimeout", "ReadError"}
    assert exc.value.details["request_id"] == "req_known_status"
    assert "ele_secret_MUST_NOT_LEAK_123" not in str(exc.value.details)


def test_success_status_with_body_read_failure_is_non_retryable() -> None:
    client = _client_for_response(200, httpx.ReadTimeout("read timeout after success status"))
    with pytest.raises(AppError) as exc:
        client.synthesize(voice_id="voice_a", output_format="pcm_24000", body={"text": "Hello.", "model_id": "eleven_multilingual_v2"})
    assert exc.value.error_code == "ELEVENLABS_RESPONSE_INVALID"
    assert exc.value.retryable is False
    assert exc.value.details["http_status"] == 200


def test_transport_failure_without_proven_response_remains_ambiguous() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no response proven")

    raw = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.elevenlabs.io")
    client = ElevenLabsHttpClient("ele_secret_MUST_NOT_LEAK_123", client=raw)
    with pytest.raises(ElevenLabsAmbiguousSubmission):
        client.synthesize(voice_id="voice_a", output_format="pcm_24000", body={"text": "Hello.", "model_id": "eleven_multilingual_v2"})
