from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.api.errors import AppError
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactType, JobStatus
from app.db.models import Artifact, Channel, Job, Project, VoiceGenerationOperation
from app.db.session import build_session_factory
from app.core.config import get_settings
from app.modules.v4.assembly_service import AssemblyService
from app.modules.v4.assembly_subtitles import UNITS_PER_SECOND, compile_subtitle_cues
from app.modules.v4.durable_voice_service import DurableVoiceService
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_candidate import SceneAssetCandidate
from app.db.scene_asset_prompt_revision import SceneAssetPromptRevision
from app.modules.v4.scene_asset_manifest import flatten_manifest_entries
from app.modules.v4.scene_asset_workspace_v2 import SceneAssetWorkspaceV2Service
from app.modules.v4.scene_breakdown_external_service import ExternalSceneBreakdownService
from app.modules.v4.scene_breakdown_service import SceneBreakdownServiceV4
from app.modules.v4.style_bible_service import StyleBibleServiceV4
from app.modules.v4.voice_audio import CANONICAL_SAMPLE_RATE, validate_canonical_wav_bytes
from app.modules.v4.voice_first_policy import VOICE_ARCHITECTURE_VERSION
from app.modules.v4.voice_service import canonical_hash
from app.repositories import ArtifactRepository
from app.services.artifacts import ArtifactService
from tests.support.external_authoring import external_import_payload
from tests.v4.combined_scene_breakdown_support import (
    _seed_approved_voice_pair,
    approved_seed,
    full_external_output,
    generate_combined_pair,
)


def _factory():
    return build_session_factory(get_settings())


def _json_artifact(artifacts: ArtifactService, artifact_id: str) -> dict:
    row = artifacts.get_required(artifact_id)
    return json.loads(artifacts.resolve_file_path(row).read_text(encoding="utf-8"))


def _stage_sentinels(session, project: Project, fields: tuple[str, ...]) -> dict[str, str]:
    artifacts = ArtifactService(session, ArtifactRepository(session))
    result: dict[str, str] = {}
    for field in fields:
        staged = artifacts.write_artifact(
            logical_id=f"project/{project.project_id}/task4-review/{field}",
            artifact_type=ArtifactType.JSON,
            filename=f"{field}.json",
            content=(json.dumps({"sentinel": field}, sort_keys=True) + "\n").encode("utf-8"),
            provider_source="task4_review_gate",
        )
        setattr(project, field, staged.artifact_id)
        result[field] = staged.artifact_id
    session.commit()
    return result


