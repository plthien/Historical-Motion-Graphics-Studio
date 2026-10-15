from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from app.api.errors import AppError
from app.modules.v4.scene_breakdown_validator import validate_and_normalize_section, validate_reusable_section_fragment


_STATE_FIELDS = (
    "meaning_now",
    "visible_state_now",
    "delta_now",
    "continuity_with_previous",
)
_STATE_TEXT_FIELDS = ("meaning_now", "visible_state_now", "delta_now")
_STATE_NORMALIZE_RE = re.compile(r"[^\w]+", re.UNICODE)


def _normalized_state_text(value: Any) -> str:
    return _STATE_NORMALIZE_RE.sub(" ", str(value).casefold()).strip()


def _validate_semantic_progression(raw_output: dict[str, Any]) -> None:
    """Reject definite timing-container collapse before visual authoring.

    This is intentionally conservative: lexical repetition is allowed, but two
    adjacent Beats in global Scene order may not carry the exact same normalized
    meaning/current-state/delta triplet. Such a pair proves that Step 1 created
    two containers without a new source-owned state/information/editorial
    contribution.
    """

    issues: list[dict[str, str]] = []
    previous: tuple[str, str, str] | None = None
    for scene_index, scene in enumerate(raw_output.get("scenes", [])):
        for beat_index, beat in enumerate(scene.get("beats", [])):
            current = tuple(_normalized_state_text(beat.get(field)) for field in _STATE_TEXT_FIELDS)
            if previous is not None and current == previous:
                issues.append(
                    {
                        "path": f"$.scenes[{scene_index}].beats[{beat_index}]",
                        "code": "duplicate_locked_beat_state",
                        "message": (
                            "Adjacent Beats in global Scene order have the same normalized meaning_now, "
                            "visible_state_now and delta_now. Merge them or author a real source-owned "
                            "state/information/explanatory-editorial contribution before locking Step 1."
                        ),
                    }
                )
            previous = current
    if issues:
        raise AppError(
            "SCENE_BREAKDOWN_SEMANTIC_STATE_COLLAPSE",
            "Scene & Beat Plan contains timing containers without distinct locked Beat state.",
            422,
            retryable=False,
            details={"errors": issues},
        )



def validate_semantic_progression_across_fragments(
    fragments: list[dict[str, Any]],
) -> None:
    """Apply adjacent Beat-state collapse detection in whole-plan order."""

    _validate_semantic_progression(
        {
            "scenes": [
                scene
                for fragment in fragments
                for scene in fragment.get("scenes", [])
                if isinstance(scene, dict)
            ]
        }
    )

def _placeholder_visual(beat: dict[str, Any]) -> dict[str, Any]:
    """Bridge semantic-only authoring through the proven source/timing validator.

    The placeholder is internal validator state only. It is never supplied to
    Step 2 and is removed before the canonical ScenePlan is materialized.
    """

    state = str(beat["visible_state_now"])
    return {
        "representation_family": "character_environment_lived",
        "selected_character_refs": [],
        "selected_location_refs": [],
        "selected_prop_refs": [],
        "selected_recurring_visual_subject_refs": [],
        "selected_motif_refs": [],
        "visual_concept": state,
        "subject_action": state,
        "environment_emphasis": "Semantic authoring placeholder; not persisted.",
        "visible_text": [],
        "framing": "medium_wide",
        "camera_angle": "eye_level",
        "camera_movement": "static",
        "composition": state[:240] or "Current Beat state.",
        "lighting_direction": None,
        "color_emphasis": None,
        "continuity_with_previous": beat["continuity_with_previous"],
    }


def validate_and_normalize_semantic_section(
    *,
    raw_output: dict[str, Any],
    section: Any,
    section_input: dict[str, Any],
    script_analysis: dict[str, Any],
    style_bible: dict[str, Any],
    word_timing_map: dict[str, Any],
) -> dict[str, Any]:
    """Validate Step 1 without coupling correctness to final visual authoring.

    Source partition, section order, approved Voice timing, marker/ref
    eligibility and reset legality reuse the existing validator. The returned
    fragment keeps semantic Beat state plus an internal placeholder visual so
    the existing cross-section carry validator remains reusable.
    """

    _validate_semantic_progression(raw_output)

    combined = {"scenes": []}
    for scene in raw_output["scenes"]:
        combined_scene = {"beats": []}
        for beat in scene["beats"]:
            combined_scene["beats"].append(
                {
                    "script_text": beat["script_text"],
                    "visual": _placeholder_visual(beat),
                }
            )
        combined["scenes"].append(combined_scene)

    normalized = validate_and_normalize_section(
        raw_output=combined,
        section=section,
        section_input=section_input,
        script_analysis=script_analysis,
        style_bible=style_bible,
        word_timing_map=word_timing_map,
    )

    for raw_scene, normalized_scene in zip(raw_output["scenes"], normalized["scenes"], strict=True):
        for raw_beat, normalized_beat in zip(raw_scene["beats"], normalized_scene["beats"], strict=True):
            for field in _STATE_FIELDS:
                normalized_beat[field] = deepcopy(raw_beat[field])

    return normalized



