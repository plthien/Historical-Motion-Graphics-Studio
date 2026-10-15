from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Mapping

import httpx

from app.api.errors import AppError


class ElevenLabsAmbiguousSubmission(Exception):
    """The synthesis POST may have reached ElevenLabs, but no response was proven."""


@dataclass(frozen=True, slots=True)
class ElevenLabsJsonResponse:
    status_code: int
    headers: Mapping[str, str]
    payload: Any


@dataclass(frozen=True, slots=True)
class ElevenLabsBytesResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes


@dataclass(slots=True)
class ElevenLabsHttpClient:
    api_key: str
    api_base_url: str = "https://api.elevenlabs.io"
    max_response_bytes: int = 104_857_600
    timeout_seconds: float = 120.0
    client: httpx.Client | None = None
    _owned_client: httpx.Client | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.api_base_url = self.api_base_url.rstrip("/") or "https://api.elevenlabs.io"

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()
            self._owned_client = None

    def list_voices(self, *, next_page_token: str | None = None) -> ElevenLabsJsonResponse:
        params = {"page_size": "100", "include_total_count": "false"}
        if next_page_token:
            params["next_page_token"] = next_page_token
        return self._request_json("GET", "/v2/voices", params=params, side_effecting=False)

    def list_models(self) -> ElevenLabsJsonResponse:
        return self._request_json("GET", "/v1/models", side_effecting=False)

    def synthesize(
        self,
        *,
        voice_id: str,
        output_format: str,
        body: dict[str, Any],
    ) -> ElevenLabsBytesResponse:
        return self._request_bytes(
            "POST",
            f"/v1/text-to-speech/{voice_id}",
            params={"output_format": output_format},
            json_body=body,
            side_effecting=True,
        )

    def forced_alignment(self, *, audio_bytes: bytes, text: str) -> ElevenLabsJsonResponse:
        headers = {"xi-api-key": self.api_key, "Accept": "application/json"}
        try:
            with self._client().stream(
                "POST",
                "/v1/forced-alignment",
                files={"file": ("section.wav", audio_bytes, "audio/wav")},
                data={"text": text},
                headers=headers,
            ) as response:
                if response.status_code >= 400:
                    raise self._known_http_error(response)
                try:
                    content = self._read_bounded(response)
                except AppError:
                    raise
                except (httpx.WriteTimeout, httpx.WriteError, httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError) as exc:
                    raise AppError(
                        "ELEVENLABS_RESPONSE_INVALID",
                        "ElevenLabs returned an incomplete successful Forced Alignment response.",
                        502,
                        retryable=False,
                        details={"http_status": response.status_code, "response_body_error": exc.__class__.__name__},
                    ) from exc
                payload = self._parse_json(content, status_code=response.status_code, retryable_on_malformed=False)
                return ElevenLabsJsonResponse(response.status_code, response.headers, payload)
        except AppError:
            raise
        except (httpx.PoolTimeout, httpx.ConnectTimeout, httpx.ConnectError) as exc:
            raise AppError(
                "ELEVENLABS_TRANSPORT_UNAVAILABLE",
                "ElevenLabs transport was unavailable before Forced Alignment submission could be proven.",
                503,
                retryable=True,
                details={"transport_error": exc.__class__.__name__},
            ) from exc
        except (httpx.WriteTimeout, httpx.WriteError, httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError) as exc:
            raise ElevenLabsAmbiguousSubmission() from exc
        except httpx.TransportError as exc:
            raise ElevenLabsAmbiguousSubmission() from exc

    def _client(self) -> httpx.Client:
        if self.client is not None:
            return self.client
        if self._owned_client is None:
            self._owned_client = httpx.Client(base_url=self.api_base_url, timeout=self.timeout_seconds)
        return self._owned_client

    def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        side_effecting: bool,
    ) -> ElevenLabsBytesResponse:
        headers = {"xi-api-key": self.api_key, "Accept": "application/octet-stream"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            with self._client().stream(method, path, params=params, json=json_body, headers=headers) as response:
                if response.status_code >= 400:
                    raise self._known_http_error(response)
                try:
                    content = self._read_bounded(response)
                except AppError:
                    raise
                except (httpx.WriteTimeout, httpx.WriteError, httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError) as exc:
                    if side_effecting:
                        raise AppError(
                            "ELEVENLABS_RESPONSE_INVALID",
                            "ElevenLabs returned an incomplete successful response.",
                            502,
                            retryable=False,
                            details={"http_status": response.status_code, "response_body_error": exc.__class__.__name__},
                        ) from exc
                    raise AppError(
                        "ELEVENLABS_TRANSPORT_UNAVAILABLE",
                        "ElevenLabs transport failed while reading the response.",
                        503,
                        retryable=True,
                        details={"http_status": response.status_code, "transport_error": exc.__class__.__name__},
                    ) from exc
                if not content:
                    raise AppError("VOICE_PROVIDER_AUDIO_INVALID", "ElevenLabs returned empty audio.", 502, retryable=False)
                return ElevenLabsBytesResponse(response.status_code, response.headers, content)
        except AppError:
            raise
        except (httpx.PoolTimeout, httpx.ConnectTimeout, httpx.ConnectError) as exc:
            raise AppError(
                "ELEVENLABS_TRANSPORT_UNAVAILABLE",
                "ElevenLabs transport was unavailable before the request was accepted.",
                503,
                retryable=True,
                details={"transport_error": exc.__class__.__name__},
            ) from exc
        except (httpx.WriteTimeout, httpx.WriteError, httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError) as exc:
            if side_effecting:
                raise ElevenLabsAmbiguousSubmission() from exc
            raise AppError("ELEVENLABS_TRANSPORT_UNAVAILABLE", "ElevenLabs transport failed.", 503, retryable=True) from exc
        except httpx.TransportError as exc:
            if side_effecting:
                raise ElevenLabsAmbiguousSubmission() from exc
            raise AppError("ELEVENLABS_TRANSPORT_UNAVAILABLE", "ElevenLabs transport failed.", 503, retryable=True) from exc

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        side_effecting: bool,
    ) -> ElevenLabsJsonResponse:
        headers = {"xi-api-key": self.api_key, "Accept": "application/json"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            with self._client().stream(method, path, params=params, json=json_body, headers=headers) as response:
                if response.status_code >= 400:
                    raise self._known_http_error(response)
                try:
                    content = self._read_bounded(response)
                except AppError:
                    raise
                except (httpx.WriteTimeout, httpx.WriteError, httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError) as exc:
                    if side_effecting:
                        raise AppError(
                            "ELEVENLABS_RESPONSE_INVALID",
                            "ElevenLabs returned an incomplete successful response.",
                            502,
                            retryable=False,
                            details={"http_status": response.status_code, "response_body_error": exc.__class__.__name__},
                        ) from exc
                    raise AppError(
                        "ELEVENLABS_TRANSPORT_UNAVAILABLE",
                        "ElevenLabs transport failed while reading the response.",
                        503,
                        retryable=True,
                        details={"http_status": response.status_code, "transport_error": exc.__class__.__name__},
                    ) from exc
                payload = self._parse_json(content, status_code=response.status_code, retryable_on_malformed=not side_effecting)
                return ElevenLabsJsonResponse(response.status_code, response.headers, payload)
        except AppError:
            raise
        except (httpx.PoolTimeout, httpx.ConnectTimeout, httpx.ConnectError) as exc:
            raise AppError(
                "ELEVENLABS_TRANSPORT_UNAVAILABLE",
                "ElevenLabs transport was unavailable before the request was accepted.",
                503,
                retryable=True,
                details={"transport_error": exc.__class__.__name__},
            ) from exc
        except (httpx.WriteTimeout, httpx.WriteError, httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError) as exc:
            if side_effecting:
                raise ElevenLabsAmbiguousSubmission() from exc
            raise AppError("ELEVENLABS_TRANSPORT_UNAVAILABLE", "ElevenLabs transport failed.", 503, retryable=True) from exc
        except httpx.TransportError as exc:
            if side_effecting:
                raise ElevenLabsAmbiguousSubmission() from exc
            raise AppError("ELEVENLABS_TRANSPORT_UNAVAILABLE", "ElevenLabs transport failed.", 503, retryable=True) from exc

    def _known_http_error(self, response: httpx.Response) -> AppError:
        payload: Any = None
        malformed_body = False
        body_unavailable: str | None = None
        try:
            content = self._read_bounded(response)
        except AppError as exc:
            body_unavailable = exc.error_code
        except httpx.TransportError as exc:
            body_unavailable = exc.__class__.__name__
        else:
            payload, malformed_body = self._parse_error_payload(content)
        error = self._http_error(response.status_code, response.headers, payload, malformed_body=malformed_body)
        if body_unavailable:
            error.details["provider_error_body_unavailable"] = body_unavailable
        return error

    def _read_bounded(self, response: httpx.Response) -> bytes:
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_response_bytes:
                    raise AppError("ELEVENLABS_RESPONSE_TOO_LARGE", "ElevenLabs response exceeds the configured size limit.", 502, retryable=False, details={"http_status": response.status_code})
            except ValueError as exc:
                raise AppError("ELEVENLABS_RESPONSE_INVALID", "ElevenLabs response headers were malformed.", 502, retryable=False, details={"http_status": response.status_code}) from exc
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > self.max_response_bytes:
                raise AppError("ELEVENLABS_RESPONSE_TOO_LARGE", "ElevenLabs response exceeds the configured size limit.", 502, retryable=False, details={"http_status": response.status_code})
        return bytes(data)

    @staticmethod
    def _parse_json(content: bytes, *, status_code: int, retryable_on_malformed: bool) -> Any:
        try:
            return json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError("ELEVENLABS_RESPONSE_INVALID", "ElevenLabs returned malformed JSON.", 502, retryable=retryable_on_malformed, details={"http_status": status_code}) from exc

    @staticmethod
    def _parse_error_payload(content: bytes) -> tuple[Any, bool]:
        try:
            return json.loads(content.decode("utf-8")), False
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, True

    @classmethod
    def _http_error(cls, status: int, headers: Mapping[str, str], payload: Any, *, malformed_body: bool = False) -> AppError:
        provider_type, provider_code, request_id = cls._error_fields(payload, headers)
        error_code, retryable = cls._map_error(status, provider_type, provider_code)
        details: dict[str, Any] = {"http_status": status}
        if malformed_body:
            details["provider_error_body_malformed"] = True
        if provider_type:
            details["provider_error_type"] = provider_type
        if provider_code:
            details["provider_error_code"] = provider_code
        if request_id:
            details["request_id"] = request_id
        return AppError(error_code, "ElevenLabs rejected the Voice provider request.", status if 400 <= status <= 599 else 502, retryable=retryable, details=details)

    @staticmethod
    def _error_fields(payload: Any, headers: Mapping[str, str]) -> tuple[str | None, str | None, str | None]:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, list) and detail:
            detail = detail[0]
        provider_type = detail.get("type") if isinstance(detail, dict) else None
        provider_code = detail.get("code") if isinstance(detail, dict) else None
        request_id = detail.get("request_id") if isinstance(detail, dict) else None
        if not isinstance(request_id, str) or not request_id.strip():
            request_id = headers.get("request-id")
        return (
            provider_type.strip() if isinstance(provider_type, str) and provider_type.strip() else None,
            provider_code.strip() if isinstance(provider_code, str) and provider_code.strip() else None,
            request_id.strip() if isinstance(request_id, str) and request_id.strip() else None,
        )

    @staticmethod
    def _map_error(status: int, provider_type: str | None, provider_code: str | None) -> tuple[str, bool]:
        if status == 400 or provider_type == "validation_error" or provider_code == "invalid_request":
            return "ELEVENLABS_REQUEST_INVALID", False
        if status == 401 or provider_type == "authentication_error":
            return "ELEVENLABS_AUTH_INVALID", False
        if status == 402 or provider_type == "payment_required" or provider_code == "insufficient_credits":
            return "ELEVENLABS_CREDITS_EXHAUSTED", False
        if status == 403 or provider_type == "authorization_error" or provider_code == "model_access_denied":
            return "ELEVENLABS_ACCESS_DENIED", False
        if status == 404 and provider_code == "voice_not_found":
            return "TTS_VOICE_INVALID", False
        if status == 404:
            return "ELEVENLABS_RESOURCE_NOT_FOUND", False
        if status in {409, 422}:
            return "ELEVENLABS_REQUEST_INVALID", False
        if status == 429 and provider_code == "concurrent_limit_exceeded":
            return "ELEVENLABS_CONCURRENCY_LIMITED", True
        if status == 429 and provider_code == "system_busy":
            return "ELEVENLABS_SYSTEM_BUSY", True
        if status == 429:
            return "ELEVENLABS_RATE_LIMITED", True
        if status == 503 or provider_code in {"service_unavailable", "maintenance"}:
            return "ELEVENLABS_UNAVAILABLE", True
        if status >= 500:
            return "ELEVENLABS_INTERNAL_ERROR", True
        if status >= 400:
            return "ELEVENLABS_REQUEST_INVALID", False
        return "ELEVENLABS_RESPONSE_INVALID", False
