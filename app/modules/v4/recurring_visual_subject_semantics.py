from __future__ import annotations

import re
import unicodedata
from typing import Literal

RecurringSubjectKind = Literal[
    "living_individual",
    "living_group",
    "non_living",
    "unspecified",
]

_WHITESPACE_RE = re.compile(r"\s+")

# Exact current-authority vocabulary only. Unknown open-string Script Analysis
# values remain unspecified; descriptions, names, IDs, and inventory position
# never participate in semantic-kind resolution.
_KIND_BY_VISUAL_TYPE: dict[str, RecurringSubjectKind] = {
    "archetype_figure": "living_individual",
    "unnamed_individual": "living_individual",
    "recurring_animal": "living_individual",
    "social_group": "living_group",
    "recurring_visual_pattern": "non_living",
    "recurring_pattern": "non_living",
    "symbolic_pattern": "non_living",
    "environmental_pattern": "non_living",
    "abstract_pattern": "non_living",
}


def _normalize_visual_type(value: str) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    return _WHITESPACE_RE.sub(" ", normalized)


def resolve_recurring_subject_kind(visual_type: str) -> RecurringSubjectKind:
    """Resolve only exact normalized current-authority visual_type values."""

    return _KIND_BY_VISUAL_TYPE.get(_normalize_visual_type(visual_type), "unspecified")
