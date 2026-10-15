from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_hexdigest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json_hash(document: Any) -> str:
    payload = json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256_hexdigest(payload)
