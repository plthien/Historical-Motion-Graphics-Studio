from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.api.errors import AppError


_EDITABLE_COLLECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("characters", "char_id", ("label", "description", "explicit_visual_traits", "identity_markers", "continuity_notes", "physical_constraints", "entity_kind", "narrative_role")),
    ("locations", "loc_id", ("label", "description", "explicit_visual_traits", "identity_markers", "continuity_notes", "physical_constraints", "location_kind")),
    ("recurring_props", "prop_id", ("label", "description", "explicit_visual_traits", "identity_markers", "continuity_notes", "physical_constraints", "prop_kind")),
    ("recurring_visual_subjects", "subject_id", ("label", "description", "visual_type", "explicit_visual_traits", "continuity_notes", "physical_constraints")),
    ("motifs", "motif_id", ("label", "description")),
)


def validate_editable_identity(current: dict[str, Any], editable: dict[str, Any]) -> None:
    for collection, id_field, _ in _EDITABLE_COLLECTIONS:
        expected = [str(item[id_field]) for item in current[collection]]
        received = [str(item[id_field]) for item in editable[collection]]
        if expected != received or len(received) != len(set(received)):
            raise AppError(
                "STYLE_BIBLE_EDIT_IDENTITY_INVALID",
                "Style Bible editable identities must match the current revision exactly.",
                422,
                retryable=False,
                details={"collection": collection, "expected_ids": expected, "received_ids": received},
            )


def apply_text_update(current: dict[str, Any], editable: dict[str, Any], revision_id: str) -> dict[str, Any]:
    validate_editable_identity(current, editable)
    revised = deepcopy(current)
    revised["style_bible_revision_id"] = revision_id
    revised["parent_style_bible_revision_id"] = current["style_bible_revision_id"]
    revised["revision_operation"] = "text_update"
    revised["status"] = "text_approved"
    for collection, _, fields in _EDITABLE_COLLECTIONS:
        for target, source in zip(revised[collection], editable[collection], strict=True):
            for field in fields:
                target[field] = deepcopy(source[field])
            if collection == "recurring_props":
                target["significance"] = target["description"]
            target["approval_status"] = "text_approved"
    return revised


def apply_text_approval(current: dict[str, Any], revision_id: str) -> dict[str, Any]:
    revised = deepcopy(current)
    revised["style_bible_revision_id"] = revision_id
    revised["parent_style_bible_revision_id"] = current["style_bible_revision_id"]
    revised["revision_operation"] = "text_approval"
    revised["status"] = "text_approved"
    for collection, _, _ in _EDITABLE_COLLECTIONS:
        for item in revised[collection]:
            item["approval_status"] = "text_approved"
    return revised
