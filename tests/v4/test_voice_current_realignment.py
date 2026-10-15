from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Channel, Project
from app.modules.v4.voice_current_realignment import VoiceCurrentRealignmentService
from app.repositories import ProjectRepository, VoiceRealignmentCommitGuard


def _character_timing(text: str) -> list[dict]:
    return [
        {
            "character_index": index,
            "text": character,
            "spoken_start": index,
            "spoken_end": index + 1,
            "start_sample": index * 100,
            "end_sample": (index + 1) * 100,
        }
        for index, character in enumerate(text)
    ]


def test_realign_current_builds_new_timing_for_the_same_audio() -> None:
    transcript = "Alpha beta"
    snapshot = {
        "script_artifact_id": "artifact_script",
        "audio_artifact_id": "artifact_audio",
        "total_sample_count": 1_000,
        "canonical_spoken_narration": transcript,
        "spoken_compilation_sha256": "a" * 64,
        "generation_provider": "kokoro_local",
        "generation_voice_id": "af_heart",
        "sections": [
            {
                "section_id": "sec_001",
                "section_order": 1,
                "authored_narration": transcript,
                "spoken_transcript": transcript,
                "spoken_transcript_sha256": "b" * 64,
                "authored_spoken_mapping": [],
                "section_audio_artifact_id": "artifact_section_audio",
                "section_audio_sha256": "c" * 64,
                "sample_count": 1_000,
                "final_section_identity_sha256": "d" * 64,
            }
        ],
    }
    timing = VoiceCurrentRealignmentService._build_timing_map(
        snapshot=snapshot,
        alignments=[
            {
                "characters": _character_timing(transcript),
                "loss": {"provider_loss": 0.01},
            }
        ],
    )

    assert timing["source_narration_audio_artifact_id"] == "artifact_audio"
    assert timing["generation_provider"] == "kokoro_local"
    assert timing["generation_voice_id"] == "af_heart"
    assert timing["total_sample_count"] == 1_000
    assert [word["text"] for word in timing["sections"][0]["words"]] == [
        "Alpha",
        "beta",
    ]
    assert all(
        word["global_end_sample"] > word["global_start_sample"]
        for word in timing["sections"][0]["words"]
    )


def test_realign_current_calls_alignment_only_and_never_tts() -> None:
    calls: list[tuple[bytes, str]] = []

    class Client:
        def forced_alignment(self, *, audio_bytes: bytes, text: str):
            calls.append((audio_bytes, text))
            characters = [
                {
                    "text": character,
                    "start": index / 100,
                    "end": (index + 1) / 100,
                }
                for index, character in enumerate(text)
            ]
            return SimpleNamespace(payload={"characters": characters, "loss": 0.1})

        def close(self) -> None:
            return None

    owner = VoiceCurrentRealignmentService(
        lambda: None, client_factory=lambda: Client()  # type: ignore[arg-type]
    )
    result = owner._align_sections(
        [
            {
                "section_id": "sec_001",
                "audio_bytes": b"existing-approved-section-audio",
                "spoken_transcript": "Alpha",
                "sample_count": 10_000,
            }
        ]
    )

    assert calls == [(b"existing-approved-section-audio", "Alpha")]
    assert result[0]["characters"]


def test_realignment_project_cas_preserves_visual_authorities_and_invalidates_only_assembly(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'realign.sqlite3'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Channel(
                channel_id="channel_realign",
                name="Realign",
                language="en",
                niche="test",
                audience="test",
                channel_context_json={},
                channel_dna_json={},
                voice_profile_json=None,
                created_at="2026-10-07T00:00:00+00:00",
                updated_at="2026-10-07T00:00:00+00:00",
            )
        )
        project = Project(
            project_id="project_realign",
            channel_id="channel_realign",
            name="Realign",
            description=None,
            channel_context_json={},
            channel_dna_json={},
            script_artifact_id="script",
            narration_audio_artifact_id="audio",
            narration_word_timing_map_artifact_id="timing_old",
            scene_plan_artifact_id="scene",
            visual_plan_artifact_id="visual",
            prompt_bundle_artifact_id="prompt",
            scene_asset_manifest_artifact_id="assets",
            beat_timing_map_artifact_id="beat_old",
            assembly_plan_artifact_id="assembly",
            subtitle_srt_artifact_id="srt",
            subtitle_vtt_artifact_id="vtt",
            final_video_artifact_id="video",
            created_at="2026-10-07T00:00:00+00:00",
            updated_at="2026-10-07T00:00:00+00:00",
        )
        session.add(project)
        session.commit()

        guard = VoiceRealignmentCommitGuard(
            script_artifact_id="script",
            narration_audio_artifact_id="audio",
            previous_narration_word_timing_map_artifact_id="timing_old",
            scene_plan_artifact_id="scene",
            visual_plan_artifact_id="visual",
            prompt_bundle_artifact_id="prompt",
            scene_asset_manifest_artifact_id="assets",
            previous_beat_timing_map_artifact_id="beat_old",
            previous_assembly_plan_artifact_id="assembly",
            previous_subtitle_srt_artifact_id="srt",
            previous_subtitle_vtt_artifact_id="vtt",
            previous_final_video_artifact_id="video",
        )
        assert ProjectRepository(session).compare_and_set_voice_realignment(
            project_id=project.project_id,
            new_narration_word_timing_map_artifact_id="timing_new",
            new_beat_timing_map_artifact_id="beat_new",
            guard=guard,
            updated_at="2026-10-07T00:01:00+00:00",
        )
        session.commit()
        session.refresh(project)

        assert project.narration_audio_artifact_id == "audio"
        assert project.narration_word_timing_map_artifact_id == "timing_new"
        assert project.beat_timing_map_artifact_id == "beat_new"
        assert project.scene_plan_artifact_id == "scene"
        assert project.visual_plan_artifact_id == "visual"
        assert project.prompt_bundle_artifact_id == "prompt"
        assert project.scene_asset_manifest_artifact_id == "assets"
        assert project.assembly_plan_artifact_id is None
        assert project.subtitle_srt_artifact_id is None
        assert project.subtitle_vtt_artifact_id is None
        assert project.final_video_artifact_id is None


def test_temporary_realign_flow_is_exposed_in_api_client_and_ui() -> None:
    route_source = Path("app/api/routes/production_v4.py").read_text(encoding="utf-8")
    client_source = Path("src/api/client.ts").read_text(encoding="utf-8")
    panel_source = Path("src/features/studio/VoicePanel.tsx").read_text(encoding="utf-8")

    assert "/projects/{project_id}/voice/realign-current" in route_source
    assert "realignCurrentVoice" in client_source
    assert "Repair Timing (Reuse Audio)" in panel_source
    realign_mutation = panel_source.split("const realignCurrent = useMutation", 1)[1].split(
        "const operationActive", 1
    )[0]
    assert "['assembly-workspace', projectId]" in realign_mutation
    assert "['render-workspace', projectId]" in realign_mutation
