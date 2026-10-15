from __future__ import annotations

from typing import Any, Iterable

from app.api.errors import AppError
from app.api.schemas import VisualStyleSnapshotModel

VISUAL_IDENTITY_FIELD_ORDER = (
    "medium",
    "linework",
    "rendering",
    "palette",
    "shape_language",
    "texture",
    "editorial_language",
)
ENTITY_TYPES = ("character", "location", "prop", "recurring_visual_subject")


def validate_visual_style_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    try:
        return VisualStyleSnapshotModel.model_validate(snapshot).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            "VISUAL_STYLE_SNAPSHOT_INVALID",
            "The inherited visual style snapshot is invalid.",
            409,
            retryable=False,
        ) from exc


def _format_group(label: str, values: list[str]) -> str:
    return f"{label}: {'; '.join(values)}." if values else ""


def compile_visual_identity_module(snapshot: dict[str, Any]) -> str:
    validated = validate_visual_style_snapshot(snapshot)
    identity = validated["visual_identity"]
    labels = {
        "medium": "Medium",
        "linework": "Linework",
        "rendering": "Rendering",
        "palette": "Palette",
        "shape_language": "Shape language",
        "texture": "Texture",
        "editorial_language": "Editorial visual language",
    }
    return " ".join(
        fragment
        for field in VISUAL_IDENTITY_FIELD_ORDER
        if (fragment := _format_group(labels[field], identity[field]))
    )


def compile_entity_style_module(snapshot: dict[str, Any], entity_type: str) -> str:
    validated = validate_visual_style_snapshot(snapshot)
    if entity_type not in ENTITY_TYPES:
        raise AppError(
            "VISUAL_STYLE_ENTITY_TYPE_INVALID",
            "The requested visual entity style is invalid.",
            422,
            retryable=False,
            details={"entity_type": entity_type},
        )
    return _format_group("Entity appearance", validated["entity_styles"][entity_type]["appearance"])


def compile_global_negative_constraints(snapshot: dict[str, Any]) -> list[str]:
    validated = validate_visual_style_snapshot(snapshot)
    return list(validated["global_negative_constraints"])


def entity_negative_constraints(snapshot: dict[str, Any], entity_type: str) -> list[str]:
    validated = validate_visual_style_snapshot(snapshot)
    if entity_type not in ENTITY_TYPES:
        raise AppError(
            "VISUAL_STYLE_ENTITY_TYPE_INVALID",
            "The requested visual entity style is invalid.",
            422,
            retryable=False,
            details={"entity_type": entity_type},
        )
    return list(validated["entity_styles"][entity_type]["avoid"])


def merge_negative_constraints(*groups: Iterable[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            if not isinstance(value, str) or not value.strip():
                raise AppError(
                    "VISUAL_STYLE_NEGATIVE_CONSTRAINT_INVALID",
                    "Negative constraints must be non-blank strings.",
                    409,
                    retryable=False,
                )
            if value not in seen:
                seen.add(value)
                merged.append(value)
    return merged
