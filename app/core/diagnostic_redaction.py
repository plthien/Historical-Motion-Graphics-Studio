from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
from pathlib import Path
import re
from typing import Any, Iterator
from urllib.parse import unquote, urlsplit, urlunsplit

REDACTION_MARKER = "<redacted>"
INTERNAL_PATH_REDACTION_MARKER = "<internal-path-redacted>"
TRUNCATION_MARKER = "...<truncated>"
RECURSIVE_MARKER = "<recursive>"
DEFAULT_MAX_STRING_CHARS = 20_000
DEFAULT_MAX_DEPTH = 12
DEFAULT_MAX_ITEMS = 2_000
_MAX_PERCENT_DECODE_PASSES = 4

# Credential recognition is grammar based rather than a closed list of exact
# variable names. Usage/accounting fields containing the word "token" remain
# useful diagnostics and are explicitly excluded.
_EXACT_SENSITIVE_LABELS = {
    "authorization",
    "proxy_authorization",
    "x_api_key",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "credential",
    "credentials",
    "cookie",
    "cookies",
    "set_cookie",
    "private_key",
    "secret_key",
    "signing_key",
}
_TOKEN_USAGE_LABELS = {
    "token_count",
    "tokens_count",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "max_tokens",
    "max_output_tokens",
    "token_limit",
    "token_usage",
    "usage_tokens",
}

_AUTHORIZATION_HEADER_PATTERN = re.compile(
    r"(?im)((?:[\"']?\b(?:proxy-authorization|authorization)\b[\"']?)\s*:\s*)"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\r\n]+)"
)
_URI_USERINFO_PATTERN = re.compile(r"(?i)(\bhttps?://)([^\s/@]+)@")
_BEARER_PATTERN = re.compile(r"(?i)(\bBearer\s+)([^\s,;\"'\]\[{}]+)")
_COOKIE_PATTERN = re.compile(r"(?im)(\b(?:cookie|set-cookie)\s*:\s*)([^\r\n]+)")
_ASSIGNMENT_PATTERN = re.compile(
    r"(?ix)"
    r"(?P<prefix>[\"']?(?P<label>[A-Za-z][A-Za-z0-9_.\-]{0,100})[\"']?\s*[:=]\s*)"
    r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;\]}]+)"
)

_FORBIDDEN_PUBLIC_KEYS = {
    "llm_call_log_file",
    "latest_llm_call_log_file",
    "diagnostic_log_file",
    "latest_diagnostic_log_file",
    "root_dir",
    "project_root",
    "log_dir",
}

_PUBLIC_LLM_DETAIL_KEYS = {
    "llm_call_log_id",
    "diagnostic_log_id",
    "llm_post_validation_log_id",
    "llm_attempt_log_id",
    "attempt_count",
    "max_attempts",
    "attempt_number",
    "attempts",
    "provider_type",
    "profile_id",
    "model",
    "schema_name",
    "schema",
    "failure_category",
    "result_category",
    "retry_decision",
    "retryable",
    "error_code",
    "message",
    "user_message",
    "status_code",
    "http_status",
    "endpoint",
    "phase",
    "operation",
    "request_sent",
    "record_written",
    "original_error_code",
    "exception_type",
    "contract_issue",
    "response_body_read_error_type",
    "response_root_type",
    "response_keys",
    "response_excerpt",
    "reason",
    "expected_root_type",
    "expected_content_path",
    "expected_envelope_path",
    "content_type",
    "root_type",
    "details",
    "error_detail",
    "errors",
    "validation_errors",
}
_PUBLIC_LLM_ATTEMPT_KEYS = {
    "attempt_number",
    "max_attempts",
    "provider_type",
    "model",
    "schema_name",
    "llm_call_log_id",
    "result_category",
    "retry_decision",
    "error_detail",
}

_FILE_URI_DETECT_PATTERN = re.compile(r"(?i)(?<![A-Za-z0-9_])file:(?:/{1,3}|\\\\)")
_WINDOWS_DRIVE_DETECT_PATTERN = re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/]")
_UNC_DETECT_PATTERN = re.compile(r"(?<![A-Za-z0-9_])(?:\\\\|//)[^/\\\s]")
_HTTP_SCHEME_PATTERN = re.compile(r"(?i)https?://")
_HTTP_PLACEHOLDER_PATTERN = re.compile(r"__SAFE_HTTP_ENDPOINT_(\d+)__")
# RFC 3986 ASCII characters. Characters such as |, >, backtick and Unicode
# arrows are deliberately not URL characters and terminate the protected span.
_URL_ASCII_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "-._~:/?#[]@!$&'()*+,;=%"
)


