from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.api.errors import AppError
from app.core.diagnostic_redaction import sanitize_diagnostic_value
from app.modules.media.bounded_http_transport import (
    BoundedHttpTransport,
    BoundedTransportBodyTooLarge,
    BoundedTransportDeadline,
    BoundedTransportError,
    BoundedTransportHttpError,
    BoundedTransportMalformedResponse,
    BoundedTransportRequestTooLarge,
    BoundedTransportUnavailable,
)
from app.modules.media.media_secret_safety import sanitize_media_provider_value
from app.modules.media.transport_policy import DEFAULT_MEDIA_TRANSPORT_POLICY, MediaTransportPolicy

_PROVIDER_STATUSES = {"pending", "running", "completed", "failed"}
_SUPPORTED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
_ERROR_BODY_LIMIT = 4096
_TASK_ID_PATTERN = re.compile(r"^[0-9A-Fa-f]{8}$")


class DownloadMetadata:
    __slots__ = ("mime_type", "size_bytes", "sha256")

    def __init__(self, *, mime_type: str, size_bytes: int, sha256: str) -> None:
        self.mime_type = mime_type
        self.size_bytes = size_bytes
        self.sha256 = sha256


class GLabsWebhookClient:
    def __init__(
        self,
        settings: dict[str, Any],
        *,
        policy: MediaTransportPolicy | None = None,
        transport: BoundedHttpTransport | None = None,
    ) -> None:
        try:
            self.settings = dict(settings)
            self.base_url = str(settings["base_url"]).rstrip("/") + "/"
            self.api_key = str(settings.get("api_key", "") or "")
            parsed = urlsplit(self.base_url)
            if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost"}:
                raise ValueError("media provider base URL must be local HTTP(S)")
            if parsed.username is not None or parsed.password is not None:
                raise ValueError("media provider base URL must not contain userinfo")
            self.policy = policy or DEFAULT_MEDIA_TRANSPORT_POLICY
            self.transport = transport or BoundedHttpTransport()
            # Compatibility-only diagnostic value; it is not an ownership or
            # total-deadline boundary.
            self.timeout = self.policy.control_plane_io_timeout_seconds
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise AppError(
                "MEDIA_PROVIDER_CONFIG_INVALID",
                "Media provider configuration is invalid.",
                409,
                retryable=False,
            ) from exc

    def health(self) -> dict[str, Any]:
        return self._request_json("GET", "api/health", auth=False)

    def test_auth(self) -> dict[str, Any]:
        return self._request_json("GET", "api/tasks", auth=True)

    def submit_image(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = self._request_json("POST", "api/image/generate", payload, auth=True, expected_status=202)
        task_id = self.normalize_task_id(body.get("task_id"))
        status = body.get("status")
        if not isinstance(status, str) or status not in {"pending", "running"}:
            raise self._upstream_contract_error("Submit response has no valid provider status.")
        normalized = dict(body)
        normalized["task_id"] = task_id
        return normalized

    def get_status(self, external_task_id: str) -> dict[str, Any]:
        canonical_task_id = self.normalize_task_id(external_task_id)
        body = self._request_json("GET", f"api/status/{canonical_task_id}", auth=True)
        self._validate_task_identity(body, canonical_task_id)
        status = body.get("status")
        if not isinstance(status, str):
            raise self._upstream_contract_error("Status response has no valid provider status.")
        if "results" in body:
            self._validate_result_list(body.get("results"), allow_empty=False)
        return body

    def get_result(self, external_task_id: str) -> dict[str, Any]:
        canonical_task_id = self.normalize_task_id(external_task_id)
        body = self._request_json("GET", f"api/result/{canonical_task_id}", auth=True)
        self._validate_task_identity(body, canonical_task_id)
        status = body.get("status")
        if status != "completed":
            raise self._upstream_contract_error("Result response is not completed.")
        self._validate_result_list(body.get("results"), allow_empty=False)
        return body

    def download_file(self, url: str, destination: Path, max_bytes: int) -> DownloadMetadata:
        self.validate_result_url(url)
        if type(max_bytes) is not int or max_bytes <= 0:
            raise AppError(
                "MEDIA_PROVIDER_CONFIG_INVALID",
                "Media download byte limit is invalid.",
                409,
                retryable=False,
            )
        try:
            metadata = self.transport.download_to_file(
                url=url,
                destination=destination,
                total_deadline_seconds=self.policy.download_total_deadline_seconds,
                io_timeout_seconds=self.policy.download_io_timeout_seconds,
                max_bytes=max_bytes,
            )
            if metadata.size_bytes == 0:
                raise self._download_error("Media result is empty.", url=url)
            with destination.open("rb") as handle:
                signature = handle.read(16)
            verified_mime = self._sniff_image_mime(signature)
            if verified_mime is None or verified_mime not in _SUPPORTED_IMAGE_MIME_TYPES:
                raise self._download_error("Media result is not a supported image.", url=url)
            if metadata.content_type is not None and metadata.content_type != verified_mime:
                raise self._download_error("Media result Content-Type does not match its image signature.", url=url)
            return DownloadMetadata(
                mime_type=verified_mime,
                size_bytes=metadata.size_bytes,
                sha256=metadata.sha256,
            )
        except AppError:
            self._remove_partial(destination)
            raise
        except BoundedTransportDeadline as exc:
            self._remove_partial(destination)
            raise self._download_error("Media result download timed out.", url=url) from exc
        except BoundedTransportHttpError as exc:
            self._remove_partial(destination)
            raise self._download_error(
                "Media result returned an unexpected HTTP status.",
                url=url,
                status_code=exc.status_code,
            ) from exc
        except BoundedTransportBodyTooLarge as exc:
            self._remove_partial(destination)
            raise self._download_error("Media result exceeds the configured byte limit.", url=url) from exc
        except (BoundedTransportUnavailable, BoundedTransportMalformedResponse, BoundedTransportError) as exc:
            self._remove_partial(destination)
            raise self._download_error("Media result download failed.", url=url) from exc
        except Exception as exc:
            self._remove_partial(destination)
            raise self._download_error("Media result download failed.", url=url) from exc

    @staticmethod
    def validate_result_url(url: str) -> None:
        if not isinstance(url, str) or not url.strip():
            raise GLabsWebhookClient._download_error("Provider returned an empty result URL.")
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
        except (TypeError, ValueError) as exc:
            raise GLabsWebhookClient._download_error("Provider returned an invalid result URL.") from exc
        if parsed.scheme not in {"http", "https"}:
            raise GLabsWebhookClient._download_error("Provider result URL must use HTTP or HTTPS.", url=url)
        if hostname not in {"localhost", "127.0.0.1"}:
            raise GLabsWebhookClient._download_error("Provider result URL must use a local host.", url=url)
        if parsed.username is not None or parsed.password is not None:
            raise GLabsWebhookClient._download_error("Provider result URL must not contain URI userinfo.", url=url)
        if not parsed.path or parsed.path == "/":
            raise GLabsWebhookClient._download_error("Provider result URL must include a file path.", url=url)
        if parsed.fragment:
            raise GLabsWebhookClient._download_error("Provider result URL must not contain a fragment.", url=url)

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        auth: bool,
        expected_status: int = 200,
    ) -> dict[str, Any]:
        try:
            data = json.dumps(payload).encode("utf-8") if payload is not None else None
            headers = {"Content-Type": "application/json"}
            if auth:
                headers["X-API-Key"] = self.api_key
            url = self.base_url + path.lstrip("/")
            body = self.transport.request_json(
                method=method,
                url=url,
                headers=headers,
                body=data,
                total_deadline_seconds=self.policy.control_plane_total_deadline_seconds,
                io_timeout_seconds=self.policy.control_plane_io_timeout_seconds,
                max_request_bytes=self.policy.control_plane_max_request_bytes,
                max_response_bytes=self.policy.control_plane_max_response_bytes,
                expected_status=expected_status,
            )
            return dict(body)
        except AppError:
            raise
        except BoundedTransportDeadline as exc:
            raise AppError(
                "MEDIA_PROVIDER_REQUEST_TIMEOUT",
                "Media provider request timed out.",
                504,
                retryable=True,
            ) from exc
        except BoundedTransportHttpError as exc:
            details = {"status_code": exc.status_code, "body": self._safe_error_body(exc.body)}
            if exc.status_code in {401, 403}:
                raise AppError(
                    "MEDIA_PROVIDER_AUTH_FAILED",
                    "Media provider authentication failed.",
                    502,
                    retryable=False,
                    details=details,
                ) from exc
            if exc.status_code == 400:
                raise AppError(
                    "MEDIA_PROVIDER_INVALID_REQUEST",
                    "Media provider rejected the request as invalid.",
                    502,
                    retryable=False,
                    details=details,
                ) from exc
            if exc.status_code == 429:
                raise AppError(
                    "MEDIA_PROVIDER_QUOTA_EXHAUSTED",
                    "Media provider quota or request capacity is exhausted.",
                    502,
                    retryable=True,
                    details=details,
                ) from exc
            raise AppError(
                "MEDIA_PROVIDER_UPSTREAM_ERROR",
                "Media provider returned an HTTP error.",
                502,
                retryable=True,
                details=details,
            ) from exc
        except BoundedTransportRequestTooLarge as exc:
            raise AppError(
                "MEDIA_PROVIDER_CONFIG_INVALID",
                "Media provider request exceeds the internal control-plane byte limit.",
                409,
                retryable=False,
            ) from exc
        except BoundedTransportBodyTooLarge as exc:
            raise self._upstream_contract_error("Media provider response exceeds the configured byte limit.") from exc
        except BoundedTransportMalformedResponse as exc:
            message = str(exc)
            if "UTF-8" in message:
                public = "Media provider returned invalid UTF-8."
            elif "object" in message:
                public = "Media provider response must be a JSON object."
            elif "JSON" in message:
                public = "Media provider returned malformed JSON."
            elif "empty" in message:
                public = "Media provider returned an empty response."
            else:
                public = "Media provider returned a malformed response."
            raise self._upstream_contract_error(public) from exc
        except BoundedTransportUnavailable as exc:
            raise AppError(
                "MEDIA_PROVIDER_UNAVAILABLE",
                "Media provider is unavailable.",
                502,
                retryable=True,
            ) from exc
        except (TypeError, ValueError, OverflowError) as exc:
            raise AppError(
                "MEDIA_PROVIDER_CONFIG_INVALID",
                "Media provider request could not be prepared.",
                409,
                retryable=False,
            ) from exc
        except Exception as exc:
            raise AppError(
                "MEDIA_PROVIDER_UNAVAILABLE",
                "Media provider request failed.",
                502,
                retryable=False,
            ) from exc

    @classmethod
    def _validate_task_identity(cls, body: dict[str, Any], expected_task_id: str) -> None:
        task_id = cls.normalize_task_id(body.get("task_id"))
        if task_id != expected_task_id:
            raise GLabsWebhookClient._upstream_contract_error("Provider response has an invalid task_id.")

    @staticmethod
    def normalize_task_id(value: Any) -> str:
        if type(value) is not str or _TASK_ID_PATTERN.fullmatch(value) is None:
            raise GLabsWebhookClient._upstream_contract_error(
                "Provider task_id must be exactly 8 hexadecimal characters."
            )
        return value.lower()

    @staticmethod
    def _validate_result_list(value: Any, *, allow_empty: bool) -> list[str]:
        if not isinstance(value, list):
            raise GLabsWebhookClient._upstream_contract_error("Provider response has an invalid results list.")
        if not allow_empty and not value:
            raise GLabsWebhookClient._upstream_contract_error("Provider response has an empty results list.")
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise GLabsWebhookClient._upstream_contract_error("Provider response has an invalid result URL.")
            normalized.append(item)
        return normalized

    @staticmethod
    def _sniff_image_mime(signature: bytes) -> str | None:
        if signature.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if signature.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if len(signature) >= 12 and signature[:4] == b"RIFF" and signature[8:12] == b"WEBP":
            return "image/webp"
        return None

    def _safe_error_body(self, raw: bytes) -> Any:
        bounded = raw[:_ERROR_BODY_LIMIT]
        text = bounded.decode("utf-8", errors="replace")
        return sanitize_media_provider_value(
            text,
            configured_api_key=self.api_key,
            max_string_chars=_ERROR_BODY_LIMIT,
        )

    @staticmethod
    def _upstream_contract_error(message: str) -> AppError:
        return AppError(
            "MEDIA_PROVIDER_UPSTREAM_ERROR",
            message,
            502,
            retryable=False,
        )

    @staticmethod
    def _download_error(
        message: str,
        *,
        url: str | None = None,
        status_code: int | None = None,
        reason: Any = None,
    ) -> AppError:
        details: dict[str, Any] = {}
        if url is not None:
            details["url"] = url
        if status_code is not None:
            details["status_code"] = status_code
        if reason is not None:
            details["reason"] = sanitize_diagnostic_value(str(reason), max_string_chars=512)
        return AppError(
            "MEDIA_RESULT_DOWNLOAD_FAILED",
            message,
            502,
            retryable=False,
            details=details,
        )

    @staticmethod
    def _remove_partial(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass
