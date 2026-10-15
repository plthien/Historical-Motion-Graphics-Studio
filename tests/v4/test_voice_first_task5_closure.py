from __future__ import annotations

import hashlib
import inspect
import json
from types import SimpleNamespace
from uuid import uuid4
import time

from sqlalchemy import select

from app.api.voice_schemas_v4 import VoiceGenerateRequest
from app.core.config import get_settings
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactType
from app.db.models import Channel, Project, VoiceGenerationOperation
from app.db.scene_asset_candidate import SceneAssetCandidate
from app.db.scene_asset_prompt_revision import SceneAssetPromptRevision
from app.db.session import build_session_factory
from app.modules.v4.assembly_service import AssemblyService
from app.modules.v4.assembly_subtitles import UNITS_PER_SECOND, compile_subtitle_cues
from app.modules.v4.durable_voice_service import DurableVoiceService
from app.modules.v4.scene_asset_manifest import flatten_manifest_entries
from app.modules.v4.scene_asset_workspace_v2 import SceneAssetWorkspaceV2Service
from app.modules.v4.scene_breakdown_external_service import ExternalSceneBreakdownService
from app.modules.v4.scene_breakdown_service import SceneBreakdownServiceV4
from app.modules.v4.script_word_budget import count_narration_words
from app.modules.v4.voice_audio import CANONICAL_SAMPLE_RATE, validate_canonical_wav_bytes
from app.modules.v4.voice_first_elevenlabs_adapter import VoiceFirstElevenLabsProviderAdapter
from app.modules.v4.voice_first_policy import ACTIVE_VOICE_PRESETS
from app.modules.v4.voice_provider_adapters import FakeImmediateProvider, KokoroProviderAdapter
from app.modules.v4.voice_provider_contracts import VoiceProviderRegistry
from app.modules.v4.voice_provider_registry import build_voice_provider_registry
from app.modules.v4.voice_service import VoiceService
from app.modules.v4.voice_task3 import VoiceTask3AlignmentService
from app.repositories import ArtifactRepository
from app.services.artifacts import ArtifactService
from app.voice_coordinator_runner import VoiceCoordinatorRunner
from tests.support.external_authoring import external_import_payload
from tests.v4.combined_scene_breakdown_support import _canonical_voice_wav, full_external_output, generate_combined_pair


def _factory():
    return build_session_factory(get_settings())


def _json_artifact(artifacts: ArtifactService, artifact_id: str) -> dict:
    row = artifacts.get_required(artifact_id)
    return json.loads(artifacts.resolve_file_path(row).read_text(encoding="utf-8"))


class _FakeVoiceFirstElevenLabs(FakeImmediateProvider):
    """External-provider boundary replacement for the full application smoke."""

    provider_key = "elevenlabs"

    def execution_identity(self, profile):
        _ = profile
        return {
            "schema_version": 1,
            "provider_key": self.provider_key,
            "test_runtime": "task5_full_flow",
            "endpoint": "/v1/text-to-speech/{voice_id}",
            "timing_authority": "forced_alignment_only",
        }


class _FakeAlignmentClient:
    def __init__(self, calls: list[dict[str, object]]) -> None:
        self.calls = calls

    def forced_alignment(self, *, audio_bytes: bytes, text: str):
        info = validate_canonical_wav_bytes(audio_bytes)
        assert text
        assert "<" not in text and ">" not in text
        duration = info.sample_count / CANONICAL_SAMPLE_RATE
        width = duration / len(text)
        characters = [
            {
                "text": char,
                "start": width * index,
                "end": width * (index + 1),
            }
            for index, char in enumerate(text)
        ]
        self.calls.append(
            {
                "audio_sha256": hashlib.sha256(audio_bytes).hexdigest(),
                "sample_count": info.sample_count,
                "text": text,
            }
        )
        return SimpleNamespace(payload={"characters": characters, "words": [], "loss": 0.0})

    def close(self) -> None:
        return None


