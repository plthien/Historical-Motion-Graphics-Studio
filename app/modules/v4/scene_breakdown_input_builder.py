from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app.api.errors import AppError
from app.modules.v4.scene_breakdown_anchor_evidence import (
    build_anchor_direct_evidence_spans,
    build_anchor_presence_spans,
)
from app.modules.platform.services import build_script_section_ranges, validate_script_full_script_integrity
from app.modules.platform.video_pacing import preferred_hold_range
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.script_word_budget import validate_planned_narration_wpm


@dataclass(frozen=True, slots=True)
class ExtractedSection:
    section_id: str
    section_order: int
    section_title: str
    section_role: str
    resolved_script_span: tuple[int, int]
    script_excerpt: str
    pacing_density: str
    planned_narration_wpm: int


def extract_ordered_sections(
    script: dict[str, Any],
    script_analysis: dict[str, Any],
    *,
    max_section_chars: int = 40_000,
) -> list[ExtractedSection]:
    """Return Script-owned sections with Script Analysis pacing annotations.

    Section topology is never inferred or changed here. Script Analysis must
    provide exactly one guidance row for every authored Script section.
    """
    full_script = validate_script_full_script_integrity(script)
    raw_sections = script.get("script_sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise AppError(
            "SCENE_BREAKDOWN_SECTION_ORDER_INVALID",
            "The current Script does not contain authored sections.",
            422,
            retryable=False,
        )
    ranges = build_script_section_ranges(raw_sections)
    if len(ranges) != len(raw_sections):
        raise AppError(
            "SCENE_BREAKDOWN_SECTION_ORDER_INVALID",
            "Every Script section must contain narration.",
            422,
            retryable=False,
        )
    guidance_items = script_analysis.get("section_guidance")
    if not isinstance(guidance_items, list):
        raise AppError(
            "SCENE_BREAKDOWN_SCRIPT_ANALYSIS_INVALID",
            "Script Analysis section guidance is unavailable.",
            422,
            retryable=False,
        )
    guidance: dict[str, str] = {}
    for item in guidance_items:
        if not isinstance(item, dict) or not isinstance(item.get("section_id"), str):
            raise AppError(
                "SCENE_BREAKDOWN_SCRIPT_ANALYSIS_INVALID",
                "Script Analysis contains invalid section guidance.",
                422,
                retryable=False,
            )
        section_id = item["section_id"]
        if section_id in guidance:
            raise AppError(
                "SCENE_BREAKDOWN_SCRIPT_ANALYSIS_INVALID",
                "Script Analysis section guidance IDs must be unique.",
                422,
                retryable=False,
                details={"section_id": section_id},
            )
        density = item.get("pacing_density")
        if density not in {"low", "medium", "high"}:
            raise AppError(
                "SCENE_BREAKDOWN_SCRIPT_ANALYSIS_INVALID",
                "Script Analysis contains an invalid section pacing density.",
                422,
                retryable=False,
                details={"section_id": section_id, "pacing_density": density},
            )
        guidance[section_id] = str(density)

    result: list[ExtractedSection] = []
    previous_end = -1
    for index, (raw, span) in enumerate(zip(raw_sections, ranges, strict=True), start=1):
        if not isinstance(raw, dict):
            raise AppError(
                "SCENE_BREAKDOWN_SECTION_ORDER_INVALID",
                "The Script contains an invalid section.",
                422,
                retryable=False,
            )
        section_id = raw.get("section_id")
        if not isinstance(section_id, str) or section_id not in guidance:
            raise AppError(
                "SCENE_BREAKDOWN_SCRIPT_ANALYSIS_INVALID",
                "Script Analysis must contain exactly one guidance row for every Script section.",
                422,
                retryable=False,
                details={"section_id": section_id},
            )
        start, end = int(span["start"]), int(span["end"])
        if start < previous_end or end <= start or end > len(full_script):
            raise AppError(
                "SCENE_BREAKDOWN_SECTION_ORDER_INVALID",
                "Script section spans are invalid.",
                422,
                retryable=False,
                details={"section_id": section_id},
            )
        excerpt = full_script[start:end]
        if len(excerpt) > max_section_chars:
            raise AppError(
                "SCENE_BREAKDOWN_SECTION_INPUT_TOO_LARGE",
                "A Script section exceeds the supported Scene Breakdown input size.",
                422,
                retryable=False,
                details={"section_id": section_id, "character_count": len(excerpt)},
            )
        result.append(
            ExtractedSection(
                section_id=section_id,
                section_order=index,
                section_title=str(raw.get("section_title") or section_id),
                section_role=str(raw.get("section_role") or "body"),
                resolved_script_span=(start, end),
                script_excerpt=excerpt,
                pacing_density=guidance[section_id],
                planned_narration_wpm=validate_planned_narration_wpm(raw.get("planned_narration_wpm")),
            )
        )
        previous_end = end
    if set(guidance) != {item.section_id for item in result}:
        raise AppError(
            "SCENE_BREAKDOWN_SCRIPT_ANALYSIS_INVALID",
            "Script Analysis contains guidance for an unknown Script section.",
            422,
            retryable=False,
        )
    return result