@dataclass(slots=True)
class _FreezeState:
    issues: set[str] = field(default_factory=set)
    seen: set[int] = field(default_factory=set)

    def issue(self, name: str) -> None:
        self.issues.add(name)


def _normalize_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _assignment_label(value: str) -> str | None:
    match = re.match(r"^[\"']?([A-Za-z][A-Za-z0-9_.\-]{0,100})[\"']?\s*[:=]", value.strip())
    return match.group(1) if match else None


def _is_sensitive_key_text(key_text: str) -> bool:
    candidate = _assignment_label(key_text) or key_text
    normalized = _normalize_label(candidate)
    if not normalized:
        return False
    if normalized in _TOKEN_USAGE_LABELS:
        return False
    if normalized in _EXACT_SENSITIVE_LABELS:
        return True
    if normalized.endswith(("secret", "token", "password", "credential", "apikey", "privatekey", "signingkey")):
        return True
    segments = tuple(part for part in normalized.split("_") if part)
    if any(part in {"secret", "password", "passwd", "credential", "credentials"} for part in segments):
        return True
    if "token" in segments and normalized not in _TOKEN_USAGE_LABELS:
        return True
    if len(segments) >= 2 and segments[-1] == "key" and segments[-2] in {
        "api",
        "private",
        "secret",
        "signing",
        "client",
        "service",
        "account",
    }:
        return True
    return False


def redact_secret_string(value: str) -> str:
    redacted = _AUTHORIZATION_HEADER_PATTERN.sub(
        lambda match: f"{match.group(1)}{REDACTION_MARKER}", value
    )
    redacted = _URI_USERINFO_PATTERN.sub(
        lambda match: f"{match.group(1)}{REDACTION_MARKER}@", redacted
    )
    redacted = _BEARER_PATTERN.sub(
        lambda match: f"{match.group(1)}{REDACTION_MARKER}", redacted
    )
    redacted = _COOKIE_PATTERN.sub(
        lambda match: f"{match.group(1)}{REDACTION_MARKER}", redacted
    )

    def replace_assignment(match: re.Match[str]) -> str:
        if not _is_sensitive_key_text(match.group("label")):
            return match.group(0)
        return f"{match.group('prefix')}{REDACTION_MARKER}"

    return _ASSIGNMENT_PATTERN.sub(replace_assignment, redacted)


def _safe_key_text(key: Any, state: _FreezeState | None = None) -> str:
    if isinstance(key, str):
        return key
    if key is None or isinstance(key, (bool, int, float)):
        return str(key)
    if isinstance(key, bytes):
        return key.decode("utf-8", errors="replace")
    if isinstance(key, Path):
        return str(key)
    if state is not None:
        state.issue("non_string_mapping_key")
    # Never execute arbitrary __str__, __repr__, __hash__ or __eq__ methods.
    return f"<{type(key).__name__}>"


def _bounded_percent_decode(value: str) -> str:
    current = value
    for _ in range(_MAX_PERCENT_DECODE_PASSES):
        try:
            decoded = unquote(current)
        except Exception:
            return current
        if decoded == current:
            break
        current = decoded
    return current


def _contains_posix_absolute_path_unprotected(text: str) -> bool:
    for index, character in enumerate(text):
        if character != "/":
            continue
        if index + 1 >= len(text) or text[index + 1] == "/":
            continue
        if index == 0:
            return True
        previous = text[index - 1]
        # A slash following a delimiter begins a local absolute path. Slashes
        # inside ordinary words/path fragments are not treated as roots.
        if not (previous.isalnum() or previous in "._-/"):
            return True
    return False


def _contains_absolute_local_path_unprotected(text: str) -> bool:
    decoded = _bounded_percent_decode(text)
    for candidate in (text, decoded):
        if (
            _FILE_URI_DETECT_PATTERN.search(candidate)
            or _WINDOWS_DRIVE_DETECT_PATTERN.search(candidate)
            or _UNC_DETECT_PATTERN.search(candidate)
            or _contains_posix_absolute_path_unprotected(candidate)
        ):
            return True
    return False


def _looks_like_external_path_after(text: str, index: int) -> bool:
    remainder = text[index:]
    if not remainder:
        return False
    # Delimiters may be followed by a local root, file URI, drive path or UNC.
    trimmed = remainder.lstrip()
    return bool(
        trimmed.startswith("/")
        or re.match(r"(?i)file:(?:/|\\)", trimmed)
        or re.match(r"(?i)[A-Z]:[\\/]", trimmed)
        or trimmed.startswith("\\\\")
        or trimmed.startswith("//")
    )


