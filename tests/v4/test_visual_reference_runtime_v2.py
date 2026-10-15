from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import AppError
from app.db import models as db_models  # noqa: F401
from app.db import scene_asset_working_models as _scene_asset_models  # noqa: F401
from app.db import visual_reference_working_models as _visual_reference_models  # noqa: F401
from app.db.base import Base
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, Channel, Job, MediaBatch, Project
from app.db.session import create_sqlalchemy_engine
from app.db.visual_reference_anchor_state import VisualReferenceAnchorState
from app.db.visual_reference_operation_anchor import VisualReferenceOperationAnchor
from app.db.visual_reference_operation_task import VisualReferenceOperationTask
from app.db.visual_reference_prompt_revision import VisualReferencePromptRevision
from app.db.visual_reference_working_operation import VisualReferenceWorkingOperation
from app.modules.v4.scene_asset_manifest import canonical_hash
from app.modules.v4.visual_reference_runtime_v2 import VisualReferenceRuntimeV2

NOW = "2026-08-18T03:00:00+00:00"


def _factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_sqlalchemy_engine(
        f"sqlite:///{(tmp_path / 'visual_reference_runtime.sqlite3').resolve()}"
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _artifact(artifact_id: str) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        logical_id=f"logical/{artifact_id}",
        version_no=1,
        is_current=True,
        superseded_by_artifact_id=None,
        artifact_type=ArtifactType.JSON,
        artifact_role=None,
        path=f"tests/{artifact_id}.json",
        content_hash=f"hash_{artifact_id}",
        sha256=f"sha_{artifact_id}",
        mime_type="application/json",
        size_bytes=1,
        width=None,
        height=None,
        duration_sec=None,
        provider_result_url=None,
        provider_source=None,
        source_job_id=None,
        status=ArtifactStatus.READY,
        created_at=NOW,
        deleted_at=None,
    )


