from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.diagnostic_redaction import (
    freeze_diagnostic_mapping,
    sanitize_public_error_details,
    sanitize_public_llm_error_details,
)


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error_code: str
    message: str
    user_message: str | None = None
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class AppError(Exception):
    error_code: str
    message: str
    status_code: int = 400
    retryable: bool = False
    user_message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)


_MISSING = object()


def _read_error_field(error: AppError, field_name: str) -> Any:
    """Read an AppError field without allowing a malformed instance to escape raw."""

    try:
        return getattr(error, field_name)
    except Exception:
        return _MISSING


def normalize_app_error_contract(
    error: AppError,
    *,
    fallback_error_code: str,
    fallback_message: str,
    fallback_status_code: int,
    fallback_retryable: bool,
    fallback_details: dict[str, Any],
) -> tuple[AppError, str | None]:
    """Return a plain, stable AppError copy or a fixed fallback.

    Runtime providers and the gateway may produce an AppError whose public fields
    were mutated after construction. This helper validates only production-reachable
    values (plain strings, integers, booleans, and JSON-like details), freezes the
    details graph, and never asks callers to continue using the malformed instance.
    """

    issue: str | None = None
    error_code = _read_error_field(error, "error_code")
    message = _read_error_field(error, "message")
    status_code = _read_error_field(error, "status_code")
    retryable = _read_error_field(error, "retryable")
    user_message = _read_error_field(error, "user_message")
    raw_details = _read_error_field(error, "details")

    if error_code is _MISSING:
        issue = "app_error_code_missing"
    elif not isinstance(error_code, str) or not error_code.strip():
        issue = "app_error_code_type"
    elif message is _MISSING:
        issue = "app_error_message_missing"
    elif not isinstance(message, str):
        issue = "app_error_message_type"
    elif not message.strip():
        issue = "app_error_message_value"
    elif status_code is _MISSING:
        issue = "app_error_status_missing"
    elif type(status_code) is not int:
        issue = "app_error_status_type"
    elif not 400 <= status_code <= 599:
        issue = "app_error_status_value"
    elif retryable is _MISSING:
        issue = "app_error_retryable_missing"
    elif type(retryable) is not bool:
        issue = "app_error_retryable_type"
    elif user_message is not _MISSING and user_message is not None and not isinstance(user_message, str):
        issue = "app_error_user_message_type"

    details: dict[str, Any] | None = None
    if issue is None:
        if raw_details is _MISSING:
            issue = "app_error_details_missing"
        else:
            details, details_issue = freeze_diagnostic_mapping(raw_details)
            issue = details_issue

    if issue is not None or details is None:
        safe_details = dict(fallback_details)
        safe_details["contract_issue"] = issue or "app_error_details_type"
        return (
            AppError(
                fallback_error_code,
                fallback_message,
                fallback_status_code,
                retryable=fallback_retryable,
                details=safe_details,
            ),
            issue or "app_error_details_type",
        )

    return (
        AppError(
            error_code,
            message,
            status_code,
            retryable=retryable,
            user_message=None if user_message is _MISSING else user_message,
            details=details,
        ),
        None,
    )


def _json_error_response(payload: ErrorResponse, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=payload.model_dump(exclude_none=True))


def sanitize_app_error_for_public(exc: AppError, *, llm_details: bool = False) -> AppError:
    """Mutate an AppError into a safe public form and keep Exception.args aligned."""
    safe_text = sanitize_public_error_details(
        {"message": exc.message, "user_message": exc.user_message}
    )
    exc.message = str(safe_text.get("message", "An error occurred."))
    exc.user_message = (
        str(safe_text["user_message"])
        if safe_text.get("user_message") is not None
        else None
    )
    exc.details = (
        sanitize_public_llm_error_details(exc.details)
        if llm_details
        else sanitize_public_error_details(exc.details)
    )
    exc.args = (exc.message,)
    return exc


async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    """Serialize only a valid public error contract.

    Gateway and runner boundaries are expected to normalize their own AppErrors,
    but this is the final HTTP guard for any internal caller that bypasses them.
    It guarantees that an error response can never use a success/redirect or an
    otherwise invalid HTTP status code.
    """

    try:
        normalized, _ = normalize_app_error_contract(
            exc,
            fallback_error_code="INTERNAL_SERVER_ERROR",
            fallback_message="An unexpected error occurred.",
            fallback_status_code=500,
            fallback_retryable=False,
            fallback_details={
                "phase": "api_error_contract",
                "request_sent": False,
            },
        )
        sanitize_app_error_for_public(
            normalized,
            llm_details=normalized.error_code.startswith(("LLM_", "REAL_LLM_")),
        )
    except Exception:
        normalized = AppError(
            "INTERNAL_SERVER_ERROR",
            "An unexpected error occurred.",
            500,
            retryable=False,
            details={
                "phase": "api_error_contract",
                "request_sent": False,
                "contract_issue": "app_error_normalization_failed",
            },
        )

    status_code = normalized.status_code
    if type(status_code) is not int or not 400 <= status_code <= 599:
        status_code = 500
        normalized = AppError(
            "INTERNAL_SERVER_ERROR",
            "An unexpected error occurred.",
            500,
            retryable=False,
            details={
                "phase": "api_error_contract",
                "request_sent": False,
                "contract_issue": "app_error_status_value",
            },
        )

    payload = ErrorResponse(
        error_code=normalized.error_code,
        message=normalized.message,
        user_message=normalized.user_message,
        retryable=normalized.retryable,
        details=normalized.details,
    )
    return _json_error_response(payload, status_code)


async def handle_request_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Framework validation entries may contain the original request value in
    # ``input``. Public validation responses intentionally expose only stable
    # structural fields and sanitize field locations as untrusted strings.
    projected_errors: list[dict[str, Any]] = []
    for item in exc.errors():
        projected_errors.append(
            {
                "type": item.get("type"),
                "loc": list(item.get("loc", ())),
                "msg": item.get("msg"),
            }
        )
    safe_details = sanitize_public_error_details({"errors": projected_errors})
    payload = ErrorResponse(
        error_code="INVALID_REQUEST",
        message="Request validation failed.",
        retryable=False,
        details=safe_details if isinstance(safe_details, dict) else {},
    )
    return _json_error_response(payload, 422)


async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    payload = ErrorResponse(
        error_code="INTERNAL_SERVER_ERROR",
        message="An unexpected error occurred.",
        retryable=False,
        details={"exception_type": exc.__class__.__name__},
    )
    return _json_error_response(payload, 500)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
