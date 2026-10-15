from __future__ import annotations

import re
from uuid import uuid4


ARTIFACT_ID_PATTERN = r"^artifact_[0-9a-f]{12}$"
_ARTIFACT_ID_RE = re.compile(ARTIFACT_ID_PATTERN)


def generate_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def validate_artifact_id(value: str) -> str:
    """Return a valid Artifact ID or raise ``ValueError``.

    The regex is shared with Pydantic and JSON Schema-facing code so runtime
    filesystem publication cannot drift from the public Artifact ID contract.
    """

    if not isinstance(value, str) or _ARTIFACT_ID_RE.fullmatch(value) is None:
        raise ValueError("invalid artifact id")
    return value
