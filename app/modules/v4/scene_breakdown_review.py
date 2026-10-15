from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.modules.v4.visual_plan_assembler import combined_fragments_from_paired_plans


def editable_section_from_paired_plans(
    scene_plan: dict[str, Any],
    visual_plan: dict[str, Any],
    section_id: str,
) -> dict[str, Any]:
    fragments = {
        item["section_id"]: item
        for item in combined_fragments_from_paired_plans(scene_plan, visual_plan)
    }
    fragment = fragments.get(section_id)
    if not isinstance(fragment, dict):
        raise KeyError(section_id)
    return {
        "section_id": section_id,
        "scenes": [
            {
                "beats": [
                    {
                        "script_text": str(beat["script_quote"]),
                        "visual": deepcopy(beat["visual"]),
                    }
                    for beat in sorted(
                        scene["beats"],
                        key=lambda item: int(item.get("local_beat_order", 0)),
                    )
                ],
            }
            for scene in sorted(
                fragment["scenes"],
                key=lambda item: int(item.get("local_scene_order", 0)),
            )
        ],
    }



def source_ownership_issues(
    template: dict[str, Any],
    candidate: dict[str, Any],
) -> list[dict[str, str]]:
    """Reject ordinary review edits that would resegment backend-owned source."""

    issues: list[dict[str, str]] = []
    template_scenes = template.get("scenes")
    candidate_scenes = candidate.get("scenes")
    if not isinstance(template_scenes, list) or not isinstance(candidate_scenes, list):
        return issues
    if len(template_scenes) != len(candidate_scenes):
        return issues

    for scene_index, (expected_scene, candidate_scene) in enumerate(
        zip(template_scenes, candidate_scenes, strict=True)
    ):
        if not isinstance(expected_scene, dict) or not isinstance(candidate_scene, dict):
            continue
        expected_beats = expected_scene.get("beats")
        candidate_beats = candidate_scene.get("beats")
        if (
            not isinstance(expected_beats, list)
            or not isinstance(candidate_beats, list)
            or len(expected_beats) != len(candidate_beats)
        ):
            continue
        for beat_index, (expected_beat, candidate_beat) in enumerate(
            zip(expected_beats, candidate_beats, strict=True)
        ):
            if not isinstance(expected_beat, dict) or not isinstance(candidate_beat, dict):
                continue
            expected_text = expected_beat.get("script_text")
            candidate_text = candidate_beat.get("script_text")
            if candidate_text != expected_text:
                issues.append(
                    {
                        "path": f"$.scenes[{scene_index}].beats[{beat_index}].script_text",
                        "code": "edit_source_ownership_changed",
                        "message": (
                            "Beat source ownership is immutable in review/edit. "
                            "Rebuild or repartition Scene Breakdown Step 1 to change segmentation."
                        ),
                    }
                )
    return issues

def raw_output_from_editable(section: dict[str, Any]) -> dict[str, Any]:
    return {"scenes": deepcopy(section["scenes"])}


def fixed_shape_issues(template: Any, candidate: Any, path: str = "$") -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if isinstance(template, dict):
        if not isinstance(candidate, dict):
            return [{"path": path, "code": "edit_structure_changed", "message": "The JSON object structure cannot be changed."}]
        template_keys = set(template)
        candidate_keys = set(candidate)
        for key in sorted(template_keys - candidate_keys):
            issues.append({"path": f"{path}.{key}", "code": "edit_field_deleted", "message": "Fields cannot be deleted."})
        for key in sorted(candidate_keys - template_keys):
            issues.append({"path": f"{path}.{key}", "code": "edit_field_added", "message": "Fields cannot be added."})
        for key in sorted(template_keys & candidate_keys):
            issues.extend(fixed_shape_issues(template[key], candidate[key], f"{path}.{key}"))
        return issues
    if isinstance(template, list):
        if not isinstance(candidate, list):
            return [{"path": path, "code": "edit_structure_changed", "message": "The JSON array structure cannot be changed."}]
        if len(candidate) != len(template):
            issues.append({"path": path, "code": "edit_array_length_changed", "message": "Scenes and beats cannot be added or deleted."})
            return issues
        for index, (expected_item, candidate_item) in enumerate(zip(template, candidate, strict=True)):
            issues.extend(fixed_shape_issues(expected_item, candidate_item, f"{path}[{index}]"))
        return issues
    if type(candidate) is not type(template):
        issues.append({"path": path, "code": "edit_value_type_changed", "message": "Only values of the existing type may be edited."})
    return issues