def _seed_project(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        session.add(
            Channel(
                channel_id="channel_vr",
                name="VR",
                language="en",
                niche="test",
                audience="test",
                channel_dna_json={},
                channel_context_json={},
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add_all(
            [
                _artifact("artifact_script"),
                _artifact("artifact_analysis"),
                _artifact("artifact_style"),
            ]
        )
        session.flush()
        session.add(
            Project(
                project_id="project_vr",
                channel_id="channel_vr",
                name="Visual Reference Runtime",
                description=None,
                channel_context_json={},
                channel_dna_json={},
                script_artifact_id="artifact_script",
                script_analysis_artifact_id="artifact_analysis",
                style_bible_artifact_id="artifact_style",
                style_bible_visual_reference_set_artifact_id=None,
                style_bible_visual_reference_approval_set_artifact_id=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()


def _inventory() -> list[dict[str, object]]:
    return [
        {
            "anchor_type": "character",
            "anchor_id": "char_001",
            "source_definition_hash": "char_hash",
            "prompt": "Character prompt",
            "negative_prompt": "No text",
        },
        {
            "anchor_type": "location",
            "anchor_id": "loc_001",
            "source_definition_hash": "loc_hash",
            "prompt": "Location prompt",
            "negative_prompt": "No people",
        },
    ]


def _style() -> dict[str, object]:
    return {
        "visual_style_snapshot": {
            "entity_styles": {
                "character": {
                    "appearance": ["rough line"],
                    "avoid": ["clean vector"],
                },
                "location": {
                    "appearance": ["rough line"],
                    "avoid": ["clean vector"],
                },
            }
        }
    }


def _provider_snapshot() -> dict[str, object]:
    return {
        "provider": "glabs_webhook",
        "enabled": True,
        "base_url": "https://example.invalid",
        "api_key_configured": True,
        "default_image_model": "nano_banana_2",
        "default_image_aspect_ratio": "1:1",
        "default_upscale": [],
        "poll_interval_seconds": 4,
        "max_parallel_tasks": 3,
        "enabled_media_kinds": ["image"],
    }


def _runtime(session: Session, monkeypatch: pytest.MonkeyPatch) -> VisualReferenceRuntimeV2:
    runtime = VisualReferenceRuntimeV2(session)
    # Do not query through this Session before submit_generation(): the runtime
    # intentionally opens BEGIN IMMEDIATE as its first DB action.
    project = SimpleNamespace(
        project_id="project_vr",
        script_artifact_id="artifact_script",
        script_analysis_artifact_id="artifact_analysis",
        style_bible_artifact_id="artifact_style",
        style_bible_visual_reference_set_artifact_id=None,
        style_bible_visual_reference_approval_set_artifact_id=None,
    )
    monkeypatch.setattr(
        runtime,
        "_ready_sources",
        lambda project_id, style_bible_artifact_id: (project, _style(), _inventory()),
    )
    monkeypatch.setattr(runtime, "_provider_snapshot", _provider_snapshot)
    return runtime


def test_full_generation_creates_only_durable_working_state_and_claims_anchors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory(tmp_path)
    _seed_project(factory)
    with factory() as session:
        result = _runtime(session, monkeypatch).submit_generation(
            "project_vr",
            {
                "style_bible_artifact_id": "artifact_style",
                "expected_visual_reference_set_artifact_id": None,
                "candidate_count": 2,
            },
        )
        assert result["status"] == "queued"
        assert result["total_anchor_count"] == 2

    with factory() as session:
        operation = session.scalar(select(VisualReferenceWorkingOperation))
        assert operation is not None
        assert operation.operation_type == "generate_all"
        assert operation.total_anchor_count == 2
        assert (
            canonical_hash(operation.provider_settings_snapshot_json)
            == operation.provider_settings_snapshot_hash
        )
        anchors = list(session.scalars(select(VisualReferenceOperationAnchor)))
        tasks = list(session.scalars(select(VisualReferenceOperationTask)))
        states = list(session.scalars(select(VisualReferenceAnchorState)))
        revisions = list(session.scalars(select(VisualReferencePromptRevision)))
        assert len(anchors) == 2
        assert len(tasks) == 4
        assert len(states) == 2
        assert len(revisions) == 2
        assert all(state.active_operation_anchor_id is not None for state in states)
        assert session.scalar(select(func.count()).select_from(Job)) == 0
        assert session.scalar(select(func.count()).select_from(MediaBatch)) == 0


def test_exact_regeneration_schedules_only_requested_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory(tmp_path)
    _seed_project(factory)
    with factory() as session:
        runtime = _runtime(session, monkeypatch)
        for item in _inventory():
            runtime.prompt_revisions.ensure_compiled(
                project_id="project_vr",
                style_bible_artifact_id="artifact_style",
                anchor=item,
                now_iso=NOW,
            )
        session.commit()

    with factory() as session:
        result = _runtime(session, monkeypatch).submit_anchor_regeneration(
            "project_vr",
            "character",
            "char_001",
            {
                "style_bible_artifact_id": "artifact_style",
                "expected_visual_reference_set_artifact_id": None,
                "candidate_count": 1,
            },
        )
        assert result["scheduled_anchors"] == [
            {"anchor_type": "character", "anchor_id": "char_001"}
        ]

    with factory() as session:
        anchors = list(session.scalars(select(VisualReferenceOperationAnchor)))
        assert len(anchors) == 1
        assert anchors[0].anchor_id == "char_001"
        location_state = session.get(
            VisualReferenceAnchorState,
            ("project_vr", "location", "loc_001"),
        )
        assert location_state is not None
        assert location_state.active_operation_anchor_id is None


def test_exact_regeneration_rejects_busy_anchor_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory(tmp_path)
    _seed_project(factory)
    with factory() as session:
        first = _runtime(session, monkeypatch).submit_anchor_regeneration(
            "project_vr",
            "character",
            "char_001",
            {
                "style_bible_artifact_id": "artifact_style",
                "expected_visual_reference_set_artifact_id": None,
                "candidate_count": 1,
            },
        )
        assert first["status"] == "queued"

    with factory() as session:
        runtime = _runtime(session, monkeypatch)
        with pytest.raises(AppError) as raised:
            runtime.submit_anchor_regeneration(
                "project_vr",
                "character",
                "char_001",
                {
                    "style_bible_artifact_id": "artifact_style",
                    "expected_visual_reference_set_artifact_id": None,
                    "candidate_count": 1,
                },
            )
        assert raised.value.error_code == "VISUAL_REFERENCE_V2_ANCHOR_BUSY"
        session.rollback()

    with factory() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(VisualReferenceWorkingOperation)
            )
            == 1
        )


def test_retry_failed_only_schedules_failed_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory(tmp_path)
    _seed_project(factory)
    with factory() as session:
        runtime = _runtime(session, monkeypatch)
        for item in _inventory():
            runtime.prompt_revisions.ensure_compiled(
                project_id="project_vr",
                style_bible_artifact_id="artifact_style",
                anchor=item,
                now_iso=NOW,
            )
        session.flush()
        failed = session.get(
            VisualReferenceAnchorState,
            ("project_vr", "character", "char_001"),
        )
        ready = session.get(
            VisualReferenceAnchorState,
            ("project_vr", "location", "loc_001"),
        )
        assert failed is not None and ready is not None
        failed.resolution_status = "failed"
        ready.resolution_status = "candidate_ready"
        ready.review_status = "unreviewed"
        session.commit()

    with factory() as session:
        result = _runtime(session, monkeypatch).submit_retry_failed(
            "project_vr",
            {
                "style_bible_artifact_id": "artifact_style",
                "expected_visual_reference_set_artifact_id": None,
                "candidate_count": 1,
            },
        )
        assert result["scheduled_anchors"] == [
            {"anchor_type": "character", "anchor_id": "char_001"}
        ]
