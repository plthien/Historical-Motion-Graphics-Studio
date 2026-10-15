from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from fastapi.testclient import TestClient
import pytest

from app.api.errors import AppError
from app.core.config import get_settings
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactRole, ArtifactStatus, ArtifactType
from app.db.models import Artifact, Channel, Project
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_candidate import SceneAssetCandidate
from app.db.scene_asset_prompt_revision import SceneAssetPromptRevision
from app.db.scene_asset_working_operation import SceneAssetWorkingOperation
from app.db.session import build_session_factory
from app.main import create_app
from app.modules.platform.services import build_full_script_from_sections, build_script_section_ranges
from app.modules.platform.video_pacing import resolve_video_pacing_profile
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.prompt_composer_service import PromptComposerService
from app.modules.v4.scene_breakdown_service import SceneBreakdownServiceV4
from app.modules.v4.scene_breakdown_two_step_external_service import ExternalSceneBreakdownTwoStepService
from app.modules.v4.scene_asset_runtime_v2 import SceneAssetRuntimeV2
from app.modules.v4.scene_asset_workspace_v2_runtime import SceneAssetWorkspaceV2Service
import app.modules.v4.scene_breakdown_two_step_external_service as two_step_module
from app.modules.v4.scene_breakdown_partition_planner import measure_scene_authoring_input
from app.modules.v4.script_analysis_input_builder import build_script_analysis_input
from app.modules.v4.script_analysis_validator import ScriptAnalysisSemanticValidator
from app.modules.v4.script_word_budget import (
    build_computed_constraints,
    build_script_section_word_counts,
    build_runtime_diagnostics,
    count_narration_words,
)
from app.modules.v4.style_bible_service import StyleBibleServiceV4
from app.modules.v4.visual_plan_assembler import combined_fragments_from_paired_plans
from app.repositories import ArtifactRepository
from app.services.artifacts import ArtifactService
from tests.support.fixtures import sample_channel_dna, sample_pacing_profiles
from tests.v4.combined_scene_breakdown_support import _seed_approved_voice_pair

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "v4" / "scene_breakdown" / "shanidar_full_scene_breakdown.json"
_SENSITIVE_TOKEN = re.compile(r"[$€£¥]?[0-9]+%?|[$€£¥%]")


def _unique_context(narration: str, start: int, end: int) -> str:
    for radius in range(12, len(narration) + 1):
        left = max(0, start - radius)
        right = min(len(narration), end + radius)
        context = narration[left:right]
        if context.count(narration[start:end]) == 1 and narration.count(context) == 1:
            return context
    return narration


def _fixture_spoken_overrides(narration: str, *, section_id: str, global_start: int) -> list[dict[str, str | int]]:
    return [
        {
            "id": f"sov_{section_id}_{index:03d}",
            "anchor_quote": match.group(0),
            "context_quote": _unique_context(narration, match.start(), match.end()),
            "spoken_text": match.group(0),
            "source_start": global_start + match.start(),
            "source_end": global_start + match.end(),
        }
        for index, match in enumerate(_SENSITIVE_TOKEN.finditer(narration), start=1)
    ]


def _load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _beat_rows(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (section["section_id"], beat)
        for section in payload["sections"]
        for scene in section["scenes"]
        for beat in scene["beats"]
    ]


