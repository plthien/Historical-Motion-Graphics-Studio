from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.api.errors import AppError
from app.modules.v4.scene_breakdown_timing import TIMING_POLICY_VERSION

CRITICAL = (
    "coverage_status",
    "semantic_partition_status",
    "order_status",
    "source_integrity_status",
    "forbidden_field_status",
    "reasoning_leakage_status",
)


def _aggregate(fragments: list[dict[str, Any]]) -> dict[str, Any]:
    result = {
        "coverage_status": "passed",
        "semantic_partition_status": "passed",
        "order_status": "passed",
        "source_integrity_status": "passed",
        "timing_status": "passed",
        "forbidden_field_status": "passed",
        "reasoning_leakage_status": "passed",
        "semantic_errors": [],
        "errors": [],
        "coverage_gaps": [],
        "coverage_overlaps": [],
        "timing_warnings": [],
        "warnings": [],
        "timing_policy_version": None,
        "timing_metrics": None,
    }
    for fragment in fragments:
        report = fragment["backend_qc"]
        for key in CRITICAL:
            if report.get(key) != "passed":
                result[key] = "failed"
        if report.get("timing_status") == "warning" and result["timing_status"] == "passed":
            result["timing_status"] = "warning"
        for key in (
            "semantic_errors",
            "errors",
            "coverage_gaps",
            "coverage_overlaps",
            "timing_warnings",
            "warnings",
        ):
            result[key].extend(deepcopy(report.get(key, [])))

    if fragments and all(
        fragment.get("backend_qc", {}).get("timing_policy_version") == TIMING_POLICY_VERSION
        for fragment in fragments
    ):
        result["timing_policy_version"] = TIMING_POLICY_VERSION
        result["timing_sections"] = [
            {
                "section_id": str(fragment["section_id"]),
                "pacing_density": str(fragment["pacing_density"]),
                "timing_status": str(fragment["backend_qc"].get("timing_status") or "passed"),
                "timing_metrics": deepcopy(fragment["backend_qc"].get("timing_metrics")),
            }
            for fragment in fragments
        ]
    return result