def _stage_voice_candidate(session, project: Project) -> dict[str, str]:
    assert project.script_artifact_id
    assert project.narration_audio_artifact_id
    assert project.narration_word_timing_map_artifact_id
    channel = session.get(Channel, project.channel_id)
    assert channel is not None
    profile = {"provider": "elevenlabs", "voice_id": "voice_task4_review", "clone_type": "professional"}
    channel.voice_profile_json = profile

    rows = ArtifactRepository(session)
    artifacts = ArtifactService(session, rows)
    old_audio = artifacts.get_required(str(project.narration_audio_artifact_id))
    old_timing = _json_artifact(artifacts, str(project.narration_word_timing_map_artifact_id))
    audio_bytes = artifacts.resolve_file_path(old_audio).read_bytes()
    candidate_audio = artifacts.stage_artifact_revision(
        logical_id=old_audio.logical_id,
        artifact_type=ArtifactType.AUDIO,
        filename="narration.wav",
        content=audio_bytes,
        provider_source="task4_review_voice_candidate",
    )
    audio_info = validate_canonical_wav_bytes(audio_bytes)
    timing_payload = deepcopy(old_timing)
    timing_payload["source_narration_audio_artifact_id"] = candidate_audio.artifact_id
    candidate_timing = artifacts.stage_artifact_revision(
        logical_id=f"project/{project.project_id}/voice/narration-word-timing-map",
        artifact_type=ArtifactType.JSON,
        filename="narration_word_timing_map.json",
        content=(json.dumps(timing_payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
        provider_source="task4_review_voice_candidate",
    )

    suffix = uuid4().hex[:12]
    now = utc_now_iso()
    job_id = f"job_task4_review_{suffix}"
    operation_id = f"voiceop_task4_review_{suffix}"
    session.add(
        Job(
            job_id=job_id,
            job_type="voice_generation_v4",
            status=JobStatus.COMPLETED,
            progress=100,
            attempt=1,
            retryable=False,
            created_at=now,
            updated_at=now,
            completed_at=now,
            finished_at=now,
        )
    )
    candidate = {
        "candidate_identity_sha256": hashlib.sha256(f"candidate-{suffix}".encode()).hexdigest(),
        "narration_audio_artifact_id": candidate_audio.artifact_id,
        "narration_audio_sha256": candidate_audio.artifact.sha256,
        "narration_word_timing_map_artifact_id": candidate_timing.artifact_id,
        "narration_word_timing_map_sha256": candidate_timing.artifact.sha256,
        "source_script_artifact_id": project.script_artifact_id,
        "sample_rate_hz": CANONICAL_SAMPLE_RATE,
        "total_sample_count": audio_info.sample_count,
        "channel_voice_profile_sha256": canonical_hash(profile),
        "voice_preset": "quality_cloned_voice",
        "provider_identity_sha256": "a" * 64,
        "generation_settings_snapshot_sha256": "b" * 64,
        "spoken_compilation_sha256": "c" * 64,
        "final_section_identity_sha256": [
            str(section.get("final_section_identity_sha256") or "d" * 64)
            for section in timing_payload.get("sections", [])
        ],
        "reviewable": True,
    }
    session.add(
        VoiceGenerationOperation(
            operation_id=operation_id,
            job_id=job_id,
            project_id=project.project_id,
            operation_type="generate_all",
            status="completed",
            script_artifact_id=str(project.script_artifact_id),
            architecture_version=VOICE_ARCHITECTURE_VERSION,
            expected_narration_audio_artifact_id=str(project.narration_audio_artifact_id),
            expected_narration_word_timing_map_artifact_id=str(project.narration_word_timing_map_artifact_id),
            voice_preset="quality_cloned_voice",
            tts_profile_id="channel_elevenlabs_pvc",
            voice_id=str(profile["voice_id"]),
            language="en",
            speaking_rate=1.0,
            provider_identity_json={"provider": "elevenlabs"},
            provider_identity_sha256="a" * 64,
            generation_settings_snapshot_json={"channel_voice_profile_sha256": canonical_hash(profile)},
            generation_settings_snapshot_sha256="b" * 64,
            segmentation_version="narration_segmentation_v2",
            segmentation_input_sha256="e" * 64,
            segment_count=max(1, len(timing_payload.get("sections", []))),
            request_json={},
            result_json={"draft_candidate": candidate},
            created_at=now,
            updated_at=now,
            completed_at=now,
        )
    )
    session.commit()
    return {
        "audio_id": candidate_audio.artifact_id,
        "timing_id": candidate_timing.artifact_id,
        "old_audio_id": str(project.narration_audio_artifact_id),
        "old_timing_id": str(project.narration_word_timing_map_artifact_id),
    }


def test_actual_scene_external_import_rejects_changed_approved_voice_without_publication() -> None:
    project_id, script_id, _analysis_id, _style_id = approved_seed()
    factory = _factory()
    with factory() as session:
        service = ExternalSceneBreakdownService(session)
        package = service.build_prompt(project_id)
        payload = external_import_payload(package, full_external_output())
        project = session.get(Project, project_id)
        assert project is not None
        before = (
            project.scene_plan_artifact_id,
            project.visual_plan_artifact_id,
            project.prompt_bundle_artifact_id,
            project.beat_timing_map_artifact_id,
        )
        old_voice = (project.narration_audio_artifact_id, project.narration_word_timing_map_artifact_id)
        _seed_approved_voice_pair(session, project, script_id)
        session.refresh(project)
        assert (project.narration_audio_artifact_id, project.narration_word_timing_map_artifact_id) != old_voice

        with pytest.raises(AppError) as exc_info:
            service.import_result(project_id, payload)
        assert exc_info.value.error_code == "AUTHORING_SOURCE_CHANGED"
        assert set(exc_info.value.details["changed_fields"]) >= {
            "narration_audio_artifact_id",
            "narration_word_timing_map_artifact_id",
        }
        session.refresh(project)
        assert (
            project.scene_plan_artifact_id,
            project.visual_plan_artifact_id,
            project.prompt_bundle_artifact_id,
            project.beat_timing_map_artifact_id,
        ) == before
        for artifact_id in package["artifact_ids"].values():
            assert session.get(Artifact, artifact_id) is None


def test_scene_four_artifact_publication_real_cas_rejects_voice_race_atomically(monkeypatch: pytest.MonkeyPatch) -> None:
    seeded = generate_combined_pair(approve=True)
    project_id = str(seeded["project_id"])
    script_id = str(seeded["script_id"])
    factory = _factory()
    with factory() as session:
        project = session.get(Project, project_id)
        assert project is not None
        approved_prompt = str(project.prompt_bundle_artifact_id)
        approved_beat_timing = str(project.beat_timing_map_artifact_id)
        external = ExternalSceneBreakdownService(session)
        package = external.build_prompt(project_id)
        imported = external.import_result(project_id, external_import_payload(package, full_external_output()))
        session.refresh(project)
        project.prompt_bundle_artifact_id = approved_prompt
        project.beat_timing_map_artifact_id = approved_beat_timing
        downstream = _stage_sentinels(
            session,
            project,
            (
                "scene_asset_manifest_artifact_id",
                "assembly_plan_artifact_id",
                "subtitle_srt_artifact_id",
                "subtitle_vtt_artifact_id",
                "final_video_artifact_id",
            ),
        )
        session.refresh(project)
        old_scene_ids = (
            str(project.scene_plan_artifact_id),
            str(project.visual_plan_artifact_id),
            str(project.prompt_bundle_artifact_id),
            str(project.beat_timing_map_artifact_id),
        )
        old_voice = (str(project.narration_audio_artifact_id), str(project.narration_word_timing_map_artifact_id))
        service = SceneBreakdownServiceV4(session)
        original_publish = service.publish_approved_scene_visual_prompt_bundle
        candidate_ids: dict[str, str] = {}
        raced_voice: dict[str, str] = {}

        def publish_after_voice_race(**kwargs):
            candidate_ids.update(
                scene=str(kwargs["scene_plan"]["scene_plan_revision_id"]),
                visual=str(kwargs["visual_plan"]["visual_plan_revision_id"]),
                prompt=str(kwargs["prompt_bundle"]["prompt_bundle_revision_id"]),
                timing=str(kwargs["beat_timing_map_artifact_id"]),
            )
            current = session.get(Project, project_id)
            assert current is not None
            _seed_approved_voice_pair(session, current, script_id)
            session.refresh(current)
            raced_voice.update(
                audio=str(current.narration_audio_artifact_id),
                timing=str(current.narration_word_timing_map_artifact_id),
            )
            assert (raced_voice["audio"], raced_voice["timing"]) != old_voice
            return original_publish(**kwargs)

        monkeypatch.setattr(service, "publish_approved_scene_visual_prompt_bundle", publish_after_voice_race)
        with pytest.raises(AppError) as exc_info:
            service.approve(
                project_id,
                {
                    "expected_scene_plan_artifact_id": imported["scene_plan_artifact_id"],
                    "expected_visual_plan_artifact_id": imported["visual_plan_artifact_id"],
                    "aspect_ratio": "16:9",
                },
            )
        assert exc_info.value.error_code == "SCENE_BREAKDOWN_VOICE_STALE"

    with factory() as session:
        project = session.get(Project, project_id)
        assert project is not None
        assert (project.narration_audio_artifact_id, project.narration_word_timing_map_artifact_id) == (
            raced_voice["audio"],
            raced_voice["timing"],
        )
        assert (
            project.scene_plan_artifact_id,
            project.visual_plan_artifact_id,
            project.prompt_bundle_artifact_id,
            project.beat_timing_map_artifact_id,
        ) == old_scene_ids
        assert {field: getattr(project, field) for field in downstream} == downstream
        for artifact_id in old_scene_ids:
            row = session.get(Artifact, artifact_id)
            assert row is not None and row.is_current is True
        assert set(candidate_ids) == {"scene", "visual", "prompt", "timing"}
        for artifact_id in candidate_ids.values():
            assert session.get(Artifact, artifact_id) is None


def test_real_assembly_compile_uses_beat_timing_for_visuals_and_word_timing_for_subtitles() -> None:
    seeded = generate_combined_pair(approve=True)
    project_id = str(seeded["project_id"])
    factory = _factory()
    with factory() as session:
        project = session.get(Project, project_id)
        assert project is not None
        artifacts = ArtifactService(session, ArtifactRepository(session))
        visual_plan = _json_artifact(artifacts, str(project.visual_plan_artifact_id))
        prompt_bundle = _json_artifact(artifacts, str(project.prompt_bundle_artifact_id))
        word_timing = _json_artifact(artifacts, str(project.narration_word_timing_map_artifact_id))
        beat_timing = _json_artifact(artifacts, str(project.beat_timing_map_artifact_id))
        prompt_by_beat = {str(item["beat_id"]): item for item in prompt_bundle["prompt_items"]}
        workspace = SceneAssetWorkspaceV2Service(session)
        candidates: dict[str, SceneAssetCandidate] = {}
        now = utc_now_iso()

        for visual_scene in visual_plan["scenes"]:
            for visual_beat in visual_scene["beat_plans"]:
                beat_id = str(visual_beat["beat_id"])
                prompt_item = prompt_by_beat[beat_id]
                prompt_revision = session.scalar(
                    select(SceneAssetPromptRevision).where(
                        SceneAssetPromptRevision.project_id == project_id,
                        SceneAssetPromptRevision.beat_id == beat_id,
                        SceneAssetPromptRevision.is_current.is_(True),
                    )
                )
                assert prompt_revision is not None
                assert prompt_revision.source_prompt_bundle_artifact_id == project.prompt_bundle_artifact_id
                assert prompt_revision.source_prompt_item_id == prompt_item["prompt_item_id"]
                assert prompt_revision.source_prompt_hash == prompt_item["prompt_hash"]
                prompt_revision_id = prompt_revision.prompt_revision_id
                image = artifacts.write_artifact(
                    logical_id=f"project/{project_id}/task4-review/images/{beat_id}",
                    artifact_type=ArtifactType.IMAGE,
                    filename=f"{beat_id}.png",
                    content=f"task4-review-image-{beat_id}".encode("utf-8"),
                    provider_source="task4_review_gate",
                )
                image.artifact.width = 1280
                image.artifact.height = 720
                candidate = SceneAssetCandidate(
                    candidate_id=generate_id("scene_asset_candidate"),
                    project_id=project_id,
                    scene_id=str(prompt_item["scene_id"]),
                    beat_id=beat_id,
                    artifact_id=image.artifact_id,
                    source_kind="uploaded",
                    operation_beat_id=None,
                    operation_task_id=None,
                    source_prompt_revision_id=prompt_revision_id,
                    source_prompt_bundle_artifact_id=str(project.prompt_bundle_artifact_id),
                    source_prompt_item_id=str(prompt_item["prompt_item_id"]),
                    source_prompt_hash=str(prompt_item["prompt_hash"]),
                    continuity_snapshot_json=None,
                    created_at=now,
                )
                session.add(candidate)
                session.flush()
                state = workspace._ensure_state(project, prompt_item)
                state.current_draft_candidate_id = candidate.candidate_id
                state.selected_candidate_id = candidate.candidate_id
                state.resolution_status = "approved"
                state.review_status = "approved"
                state.active_operation_beat_id = None
                state.continuity_source_scene_id = None
                state.continuity_source_beat_id = None
                state.continuity_source_candidate_id = None
                state.last_error_json = None
                state.last_progress_at = now
                state.updated_at = now
                candidates[beat_id] = candidate
                session.flush()

        for prompt_item in prompt_bundle["prompt_items"]:
            if isinstance(prompt_item.get("continuity_reference_requirement"), dict):
                candidates[str(prompt_item["beat_id"])].continuity_snapshot_json = workspace._continuity_snapshot(
                    project,
                    prompt_item,
                    require_ready=True,
                )
        session.commit()

        published = workspace.publish_approved_manifest(project_id)
        assert published["status"] == "approved"
        assert published["already_current"] is False
        session.expire_all()
        project = session.get(Project, project_id)
        assert project is not None
        manifest = _json_artifact(artifacts, str(project.scene_asset_manifest_artifact_id))
        assert manifest["schema_version"] == "4.0"
        assert manifest["status"] == "approved"
        entries = flatten_manifest_entries(manifest)
        assert len(entries) == len(beat_timing["beats"])
        manifest_by_beat = {str(item["beat_id"]): item for item in entries}

        assembly = AssemblyService(session)
        request = {
            "expected_assembly_plan_artifact_id": project.assembly_plan_artifact_id,
            "expected_subtitle_srt_artifact_id": project.subtitle_srt_artifact_id,
            "expected_subtitle_vtt_artifact_id": project.subtitle_vtt_artifact_id,
            "expected_scene_plan_artifact_id": project.scene_plan_artifact_id,
            "expected_scene_asset_manifest_artifact_id": project.scene_asset_manifest_artifact_id,
            "expected_narration_audio_artifact_id": project.narration_audio_artifact_id,
            "expected_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
            "expected_beat_timing_map_artifact_id": project.beat_timing_map_artifact_id,
            "background_music": None,
            "sound_effects": [],
        }
        plan, _srt, _vtt = assembly.compile_plan(
            project=project,
            request=request,
            assembly_id=generate_id("artifact"),
            srt_id=generate_id("artifact"),
            vtt_id=generate_id("artifact"),
        )

        timing_by_beat = {str(item["beat_id"]): item for item in beat_timing["beats"]}
        assert plan["beats"][0]["start_time_units"] == 0
        for index, beat in enumerate(plan["beats"]):
            timing = timing_by_beat[str(beat["beat_id"])]
            assert beat["start_time_units"] == int(timing["visual_start_sample"]) * UNITS_PER_SECOND // CANONICAL_SAMPLE_RATE
            assert beat["end_time_units"] == int(timing["visual_end_sample"]) * UNITS_PER_SECOND // CANONICAL_SAMPLE_RATE
            assert beat["planned_duration_seconds"] == manifest_by_beat[str(beat["beat_id"])]["computed_duration_seconds"]
            if index:
                assert plan["beats"][index - 1]["end_time_units"] == beat["start_time_units"]
        total_samples = int(word_timing["total_sample_count"])
        total_units = total_samples * UNITS_PER_SECOND // CANONICAL_SAMPLE_RATE
        assert plan["beats"][-1]["end_time_units"] == total_units
        assert plan["total_duration_time_units"] == total_units
        assert plan["narration_track"]["sample_count"] == total_samples

        expected_cues, _warnings = compile_subtitle_cues(
            full_script=_json_artifact(artifacts, str(project.script_artifact_id))["full_script"],
            word_timing_map=word_timing,
            beat_timing_map=beat_timing,
        )
        assert plan["subtitle_cues"] == expected_cues
        first_word = word_timing["sections"][0]["words"][0]
        assert plan["subtitle_cues"][0]["start_time_units"] == int(first_word["global_start_sample"]) * UNITS_PER_SECOND // CANONICAL_SAMPLE_RATE
        assert plan["subtitle_cues"][0]["start_time_units"] != plan["beats"][0]["start_time_units"]


def test_voice_scene_style_invalidation_direction_preserves_authority_boundaries() -> None:
    seeded = generate_combined_pair(approve=True)
    project_id = str(seeded["project_id"])
    analysis_id = str(seeded["analysis_id"])
    factory = _factory()
    downstream_fields = (
        "scene_asset_manifest_artifact_id",
        "assembly_plan_artifact_id",
        "subtitle_srt_artifact_id",
        "subtitle_vtt_artifact_id",
        "final_video_artifact_id",
    )
    with factory() as session:
        project = session.get(Project, project_id)
        assert project is not None
        downstream = _stage_sentinels(session, project, downstream_fields)
        session.refresh(project)
        approved_scene = (
            project.scene_plan_artifact_id,
            project.visual_plan_artifact_id,
            project.prompt_bundle_artifact_id,
            project.beat_timing_map_artifact_id,
        )
        style_prerequisites = (
            project.style_bible_artifact_id,
            project.style_bible_visual_reference_set_artifact_id,
            project.style_bible_visual_reference_approval_set_artifact_id,
        )
        voice_a = (project.narration_audio_artifact_id, project.narration_word_timing_map_artifact_id)
        candidate = _stage_voice_candidate(session, project)
        session.refresh(project)
        assert (project.narration_audio_artifact_id, project.narration_word_timing_map_artifact_id) == voice_a
        assert (
            project.scene_plan_artifact_id,
            project.visual_plan_artifact_id,
            project.prompt_bundle_artifact_id,
            project.beat_timing_map_artifact_id,
        ) == approved_scene
        assert {field: getattr(project, field) for field in downstream} == downstream

        approved = DurableVoiceService(session).approve(
            project_id,
            {
                "candidate_narration_audio_artifact_id": candidate["audio_id"],
                "candidate_narration_word_timing_map_artifact_id": candidate["timing_id"],
                "expected_narration_audio_artifact_id": candidate["old_audio_id"],
                "expected_narration_word_timing_map_artifact_id": candidate["old_timing_id"],
            },
        )
        voice_b = (approved["narration_audio_artifact_id"], approved["narration_word_timing_map_artifact_id"])
        session.refresh(project)
        assert (project.narration_audio_artifact_id, project.narration_word_timing_map_artifact_id) == voice_b
        assert (
            project.scene_plan_artifact_id,
            project.visual_plan_artifact_id,
            project.prompt_bundle_artifact_id,
            project.beat_timing_map_artifact_id,
        ) == (None, None, None, None)
        assert {field: getattr(project, field) for field in downstream} == {field: None for field in downstream}
        assert (
            project.style_bible_artifact_id,
            project.style_bible_visual_reference_set_artifact_id,
            project.style_bible_visual_reference_approval_set_artifact_id,
        ) == style_prerequisites

        external = ExternalSceneBreakdownService(session)
        package = external.build_prompt(project_id)
        imported = external.import_result(project_id, external_import_payload(package, full_external_output()))
        session.expire_all()
        SceneBreakdownServiceV4(session).approve(
            project_id,
            {
                "expected_scene_plan_artifact_id": imported["scene_plan_artifact_id"],
                "expected_visual_plan_artifact_id": imported["visual_plan_artifact_id"],
                "aspect_ratio": "16:9",
            },
        )
        session.refresh(project)
        assert (project.narration_audio_artifact_id, project.narration_word_timing_map_artifact_id) == voice_b
        assert all(
            value is not None
            for value in (
                project.scene_plan_artifact_id,
                project.visual_plan_artifact_id,
                project.prompt_bundle_artifact_id,
                project.beat_timing_map_artifact_id,
            )
        )

        StyleBibleServiceV4(session).generate(project_id, analysis_id)
        session.refresh(project)
        assert (project.narration_audio_artifact_id, project.narration_word_timing_map_artifact_id) == voice_b
        assert project.scene_plan_artifact_id is None
        assert project.visual_plan_artifact_id is None
        assert project.prompt_bundle_artifact_id is None
        assert project.beat_timing_map_artifact_id is None
