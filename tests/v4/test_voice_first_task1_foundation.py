from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, text

from app.api.errors import AppError
from app.api.schemas import ChannelVoiceProfileModel
from app.api.voice_schemas_v4 import VoiceGenerateRequest, VoiceSectionRegenerateRequest
from app.core.config import get_settings
from app.db.base import Base
from app.db.models import Project, VoiceAlignmentTask, VoiceGenerationOperation
from app.db.session import build_session_factory, create_sqlalchemy_engine
from app.modules.platform.external_script_analysis import ExternalScriptAnalysisService
from app.modules.platform.services import ChannelService
from app.modules.v4.voice_first_policy import ACTIVE_VOICE_PRESETS, VOICE_ARCHITECTURE_VERSION
from app.modules.v4.voice_spoken_text import compile_voice_spoken_text, kokoro_pieces_for_segment, provider_text_for_segment
from tests.support.external_authoring import (
    combined_script_analysis_output,
    create_channel_project,
    external_import_payload,
    import_and_select_topic,
)


def _request() -> dict:
    return {
        "user_input": {
            "target_duration_minutes": 1,
            "pov_style": "second person with measured explanatory passages",
            "avoid": [],
            "section_count_mode": "llm_decides",
            "pacing_mode": "medium",
            "narration_wpm": {"min": 145, "max": 165},
        },
        "selected_topic_idea_id": "idea_001",
    }


def _migration_config(database_path: Path) -> Config:
    settings = get_settings()
    config = Config(str((settings.root_dir / "alembic.ini").resolve()))
    config.set_main_option("script_location", str((settings.root_dir / "alembic").resolve()))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.resolve()}")
    return config


def _voice_first_output() -> dict:
    output = combined_script_analysis_output()
    section = output["script"]["script_sections"][0]
    section["narration"] = (
        "Nobody moved. The D3444 code blinked once. "
        "Nobody moved again as the room listened."
    )
    section["spoken_overrides"] = [
        {
            "anchor_quote": "D3444",
            "context_quote": "The D3444 code blinked once.",
            "spoken_text": "D three four four four",
        }
    ]
    section["delivery_cues"] = [
        {
            "anchor_quote": "Nobody moved.",
            "context_quote": "Nobody moved. The D3444 code blinked once.",
            "kind": "pause_after",
            "pause_strength": "medium",
        }
    ]
    return output


def test_external_authoring_v2_resolves_spoken_overrides_and_sparse_pause_cue() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        assert package["output_schema_name"] == "script_and_analysis_external_output_v2.schema.json"
        assert "spoken_overrides" in package["prompt_text"]

        result = service.import_result(project_id, external_import_payload(package, _voice_first_output()))
        section = result["script"]["script_sections"][0]
        full_script = result["script"]["full_script"]

        override = section["spoken_overrides"][0]
        assert override["id"] == "sov_sec_001_001"
        assert full_script[override["source_start"]:override["source_end"]] == "D3444"
        assert override["spoken_text"] == "D three four four four"

        cue = section["delivery_cues"][0]
        assert cue["id"] == "dvc_sec_001_001"
        assert cue["kind"] == "pause_after"
        assert cue["pause_strength"] == "medium"
        assert cue["source_offset"] == len("Nobody moved.")