def _section_markers(script_analysis: dict[str, Any], section_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for marker in script_analysis.get("narrative_markers", []):
        if not isinstance(marker, dict) or marker.get("section_id") != section_id:
            continue
        result.append(
            {
                "marker_id": marker["marker_id"],
                "marker_type": marker["marker_type"],
                "importance": marker["importance"],
                "purpose": marker["purpose"],
                "resolved_script_span": deepcopy(marker["resolved_script_span"]),
            }
        )
    return result


def _local_marker_guidance(
    *,
    script_analysis: dict[str, Any],
    section: ExtractedSection,
) -> list[dict[str, Any]]:
    section_start, section_end = section.resolved_script_span
    result: list[dict[str, Any]] = []
    for marker in _section_markers(script_analysis, section.section_id):
        span = marker["resolved_script_span"]
        if not isinstance(span, list) or len(span) != 2:
            raise AppError(
                "SCENE_BREAKDOWN_SCRIPT_ANALYSIS_INVALID",
                "Script Analysis marker spans are invalid.",
                422,
                retryable=False,
                details={"section_id": section.section_id, "marker_id": marker["marker_id"]},
            )
        absolute_start = int(span[0])
        absolute_end = int(span[1])
        if absolute_start < section_start or absolute_end > section_end or absolute_start >= absolute_end:
            raise AppError(
                "SCENE_BREAKDOWN_SCRIPT_ANALYSIS_INVALID",
                "Script Analysis marker span is outside its Script section.",
                422,
                retryable=False,
                details={"section_id": section.section_id, "marker_id": marker["marker_id"]},
            )
        start = absolute_start - section_start
        end = absolute_end - section_start
        if start >= end:
            continue
        result.append(
            {
                "marker_id": marker["marker_id"],
                "marker_type": marker["marker_type"],
                "importance": marker["importance"],
                "purpose": marker["purpose"],
                "script_text": section.script_excerpt[start:end],
            }
        )
    return result



def _joined_required(values: Any, *, field: str) -> str:
    items = [str(item).strip() for item in values] if isinstance(values, list) else []
    items = [item for item in items if item]
    if not items:
        raise AppError(
            "SCENE_BREAKDOWN_STYLE_BIBLE_INVALID",
            f"The current Style Bible does not define required visual style field {field}.",
            422,
            retryable=False,
            details={"field": field},
        )
    return "; ".join(items)


def _joined_optional(values: Any) -> str | None:
    items = [str(item).strip() for item in values] if isinstance(values, list) else []
    items = [item for item in items if item]
    return "; ".join(items) if items else None


def _visual_style_context(style_bible: dict[str, Any]) -> dict[str, Any]:
    snapshot = style_bible.get("visual_style_snapshot")
    if not isinstance(snapshot, dict):
        raise AppError(
            "SCENE_BREAKDOWN_STYLE_BIBLE_INVALID",
            "The current Style Bible does not contain a visual style snapshot.",
            422,
            retryable=False,
        )
    identity = snapshot.get("visual_identity")
    entity_styles = snapshot.get("entity_styles")
    if not isinstance(identity, dict) or not isinstance(entity_styles, dict):
        raise AppError(
            "SCENE_BREAKDOWN_STYLE_BIBLE_INVALID",
            "The current Style Bible visual style snapshot is incomplete.",
            422,
            retryable=False,
        )

    def appearance(kind: str) -> str | None:
        value = entity_styles.get(kind)
        return _joined_optional(value.get("appearance")) if isinstance(value, dict) else None

    palette = [str(item).strip() for item in identity.get("palette", []) if str(item).strip()]
    if not palette:
        raise AppError(
            "SCENE_BREAKDOWN_STYLE_BIBLE_INVALID",
            "The current Style Bible does not define an approved palette.",
            422,
            retryable=False,
        )
    style = {
        "medium": _joined_required(identity.get("medium"), field="visual_identity.medium"),
        "linework": _joined_required(identity.get("linework"), field="visual_identity.linework"),
        "rendering": _joined_required(identity.get("rendering"), field="visual_identity.rendering"),
        "palette": palette,
        "character_style": appearance("character"),
        "environment_style": appearance("location"),
        "prop_style": appearance("prop"),
        "subject_style": appearance("recurring_visual_subject"),
        "global_avoid": [
            str(item).strip()
            for item in snapshot.get("global_negative_constraints", [])
            if str(item).strip()
        ],
    }
    style["editorial_language"] = _joined_required(
        identity.get("editorial_language"),
        field="visual_identity.editorial_language",
    )
    return style


def _reference_catalog(
    *,
    style_bible: dict[str, Any],
    section: ExtractedSection,
) -> dict[str, Any]:
    direct_evidence = build_anchor_direct_evidence_spans(
        style_bible=style_bible,
        section_id=section.section_id,
        section_excerpt=section.script_excerpt,
        section_absolute_start=section.resolved_script_span[0],
    )
    presence_evidence = build_anchor_presence_spans(
        style_bible=style_bible,
        section_id=section.section_id,
        section_excerpt=section.script_excerpt,
        section_absolute_start=section.resolved_script_span[0],
    )
    specs = (
        ("characters", "characters", "char_id"),
        ("locations", "locations", "loc_id"),
        ("recurring_props", "props", "prop_id"),
        ("recurring_visual_subjects", "subjects", "subject_id"),
        ("motifs", "motifs", "motif_id"),
    )
    result: dict[str, dict[str, Any]] = {
        "characters": {},
        "locations": {},
        "props": {},
        "subjects": {},
        "motifs": {},
    }
    for style_key, output_key, id_key in specs:
        direct_by_id = direct_evidence.get(style_key, {})
        presence_by_id = presence_evidence.get(style_key, {})
        for item in style_bible.get(style_key, []) if isinstance(style_bible.get(style_key), list) else []:
            if not isinstance(item, dict) or item.get("approval_status") != "text_approved":
                continue
            anchor_id = item.get(id_key)
            if not isinstance(anchor_id, str):
                continue
            direct_spans = direct_by_id.get(anchor_id, [])
            presence_spans = presence_by_id.get(anchor_id, [])
            if not direct_spans and not presence_spans:
                continue
            evidence_texts = [
                section.script_excerpt[int(start):int(end)]
                for start, end in direct_spans
            ]
            descriptor = {
                "label": str(item.get("label") or item.get("description") or anchor_id),
                "description": str(item.get("description") or item.get("label") or anchor_id),
                "visual_traits": [str(value) for value in item.get("explicit_visual_traits", [])][:12],
                "continuity_notes": [str(value) for value in item.get("continuity_notes", [])][:10],
                "evidence_script_texts": evidence_texts[:20],
            }
            if style_key != "motifs":
                descriptor["physical_constraints"] = [
                    str(value) for value in item.get("physical_constraints", [])
                ][:8]
                descriptor["presence_ranges"] = [
                    {
                        "section_id": str(presence_range["section_id"]),
                        "start_quote": str(presence_range["start_quote"]),
                        "end_quote": str(presence_range["end_quote"]),
                    }
                    for presence_range in item["presence_ranges"]
                    if isinstance(presence_range, dict)
                    and presence_range.get("section_id") == section.section_id
                ][:40]
            result[output_key][anchor_id] = descriptor
    return result


def _previous_visual_tail(carry_over: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(carry_over, dict):
        return None
    value = carry_over.get("previous_visual_tail")
    return deepcopy(value) if isinstance(value, dict) else None

def build_section_input(
    *,
    section: ExtractedSection,
    script_analysis: dict[str, Any],
    style_bible: dict[str, Any],
    carry_over: dict[str, Any] | None = None,
    video_pacing_profile: dict[str, Any],
) -> dict[str, Any]:
    if style_bible.get("status") != "text_approved":
        raise AppError(
            "SCENE_BREAKDOWN_STYLE_BIBLE_NOT_TEXT_APPROVED",
            "The current Style Bible must be text-approved.",
            409,
            retryable=False,
        )
    markers = _local_marker_guidance(
        script_analysis=script_analysis,
        section=section,
    )
    minimum_hold, maximum_hold = preferred_hold_range(video_pacing_profile, section.pacing_density)
    payload = {
        "section_context": {
            "section_id": section.section_id,
            "section_order": section.section_order,
            "section_title": section.section_title,
            "section_role": section.section_role,
            "script_text": section.script_excerpt,
        },
        "analysis_guidance": {
            "important_narrative_markers": markers,
        },
        "visual_cadence_guidance": {
            "preferred_hold_seconds": {"min": minimum_hold, "max": maximum_hold}
        },
        "visual_context": {
            "style": _visual_style_context(style_bible),
            "reference_catalog": _reference_catalog(style_bible=style_bible, section=section),
            "previous_visual_tail": _previous_visual_tail(carry_over),
        },
    }
    return V4ContractValidator().validate(
        "scene_breakdown_section_llm_input_v2.schema.json",
        payload,
        error_code="SCENE_BREAKDOWN_SECTION_INPUT_INVALID",
    )
