from __future__ import annotations

import json
import re
from io import BytesIO
import wave
from typing import Any

import pytest

from app.core.config import get_settings
from app.db.enums import ArtifactType
from app.db.models import Project
from app.db.session import build_session_factory
from app.modules.platform.services import build_script_section_ranges
from app.modules.v4.scene_breakdown_external_service import ExternalSceneBreakdownService
from app.modules.v4.scene_breakdown_service import SceneBreakdownServiceV4
from app.modules.v4.voice_audio import CANONICAL_MIME, CANONICAL_SAMPLE_RATE
from app.modules.v4.voice_spoken_text import SCRIPT_SECTION_SEPARATOR
from app.repositories import ArtifactRepository
from app.services.artifacts import ArtifactService
from app.modules.v4.style_bible_service import StyleBibleServiceV4
from tests.support.external_authoring import external_import_payload
from tests.support.fixtures import sample_script_artifact
from tests.v4.test_style_bible_consumer import seed_project

_TOKEN_RE = re.compile(r"\S+", re.UNICODE)


def _chunks_by_words(text: str, *, words_per_chunk: int = 7) -> list[str]:
    tokens = list(_TOKEN_RE.finditer(text))
    chunks: list[str] = []
    cursor = 0
    for index in range(0, len(tokens), words_per_chunk):
        group = tokens[index : index + words_per_chunk]
        end = group[-1].end()
        chunks.append(text[cursor:end])
        cursor = end
    if chunks and cursor < len(text):
        chunks[-1] += text[cursor:]
    return chunks


def _visual(*, scene_index: int, local_beat_index: int) -> dict[str, Any]:
    relation = "new_setup" if local_beat_index == 0 else "same_setup"
    return {
        "representation_family": "character_environment_lived",
        "selected_character_refs": [],
        "selected_location_refs": [],
        "selected_prop_refs": [],
        "selected_recurring_visual_subject_refs": [],
        "selected_motif_refs": [],
        "visual_concept": f"A clear generated image for scene {scene_index + 1}",
        "subject_action": f"Communicate beat {local_beat_index + 1} visibly",
        "environment_emphasis": "A simple context appropriate to the narration",
        "visible_text": [],
        "framing": "medium",
        "camera_angle": "eye_level",
        "camera_movement": "static",
        "composition": "The primary subject is centered with supporting context clearly separated.",
        "lighting_direction": None,
        "color_emphasis": None,
        "continuity_with_previous": relation,
        "previous_beat_image_reference_required": relation == "same_setup",
    }


def output_for_text(text: str, *, words_per_chunk: int = 7) -> dict[str, Any]:
    beats = [
        {"script_text": chunk}
        for chunk in _chunks_by_words(text, words_per_chunk=words_per_chunk)
    ]
    scenes: list[dict[str, Any]] = []
    for start in range(0, len(beats), 3):
        scene_index = len(scenes)
        scene_beats = [
            {**beat, "visual": _visual(scene_index=scene_index, local_beat_index=local_index)}
            for local_index, beat in enumerate(beats[start : start + 3])
        ]
        scenes.append({"beats": scene_beats})
    return {"scenes": scenes}


def outputs_for_sample_script() -> dict[str, dict[str, Any]]:
    script = sample_script_artifact()
    ranges = build_script_section_ranges(script["script_sections"])
    return {
        section["section_id"]: output_for_text(script["full_script"][span["start"] : span["end"]])
        for section, span in zip(script["script_sections"], ranges, strict=True)
    }


def _canonical_voice_wav(sample_count: int) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(CANONICAL_SAMPLE_RATE)
        wav.writeframes(b"\x00\x00" * sample_count)
    return output.getvalue()