def test_spoken_compiler_preserves_override_mapping_and_sparse_delivery() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        result = service.import_result(project_id, external_import_payload(package, _voice_first_output()))

        script = result["script"]
        compiled = compile_voice_spoken_text(script)
        section = compiled.sections[0]
        canonical_spoken = section["canonical_spoken_text"]
        provider_text = section["provider_text"]

        assert "D three four four four" in canonical_spoken
        assert "D3444" not in canonical_spoken
        assert "<break" not in canonical_spoken.lower()
        assert "<" not in canonical_spoken
        assert ">" not in canonical_spoken

        intended_break = '<break time="0.55s" />'
        assert provider_text.count(intended_break) == 1
        assert provider_text.count("<break") == 1
        assert provider_text.count("<") == 1
        assert provider_text.count(">") == 1

        mapping = section["authored_spoken_mapping"][0]
        full_script = script["full_script"]
        assert full_script[mapping["authored_start"]:mapping["authored_end"]] == "D3444"
        assert canonical_spoken[mapping["spoken_start"]:mapping["spoken_end"]] == "D three four four four"

        authored_start = mapping["section_authored_start"]
        authored_end = mapping["section_authored_end"]
        safe_boundaries = set(section["safe_authored_boundaries"])
        assert authored_start in safe_boundaries
        assert authored_end in safe_boundaries
        assert all(boundary not in safe_boundaries for boundary in range(authored_start + 1, authored_end))


def test_kokoro_delivery_plan_uses_markup_free_text_and_deterministic_silence() -> None:
    narration = "Nobody moved. The D3444 code blinked once."
    override_start = narration.index("D3444")
    pause_boundary = len("Nobody moved.")
    script = {
        "script_sections": [
            {
                "section_id": "section_001",
                "section_title": "Hook",
                "section_role": "hook",
                "planned_narration_wpm": 155,
                "narration": narration,
                "spoken_overrides": [
                    {
                        "id": "spoken_override_001",
                        "source_start": override_start,
                        "source_end": override_start + len("D3444"),
                        "spoken_text": "D three four four four",
                    }
                ],
                "delivery_cues": [
                    {
                        "id": "delivery_cue_001",
                        "kind": "pause_after",
                        "source_offset": pause_boundary,
                        "pause_strength": "medium",
                    }
                ],
            }
        ]
    }
    compiled = compile_voice_spoken_text(script)
    section = compiled.sections[0]
    segment = {
        "segment_id": "segment_001",
        "section_id": section["section_id"],
        "source_start": 0,
        "source_end": len(section["canonical_spoken_text"]),
    }

    elevenlabs_text = provider_text_for_segment(compiled, segment)
    kokoro_pieces = kokoro_pieces_for_segment(compiled, segment)
    kokoro_text = "".join(str(item.get("text") or "") for item in kokoro_pieces if item.get("kind") == "text")

    assert '<break time="0.55s" />' in elevenlabs_text
    assert kokoro_text == section["canonical_spoken_text"]
    assert "<" not in kokoro_text and ">" not in kokoro_text
    assert {"kind": "silence", "duration_seconds": 0.55} in kokoro_pieces


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    [
        ("uncovered", "SCRIPT_SPOKEN_OVERRIDE_REQUIRED"),
        ("ambiguous", "SCRIPT_SPOKEN_OVERRIDE_INVALID"),
        ("overlap", "SCRIPT_SPOKEN_OVERRIDE_OVERLAP"),
    ],
)
def test_external_authoring_v2_rejects_sensitive_ambiguity_and_overlap(mutation: str, error_code: str) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        output = _voice_first_output()
        section = output["script"]["script_sections"][0]
        if mutation == "uncovered":
            section["spoken_overrides"] = []
        elif mutation == "ambiguous":
            section["narration"] = "D3444 blinked. Later D3444 blinked."
            section["spoken_overrides"] = [
                {"anchor_quote": "D3444", "context_quote": "D3444", "spoken_text": "D three four four four"}
            ]
            section["delivery_cues"] = []
        elif mutation == "overlap":
            section["spoken_overrides"].append(dict(section["spoken_overrides"][0]))

        with pytest.raises(AppError) as exc_info:
            service.validate_import(project_id, external_import_payload(package, output))
        assert exc_info.value.error_code == error_code


