from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.core.config import get_settings as get_runtime_settings
from app.core.time import utc_now_iso
from app.db.models import Setting
from app.modules.media.media_secret_safety import sanitize_media_provider_value
from app.repositories import SettingsRepository

SETTINGS_KEY = "media_provider_settings"
SECRET_KEY = "media_provider_api_keys"
_RETIRED_SETTINGS_KEYS = frozenset({"supported_image_aspect_ratios", "max_poll_attempts"})


def default_media_provider_settings() -> dict[str, Any]:
    return {
        "provider": "glabs_webhook",
        "enabled": True,
        "base_url": os.getenv("MEDIA_PROVIDER_BASE_URL", "http://127.0.0.1:8765"),
        "api_key_configured": False,
        "default_image_model": os.getenv("MEDIA_PROVIDER_DEFAULT_IMAGE_MODEL", "nano_banana_2"),
        "default_image_aspect_ratio": os.getenv("MEDIA_PROVIDER_DEFAULT_IMAGE_ASPECT_RATIO", "1:1"),
        "default_upscale": [],
        "poll_interval_seconds": int(os.getenv("MEDIA_PROVIDER_POLL_INTERVAL_SECONDS", "4")),
        "max_parallel_tasks": int(os.getenv("MEDIA_PROVIDER_MAX_PARALLEL_TASKS", "3")),
        "enabled_media_kinds": ["image"],
    }


class MediaProviderSettingsService:
    def __init__(self, session: Session, repository: SettingsRepository) -> None:
        self.session = session
        self.repository = repository
        self.root = get_runtime_settings().root_dir

    def _schema(self) -> dict[str, Any]:
        return json.loads((self.root / "contracts" / "schemas" / "platform" / "media_provider_settings.schema.json").read_text(encoding="utf-8"))

    def get_settings(self) -> dict[str, Any]:
        row = self.repository.get(SETTINGS_KEY)
        settings = deepcopy(row.value_json) if row is not None and isinstance(row.value_json, dict) else default_media_provider_settings()
        for key in _RETIRED_SETTINGS_KEYS:
            settings.pop(key, None)
        defaults = default_media_provider_settings()
        merged = {**defaults, **settings}
        merged["api_key_configured"] = self._api_key_configured()
        return self._masked(merged)

    def get_settings_for_backend(self) -> dict[str, Any]:
        settings = self.get_settings()
        api_key = self._current_api_key()
        settings["api_key_configured"] = bool(api_key)
        settings["api_key"] = api_key
        return settings

    def put_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = deepcopy(payload)
        api_key = str(settings.pop("api_key", "") or "")
        settings.pop("api_key_configured", None)
        settings["api_key_configured"] = bool(api_key or self._current_api_key())
        self._validate(settings)
        now = utc_now_iso()
        self.repository.upsert(Setting(key=SETTINGS_KEY, value_json=self._masked(settings), updated_at=now))
        if api_key:
            self.repository.upsert(Setting(key=SECRET_KEY, value_json={"glabs_webhook": api_key}, updated_at=now))
        self.session.commit()
        return self.get_settings()

    def test_connection(self) -> dict[str, Any]:
        settings = self.get_settings_for_backend()
        self._validate_runtime_ready(settings)
        from app.modules.media.glabs_webhook_client import GLabsWebhookClient

        client = GLabsWebhookClient(settings)
        try:
            client.health()
            client.test_auth()
        except AppError as exc:
            safe_message = sanitize_media_provider_value(
                exc.message,
                configured_api_key=str(settings.get("api_key", "") or ""),
                max_string_chars=2048,
            )
            safe_details = sanitize_media_provider_value(
                exc.details,
                configured_api_key=str(settings.get("api_key", "") or ""),
                max_string_chars=4096,
            )
            safe_user_message = sanitize_media_provider_value(
                exc.user_message,
                configured_api_key=str(settings.get("api_key", "") or ""),
                max_string_chars=2048,
            ) if exc.user_message is not None else None
            raise AppError(
                exc.error_code,
                str(safe_message),
                exc.status_code,
                retryable=exc.retryable,
                user_message=str(safe_user_message) if safe_user_message is not None else None,
                details=safe_details if isinstance(safe_details, dict) else {},
            ) from exc
        return {
            "status": "ready",
            "message": "Media provider configuration is ready.",
            "details": {
                "provider": "glabs_webhook",
                "health_reachable": True,
                "authentication_accepted": True,
            },
        }

    def readiness(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        current = deepcopy(settings or self.get_settings())
        issues: list[dict[str, str]] = []
        if not current.get("enabled"):
            issues.append({"code": "disabled", "message": "Media provider is disabled."})
        if not current.get("base_url"):
            issues.append({"code": "base_url_missing", "message": "Media provider base URL is missing."})
        if not current.get("api_key_configured"):
            issues.append({"code": "api_key_missing", "message": "Media provider API key is not configured."})
        return {
            "status": "ready" if not issues else "not_ready",
            "issues": issues,
            "provider": current.get("provider"),
            "enabled_media_kinds": list(current.get("enabled_media_kinds", [])),
        }

    def _validate(self, settings: dict[str, Any]) -> None:
        validator = Draft202012Validator(self._schema())
        issues = sorted(validator.iter_errors(settings), key=lambda issue: list(issue.absolute_path))
        if issues:
            raise AppError(
                "MEDIA_PROVIDER_CONFIG_INVALID",
                "Media provider settings do not match the contract.",
                422,
                retryable=False,
                details={"errors": [{"path": "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in issue.absolute_path), "message": issue.message} for issue in issues]},
            )
        base_url = str(settings.get("base_url", ""))
        try:
            parts = urlsplit(base_url)
        except ValueError as exc:
            raise AppError("MEDIA_PROVIDER_CONFIG_INVALID", "Media provider base_url is invalid.", 422, retryable=False) from exc
        if parts.scheme not in {"http", "https"} or parts.hostname not in {"127.0.0.1", "localhost"}:
            raise AppError("MEDIA_PROVIDER_CONFIG_INVALID", "Media provider base_url must target localhost over HTTP(S).", 422, retryable=False)
        if parts.username is not None or parts.password is not None:
            raise AppError("MEDIA_PROVIDER_CONFIG_INVALID", "Media provider base_url must not contain URI userinfo.", 422, retryable=False)

    def _validate_runtime_ready(self, settings: dict[str, Any]) -> None:
        self._validate({key: value for key, value in settings.items() if key != "api_key"})
        if not settings.get("enabled"):
            raise AppError("MEDIA_PROVIDER_CONFIG_INVALID", "Media provider is disabled.", 409, retryable=False)
        if not settings.get("api_key"):
            raise AppError("MEDIA_PROVIDER_CONFIG_INVALID", "Media provider API key is missing.", 409, retryable=False)

    def _load_secrets(self) -> dict[str, str]:
        row = self.repository.get(SECRET_KEY)
        return dict(row.value_json) if row is not None and isinstance(row.value_json, dict) else {}

    def _current_api_key(self) -> str:
        secrets = self._load_secrets()
        return str(secrets.get("glabs_webhook") or os.getenv("GLABS_WEBHOOK_API_KEY", "") or "")

    def _api_key_configured(self) -> bool:
        return bool(self._current_api_key())

    @staticmethod
    def _masked(settings: dict[str, Any]) -> dict[str, Any]:
        clean = deepcopy(settings)
        clean.pop("api_key", None)
        clean["api_key_configured"] = bool(clean.get("api_key_configured"))
        return clean
