from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.diagnostic_redaction import REDACTION_MARKER, sanitize_diagnostic_value


def sanitize_media_provider_value(
    value: Any,
    *,
    configured_api_key: str,
    max_string_chars: int,
) -> Any:
    """Return a detached JSON-safe value with exact media-key redaction.

    Generic credential-pattern redaction cannot protect a bare provider echo. This
    helper first applies the shared diagnostic sanitizer and then removes every
    exact occurrence of the configured G-Labs key from mapping keys and values.
    """

    safe = sanitize_diagnostic_value(value, max_string_chars=max_string_chars)
    key = configured_api_key if type(configured_api_key) is str else ""

    def visit(item: Any) -> Any:
        if isinstance(item, str):
            bounded = item[:max_string_chars]
            if key:
                bounded = bounded.replace(key, REDACTION_MARKER)
            return bounded
        if isinstance(item, list):
            return [visit(entry) for entry in item]
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            for raw_key, raw_value in item.items():
                key_text = str(raw_key)[:max_string_chars]
                if key:
                    key_text = key_text.replace(key, REDACTION_MARKER)
                result[key_text] = visit(raw_value)
            return result
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return visit(str(item))

    return visit(safe)


def normalize_provider_error_code(
    value: object,
    *,
    configured_api_key: str,
) -> int | None:
    """Normalize the provider's optional failure code without coercion.

    G-Labs documents either ``0`` or an HTTP-like ``400..599`` integer.  Exact
    type checking intentionally rejects bool, float, string, and arbitrary-size
    integers before they can reach SQLite or a public response.
    """

    if type(value) is not int:
        return None
    code = value
    if code != 0 and not 400 <= code <= 599:
        return None

    key = configured_api_key if type(configured_api_key) is str else ""
    if key and key.isdecimal():
        try:
            is_canonical_decimal = str(int(key)) == key
        except (ValueError, OverflowError):
            is_canonical_decimal = False
        if is_canonical_decimal and str(code) == key:
            return None
    return code