def _occurrences_for_ref(payload: dict[str, Any], ref_id: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for section_id, beat in _beat_rows(payload):
        visual = beat["visual"]
        selected = set(visual["selected_character_refs"])
        selected.update(visual["selected_prop_refs"])
        selected.update(visual["selected_recurring_visual_subject_refs"])
        selected.update(visual["selected_motif_refs"])
        selected.update(visual["selected_location_refs"])
        if ref_id in selected:
            quote = str(beat["script_text"]).strip()
            result.append({"section_id": section_id, "quote": quote, "context_quote": quote})
    # The raw Script Analysis contract requires recurring catalog entries to have
    # at least two occurrences. loc_001 is selected once in this real payload, so
    # use another exact quote from the same section as supporting location evidence.
    if len(result) == 1:
        for section_id, beat in _beat_rows(payload):
            quote = str(beat["script_text"]).strip()
            if section_id == result[0]["section_id"] and quote != result[0]["quote"]:
                result.append({"section_id": section_id, "quote": quote, "context_quote": quote})
                break
    assert len(result) >= 2, ref_id
    return result


def _presence_ranges_for_ref(payload: dict[str, Any], ref_id: str) -> list[dict[str, str]]:
    occurrences = _occurrences_for_ref(payload, ref_id)
    beat_order: dict[tuple[str, str], int] = {}
    for order, (section_id, beat) in enumerate(_beat_rows(payload)):
        beat_order.setdefault((section_id, str(beat["script_text"]).strip()), order)
    ordered = sorted(occurrences, key=lambda item: beat_order[(item["section_id"], item["quote"])])
    grouped: dict[str, list[dict[str, str]]] = {}
    section_order: list[str] = []
    for occurrence in ordered:
        section_id = occurrence["section_id"]
        if section_id not in grouped:
            grouped[section_id] = []
            section_order.append(section_id)
        grouped[section_id].append(occurrence)
    return [
        {
            "section_id": section_id,
            "start_quote": grouped[section_id][0]["quote"],
            "end_quote": grouped[section_id][-1]["quote"],
        }
        for section_id in section_order
    ]


def _raw_analysis(project_id: str, script_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    common = {
        "description": "Stable visual continuity definition for the imported Scene Breakdown fixture.",
        "explicit_visual_traits": ["consistent simple visual identity"],
        "continuity_notes": ["preserve the same identity across scenes"],
        "physical_constraints": [],
    }
    return {
        "artifact_type": "script_analysis_llm_output",
        "schema_version": "1.0",
        "project_id": project_id,
        "source_script_revision_id": script_id,
        "section_guidance": [
            {
                "section_id": section["section_id"],
                "pacing_density": "high" if index == 0 else "medium",
            }
            for index, section in enumerate(payload["sections"])
        ],
        "narrative_markers": [],
        "continuity_catalog": {
            "characters": [
                {
                    "label": "Shanidar 1",
                    **common,
                    "identity_markers": ["older Neanderthal with a damaged right arm"],
                    "entity_kind": "human_character",
                    "narrative_role": "primary case subject",
                    "occurrences": _occurrences_for_ref(payload, "char_001"),
                    "presence_ranges": _presence_ranges_for_ref(payload, "char_001"),
                }
            ],
            "locations": [
                {
                    "label": "Shanidar Cave",
                    **common,
                    "identity_markers": ["rocky cave in Iraqi Kurdistan"],
                    "location_kind": "archaeological cave site",
                    "occurrences": _occurrences_for_ref(payload, "loc_001"),
                    "presence_ranges": _presence_ranges_for_ref(payload, "loc_001"),
                }
            ],
            "recurring_props": [
                {
                    "label": "Healed bone evidence",
                    **common,
                    "identity_markers": ["remodeled prehistoric bone"],
                    "prop_kind": "archaeological evidence",
                    "occurrences": _occurrences_for_ref(payload, "prop_001"),
                    "presence_ranges": _presence_ranges_for_ref(payload, "prop_001"),
                }
            ],
            "recurring_visual_subjects": [
                {
                    "label": "Distributed group support",
                    "description": common["description"],
                    "visual_type": "recurring social support tableau",
                    "explicit_visual_traits": common["explicit_visual_traits"],
                    "continuity_notes": common["continuity_notes"],
                    "physical_constraints": [],
                    "occurrences": _occurrences_for_ref(payload, "subject_001"),
                    "presence_ranges": _presence_ranges_for_ref(payload, "subject_001"),
                }
            ],
            "visual_motifs": [
                {
                    "label": "Gap between bone and behavior",
                    "description": "A repeated visual separation between physical evidence and inferred social behavior.",
                    "occurrences": _occurrences_for_ref(payload, "motif_001"),
                },
                {
                    "label": "Redistributed survival",
                    "description": "A repeated visual pattern of survival tasks shared across multiple bodies.",
                    "occurrences": _occurrences_for_ref(payload, "motif_002"),
                },
            ],
        },
        "visual_context_catalog": [
            {
                "label": "Complete Shanidar narrative",
                "mode": "story_world",
                "timeframe": "prehistoric archaeological reconstruction",
                "setting": "Shanidar Cave and evidence views",
                "constraints": [],
                "presence_ranges": [
                    {
                        "section_id": section["section_id"],
                        "start_quote": narration,
                        "end_quote": narration,
                    }
                    for section in payload["sections"]
                    for narration in [
                        "".join(
                            str(beat["script_text"])
                            for scene in section["scenes"]
                            for beat in scene["beats"]
                        ).strip()
                    ]
                ],
            }
        ],
    }


def _seed_from_fixture() -> tuple[str, dict[str, Any]]:
    payload = _load_fixture()
    sections: list[dict[str, Any]] = []
    roles = ["hook", "context", "evidence", "development", "evidence", "development", "evidence", "analysis", "turning_point", "resolution"]
    for index, (section, role) in enumerate(zip(payload["sections"], roles, strict=True), start=1):
        narration = "".join(
            str(beat["script_text"])
            for scene in section["scenes"]
            for beat in scene["beats"]
        ).strip()
        sections.append(
            {
                "section_id": section["section_id"],
                "section_title": f"Shanidar evidence phase {index}",
                "section_role": role,
                "narration": narration,
                "spoken_overrides": [],
                "delivery_cues": [],
            }
        )

    full_script = build_full_script_from_sections(sections)
    duration = 15
    actual = count_narration_words(full_script)
    section_wpm = max(80, min(240, round(actual / duration)))
    for section in sections:
        section["planned_narration_wpm"] = section_wpm
    for item in build_script_section_ranges(sections):
        section = next(section for section in sections if section["section_id"] == item["section_id"])
        section["spoken_overrides"] = _fixture_spoken_overrides(
            section["narration"],
            section_id=section["section_id"],
            global_start=int(item["start"]),
        )
    diagnostics = build_runtime_diagnostics(target_duration_minutes=duration, script_sections=sections)
    script = {
        "artifact_type": "script",
        "schema_version": "1.0",
        "channel_context": {"name": "Shanidar Test", "language": "English"},
        "source_topic_ideas_artifact_id": "artifact_aaaaaaaaaaaa",
        "source_topic_idea": {
            "idea_id": "idea_001",
            "title": "What Shanidar 1 Reveals About Prehistoric Care",
            "core_question": "What assistance was necessary for injured prehistoric people to survive?",
            "viewer_hook": "The injuries should have ended the story, but the skeleton says otherwise.",
            "topic_angle": "forensic evidence and cautious social inference",
            "tone": "evidence-led",
            "pov_style": "investigative documentary",
            "avoid": ["unsupported certainty"],
            "content_tags": ["archaeology", "Neanderthals"],
            "risk_notes": [],
            "recommended_pacing_mode": "medium",
            "pacing_mode_reason": "Balanced evidence progression with deliberate interpretive holds.",
        },
        "source_user_input": {
            "target_duration_minutes": duration,
            "pov_style": "investigative documentary",
            "avoid": ["unsupported certainty"],
            "section_count_mode": "llm_decides",
            "pacing_mode": "medium",
            "narration_wpm": {"min": 80, "max": 240},
        },
        "video_pacing_profile": resolve_video_pacing_profile(sample_pacing_profiles(), "medium"),
        "planned_average_wpm": diagnostics.planned_average_wpm,
        "computed_constraints": build_computed_constraints(
            duration,
            diagnostics.planned_average_wpm,
            section_count_mode="llm_decides",
            section_roles=roles,
            planned_narration_wpm_range={"min": 80, "max": 240},
        ),
        "title": "What Shanidar 1 Reveals About Prehistoric Care",
        "hook": "The injuries should have ended the story, but the skeleton says otherwise.",
        "script_sections": sections,
        "full_script": full_script,
        "section_word_counts": build_script_section_word_counts(sections),
        "word_count": actual,
        "runtime_diagnostics": diagnostics.to_dict(),
    }


    factory = build_session_factory(get_settings())
    with factory() as session:
        now = utc_now_iso()
        channel = Channel(
            channel_id=generate_id("channel"),
            name="Shanidar fixture channel",
            language="English",
            niche="archaeology",
            audience="curious viewers",
            channel_dna_json=sample_channel_dna(),
            channel_context_json={"name": "Shanidar Test", "language": "English", "niche": "archaeology", "audience": "curious viewers"},
            created_at=now,
            updated_at=now,
        )
        session.add(channel)
        project = Project(
            project_id=generate_id("project"),
            channel_id=channel.channel_id,
            name="Shanidar real JSON import",
            description=None,
            channel_context_json=deepcopy(channel.channel_context_json),
            channel_dna_json=deepcopy(channel.channel_dna_json),
            created_at=now,
            updated_at=now,
        )
        session.add(project)
        session.flush()
        artifacts = ArtifactService(session, ArtifactRepository(session), get_settings())
        staged_script = artifacts.write_artifact(
            logical_id=f"project/{project.project_id}/script",
            artifact_type=ArtifactType.JSON,
            artifact_role=ArtifactRole.SCRIPT,
            filename="script.json",
            content=json.dumps(script, ensure_ascii=False, indent=2).encode("utf-8"),
            provider_source="test",
        )
        script_id = staged_script.artifact.artifact_id
        project.script_artifact_id = script_id
        llm_input = build_script_analysis_input(
            project_id=project.project_id,
            script_revision_id=script_id,
            script_artifact=script,
        )
        canonical_analysis = ScriptAnalysisSemanticValidator().build_canonical(
            llm_input=llm_input,
            raw_output=_raw_analysis(project.project_id, script_id, payload),
            section_ranges=build_script_section_ranges(script["script_sections"]),
        )
        staged_analysis = artifacts.write_artifact(
            logical_id=f"project/{project.project_id}/script_analysis",
            artifact_type=ArtifactType.JSON,
            artifact_role=None,
            filename="script_analysis.json",
            content=json.dumps(canonical_analysis, ensure_ascii=False, indent=2).encode("utf-8"),
            provider_source="test",
        )
        analysis_id = staged_analysis.artifact.artifact_id
        project.script_analysis_artifact_id = analysis_id
        session.commit()
        style, style_id = StyleBibleServiceV4(session).generate(project.project_id, analysis_id)
        if style["status"] != "text_approved":
            _style, style_id = StyleBibleServiceV4(session).approve_text(project.project_id, style_id)

        current_for_voice = session.get(Project, project.project_id)
        assert current_for_voice is not None
        _seed_approved_voice_pair(session, current_for_voice, script_id, script)

        return project.project_id, payload


def _scene_authoring_plan(project_id: str) -> dict[str, Any]:
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ExternalSceneBreakdownTwoStepService(session)
        _project, _script, _analysis, _style, word_timing, full_input, sections = service._context(project_id)
        return service._build_scene_authoring_plan(
            full_input,
            sections,
            word_timing,
        )


def _scene_prompt_input_for_metrics(
    project_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ExternalSceneBreakdownTwoStepService(session)
        (
            _project,
            _script,
            _analysis,
            _style,
            word_timing,
            full_input,
            sections,
        ) = service._context(project_id)
        authoring_plan = service._build_scene_authoring_plan(
            full_input,
            sections,
            word_timing,
        )
        prompt_input = service._scene_prompt_input(
            full_input,
            sections,
            authoring_plan,
        )
        return authoring_plan, prompt_input


def _semantic_output_from_authoring_plan(
    authoring_plan: dict[str, Any],
    artifact_id: str,
) -> dict[str, Any]:
    sections: list[dict[str, Any]] = []
    global_beat_order = 0
    for planned_section in authoring_plan["sections"]:
        scenes: list[dict[str, Any]] = []
        for planned_scene in planned_section["scenes"]:
            boundary_ids = []
            beat_count = 1
            beats: list[dict[str, Any]] = []
            for local_index in range(1, beat_count + 1):
                global_beat_order += 1
                beats.append(
                    {
                        "meaning_now": f"Fixture meaning state {global_beat_order}",
                        "visible_state_now": f"Fixture visible state {global_beat_order}",
                        "delta_now": f"Fixture source-owned delta {global_beat_order}",
                        "continuity_with_previous": "new_setup",
                    }
                )
            scenes.append(
                {
                    "scene_key": planned_scene["scene_key"],
                    "partition": {"boundary_ids": boundary_ids},
                    "beats": beats,
                }
            )
        sections.append({"section_id": planned_section["section_id"], "scenes": scenes})
    return {"artifact_id": artifact_id, "sections": sections}


def _visual_output_from_scene_plan(
    scene_plan: dict[str, Any],
    artifact_id: str,
    *,
    previous_required_by_beat: dict[str, bool] | None = None,
) -> dict[str, Any]:
    authored_dependencies = previous_required_by_beat or {}
    scenes: list[dict[str, Any]] = []
    for scene in scene_plan["scenes"]:
        beat_plans: list[dict[str, Any]] = []
        for beat in scene["beats"]:
            beat_id = str(beat["beat_id"])
            beat_plans.append(
                {
                    "beat_id": beat_id,
                    "visual_context_ref": beat["visual_context_ref"],
                    "representation_family": "character_environment_lived",
                    "selected_character_refs": [],
                    "selected_location_refs": [],
                    "selected_prop_refs": [],
                    "selected_recurring_visual_subject_refs": [],
                    "selected_motif_refs": [],
                    "visual_concept": f"Concrete fixture visual concept for {beat_id}.",
                    "subject_action": f"Fixture subject action for {beat_id}.",
                    "environment_emphasis": f"Fixture environment state for {beat_id}.",
                    "visible_text": [],
                    "framing": "medium_wide",
                    "camera_angle": "eye_level",
                    "camera_movement": "static",
                    "composition": f"Distinct fixture composition for {beat_id}.",
                    "lighting_direction": None,
                    "color_emphasis": None,
                    # The default fixture authors every Step-1 Beat as new_setup.
                    "previous_beat_image_reference_required": authored_dependencies.get(beat_id, False),
                }
            )
        scenes.append({"scene_id": scene["scene_id"], "beat_plans": beat_plans})
    return {"artifact_id": artifact_id, "scenes": scenes}


def _import_two_step_pair(
    service: ExternalSceneBreakdownTwoStepService,
    project_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    scene_package = service.build_scene_prompt(project_id)
    scene_output = _semantic_output_from_authoring_plan(
        _scene_authoring_plan(project_id),
        scene_package["artifact_ids"]["scene_plan"],
    )
    step_one = service.import_scene_result(
        project_id,
        {"result_json": json.dumps(scene_output, ensure_ascii=False)},
    )
    visual_package = step_one["visual_prompt_package"]
    visual_output = _visual_output_from_scene_plan(
        step_one["scene_plan"],
        visual_package["artifact_ids"]["visual_plan"],
    )
    return step_one, {
        "result_json": json.dumps(visual_output, ensure_ascii=False),
    }


def test_real_fixture_step1_input_uses_source_once_and_exposes_complete_legal_grid() -> None:
    project_id, _payload = _seed_from_fixture()
    authoring_plan, prompt_input = _scene_prompt_input_for_metrics(project_id)
    metrics = measure_scene_authoring_input(prompt_input)

    factory = build_session_factory(get_settings())
    with factory() as session:
        package = ExternalSceneBreakdownTwoStepService(session).build_scene_prompt(
            project_id
        )
    assert package["prompt_version"] == "scene_breakdown_scene_external_v9"

    for section_context in prompt_input["sections"]:
        internal_section = next(
            item
            for item in authoring_plan["sections"]
            if item["section_id"] == section_context["section_id"]
        )
        assert section_context["preferred_hold_seconds"] == internal_section[
            "preferred_hold_seconds"
        ]

    internal_source_chars = sum(
        len(str(scene["script_text"]))
        for section in authoring_plan["sections"]
        for scene in section["scenes"]
    )
    full_technical_boundary_count = sum(
        len(scene["legal_boundaries"])
        for section in authoring_plan["sections"]
        for scene in section["scenes"]
    )
    internal_scene_count = sum(
        len(section["scenes"])
        for section in authoring_plan["sections"]
    )
    assert metrics["source_narration_characters"] == internal_source_chars
    assert metrics["legal_boundary_count"] == full_technical_boundary_count
    assert metrics["scene_count"] == internal_scene_count

    for section in prompt_input["scene_authoring_plan"]["sections"]:
        internal_section = next(
            item
            for item in authoring_plan["sections"]
            if item["section_id"] == section["section_id"]
        )
        for scene, internal_scene in zip(
            section["scenes"],
            internal_section["scenes"],
            strict=True,
        ):
            segment_boundaries = [
                item["boundary_id_after"]
                for item in scene["source_segments"]
                if item["boundary_id_after"] is not None
            ]
            technical_boundaries = {
                item["boundary_id"]
                for item in internal_scene["legal_boundaries"]
            }
            assert set(segment_boundaries).issubset(technical_boundaries)
            assert len(segment_boundaries) == len(set(segment_boundaries))
            assert "".join(
                item["text"] for item in scene["source_segments"]
            ) == internal_scene["script_text"]
            assert set(segment_boundaries) == technical_boundaries

    compact_serialized = json.dumps(
        prompt_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert len(compact_serialized) == metrics["serialized_input_characters"]

    semantic_output = _semantic_output_from_authoring_plan(
        authoring_plan,
        "artifact_000000000000",
    )
    semantic_serialized = json.dumps(
        semantic_output,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    semantic_beat_count = sum(
        len(scene["beats"])
        for section in semantic_output["sections"]
        for scene in section["scenes"]
    )
    semantic_prose_characters = sum(
        sum(
            len(str(beat[field]))
            for beat in scene["beats"]
            for field in (
                "meaning_now",
                "visible_state_now",
                "delta_now",
            )
        )
        for section in semantic_output["sections"]
        for scene in section["scenes"]
    )
    scene_count = sum(
        len(section["scenes"])
        for section in semantic_output["sections"]
    )
    old_prose_field_count = scene_count + semantic_beat_count * 4
    new_prose_field_count = semantic_beat_count * 3
    prose_field_reduction_pct = round(
        100.0 * (old_prose_field_count - new_prose_field_count) / old_prose_field_count,
        2,
    )
    evidence = {
        **metrics,
        "full_technical_boundary_count": full_technical_boundary_count,
        "semantic_beat_count": semantic_beat_count,
        "semantic_output_serialized_characters": len(semantic_serialized),
        "semantic_output_serialized_bytes": len(
            semantic_serialized.encode("utf-8")
        ),
        "semantic_prose_characters": semantic_prose_characters,
        "old_prose_field_count": old_prose_field_count,
        "new_prose_field_count": new_prose_field_count,
        "prose_field_reduction_percent": prose_field_reduction_pct,
        "maximum_legal_boundaries_per_scene": max(
            len(
                [
                    item
                    for item in scene["source_segments"]
                    if item["boundary_id_after"] is not None
                ]
            )
            for section in prompt_input["scene_authoring_plan"]["sections"]
            for scene in section["scenes"]
        ),
        "average_legal_boundaries_per_scene": round(
            metrics["legal_boundary_count"] / metrics["scene_count"], 2
        ),
    }
    print(
        "STEP1_SHANIDAR_METRICS "
        + json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    )

    assert semantic_beat_count > 0
    assert semantic_prose_characters > 0


def test_real_uploaded_scene_breakdown_validates_and_imports_over_http_with_cors() -> None:
    project_id, payload = _seed_from_fixture()
    headers = {"Origin": "http://localhost:5173"}

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        scene_package_response = client.post(
            f"/api/v4/projects/{project_id}/scene-breakdown/scene/prompt",
            headers=headers,
        )
        assert scene_package_response.status_code == 200, scene_package_response.text
        scene_package = scene_package_response.json()
        authoring_plan = _scene_authoring_plan(project_id)
        scene_output = _semantic_output_from_authoring_plan(
            authoring_plan,
            scene_package["artifact_ids"]["scene_plan"],
        )
        scene_request = {"result_json": json.dumps(scene_output, ensure_ascii=False)}

        validated_scene = client.post(
            f"/api/v4/projects/{project_id}/scene-breakdown/scene/import/validate",
            json=scene_request,
            headers=headers,
        )
        assert validated_scene.status_code == 200, validated_scene.text
        assert validated_scene.headers.get("access-control-allow-origin") == "http://localhost:5173"
        assert validated_scene.json()["valid"] is True, validated_scene.json()

        imported_scene = client.post(
            f"/api/v4/projects/{project_id}/scene-breakdown/scene/import",
            json=scene_request,
            headers=headers,
        )
        assert imported_scene.status_code == 200, imported_scene.text
        assert imported_scene.headers.get("access-control-allow-origin") == "http://localhost:5173"
        scene_body = imported_scene.json()

        visual_package = scene_body["visual_prompt_package"]
        visual_output = _visual_output_from_scene_plan(
            scene_body["scene_plan"],
            visual_package["artifact_ids"]["visual_plan"],
        )
        visual_request = {"result_json": json.dumps(visual_output, ensure_ascii=False)}

        validated_visual = client.post(
            f"/api/v4/projects/{project_id}/scene-breakdown/visual/import/validate",
            json=visual_request,
            headers=headers,
        )
        assert validated_visual.status_code == 200, validated_visual.text
        assert validated_visual.json()["valid"] is True, validated_visual.json()

        imported_visual = client.post(
            f"/api/v4/projects/{project_id}/scene-breakdown/visual/import",
            json=visual_request,
            headers=headers,
        )
        assert imported_visual.status_code == 200, imported_visual.text
        assert imported_visual.headers.get("access-control-allow-origin") == "http://localhost:5173"
        body = imported_visual.json()
        assert body["scene_plan_artifact_id"] == scene_package["artifact_ids"]["scene_plan"]
        assert body["visual_plan_artifact_id"] == visual_package["artifact_ids"]["visual_plan"]
        assert all(
            "scene_purpose" not in scene
            and all("narrative_role" not in beat for beat in scene["beats"])
            for scene in body["scene_plan"]["scenes"]
        )
        assert all("scene_visual_strategy" not in scene for scene in body["visual_plan"]["scenes"])

    factory = build_session_factory(get_settings())
    with factory() as session:
        current = SceneBreakdownServiceV4(session).get_current_scene_plan(project_id)
        assert current["generated"] is True
        assert current["scene_plan_artifact_id"] == scene_package["artifact_ids"]["scene_plan"]
        assert current["visual_plan_artifact_id"] == visual_package["artifact_ids"]["visual_plan"]


def test_step1_rebuild_can_replace_a_stored_visual_plan_from_the_previous_contract() -> None:
    project_id, _payload = _seed_from_fixture()
    factory = build_session_factory(get_settings())
    with factory() as session:
        external = ExternalSceneBreakdownTwoStepService(session)
        _step_one, visual_request = _import_two_step_pair(external, project_id)
        current_pair = external.import_visual_result(project_id, visual_request)

        row = external.service.artifacts.get_required(current_pair["visual_plan_artifact_id"])
        path = external.service.artifacts.resolve_file_path(row)
        stored = json.loads(path.read_text(encoding="utf-8"))
        for scene in stored["scenes"]:
            for beat in scene["beat_plans"]:
                del beat["previous_beat_image_reference_required"]
        encoded = (
            json.dumps(stored, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        path.write_bytes(encoded)
        digest = sha256(encoded).hexdigest()
        row.size_bytes = len(encoded)
        row.sha256 = digest
        row.content_hash = digest
        session.commit()

        with pytest.raises(AppError) as exc_info:
            SceneBreakdownServiceV4(session).get_current_visual_plan(project_id)
        assert exc_info.value.error_code == "VISUAL_PLAN_INVALID"

        package = external.build_scene_prompt(project_id)
        project, _script, _analysis, _style, word_timing, full_input, sections = external._context(project_id)
        authoring_plan = external._build_scene_authoring_plan(full_input, sections, word_timing)
        scene_output = _semantic_output_from_authoring_plan(
            authoring_plan,
            package["artifact_ids"]["scene_plan"],
        )
        step_one = external.import_scene_result(
            project.project_id,
            {"result_json": json.dumps(scene_output, ensure_ascii=False)},
        )

        assert step_one["visual_prompt_package"]["artifact_ids"]["visual_plan"]


def test_two_step_dependency_decision_survives_import_round_trip_approval_and_composer_input() -> None:
    project_id, _payload = _seed_from_fixture()
    factory = build_session_factory(get_settings())
    with factory() as session:
        external = ExternalSceneBreakdownTwoStepService(session)
        scene_package = external.build_scene_prompt(project_id)
        scene_output = _semantic_output_from_authoring_plan(
            _scene_authoring_plan(project_id),
            scene_package["artifact_ids"]["scene_plan"],
        )
        scene_output["sections"][1]["scenes"][0]["beats"][0]["continuity_with_previous"] = "match_subject"
        scene_output["sections"][2]["scenes"][0]["beats"][0]["continuity_with_previous"] = "match_location"
        step_one = external.import_scene_result(
            project_id,
            {"result_json": json.dumps(scene_output, ensure_ascii=False)},
        )
        beat_ids = [
            str(beat["beat_id"])
            for scene in step_one["scene_plan"]["scenes"]
            for beat in scene["beats"]
        ]
        visual_output = _visual_output_from_scene_plan(
            step_one["scene_plan"],
            step_one["visual_prompt_package"]["artifact_ids"]["visual_plan"],
            previous_required_by_beat={beat_ids[1]: True, beat_ids[2]: False},
        )
        pair = external.import_visual_result(
            project_id,
            {"result_json": json.dumps(visual_output, ensure_ascii=False)},
        )

        canonical = {
            str(beat["beat_id"]): beat
            for scene in pair["visual_plan"]["scenes"]
            for beat in scene["beat_plans"]
        }
        assert canonical[beat_ids[1]]["continuity_with_previous"] == "match_subject"
        assert canonical[beat_ids[1]]["previous_beat_image_reference_required"] is True
        assert canonical[beat_ids[2]]["continuity_with_previous"] == "match_location"
        assert canonical[beat_ids[2]]["previous_beat_image_reference_required"] is False

        round_trip = combined_fragments_from_paired_plans(pair["scene_plan"], pair["visual_plan"])
        round_trip_visuals = {
            beat_id: beat["visual"]
            for fragment in round_trip
            for scene in fragment["scenes"]
            for beat_id, beat in zip(
                [
                    str(source_beat["beat_id"])
                    for source_scene in pair["scene_plan"]["scenes"]
                    if source_scene["section_id"] == fragment["section_id"]
                    for source_beat in source_scene["beats"]
                ],
                scene["beats"],
                strict=True,
            )
        }
        assert round_trip_visuals[beat_ids[1]]["previous_beat_image_reference_required"] is True
        assert round_trip_visuals[beat_ids[2]]["previous_beat_image_reference_required"] is False

        approved = SceneBreakdownServiceV4(session).approve(
            project_id,
            {
                "expected_scene_plan_artifact_id": pair["scene_plan_artifact_id"],
                "expected_visual_plan_artifact_id": pair["visual_plan_artifact_id"],
                "aspect_ratio": "16:9",
            },
        )
        approved_beats = {
            str(beat["beat_id"]): beat
            for scene in approved["visual_plan"]["scenes"]
            for beat in scene["beat_plans"]
        }
        assert approved_beats[beat_ids[1]]["previous_beat_image_reference_required"] is True
        assert approved_beats[beat_ids[2]]["previous_beat_image_reference_required"] is False

        prompt_bundle = PromptComposerService(session).get_current_prompt_bundle(project_id)["artifact"]
        prompt_items = {str(item["beat_id"]): item for item in prompt_bundle["prompt_items"]}
        assert prompt_items[beat_ids[1]]["continuity_reference_requirement"] == {
            "reference_tag": "prev_beat",
            "source_beat_id": beat_ids[0],
            "relation": "match_subject",
        }
        assert prompt_items[beat_ids[2]]["continuity_reference_requirement"] is None
        assert "@prev_beat" not in prompt_items[beat_ids[2]]["image_prompt"]


def test_two_step_rebuild_invalidates_old_scene_assets_at_step2_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _payload = _seed_from_fixture()
    factory = build_session_factory(get_settings())

    with factory() as session:
        external = ExternalSceneBreakdownTwoStepService(session)
        _first_step_one, first_visual_request = _import_two_step_pair(external, project_id)
        first_pair = external.import_visual_result(project_id, first_visual_request)
        approved = SceneBreakdownServiceV4(session).approve(
            project_id,
            {
                "expected_scene_plan_artifact_id": first_pair["scene_plan_artifact_id"],
                "expected_visual_plan_artifact_id": first_pair["visual_plan_artifact_id"],
                "aspect_ratio": "16:9",
            },
        )
        project = session.get(Project, project_id)
        assert project is not None
        old_beats = [
            (str(scene["scene_id"]), str(beat["beat_id"]))
            for scene in approved["scene_plan"]["scenes"]
            for beat in scene["beats"]
        ][:2]
        seeded_candidates: dict[str, tuple[str, str]] = {}
        for index, (old_scene_id, beat_id) in enumerate(old_beats):
            revision = session.query(SceneAssetPromptRevision).filter_by(
                project_id=project_id,
                beat_id=beat_id,
                is_current=True,
            ).one()
            image_id = generate_id("artifact")
            candidate_id = generate_id("scene_asset_candidate")
            session.add(
                Artifact(
                    artifact_id=image_id,
                    logical_id=f"project/{project_id}/scene_assets/{beat_id}/{image_id}",
                    version_no=1,
                    is_current=True,
                    artifact_type=ArtifactType.IMAGE,
                    artifact_role=None,
                    path=f"tests/{image_id}.png",
                    content_hash=f"hash_{image_id}",
                    sha256=chr(ord("a") + index) * 64,
                    mime_type="image/png",
                    size_bytes=1,
                    width=1280,
                    height=720,
                    status=ArtifactStatus.READY,
                    created_at=utc_now_iso(),
                )
            )
            session.flush()
            session.add(
                SceneAssetCandidate(
                    candidate_id=candidate_id,
                    project_id=project_id,
                    scene_id=old_scene_id,
                    beat_id=beat_id,
                    artifact_id=image_id,
                    source_kind="uploaded",
                    operation_beat_id=None,
                    operation_task_id=None,
                    source_prompt_revision_id=revision.prompt_revision_id,
                    source_prompt_bundle_artifact_id=revision.source_prompt_bundle_artifact_id,
                    source_prompt_item_id=revision.source_prompt_item_id,
                    source_prompt_hash=revision.source_prompt_hash,
                    continuity_snapshot_json=None,
                    created_at=utc_now_iso(),
                )
            )
            session.flush()
            session.add(
                SceneAssetBeatState(
                    project_id=project_id,
                    beat_id=beat_id,
                    scene_id=old_scene_id,
                    version=7,
                    resolution_status="approved",
                    review_status="approved",
                    current_draft_candidate_id=candidate_id,
                    selected_candidate_id=candidate_id,
                    active_operation_beat_id=None,
                    continuity_source_scene_id=None,
                    continuity_source_beat_id=None,
                    continuity_source_candidate_id=None,
                    last_error_json={"error_code": "OLD_IMAGE"},
                    last_progress_at=utc_now_iso(),
                    created_at=utc_now_iso(),
                    updated_at=utc_now_iso(),
                )
            )
            seeded_candidates[beat_id] = (candidate_id, image_id)
        manifest_image_id = next(iter(seeded_candidates.values()))[1]
        project.scene_asset_manifest_artifact_id = manifest_image_id
        session.commit()

        old_scene_plan_id = project.scene_plan_artifact_id
        old_visual_plan_id = project.visual_plan_artifact_id
        old_prompt_bundle_id = project.prompt_bundle_artifact_id
        second_step_one, second_visual_request = _import_two_step_pair(external, project_id)
        session.commit()
        session.refresh(project)
        assert project.scene_plan_artifact_id == old_scene_plan_id
        assert project.visual_plan_artifact_id == old_visual_plan_id
        assert project.prompt_bundle_artifact_id == old_prompt_bundle_id
        assert project.scene_asset_manifest_artifact_id == manifest_image_id
        for beat_id, (candidate_id, _image_id) in seeded_candidates.items():
            step_one_state = session.get(SceneAssetBeatState, (project_id, beat_id))
            assert step_one_state is not None
            assert step_one_state.version == 7
            assert step_one_state.selected_candidate_id == candidate_id

        operation_id = generate_id("scene_asset_operation")
        session.add(
            SceneAssetWorkingOperation(
                operation_id=operation_id,
                project_id=project_id,
                operation_type="generate_beat",
                status="running",
                source_script_artifact_id=str(project.script_artifact_id),
                source_script_analysis_artifact_id=str(project.script_analysis_artifact_id),
                source_style_bible_artifact_id=str(project.style_bible_artifact_id),
                source_scene_plan_artifact_id=str(project.scene_plan_artifact_id),
                source_visual_plan_artifact_id=str(project.visual_plan_artifact_id),
                source_prompt_bundle_artifact_id=str(project.prompt_bundle_artifact_id),
                source_reference_approval_set_artifact_id=project.style_bible_visual_reference_approval_set_artifact_id,
                provider_settings_snapshot_json={},
                provider_settings_snapshot_hash="provider",
                total_beat_count=1,
                completed_count=0,
                failed_count=0,
                cancelled_count=0,
                next_reconcile_at=utc_now_iso(),
                last_progress_at=utc_now_iso(),
                reconcile_owner=None,
                reconcile_token=None,
                reconcile_lease_expires_at=None,
                cancel_requested_at=None,
                cancelled_at=None,
                request_json={},
                result_json={},
                error_code=None,
                error_message=None,
                error_details_json=None,
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
                completed_at=None,
            )
        )
        session.commit()

        with pytest.raises(AppError) as exc_info:
            external.import_visual_result(project_id, second_visual_request)
        assert exc_info.value.error_code == "SCENE_ASSET_GENERATION_ACTIVE"
        session.expire_all()
        project = session.get(Project, project_id)
        assert project is not None
        assert project.scene_plan_artifact_id == old_scene_plan_id
        assert project.visual_plan_artifact_id == old_visual_plan_id
        assert project.prompt_bundle_artifact_id == old_prompt_bundle_id
        for beat_id, (candidate_id, _image_id) in seeded_candidates.items():
            blocked_state = session.get(SceneAssetBeatState, (project_id, beat_id))
            assert blocked_state is not None
            assert blocked_state.version == 7
            assert blocked_state.selected_candidate_id == candidate_id
            assert session.query(SceneAssetPromptRevision).filter_by(
                project_id=project_id,
                beat_id=beat_id,
                is_current=True,
            ).one().source_prompt_bundle_artifact_id == old_prompt_bundle_id

        operation = session.get(SceneAssetWorkingOperation, operation_id)
        assert operation is not None
        operation.status = "cancelled"
        operation.cancelled_at = utc_now_iso()
        operation.completed_at = utc_now_iso()
        session.commit()

        second_pair = ExternalSceneBreakdownTwoStepService(session).import_visual_result(
            project_id,
            second_visual_request,
        )
        session.expire_all()
        project = session.get(Project, project_id)
        assert project is not None
        assert project.scene_plan_artifact_id == second_pair["scene_plan_artifact_id"]
        assert project.visual_plan_artifact_id == second_pair["visual_plan_artifact_id"]
        assert project.prompt_bundle_artifact_id is None
        assert project.scene_asset_manifest_artifact_id is None
        for beat_id, (candidate_id, _image_id) in seeded_candidates.items():
            reset_state = session.get(SceneAssetBeatState, (project_id, beat_id))
            assert reset_state is not None
            new_scene = next(
                scene
                for scene in second_step_one["scene_plan"]["scenes"]
                if any(beat["beat_id"] == beat_id for beat in scene["beats"])
            )
            assert reset_state.scene_id == new_scene["scene_id"]
            assert reset_state.version == 8
            assert reset_state.resolution_status == "not_generated"
            assert reset_state.review_status == "unreviewed"
            assert reset_state.current_draft_candidate_id is None
            assert reset_state.selected_candidate_id is None
            assert session.get(SceneAssetCandidate, candidate_id) is not None

        SceneBreakdownServiceV4(session).approve(
            project_id,
            {
                "expected_scene_plan_artifact_id": second_pair["scene_plan_artifact_id"],
                "expected_visual_plan_artifact_id": second_pair["visual_plan_artifact_id"],
                "aspect_ratio": "16:9",
            },
        )
        session.expire_all()
        for beat_id in seeded_candidates:
            approved_state = session.get(SceneAssetBeatState, (project_id, beat_id))
            assert approved_state is not None
            assert approved_state.version == 8
            assert approved_state.selected_candidate_id is None

        workspace = SceneAssetWorkspaceV2Service(session).workspace(project_id)
        projected = {
            beat["beat_id"]: beat
            for scene in workspace["scenes"]
            for beat in scene["beats"]
        }
        assert workspace["actions"]["approved_count"] == 0
        for beat_id in seeded_candidates:
            beat = projected[beat_id]
            assert beat["resolution_status"] == "not_generated"
            assert beat["review_status"] == "unreviewed"
            assert beat["current_draft_candidate_id"] is None
            assert beat["selected_candidate_id"] is None
            assert beat["candidates"] == []
        assert projected[next(iter(seeded_candidates))]["can_generate"] is True

        runtime = SceneAssetRuntimeV2(session)
        monkeypatch.setattr(
            runtime,
            "_provider_snapshot",
            lambda: {
                "provider": "glabs_webhook",
                "default_image_model": "nano_banana_2",
                "default_image_aspect_ratio": "16:9",
                "default_upscale": [],
                "poll_interval_seconds": 4,
                "max_parallel_tasks": 3,
                "enabled_media_kinds": ["image"],
                "enabled": True,
            },
        )
        generation = runtime.submit_generation(
            project_id,
            {"operation_type": "generate_all", "candidate_count": 1},
        )
        assert generation["total_beat_count"] > 0
        assert next(iter(seeded_candidates)) in generation["scheduled_beat_ids"]


def test_step2_stale_prompt_fails_closed_and_rebuild_preserves_locked_step1_candidate() -> None:
    project_id, _payload = _seed_from_fixture()
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ExternalSceneBreakdownTwoStepService(session)
        scene_package = service.build_scene_prompt(project_id)
        scene_output = _semantic_output_from_authoring_plan(
            _scene_authoring_plan(project_id),
            scene_package["artifact_ids"]["scene_plan"],
        )
        imported = service.import_scene_result(
            project_id,
            {"result_json": json.dumps(scene_output, ensure_ascii=False)},
        )
        old_visual_package = imported["visual_prompt_package"]
        old_scene_plan = deepcopy(imported["scene_plan"])
        reservation = service.reservations.pending_for(project_id=project_id, stage="scene_breakdown_visual")
        assert reservation is not None
        stale_source = service.reservations.source(reservation)
        stale_source["prompt_sha256"] = "sha256:" + "0" * 64
        reservation.source_json = stale_source
        session.commit()

    with factory() as session:
        service = ExternalSceneBreakdownTwoStepService(session)
        pending = service.get_pending_visual_prompt(project_id)
        assert pending == {
            "generated": False,
            "rebuild_required": True,
            "prompt_package": None,
            "request_input": {
                "scene_plan_artifact_id": old_scene_plan["scene_plan_revision_id"],
                "scene_plan": old_scene_plan,
            },
        }
        with pytest.raises(AppError) as exc_info:
            service.validate_visual_import(
                project_id,
                {"result_json": json.dumps({"artifact_id": old_visual_package["artifact_ids"]["visual_plan"], "unexpected": True})},
            )
        assert exc_info.value.error_code == "SCENE_BREAKDOWN_VISUAL_PROMPT_STALE"

        rebuilt = service.build_visual_prompt(project_id)
        assert rebuilt["prompt_version"] == "scene_breakdown_visual_external_v4"
        assert rebuilt["output_schema_name"] == "scene_breakdown_visual_llm_output_v2.schema.json"
        assert rebuilt["artifact_ids"]["visual_plan"] != old_visual_package["artifact_ids"]["visual_plan"]

        replacement = service.reservations.pending_for(project_id=project_id, stage="scene_breakdown_visual")
        assert replacement is not None
        replacement_source = service.reservations.source(replacement)
        assert replacement_source["scene_plan"] == old_scene_plan
        assert replacement_source["locked_fragments"] == stale_source["locked_fragments"]


def test_step1_current_contract_remains_pending_and_importable() -> None:
    project_id, _payload = _seed_from_fixture()
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ExternalSceneBreakdownTwoStepService(session)
        package = service.build_scene_prompt(project_id)
        scene_output = _semantic_output_from_authoring_plan(
            _scene_authoring_plan(project_id),
            package["artifact_ids"]["scene_plan"],
        )
        pending = service.get_pending_scene_prompt(project_id)
        assert pending["generated"] is True
        assert pending["rebuild_required"] is False
        assert pending["prompt_package"] == package
        assert pending["request_input"] is None
        validation = service.validate_scene_import(
            project_id,
            {"result_json": json.dumps(scene_output, ensure_ascii=False)},
        )
        assert validation["valid"] is True

        reservation = service.reservations.pending_for(
            project_id=project_id,
            stage="scene_breakdown_scene",
        )
        assert reservation is not None
        source = service.reservations.source(reservation)
        assert source["scene_input_schema_id"].endswith(
            "/scene_breakdown_scene_authoring_input_v2.schema.json"
        )
        assert source["scene_input_schema_sha256"].startswith("sha256:")


@pytest.mark.parametrize(
    "drift_kind",
    ["system_prompt", "user_prompt", "prompt_version", "output_schema", "input_schema"],
)
def test_step1_contract_drift_fails_closed_for_pending_and_import(
    monkeypatch: pytest.MonkeyPatch,
    drift_kind: str,
) -> None:
    project_id, _payload = _seed_from_fixture()
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ExternalSceneBreakdownTwoStepService(session)
        package = service.build_scene_prompt(project_id)
        scene_output = _semantic_output_from_authoring_plan(
            _scene_authoring_plan(project_id),
            package["artifact_ids"]["scene_plan"],
        )
        system_prompt, user_prompt = service._scene_prompt_texts()

    if drift_kind == "system_prompt":
        monkeypatch.setattr(
            ExternalSceneBreakdownTwoStepService,
            "_scene_prompt_texts",
            lambda self: (system_prompt + "\nContract drift.", user_prompt),
        )
    elif drift_kind == "user_prompt":
        monkeypatch.setattr(
            ExternalSceneBreakdownTwoStepService,
            "_scene_prompt_texts",
            lambda self: (system_prompt, user_prompt + "\nContract drift."),
        )
    elif drift_kind == "prompt_version":
        monkeypatch.setattr(
            two_step_module,
            "_SCENE_PROMPT_VERSION",
            "scene_breakdown_scene_external_test_drift",
        )
    else:
        target_schema = (
            "scene_breakdown_scene_llm_output_v2.schema.json"
            if drift_kind == "output_schema"
            else "scene_breakdown_scene_authoring_input_v2.schema.json"
        )
        original_load_schema = V4ContractValidator.load_schema

        def drifted_load_schema(self, name: str):
            schema = deepcopy(original_load_schema(self, name))
            if name == target_schema:
                schema["description"] = str(schema.get("description") or "") + " Contract drift."
            return schema

        monkeypatch.setattr(V4ContractValidator, "load_schema", drifted_load_schema)

    with factory() as session:
        service = ExternalSceneBreakdownTwoStepService(session)
        assert service.get_pending_scene_prompt(project_id) == {
            "generated": False,
            "rebuild_required": True,
            "prompt_package": None,
            "request_input": None,
        }
        with pytest.raises(AppError) as exc_info:
            service.validate_scene_import(
                project_id,
                {"result_json": json.dumps(scene_output, ensure_ascii=False)},
            )
        assert exc_info.value.error_code == "SCENE_BREAKDOWN_SCENE_PROMPT_STALE"
        assert exc_info.value.status_code == 409


def test_real_fixture_import_rejects_projection_drift_as_stale_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _payload = _seed_from_fixture()
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ExternalSceneBreakdownTwoStepService(session)
        package = service.build_scene_prompt(project_id)
        authoring_plan = _scene_authoring_plan(project_id)
        scene_output = _semantic_output_from_authoring_plan(
            authoring_plan,
            package["artifact_ids"]["scene_plan"],
        )

    original = two_step_module.project_scene_authoring_input

    def drifted_projection(plan):
        projected = original(plan)
        projected["source_segments"][0]["text"] += " drift"
        return projected

    monkeypatch.setattr(
        two_step_module,
        "project_scene_authoring_input",
        drifted_projection,
    )
    with factory() as session:
        with pytest.raises(AppError) as exc_info:
            ExternalSceneBreakdownTwoStepService(session).import_scene_result(
                project_id,
                {"result_json": json.dumps(scene_output, ensure_ascii=False)},
            )
    assert exc_info.value.error_code == "SCENE_BREAKDOWN_AUTHORING_SURFACE_STALE"


def test_real_fixture_import_preserves_internal_plan_lineage_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _payload = _seed_from_fixture()
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = ExternalSceneBreakdownTwoStepService(session)
        package = service.build_scene_prompt(project_id)
        authoring_plan = _scene_authoring_plan(project_id)
        scene_output = _semantic_output_from_authoring_plan(
            authoring_plan,
            package["artifact_ids"]["scene_plan"],
        )

    original_builder = ExternalSceneBreakdownTwoStepService._build_scene_authoring_plan

    def drifted_builder(cls, full_input, sections, word_timing_map):
        drifted = deepcopy(original_builder(full_input, sections, word_timing_map))
        drifted["sections"][0]["preferred_hold_seconds"]["max"] += 0.001
        return drifted

    monkeypatch.setattr(
        ExternalSceneBreakdownTwoStepService,
        "_build_scene_authoring_plan",
        classmethod(drifted_builder),
    )
    with factory() as session:
        with pytest.raises(AppError) as exc_info:
            ExternalSceneBreakdownTwoStepService(session).import_scene_result(
                project_id,
                {"result_json": json.dumps(scene_output, ensure_ascii=False)},
            )
    assert exc_info.value.error_code == "SCENE_BREAKDOWN_AUTHORING_PLAN_STALE"


def test_text_led_visual_from_real_fixture_uses_unified_image_contract() -> None:
    payload = _load_fixture()
    visual = next(
        beat["visual"]
        for section in payload["sections"]
        for scene in section["scenes"]
        for beat in scene["beats"]
        if beat["visual"].get("visible_text")
        == ["These were not final wounds", "The body survived them."]
    )

    assert "asset_strategy" not in visual
    assert "asset_details" not in visual
    assert visual["visual_concept"].startswith("Editorial text-led generated image")
    assert visual["subject_action"]
    assert visual["environment_emphasis"]