def _scan_http_endpoint(text: str, start: int) -> int:
    index = start
    while index < len(text):
        if text.startswith(REDACTION_MARKER, index):
            index += len(REDACTION_MARKER)
            continue
        character = text[index]
        if character not in _URL_ASCII_CHARS:
            break
        if character in ",;" and _looks_like_external_path_after(text, index + 1):
            break
        index += 1
    return index


def _trim_url_trailing_punctuation(candidate: str) -> tuple[str, str]:
    trailing = ""
    while candidate and candidate[-1] in ".,;":
        trailing = candidate[-1] + trailing
        candidate = candidate[:-1]
    pairs = {')': '(', ']': '[', '}': '{'}
    while candidate and candidate[-1] in pairs:
        closing = candidate[-1]
        opening = pairs[closing]
        if candidate.count(closing) <= candidate.count(opening):
            break
        trailing = closing + trailing
        candidate = candidate[:-1]
    return candidate, trailing


def _sanitize_url_component(component: str) -> str:
    if not component:
        return component
    # Preserve query/fragment separators while sanitizing each component.
    pieces = re.split(r"([&;])", component)
    clean: list[str] = []
    for piece in pieces:
        if piece in {"&", ";"}:
            clean.append(piece)
            continue
        if not piece:
            clean.append(piece)
            continue
        decoded_piece = _bounded_percent_decode(piece)
        decoded_label = _assignment_label(decoded_piece)
        if decoded_label and _is_sensitive_key_text(decoded_label):
            clean.append(REDACTION_MARKER)
            continue
        if "=" in piece:
            key, item = piece.split("=", 1)
            safe_key = (
                INTERNAL_PATH_REDACTION_MARKER
                if _contains_absolute_local_path_unprotected(key)
                else redact_secret_string(key)
            )
            decoded_item = _bounded_percent_decode(item)
            nested_label = _assignment_label(decoded_item)
            if _is_sensitive_key_text(key) or (nested_label and _is_sensitive_key_text(nested_label)):
                safe_item = REDACTION_MARKER
            elif _contains_absolute_local_path_unprotected(item):
                safe_item = INTERNAL_PATH_REDACTION_MARKER
            else:
                safe_item = redact_secret_string(item)
            clean.append(f"{safe_key}={safe_item}")
        elif _contains_absolute_local_path_unprotected(piece):
            clean.append(INTERNAL_PATH_REDACTION_MARKER)
        else:
            clean.append(redact_secret_string(piece))
    return "".join(clean)


def _sanitize_http_endpoint(endpoint: str) -> str:
    endpoint = redact_secret_string(endpoint)
    try:
        parsed = urlsplit(endpoint)
        # Accessing hostname validates bracketed IPv6 syntax. It is guarded
        # because malformed URLs are untrusted diagnostic text.
        _ = parsed.hostname
    except Exception:
        return endpoint
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return endpoint
    netloc = parsed.netloc
    if "@" in netloc:
        _, host = netloc.rsplit("@", 1)
        netloc = f"{REDACTION_MARKER}@{host}"
    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            _sanitize_url_component(parsed.query),
            _sanitize_url_component(parsed.fragment),
        )
    )


def _protect_http_endpoints(text: str) -> tuple[str, list[str]]:
    endpoints: list[str] = []
    output: list[str] = []
    cursor = 0
    while True:
        match = _HTTP_SCHEME_PATTERN.search(text, cursor)
        if match is None:
            output.append(text[cursor:])
            break
        output.append(text[cursor:match.start()])
        end = _scan_http_endpoint(text, match.start())
        candidate, trailing = _trim_url_trailing_punctuation(text[match.start():end])
        if not candidate:
            output.append(text[match.start():end])
            cursor = end
            continue
        endpoints.append(_sanitize_http_endpoint(candidate))
        output.append(f"__SAFE_HTTP_ENDPOINT_{len(endpoints) - 1}__")
        output.append(trailing)
        cursor = end
    return "".join(output), endpoints


