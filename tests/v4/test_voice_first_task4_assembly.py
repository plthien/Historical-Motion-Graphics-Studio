from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.api.assembly_schemas_v4 import AssemblyBuildRequest, AssemblyPlanModel, SubtitleCueModel
from app.db.base import Base
from app.db.enums import ArtifactRole, ArtifactStatus, ArtifactType
from app.db.models import Artifact
from app.modules.v4.assembly_subtitles import compile_subtitle_cues, samples_to_units
from app.repositories import ArtifactRepository, AssemblyOutputSetCommitGuard


def _word_map() -> dict:
    return {
        "schema_version": "narration_word_timing_map_v1",
        "source_script_artifact_id": "script_current",
        "source_narration_audio_artifact_id": "audio_current",
        "sample_rate_hz": 48_000,
        "total_sample_count": 4_800,
        "sections": [{
            "section_id": "sec_001",
            "words": [
                {"section_word_index": 0, "authored_start": 0, "authored_end": 5, "global_start_sample": 480, "global_end_sample": 960},
                {"section_word_index": 1, "authored_start": 6, "authored_end": 10, "global_start_sample": 1_200, "global_end_sample": 1_680},
                {"section_word_index": 2, "authored_start": 11, "authored_end": 16, "global_start_sample": 2_400, "global_end_sample": 2_880},
            ],
        }],
    }


def _beat_map() -> dict:
    return {
        "schema_version": "beat_timing_map_v1",
        "beats": [
            {"scene_id": "scene_001", "beat_id": "beat_001", "source_start": 0, "source_end": 10, "visual_start_sample": 0, "visual_end_sample": 2_400},
            {"scene_id": "scene_002", "beat_id": "beat_002", "source_start": 11, "source_end": 16, "visual_start_sample": 2_400, "visual_end_sample": 4_800},
        ],
    }


def test_subtitles_use_word_timing_samples_and_beat_timing_only_for_association() -> None:
    cues, warnings = compile_subtitle_cues(
        full_script="Alpha beta gamma",
        word_timing_map=_word_map(),
        beat_timing_map=_beat_map(),
    )
    assert warnings == []
    assert [word_id for cue in cues for word_id in cue["word_ids"]] == [
        "sec_001:word:0", "sec_001:word:1", "sec_001:word:2"
    ]
    assert cues[0]["start_time_units"] == samples_to_units(480, 48_000)
    assert cues[-1]["end_time_units"] == samples_to_units(2_880, 48_000)
    assert all(cue["segment_ids"] == [] for cue in cues)
    assert {beat_id for cue in cues for beat_id in cue["beat_ids"]} == {"beat_001", "beat_002"}


def test_subtitles_reject_legacy_flat_timing_shape() -> None:
    with pytest.raises(AppError) as exc:
        compile_subtitle_cues(
            full_script="Alpha",
            word_timing_map={"sample_rate_hz": 48_000, "total_sample_count": 480, "words": []},
            beat_timing_map=_beat_map(),
        )
    assert exc.value.error_code == "ASSEMBLY_WORD_TIMING_INVALID"


def test_subtitles_reject_zero_duration_word_with_actionable_details() -> None:
    malformed = _word_map()
    bad_word = malformed["sections"][0]["words"][1]
    bad_word["global_end_sample"] = bad_word["global_start_sample"]

    with pytest.raises(AppError) as exc_info:
        compile_subtitle_cues(
            full_script="Alpha beta gamma",
            word_timing_map=malformed,
            beat_timing_map=_beat_map(),
        )

    assert exc_info.value.error_code == "ASSEMBLY_WORD_TIMING_INVALID"
    assert exc_info.value.details == {
        "section_id": "sec_001",
        "section_word_index": 1,
        "authored_start": 6,
        "authored_end": 10,
        "global_start_sample": 1_200,
        "global_end_sample": 1_200,
        "total_sample_count": 4_800,
    }


def test_assembly_contracts_freeze_exact_word_timing_identity_and_allow_scene_free_segments() -> None:
    fields = AssemblyBuildRequest.model_fields
    assert "expected_narration_word_timing_map_artifact_id" in fields
    assert "source_narration_word_timing_map_artifact_id" in AssemblyPlanModel.model_fields
    cue = SubtitleCueModel.model_validate({
        "cue_id": "cue_0001", "cue_order": 1, "source_start": 0, "source_end": 5,
        "exact_text": "Alpha", "start_time_units": 10_000, "end_time_units": 20_000,
        "duration_time_units": 10_000, "start_seconds": 0.01, "end_seconds": 0.02,
        "duration_seconds": 0.01, "line_1": "Alpha", "line_2": None,
        "word_ids": ["sec_001:word:0"], "segment_ids": [], "scene_ids": ["scene_001"], "beat_ids": ["beat_001"],
    })
    assert cue.segment_ids == []
    guard_fields = AssemblyOutputSetCommitGuard.__dataclass_fields__
    assert "narration_word_timing_map_artifact_id" in guard_fields


def test_assembly_source_has_no_legacy_voice_timing_reader() -> None:
    source = Path("app/modules/v4/assembly_service.py").read_text(encoding="utf-8")
    assert "VoiceService" not in source
    assert "_load_current_pair" not in source
    assert "load_narration_word_timing_map" in source
    assert "load_beat_timing_map" in source
    assert "visual_start_sample" in source and "visual_end_sample" in source


def test_output_publication_repairs_orphaned_duplicate_current_revisions(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'assembly-current.sqlite3'}")
    Base.metadata.create_all(engine)
    logical_id = "project/project_assembly/assembly_plan"

    def revision(artifact_id: str, version_no: int, *, current: bool) -> Artifact:
        return Artifact(
            artifact_id=artifact_id,
            logical_id=logical_id,
            version_no=version_no,
            is_current=current,
            superseded_by_artifact_id=None,
            artifact_type=ArtifactType.JSON,
            artifact_role=ArtifactRole.PLAN_JSON,
            path=f"assembly/v{version_no}/assembly.json",
            content_hash=artifact_id,
            sha256="a" * 64,
            mime_type="application/json",
            size_bytes=2,
            status=ArtifactStatus.READY,
            created_at=f"2026-10-08T00:00:0{version_no}+00:00",
        )

    with Session(engine, expire_on_commit=False) as session:
        stale_a = revision("artifact_000000000001", 1, current=True)
        stale_b = revision("artifact_000000000002", 2, current=True)
        winner = revision("artifact_000000000003", 3, current=False)
        session.add_all([stale_a, stale_b, winner])
        session.commit()

        assert ArtifactRepository(session).promote_exact_current_artifact(
            logical_id=logical_id,
            new_artifact_id=winner.artifact_id,
        )
        session.commit()

        current = list(
            session.scalars(
                select(Artifact).where(
                    Artifact.logical_id == logical_id,
                    Artifact.is_current.is_(True),
                )
            )
        )
        assert [item.artifact_id for item in current] == [winner.artifact_id]
        assert winner.is_current is True
        assert stale_a.is_current is False
        assert stale_b.is_current is False
        assert stale_a.superseded_by_artifact_id == winner.artifact_id
        assert stale_b.superseded_by_artifact_id == winner.artifact_id
