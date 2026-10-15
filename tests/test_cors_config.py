from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app


def test_blank_cors_env_values_fall_back_to_safe_local_defaults(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "")
    monkeypatch.setenv("CORS_ALLOW_ORIGIN_REGEX", "")

    settings = Settings.from_environment()

    assert settings.cors_allow_origins == (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    assert settings.cors_allow_origin_regex == r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"


def test_cors_headers_remain_available_when_blank_env_overrides_are_present(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "")
    monkeypatch.setenv("CORS_ALLOW_ORIGIN_REGEX", "")
    get_settings.cache_clear()

    try:
        with TestClient(create_app()) as client:
            response = client.options(
                "/api/v3/projects/project_test/topic-ideas/prompt",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )

        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    finally:
        get_settings.cache_clear()