def _restore_http_endpoints(text: str, endpoints: list[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return endpoints[index] if 0 <= index < len(endpoints) else ""

    return _HTTP_PLACEHOLDER_PATTERN.sub(replace, text)


def _redact_absolute_paths(text: str) -> str:
    protected, endpoints = _protect_http_endpoints(text)
    output: list[str] = []
    cursor = 0
    for match in _HTTP_PLACEHOLDER_PATTERN.finditer(protected):
        segment = protected[cursor:match.start()]
        output.append(
            INTERNAL_PATH_REDACTION_MARKER
            if _contains_absolute_local_path_unprotected(segment)
            else segment
        )
        output.append(match.group(0))
        cursor = match.end()
    tail = protected[cursor:]
    if _contains_absolute_local_path_unprotected(tail):
        # Comma/semicolon were historically treated as prose delimiters; keep
        # the conservative whole-field behavior for those ambiguous forms.
        if tail.lstrip().startswith((",", ";")):
            return INTERNAL_PATH_REDACTION_MARKER
        output.append(INTERNAL_PATH_REDACTION_MARKER)
    else:
        output.append(tail)
    return _restore_http_endpoints("".join(output), endpoints)


def _sanitize_mapping_key(
    key: Any,
    *,
    max_string_chars: int,
    state: _FreezeState | None = None,
) -> tuple[str, bool]:
    original = _safe_key_text(key, state)
    sanitized = _redact_absolute_paths(redact_secret_string(original))
    if len(sanitized) > max_string_chars:
        sanitized = sanitized[:max_string_chars] + TRUNCATION_MARKER
    sensitive = _is_sensitive_key_text(original) or _is_sensitive_key_text(sanitized)
    return sanitized, sensitive


def _deduplicate_key(clean: dict[str, Any], candidate: str) -> str:
    if candidate not in clean:
        return candidate
    index = 2
    while f"{candidate}#{index}" in clean:
        index += 1
    return f"{candidate}#{index}"


def _safe_mapping_entries(value: Mapping[Any, Any], state: _FreezeState) -> Iterator[tuple[Any, Any]]:
    try:
        iterator = iter(value.items())
    except Exception:
        state.issue("mapping_items_error")
        return
    count = 0
    while count < DEFAULT_MAX_ITEMS:
        try:
            entry = next(iterator)
        except StopIteration:
            return
        except Exception:
            state.issue("mapping_iteration_error")
            return
        count += 1
        if not isinstance(entry, (tuple, list)):
            state.issue("malformed_mapping_entry")
            return
        try:
            if len(entry) != 2:
                state.issue("malformed_mapping_entry")
                return
            key = entry[0]
            item = entry[1]
        except Exception:
            state.issue("malformed_mapping_entry")
            return
        yield key, item
    state.issue("max_items_exceeded")


def _safe_sequence_items(value: Any, state: _FreezeState) -> Iterator[Any]:
    try:
        iterator = iter(value)
    except Exception:
        state.issue("sequence_iteration_error")
        return
    count = 0
    while count < DEFAULT_MAX_ITEMS:
        try:
            item = next(iterator)
        except StopIteration:
            return
        except Exception:
            state.issue("sequence_iteration_error")
            return
        count += 1
        yield item
    state.issue("max_items_exceeded")


def _freeze(
    value: Any,
    *,
    max_string_chars: int,
    state: _FreezeState,
    depth: int,
) -> Any:
    if depth > DEFAULT_MAX_DEPTH:
        state.issue("max_depth_exceeded")
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        text = redact_secret_string(value)
        return text if len(text) <= max_string_chars else text[:max_string_chars] + TRUNCATION_MARKER
    if isinstance(value, bytes):
        return _freeze(
            value.decode("utf-8", errors="replace"),
            max_string_chars=max_string_chars,
            state=state,
            depth=depth,
        )
    if isinstance(value, Path):
        return _freeze(str(value), max_string_chars=max_string_chars, state=state, depth=depth)

    object_id = id(value)
    if object_id in state.seen:
        state.issue("recursive_reference")
        return RECURSIVE_MARKER
    state.seen.add(object_id)
    try:
        if isinstance(value, Mapping):
            clean: dict[str, Any] = {}
            before = set(state.issues)
            entries = list(_safe_mapping_entries(value, state))
            if state.issues != before and not entries:
                return f"<{type(value).__name__}>"
            for key, item in entries:
                try:
                    key_text, sensitive = _sanitize_mapping_key(
                        key,
                        max_string_chars=max_string_chars,
                        state=state,
                    )
                    output_key = _deduplicate_key(clean, key_text)
                    clean[output_key] = REDACTION_MARKER if sensitive else _freeze(
                        item,
                        max_string_chars=max_string_chars,
                        state=state,
                        depth=depth + 1,
                    )
                except Exception:
                    state.issue("mapping_entry_error")
                    return f"<{type(value).__name__}>"
            return clean
        if isinstance(value, (list, tuple, set, frozenset)):
            before = set(state.issues)
            items = list(_safe_sequence_items(value, state))
            if state.issues != before and not items:
                return f"<{type(value).__name__}>"
            clean_items: list[Any] = []
            for item in items:
                try:
                    clean_items.append(
                        _freeze(
                            item,
                            max_string_chars=max_string_chars,
                            state=state,
                            depth=depth + 1,
                        )
                    )
                except Exception:
                    state.issue("sequence_item_error")
                    clean_items.append(f"<{type(item).__name__}>")
            return clean_items
        state.issue("unsupported_object")
        return f"<{type(value).__name__}>"
    except Exception:
        state.issue("freeze_error")
        return f"<{type(value).__name__}>"
    finally:
        state.seen.discard(object_id)


def _freeze_with_state(
    value: Any,
    *,
    max_string_chars: int = DEFAULT_MAX_STRING_CHARS,
) -> tuple[Any, _FreezeState]:
    state = _FreezeState()
    frozen = _freeze(value, max_string_chars=max_string_chars, state=state, depth=0)
    return frozen, state


def sanitize_diagnostic_value(value: Any, *, max_string_chars: int = DEFAULT_MAX_STRING_CHARS) -> Any:
    """Return bounded JSON-safe diagnostics without executing hostile object protocols."""
    frozen, _ = _freeze_with_state(value, max_string_chars=max_string_chars)
    return frozen


def freeze_diagnostic_mapping(value: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Strictly freeze untrusted AppError.details before gateway/runner enrichment.

    A malformed graph is a provider/gateway contract violation, not data that may
    be traversed by allowlist code. The returned mapping contains only plain
    string keys and JSON-safe values.
    """
    if not isinstance(value, Mapping):
        return None, "app_error_details_type"
    frozen, state = _freeze_with_state(value)
    if not isinstance(frozen, dict):
        return None, "app_error_details_type"
    fatal_issues = state.issues - {"unsupported_object"}
    if fatal_issues:
        return None, "app_error_details_graph"
    return frozen, None


def _strip_public_paths(value: Any) -> Any:
    # Input has already been frozen by sanitize_diagnostic_value, so traversal is
    # over plain builtins only.
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text, _ = _sanitize_mapping_key(key, max_string_chars=DEFAULT_MAX_STRING_CHARS)
            lowered = key_text.lower()
            if lowered in _FORBIDDEN_PUBLIC_KEYS or lowered.endswith("_file"):
                continue
            output_key = _deduplicate_key(clean, key_text)
            clean[output_key] = _strip_public_paths(item)
        return clean
    if isinstance(value, list):
        return [_strip_public_paths(item) for item in value]
    if isinstance(value, str):
        return _redact_absolute_paths(value)
    return value


def sanitize_public_error_details(value: Any) -> Any:
    """Sanitize public messages/details and remove absolute local path content."""
    return _strip_public_paths(sanitize_diagnostic_value(value))


def _allowlist_llm_attempt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    clean: dict[str, Any] = {}
    for key in _PUBLIC_LLM_ATTEMPT_KEYS:
        if key not in value:
            continue
        item = value[key]
        clean[key] = _allowlist_llm_details(item) if key == "error_detail" else item
    return clean


def _allowlist_llm_details(value: Any) -> Any:
    # This function is intentionally private and accepts only the plain builtins
    # produced by sanitize_diagnostic_value. It never traverses adapter-owned data.
    if isinstance(value, list):
        return [_allowlist_llm_details(item) for item in value]
    if not isinstance(value, dict):
        return value
    clean: dict[str, Any] = {}
    for key, item in value.items():
        if key not in _PUBLIC_LLM_DETAIL_KEYS:
            continue
        if key == "attempts" and isinstance(item, list):
            clean[key] = [_allowlist_llm_attempt(attempt) for attempt in item]
        elif key in {"details", "error_detail"}:
            clean[key] = _allowlist_llm_details(item)
        else:
            clean[key] = item
    return clean


def sanitize_public_llm_error_details(value: Any) -> dict[str, Any]:
    """Safe-freeze, allowlist and public-sanitize LLM error metadata."""
    frozen = sanitize_diagnostic_value(value)
    allowlisted = _allowlist_llm_details(frozen)
    sanitized = sanitize_public_error_details(allowlisted)
    return sanitized if isinstance(sanitized, dict) else {}