def _publish_scene_asset_manifest(session, project: Project) -> dict:
    artifacts = ArtifactService(session, ArtifactRepository(session))
    visual_plan = _json_artifact(artifacts, str(project.visual_plan_artifact_id))
    prompt_bundle = _json_artifact(artifacts, str(project.prompt_bundle_artifact_id))
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
                    SceneAssetPromptRevision.project_id == project.project_id,
                    SceneAssetPromptRevision.beat_id == beat_id,
                    SceneAssetPromptRevision.is_current.is_(True),
                )
            )
            assert prompt_revision is not None
            assert prompt_revision.source_prompt_bundle_artifact_id == project.prompt_bundle_artifact_id
            assert prompt_revision.source_prompt_item_id == prompt_item["prompt_item_id"]
            assert prompt_revision.source_prompt_hash == prompt_item["prompt_hash"]
            image = artifacts.write_artifact(
                logical_id=f"project/{project.project_id}/task5-flow/images/{beat_id}",
                artifact_type=ArtifactType.IMAGE,
                filename=f"{beat_id}.png",
                content=f"task5-flow-image-{beat_id}".encode("utf-8"),
                provider_source="task5_fake_provider_flow",
            )
            image.artifact.width = 1280
            image.artifact.height = 720
            candidate = SceneAssetCandidate(
                candidate_id=generate_id("scene_asset_candidate"),
                project_id=project.project_id,
                scene_id=str(prompt_item["scene_id"]),
                beat_id=beat_id,
                artifact_id=image.artifact_id,
                source_kind="uploaded",
                operation_beat_id=None,
                operation_task_id=None,
                source_prompt_revision_id=prompt_revision.prompt_revision_id,
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
    published = workspace.publish_approved_manifest(project.project_id)
    assert published["status"] == "approved"
    session.expire_all()
    refreshed = session.get(Project, project.project_id)
    assert refreshed is not None and refreshed.scene_asset_manifest_artifact_id
    manifest = _json_artifact(artifacts, str(refreshed.scene_asset_manifest_artifact_id))
    assert manifest["schema_version"] == "4.0"
    return manifest


def test_active_production_architecture_has_one_voice_and_timing_path() -> None:
    registry = build_voice_provider_registry(get_settings())
    assert registry.provider_keys() == ("elevenlabs", "kokoro_local")
    adapter = registry.resolve("elevenlabs")
    assert isinstance(adapter, VoiceFirstElevenLabsProviderAdapter)
    assert isinstance(registry.resolve("kokoro_local"), KokoroProviderAdapter)
    capabilities = adapter.capabilities()
    assert capabilities.supports_word_timing is False
    assert capabilities.supports_character_timing is False
    assert ACTIVE_VOICE_PRESETS == {
        "economy": "eleven_flash_v2_5",
        "quality_cloned_voice": "eleven_multilingual_v2",
    }

    public_fields = set(VoiceGenerateRequest.model_fields)
    assert "voice_preset" in public_fields
    assert "generation_provider" in public_fields
    assert "generation_voice_id" in public_fields
    assert not {"provider", "model_id", "voice_id", "scene_plan_artifact_id"} & public_fields

    load_sources = inspect.getsource(VoiceService._load_sources)
    assert "load_script" in load_sources
    assert "scene_plan_artifact_id" not in load_sources
    assert "source_scene_plan_revision_id" not in load_sources

    generation = inspect.getsource(DurableVoiceService.submit_generation)
    assert "generation_provider" in generation
    assert "KokoroProviderAdapter" in generation
    assert "ACTIVE_VOICE_PRESETS" in generation
    assert "narration_timing_map_artifact_id" not in generation
    assert "weighted_text_alignment" not in generation
    assert "with-timestamps" not in generation

    alignment = inspect.getsource(VoiceTask3AlignmentService.reconcile_operation)
    assert "forced_alignment" in alignment
    assert "validate_canonical_wav_bytes" in alignment
    assert "weighted_text_alignment" not in alignment
    assert "provider_timing_json" not in alignment
    assert "with-timestamps" not in alignment

    assembly = inspect.getsource(AssemblyService.compile_plan)
    assert 'beat_timing["visual_start_sample"]' in assembly
    assert 'beat_timing["visual_end_sample"]' in assembly
    assert "source_narration_word_timing_map_artifact_id" in assembly
    assert "source_beat_timing_map_artifact_id" in assembly
    assert "narration_timing_map_artifact_id" not in assembly
    assert "weighted_text_alignment" not in assembly

    subtitles = inspect.getsource(compile_subtitle_cues)
    assert "_flatten_word_timing" in subtitles
    flatten_words = inspect.getsource(compile_subtitle_cues.__globals__["_flatten_word_timing"])
    assert "global_start_sample" in flatten_words
    assert "global_end_sample" in flatten_words
    assert "weighted_text_alignment" not in subtitles
    assert "weighted_text_alignment" not in flatten_words


def test_full_fake_provider_application_flow_preserves_exact_voice_scene_and_assembly_lineage(monkeypatch) -> None:
    seeded = generate_combined_pair(approve=True)
    project_id = str(seeded["project_id"])
    factory = _factory()
    provider = _FakeVoiceFirstElevenLabs()
    registry = VoiceProviderRegistry()
    registry.register(provider)
    alignment_calls: list[dict[str, object]] = []

    with factory() as session:
        project = session.get(Project, project_id)
        assert project is not None
        channel = session.get(Channel, project.channel_id)
        assert channel is not None
        channel.voice_profile_json = {
            "schema_version": 1,
            "provider": "elevenlabs",
            "voice_id": "fake_voice",
            "voice_kind": "professional",
            "default_preset": "economy",
            "calibration": {
                "economy": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                    "use_speaker_boost": True,
                },
                "quality_cloned_voice": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                    "use_speaker_boost": True,
                },
            },
        }
        old_voice = (
            str(project.narration_audio_artifact_id),
            str(project.narration_word_timing_map_artifact_id),
        )
        old_scene = (
            str(project.scene_plan_artifact_id),
            str(project.visual_plan_artifact_id),
            str(project.prompt_bundle_artifact_id),
            str(project.beat_timing_map_artifact_id),
        )
        service = DurableVoiceService(session)
        service.tts_settings.registry = registry
        submitted = service.submit_generation(
            project_id,
            {
                "operation_type": "generate_all",
                "generation_provider": "elevenlabs",
                "voice_preset": "economy",
                "expected_narration_audio_artifact_id": old_voice[0],
                "expected_narration_word_timing_map_artifact_id": old_voice[1],
            },
        )
        operation_id = str(submitted["operation_id"])
        operation = session.get(VoiceGenerationOperation, operation_id)
        assert operation is not None
        planned_average_wpm = int(operation.request_json["script_section_pacing"]["planned_average_wpm"])
        session.refresh(project)
        assert (
            str(project.narration_audio_artifact_id),
            str(project.narration_word_timing_map_artifact_id),
        ) == old_voice
        assert (
            str(project.scene_plan_artifact_id),
            str(project.visual_plan_artifact_id),
            str(project.prompt_bundle_artifact_id),
            str(project.beat_timing_map_artifact_id),
        ) == old_scene

    def pacing_compatible_fake_wav(text: str, *, speaking_rate: float = 1.0) -> bytes:
        word_count = count_narration_words(text)
        assert word_count > 0
        target_wpm = planned_average_wpm * float(speaking_rate)
        sample_count = max(
            1,
            round(word_count * 60.0 * CANONICAL_SAMPLE_RATE / target_wpm),
        )
        return _canonical_voice_wav(sample_count)

    monkeypatch.setattr(
        "app.modules.v4.voice_provider_adapters.deterministic_fake_wav",
        pacing_compatible_fake_wav,
    )

    runner = VoiceCoordinatorRunner(factory, registry=registry, provider_pool_workers=1)
    runner.task3 = VoiceTask3AlignmentService(
        factory,
        client_factory=lambda: _FakeAlignmentClient(alignment_calls),
    )
    try:
        deadline = time.monotonic() + 20.0
        operation_status = None
        while time.monotonic() < deadline:
            runner.run_once()
            with factory() as session:
                operation = session.get(VoiceGenerationOperation, operation_id)
                assert operation is not None
                operation_status = operation.status
                if operation_status in {"completed", "failed", "superseded", "cancelled"}:
                    break
            time.sleep(0.02)
    finally:
        runner.close()
    with factory() as session:
        operation = session.get(VoiceGenerationOperation, operation_id)
        assert operation is not None
        operation_problem = (
            operation.error_code,
            operation.error_message,
            operation.error_details_json,
        )
    assert operation_status == "completed", operation_problem
    assert alignment_calls

    with factory() as session:
        project = session.get(Project, project_id)
        operation = session.get(VoiceGenerationOperation, operation_id)
        assert project is not None and operation is not None
        candidate = operation.result_json["draft_candidate"]
        assert candidate["reviewable"] is True
        assert (
            str(project.narration_audio_artifact_id),
            str(project.narration_word_timing_map_artifact_id),
        ) == old_voice
        assert (
            str(project.scene_plan_artifact_id),
            str(project.visual_plan_artifact_id),
            str(project.prompt_bundle_artifact_id),
            str(project.beat_timing_map_artifact_id),
        ) == old_scene

        artifacts = ArtifactService(session, ArtifactRepository(session))
        candidate_audio = artifacts.get_required(str(candidate["narration_audio_artifact_id"]))
        candidate_audio_bytes = artifacts.resolve_file_path(candidate_audio).read_bytes()
        candidate_audio_info = validate_canonical_wav_bytes(candidate_audio_bytes)
        candidate_word_timing = _json_artifact(
            artifacts,
            str(candidate["narration_word_timing_map_artifact_id"]),
        )
        assert candidate_word_timing["source_narration_audio_artifact_id"] == candidate_audio.artifact_id
        assert candidate_word_timing["total_sample_count"] == candidate_audio_info.sample_count
        assert "scenes" not in candidate_word_timing
        assert "beats" not in candidate_word_timing
        aligned_sections = {str(item["audio_sha256"]): item for item in alignment_calls}
        for section in candidate_word_timing["sections"]:
            call = aligned_sections[str(section["section_audio_sha256"])]
            assert call["text"] == section["canonical_spoken_text"]
            assert "<" not in str(call["text"]) and ">" not in str(call["text"])

        approved = DurableVoiceService(session).approve(
            project_id,
            {
                "candidate_narration_audio_artifact_id": candidate["narration_audio_artifact_id"],
                "candidate_narration_word_timing_map_artifact_id": candidate["narration_word_timing_map_artifact_id"],
                "expected_narration_audio_artifact_id": old_voice[0],
                "expected_narration_word_timing_map_artifact_id": old_voice[1],
            },
        )
        session.refresh(project)
        new_voice = (
            str(approved["narration_audio_artifact_id"]),
            str(approved["narration_word_timing_map_artifact_id"]),
        )
        assert new_voice == (
            str(candidate["narration_audio_artifact_id"]),
            str(candidate["narration_word_timing_map_artifact_id"]),
        )
        assert new_voice != old_voice
        assert (
            project.scene_plan_artifact_id,
            project.visual_plan_artifact_id,
            project.prompt_bundle_artifact_id,
            project.beat_timing_map_artifact_id,
        ) == (None, None, None, None)

        external = ExternalSceneBreakdownService(session)
        package = external.build_prompt(project_id)
        reservation = external.reservations.pending_for(project_id=project_id, stage="scene_breakdown")
        assert reservation is not None
        reservation_source = external.reservations.source(reservation)
        assert reservation_source["narration_audio_artifact_id"] == new_voice[0]
        assert reservation_source["narration_word_timing_map_artifact_id"] == new_voice[1]
        imported = external.import_result(
            project_id,
            external_import_payload(package, full_external_output()),
        )
        # External import and Scene approval are separate application request boundaries.
        # Reload ORM state so approval observes the CAS-published draft pair rather than
        # the Project identity that was loaded before import_result committed it.
        session.expire_all()
        scene_result = SceneBreakdownServiceV4(session).approve(
            project_id,
            {
                "expected_scene_plan_artifact_id": imported["scene_plan_artifact_id"],
                "expected_visual_plan_artifact_id": imported["visual_plan_artifact_id"],
                "aspect_ratio": "16:9",
            },
        )
        session.refresh(project)
        scene_ids = (
            str(project.scene_plan_artifact_id),
            str(project.visual_plan_artifact_id),
            str(project.prompt_bundle_artifact_id),
            str(project.beat_timing_map_artifact_id),
        )
        assert all(scene_ids)
        # Scene approval response owns the approved Scene/Visual revision pair.
        # PromptBundle and BeatTimingMap are atomically published downstream pointers
        # and are verified from Project + artifact lineage below.
        assert scene_ids[:2] == (
            str(scene_result["scene_plan_artifact_id"]),
            str(scene_result["visual_plan_artifact_id"]),
        )
        beat_timing = _json_artifact(artifacts, scene_ids[3])
        assert beat_timing["source_scene_plan_revision_id"] == scene_ids[0]
        assert beat_timing["source_narration_audio_artifact_id"] == new_voice[0]
        assert beat_timing["source_narration_word_timing_map_artifact_id"] == new_voice[1]

        manifest = _publish_scene_asset_manifest(session, project)
        session.expire_all()
        project = session.get(Project, project_id)
        assert project is not None
        assert len(flatten_manifest_entries(manifest)) == len(beat_timing["beats"])
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
        plan, _srt, _vtt = AssemblyService(session).compile_plan(
            project=project,
            request=request,
            assembly_id=f"assembly_{uuid4().hex}",
            srt_id=f"subtitle_srt_{uuid4().hex}",
            vtt_id=f"subtitle_vtt_{uuid4().hex}",
        )
        assert plan["source_script_artifact_id"] == str(project.script_artifact_id)
        assert plan["source_scene_plan_artifact_id"] == str(project.scene_plan_artifact_id)
        assert plan["source_narration_audio_artifact_id"] == new_voice[0]
        assert plan["source_narration_word_timing_map_artifact_id"] == new_voice[1]
        assert plan["source_beat_timing_map_artifact_id"] == str(project.beat_timing_map_artifact_id)

        timing_by_beat = {str(item["beat_id"]): item for item in beat_timing["beats"]}
        for beat in plan["beats"]:
            source = timing_by_beat[str(beat["beat_id"])]
            assert beat["start_time_units"] == int(source["visual_start_sample"]) * UNITS_PER_SECOND // CANONICAL_SAMPLE_RATE
            assert beat["end_time_units"] == int(source["visual_end_sample"]) * UNITS_PER_SECOND // CANONICAL_SAMPLE_RATE

        expected_cues, _warnings = compile_subtitle_cues(
            full_script=_json_artifact(artifacts, str(project.script_artifact_id))["full_script"],
            word_timing_map=candidate_word_timing,
            beat_timing_map=beat_timing,
        )
        assert plan["subtitle_cues"] == expected_cues
        assert plan["narration_track"]["sample_count"] == candidate_word_timing["total_sample_count"]