def test_migration_028_to_029_voice_first_shape_and_active_operation_guard(tmp_path: Path) -> None:
    blocked_db = tmp_path / "blocked.sqlite3"
    blocked_config = _migration_config(blocked_db)
    command.upgrade(blocked_config, "028_durable_voice_workflow")
    engine = create_sqlalchemy_engine(f"sqlite:///{blocked_db.resolve()}")
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO channels (channel_id, name, language, niche, audience, channel_dna_json, channel_context_json, created_at, updated_at) VALUES ('channel_test', 'Test', 'English', 'niche', 'audience', '{}', '{}', 'now', 'now')"))
            conn.execute(text("INSERT INTO projects (project_id, channel_id, name, channel_context_json, channel_dna_json, created_at, updated_at) VALUES ('project_test', 'channel_test', 'Test', '{}', '{}', 'now', 'now')"))
            conn.execute(text("INSERT INTO jobs (job_id, job_type, status, progress, created_at, updated_at) VALUES ('job_test', 'voice_generation', 'queued', 0, 'now', 'now')"))
            conn.execute(text("""
                INSERT INTO voice_generation_operations (
                    operation_id, job_id, project_id, operation_type, status, script_artifact_id,
                    scene_plan_artifact_id, expected_narration_audio_artifact_id,
                    expected_narration_timing_map_artifact_id, tts_profile_id, voice_id, language,
                    speaking_rate, provider_identity_json, provider_identity_sha256,
                    generation_settings_snapshot_json, generation_settings_snapshot_sha256,
                    segmentation_version, segmentation_input_sha256, segment_count,
                    request_json, result_json, created_at, updated_at
                ) VALUES (
                    'voiceop_test', 'job_test', 'project_test', 'generate_all', 'running', 'artifact_script',
                    'artifact_scene', NULL, NULL, 'profile', 'voice', 'English', 1.0,
                    '{}', 'hash', '{}', 'hash', 'narration_segmentation_v1', 'hash', 1,
                    '{}', '{}', 'now', 'now'
                )
            """))
        with pytest.raises(RuntimeError, match="refuses to run"):
            command.upgrade(blocked_config, "029_voice_first_elevenlabs")
    finally:
        engine.dispose()

    migrated_db = tmp_path / "migrated.sqlite3"
    migrated_config = _migration_config(migrated_db)
    command.upgrade(migrated_config, "028_durable_voice_workflow")
    command.upgrade(migrated_config, "029_voice_first_elevenlabs")
    engine = create_sqlalchemy_engine(f"sqlite:///{migrated_db.resolve()}")
    try:
        inspector = inspect(engine)
        project_columns = {column["name"] for column in inspector.get_columns("projects")}
        operation_columns = {column["name"] for column in inspector.get_columns("voice_generation_operations")}
        assert "narration_timing_map_artifact_id" not in project_columns
        assert {"narration_word_timing_map_artifact_id", "beat_timing_map_artifact_id"} <= project_columns
        assert "scene_plan_artifact_id" not in operation_columns
        assert "expected_narration_timing_map_artifact_id" not in operation_columns
        assert {"architecture_version", "expected_narration_word_timing_map_artifact_id", "voice_preset"} <= operation_columns
        assert "voice_alignment_tasks" in inspector.get_table_names()
    finally:
        engine.dispose()

    assert {"narration_word_timing_map_artifact_id", "beat_timing_map_artifact_id"} <= set(Project.__table__.columns.keys())
    assert "narration_timing_map_artifact_id" not in Project.__table__.columns
    assert "scene_plan_artifact_id" not in VoiceGenerationOperation.__table__.columns
    assert "voice_alignment_tasks" in Base.metadata.tables
    assert VoiceAlignmentTask.__table__.c.status is not None