def validate_and_normalize_materialized_semantic_section(
    *,
    materialized_output: dict[str, Any],
    section: Any,
    section_input: dict[str, Any],
    script_analysis: dict[str, Any],
    style_bible: dict[str, Any],
    word_timing_map: dict[str, Any],
) -> dict[str, Any]:
    """Validate Step-1 v7 exact backend-owned source spans without re-tokenizing.

    The micro-Scene materializer owns Scene/Beat source boundaries. Reusable
    fragment validation then recomputes derived marker/ref/timing facts from
    those exact spans and applies the same visual-continuity invariants using
    internal semantic placeholders.
    """

    semantic_view = {
        "scenes": [
            {
                "beats": [
                    {
                        field: deepcopy(beat[field])
                        for field in _STATE_FIELDS
                    }
                    for beat in scene["beats"]
                ]
            }
            for scene in materialized_output["scenes"]
        ]
    }
    _validate_semantic_progression(semantic_view)

    fragment = {
        "section_id": section.section_id,
        "section_order": section.section_order,
        "section_title": section.section_title,
        "section_role": section.section_role,
        "resolved_script_span": list(section.resolved_script_span),
        "pacing_density": section.pacing_density,
        "scenes": [],
    }
    for scene in materialized_output["scenes"]:
        fragment_scene = {
            "local_scene_order": int(scene["local_scene_order"]),
            "resolved_script_span": deepcopy(scene["resolved_script_span"]),
            "start_quote": str(scene["start_quote"]),
            "end_quote": str(scene["end_quote"]),
            "beats": [],
        }
        for beat in scene["beats"]:
            fragment_scene["beats"].append(
                {
                    "local_beat_order": int(beat["local_beat_order"]),
                    "resolved_script_span": deepcopy(beat["resolved_script_span"]),
                    "script_quote": str(beat["script_quote"]),
                    "visual_context_ref": str(beat["visual_context_ref"]),
                    "visual": _placeholder_visual(beat),
                }
            )
        fragment["scenes"].append(fragment_scene)

    normalized = validate_reusable_section_fragment(
        fragment=fragment,
        section=section,
        section_input=section_input,
        script_analysis=script_analysis,
        style_bible=style_bible,
        word_timing_map=word_timing_map,
    )
    for source_scene, normalized_scene in zip(
        materialized_output["scenes"],
        normalized["scenes"],
        strict=True,
    ):
        for source_beat, normalized_beat in zip(
            source_scene["beats"],
            normalized_scene["beats"],
            strict=True,
        ):
            for field in _STATE_FIELDS:
                normalized_beat[field] = deepcopy(source_beat[field])
            normalized_beat["visual_context_ref"] = str(source_beat["visual_context_ref"])
    return normalized

def canonical_fragment_from_locked(fragment: dict[str, Any]) -> dict[str, Any]:
    """Project a locked Step-1 fragment onto the unchanged canonical ScenePlan schema."""

    result = deepcopy(fragment)
    for scene in result["scenes"]:
        for beat in scene["beats"]:
            beat.pop("visual", None)
            for field in _STATE_FIELDS:
                beat.pop(field, None)
    return result


def combined_fragment_with_visuals(
    *,
    locked_fragment: dict[str, Any],
    visuals_by_beat_id: dict[str, dict[str, Any]],
    beat_ids_in_order: list[str],
) -> dict[str, Any]:
    """Create the existing paired-plan fragment after Step 2 has passed.

    Continuity/reset is owned by Step 1, so Step 2 never supplies it.
    """

    result = canonical_fragment_from_locked(locked_fragment)
    beat_cursor = 0
    for scene in result["scenes"]:
        for beat in scene["beats"]:
            beat_id = beat_ids_in_order[beat_cursor]
            beat_cursor += 1
            source_scene = locked_fragment["scenes"][scene["local_scene_order"] - 1]
            source_beat = source_scene["beats"][beat["local_beat_order"] - 1]
            visual = deepcopy(visuals_by_beat_id[beat_id])
            visual["continuity_with_previous"] = source_beat["continuity_with_previous"]
            beat["visual"] = visual
    return result
