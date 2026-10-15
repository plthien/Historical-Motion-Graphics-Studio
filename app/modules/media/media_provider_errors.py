from __future__ import annotations

from app.api.errors import AppError


PROVIDER_ERROR_MAP = {
    429: "MEDIA_PROVIDER_QUOTA_EXHAUSTED",
    403: "MEDIA_PROVIDER_PERMISSION_DENIED",
    400: "MEDIA_PROVIDER_INVALID_REQUEST",
    500: "MEDIA_PROVIDER_UPSTREAM_ERROR",
    0: "MEDIA_PROVIDER_ENVIRONMENT_ERROR",
}


def normalize_provider_failure(provider_error_code: int | None, error: str | None, error_detail: str | None) -> AppError:
    code = PROVIDER_ERROR_MAP.get(provider_error_code or 0, "MEDIA_PROVIDER_ENVIRONMENT_ERROR")
    return AppError(
        code,
        "Media provider task failed.",
        502,
        retryable=False,
        details={
            "provider_error_code": provider_error_code,
            "error": error,
            "error_detail": error_detail,
        },
    )