def test_channel_profile_supports_both_presets_and_voice_request_rejects_arbitrary_selection() -> None:
    profile = {
        "schema_version": 1,
        "provider": "elevenlabs",
        "voice_id": "eleven_pvc_123",
        "voice_kind": "professional",
        "default_preset": "quality_cloned_voice",
        "calibration": {
            "economy": {"stability": 0.5, "similarity_boost": 0.75, "use_speaker_boost": True},
            "quality_cloned_voice": {"stability": 0.5, "similarity_boost": 0.75, "use_speaker_boost": True},
        },
    }
    assert set(ChannelVoiceProfileModel.model_validate(profile).calibration) == set(ACTIVE_VOICE_PRESETS)

    factory = build_session_factory(get_settings())
    with factory() as session:
        channel_id, _project_id = create_channel_project(session)
        service = ChannelService(session)
        saved = service.save_voice_profile(channel_id, profile)
        assert saved.voice_profile_json == profile
        read_profile, _updated_at = service.get_voice_profile(channel_id)
        assert read_profile == profile

    assert VoiceGenerateRequest.model_validate({
        "operation_type": "generate_all",
        "generation_provider": "elevenlabs",
        "voice_preset": "economy",
    }).voice_preset == "economy"
    assert VoiceGenerateRequest.model_validate({
        "operation_type": "generate_all",
        "generation_provider": "kokoro_local",
        "generation_voice_id": "af_heart",
    }).generation_voice_id == "af_heart"
    with pytest.raises(ValidationError):
        VoiceGenerateRequest.model_validate({
            "operation_type": "generate_all",
            "generation_provider": "elevenlabs",
            "voice_preset": "economy",
            "provider": "elevenlabs",
            "model_id": "eleven_turbo_v2_5",
            "voice_id": "arbitrary_voice",
            "scene_plan_artifact_id": "artifact_scene",
        })
    with pytest.raises(ValidationError):
        VoiceGenerateRequest.model_validate({
            "operation_type": "generate_all",
            "generation_provider": "elevenlabs",
            "voice_preset": "eleven_multilingual_v2",
        })
    with pytest.raises(ValidationError):
        VoiceGenerateRequest.model_validate({
            "operation_type": "generate_all",
            "generation_provider": "kokoro_local",
            "voice_preset": "economy",
            "generation_voice_id": "af_heart",
        })
    assert VoiceSectionRegenerateRequest.model_validate({
        "source_remediation_operation_id": "voice_operation_abc",
        "expected_remediation_report_sha256": "a" * 64,
    }).source_remediation_operation_id == "voice_operation_abc"
    with pytest.raises(ValidationError):
        VoiceSectionRegenerateRequest.model_validate({
            "source_remediation_operation_id": "voice_operation_abc",
            "expected_remediation_report_sha256": "a" * 64,
            "voice_preset": "economy",
        })
    assert VOICE_ARCHITECTURE_VERSION == "voice_generation_providers_v1"

def test_spoken_compiler_supports_performance_and_rhetorical_pause_mix() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        _channel_id, project_id = create_channel_project(session)
        import_and_select_topic(session, project_id)
        service = ExternalScriptAnalysisService(session)
        package = service.build_prompt(project_id, _request())
        output = combined_script_analysis_output()
        section = output["script"]["script_sections"][0]
        section["narration"] = (
            "The evidence holds. That changes the question. The answer lands."
        )
        section["spoken_overrides"] = []
        section["delivery_cues"] = [
            {
                "anchor_quote": "The evidence holds.",
                "context_quote": "The evidence holds. That changes the question.",
                "kind": "pause_after",
                "pause_strength": "short",
            },
            {
                "anchor_quote": "That changes the question.",
                "context_quote": "That changes the question. The answer lands.",
                "kind": "pause_after",
                "pause_strength": "medium",
            },
        ]
        result = service.import_result(
            project_id, external_import_payload(package, output)
        )
        compiled = compile_voice_spoken_text(result["script"])
        first = compiled.sections[0]
        assert "<break" not in first["canonical_spoken_text"].lower()
        assert first["provider_text"].count('<break time="0.25s" />') == 1
        assert first["provider_text"].count('<break time="0.55s" />') == 1
        assert first["provider_text"].count("<break") == 2