def _seed_approved_voice_pair(session, project: Project, script_id: str, script: dict[str, Any] | None = None) -> None:
    script = script or sample_script_artifact()
    ranges = build_script_section_ranges(script["script_sections"])
    sample_cursor = 0
    timing_sections: list[dict[str, Any]] = []
    for section, span in zip(script["script_sections"], ranges, strict=True):
        narration = str(section["narration"])
        tokens = list(_TOKEN_RE.finditer(narration))
        section_sample_count = max(4_800, 960 + len(tokens) * 1_440)
        words: list[dict[str, Any]] = []
        for index, token in enumerate(tokens):
            local_start = 480 + index * 1_440
            local_end = local_start + 960
            words.append({
                "section_word_index": index,
                "word_index": len([w for s in timing_sections for w in s["words"]]) + index,
                "section_id": section["section_id"],
                "text": token.group(0),
                "spoken_start": token.start(),
                "spoken_end": token.end(),
                "authored_start": int(span["start"]) + token.start(),
                "authored_end": int(span["start"]) + token.end(),
                "start_sample": local_start,
                "end_sample": local_end,
                "global_start_sample": sample_cursor + local_start,
                "global_end_sample": sample_cursor + local_end,
            })
        timing_sections.append({
            "section_id": section["section_id"],
            "section_order": len(timing_sections) + 1,
            "authored_section_start": int(span["start"]),
            "authored_section_end": int(span["end"]),
            "section_audio_artifact_id": f"fixture_section_audio_{section['section_id']}",
            "section_audio_sha256": "0" * 64,
            "final_section_identity_sha256": "1" * 64,
            "sample_count": section_sample_count,
            "global_start_sample": sample_cursor,
            "global_end_sample": sample_cursor + section_sample_count,
            "canonical_spoken_text": narration,
            "canonical_spoken_text_sha256": "2" * 64,
            "authored_spoken_mapping": [],
            "character_timing": [],
            "words": words,
            "alignment_task_id": f"fixture_alignment_{section['section_id']}",
            "alignment_loss_telemetry": {"fixture": True},
        })
        sample_cursor += section_sample_count
    artifacts = ArtifactService(session, ArtifactRepository(session))
    audio = artifacts.write_artifact(
        logical_id=f"project/{project.project_id}/voice/narration-audio",
        artifact_type=ArtifactType.AUDIO,
        filename="narration.wav",
        content=_canonical_voice_wav(sample_cursor),
        provider_source="task4_test_fixture",
    )
    audio.artifact.mime_type = CANONICAL_MIME
    timing_map = {
        "schema_version": "narration_word_timing_map_v1",
        "architecture_version": "voice_generation_providers_v1",
        "source_script_artifact_id": script_id,
        "source_narration_audio_artifact_id": audio.artifact_id,
        "sample_rate_hz": CANONICAL_SAMPLE_RATE,
        "total_sample_count": sample_cursor,
        "canonical_spoken_narration": script["full_script"],
        "canonical_spoken_narration_sha256": "3" * 64,
        "spoken_compilation_sha256": "4" * 64,
        "sections": timing_sections,
        "alignment_provider": "elevenlabs",
        "alignment_policy_version": "elevenlabs_forced_alignment_v1",
        "sample_rounding_policy": "seconds_x_48000_half_up_v1",
        "alignment_summary": {"section_count": len(timing_sections), "completed_alignment_task_ids": [], "loss_telemetry": []},
    }
    timing = artifacts.write_artifact(
        logical_id=f"project/{project.project_id}/voice/narration-word-timing-map",
        artifact_type=ArtifactType.JSON,
        filename="narration_word_timing_map.json",
        content=json.dumps(timing_map, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        provider_source="task4_test_fixture",
    )
    project.narration_audio_artifact_id = audio.artifact_id
    project.narration_word_timing_map_artifact_id = timing.artifact_id
    session.commit()


def approved_seed() -> tuple[str, str, str, str]:
    project_id, script_id, analysis_id = seed_project()
    factory = build_session_factory(get_settings())
    with factory() as session:
        style, style_id = StyleBibleServiceV4(session).generate(project_id, analysis_id)
        if style["status"] != "text_approved":
            style, style_id = StyleBibleServiceV4(session).approve_text(project_id, style_id)
        assert style["status"] == "text_approved"
        project = session.get(Project, project_id)
        assert project is not None
        _seed_approved_voice_pair(session, project, script_id)
    return project_id, script_id, analysis_id, style_id


def full_external_output() -> dict[str, Any]:
    outputs = outputs_for_sample_script()
    return {
        "sections": [
            {"section_id": section["section_id"], "scenes": outputs[section["section_id"]]["scenes"]}
            for section in sample_script_artifact()["script_sections"]
        ]
    }


def generate_combined_pair(
    monkeypatch: pytest.MonkeyPatch | None = None,
    *,
    approve: bool = False,
) -> dict[str, Any]:
    del monkeypatch
    project_id, script_id, analysis_id, style_id = approved_seed()
    factory = build_session_factory(get_settings())
    with factory() as session:
        external = ExternalSceneBreakdownService(session)
        package = external.build_prompt(project_id)
        imported = external.import_result(
            project_id,
            external_import_payload(package, full_external_output()),
        )
        current = {
            "generated": True,
            "scene_plan_artifact_id": imported["scene_plan_artifact_id"],
            "scene_plan": imported["scene_plan"],
            "visual_plan_artifact_id": imported["visual_plan_artifact_id"],
            "visual_plan": imported["visual_plan"],
        }
        if approve:
            approved = SceneBreakdownServiceV4(session).approve(
                project_id,
                {
                    "expected_scene_plan_artifact_id": current["scene_plan_artifact_id"],
                    "expected_visual_plan_artifact_id": current["visual_plan_artifact_id"],
                    "aspect_ratio": "16:9",
                },
            )
            current = {"generated": True, **approved}
    return {
        "project_id": project_id,
        "script_id": script_id,
        "analysis_id": analysis_id,
        "style_id": style_id,
        **current,
    }


def prompt_lineage(project: Project, *, expected_prompt: str | None) -> dict[str, Any]:
    return {
        "style_bible_artifact_id": project.style_bible_artifact_id,
        "visual_plan_artifact_id": project.visual_plan_artifact_id,
        "expected_prompt_bundle_artifact_id": expected_prompt,
    }