def assemble_scene_plan(
    *,
    project_id: str,
    revision_id: str,
    parent_revision_id: str | None,
    revision_operation: str,
    source_script_revision_id: str,
    source_script_analysis_revision_id: str,
    source_style_bible_revision_id: str,
    source_run_artifact_id: str,
    ordered_sections: list[dict[str, Any]],
    status: str = "draft_ready",
) -> dict[str, Any]:
    fragments = deepcopy(ordered_sections)
    if not fragments:
        raise AppError(
            "SCENE_BREAKDOWN_VALIDATION_FAILED",
            "No validated Script sections are available for assembly.",
            422,
            retryable=False,
        )
    scene_no = 0
    beat_no = 0
    sections: list[dict[str, Any]] = []
    scenes: list[dict[str, Any]] = []
    previous_end = -1
    total_duration = 0.0
    for expected_order, fragment in enumerate(fragments, start=1):
        if (
            fragment.get("section_order") != expected_order
            or int(fragment["resolved_script_span"][0]) < previous_end
        ):
            raise AppError(
                "SCENE_BREAKDOWN_SECTION_ORDER_INVALID",
                "Validated section fragments are not in Script order.",
                422,
                retryable=False,
            )
        scene_ids: list[str] = []
        beat_ids: list[str] = []
        for local_scene_order, raw_scene in enumerate(fragment["scenes"], start=1):
            if raw_scene.get("local_scene_order") != local_scene_order:
                raise AppError(
                    "SCENE_BREAKDOWN_SECTION_ORDER_INVALID",
                    "Scene order is invalid.",
                    422,
                    retryable=False,
                )
            scene_no += 1
            scene_id = f"scene_{scene_no:03d}"
            scene_ids.append(scene_id)
            normalized_beats: list[dict[str, Any]] = []
            for local_beat_order, raw_beat in enumerate(raw_scene["beats"], start=1):
                if raw_beat.get("local_beat_order") != local_beat_order:
                    raise AppError(
                        "SCENE_BREAKDOWN_SECTION_ORDER_INVALID",
                        "Beat order is invalid.",
                        422,
                        retryable=False,
                    )
                beat_no += 1
                beat_id = f"beat_{beat_no:03d}"
                beat_ids.append(beat_id)
                total_duration += float(raw_beat["estimated_duration_seconds"])
                normalized_beats.append(
                    {
                        "beat_id": beat_id,
                        "beat_order": beat_no,
                        **{
                            key: deepcopy(value)
                            for key, value in raw_beat.items()
                            if key != "visual"
                        },
                    }
                )
            scenes.append(
                {
                    "scene_id": scene_id,
                    "scene_order": scene_no,
                    "section_id": fragment["section_id"],
                    **{
                        key: deepcopy(value)
                        for key, value in raw_scene.items()
                        if key != "beats"
                    },
                    "beats": normalized_beats,
                }
            )
        sections.append(
            {
                "section_id": fragment["section_id"],
                "section_order": fragment["section_order"],
                "section_title": fragment["section_title"],
                "section_role": fragment["section_role"],
                "resolved_script_span": deepcopy(fragment["resolved_script_span"]),
                "pacing_density": fragment["pacing_density"],
                "scene_ids": scene_ids,
                "beat_ids": beat_ids,
            }
        )
        previous_end = int(fragment["resolved_script_span"][1])
    backend_qc = _aggregate(fragments)
    if any(backend_qc[key] != "passed" for key in CRITICAL):
        raise AppError(
            "SCENE_BREAKDOWN_VALIDATION_FAILED",
            "Critical Scene Breakdown QA failed.",
            422,
            retryable=False,
        )
    return {
        "artifact_type": "scene_plan",
        "schema_version": "2.0",
        "project_id": project_id,
        "scene_plan_revision_id": revision_id,
        "parent_scene_plan_revision_id": parent_revision_id,
        "revision_operation": revision_operation,
        "source_script_revision_id": source_script_revision_id,
        "source_script_analysis_revision_id": source_script_analysis_revision_id,
        "source_style_bible_revision_id": source_style_bible_revision_id,
        "source_run_artifact_id": source_run_artifact_id,
        "status": status,
        "scene_count": scene_no,
        "beat_count": beat_no,
        "total_expected_duration_seconds": round(total_duration, 1),
        "sections": sections,
        "scenes": scenes,
        "backend_qc": backend_qc,
    }


def fragments_from_scene_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    scenes_by_section: dict[str, list[dict[str, Any]]] = {}
    for scene in plan["scenes"]:
        local = {
            "local_scene_order": scene["local_scene_order"],
            "beats": [
                {
                    "local_beat_order": beat["local_beat_order"],
                    "script_quote": beat["script_quote"],
                    "resolved_script_span": deepcopy(beat["resolved_script_span"]),
                    "visual_context_ref": beat["visual_context_ref"],
                }
                for beat in scene["beats"]
            ],
        }
        scenes_by_section.setdefault(scene["section_id"], []).append(local)
    return [
        {
            "section_id": section["section_id"],
            "section_order": section["section_order"],
            "section_title": section["section_title"],
            "section_role": section["section_role"],
            "resolved_script_span": deepcopy(section["resolved_script_span"]),
            "pacing_density": section["pacing_density"],
            "scenes": scenes_by_section.get(section["section_id"], []),
            "backend_qc": deepcopy(plan["backend_qc"]),
        }
        for section in plan["sections"]
    ]


def approve_scene_plan(current_plan: dict[str, Any], new_revision_id: str) -> dict[str, Any]:
    result = deepcopy(current_plan)
    result["parent_scene_plan_revision_id"] = current_plan["scene_plan_revision_id"]
    result["scene_plan_revision_id"] = new_revision_id
    result["revision_operation"] = "approval"
    result["status"] = "approved"
    return result

