from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import AppError
from app.db import models as db_models  # noqa: F401
from app.db import scene_asset_working_models as _scene_asset_models  # noqa: F401
from app.db import visual_reference_working_models as _visual_reference_models  # noqa: F401
from app.db.base import Base
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, Channel, MediaBatch, MediaTask, Project, Setting
from app.db.session import create_sqlalchemy_engine
from app.db.visual_reference_anchor_state import VisualReferenceAnchorState
from app.db.visual_reference_candidate import VisualReferenceCandidate
from app.db.visual_reference_operation_anchor import VisualReferenceOperationAnchor
from app.db.visual_reference_operation_task import VisualReferenceOperationTask
from app.db.visual_reference_prompt_revision import VisualReferencePromptRevision
from app.db.visual_reference_working_operation import VisualReferenceWorkingOperation
from app.modules.v4.scene_asset_manifest import canonical_hash
from app.modules.v4.visual_reference_coordinator import VisualReferenceCoordinator
from app.modules.v4.visual_reference_provider_driver import VisualReferenceDurableProviderExecutor
from app.modules.v4.visual_reference_provider_execution import VisualReferenceProviderExecutionService
from app.services.media_settings import SECRET_KEY, SETTINGS_KEY

NOW = "2026-08-18T02:00:00+00:00"


class PendingClient:
    def __init__(self) -> None:
        self.submit_payloads: list[dict[str, object]] = []

    def submit_image(self, payload: dict[str, object]) -> dict[str, object]:
        self.submit_payloads.append(dict(payload))
        return {"task_id": "vrdeadbeef", "status": "pending"}


class AmbiguousSubmitClient:
    def submit_image(self, payload: dict[str, object]) -> dict[str, object]:
        raise AppError(
            "MEDIA_PROVIDER_REQUEST_TIMEOUT",
            "Media provider request timed out.",
            504,
            retryable=True,
        )


def _factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_sqlalchemy_engine(f"sqlite:///{(tmp_path / 'visual_reference_provider.sqlite3').resolve()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _snapshot() -> dict[str, object]:
    return {
        "provider": "glabs_webhook",
        "default_image_model": "nano_banana_2",
        "default_image_aspect_ratio": "1:1",
        "default_upscale": [],
        "poll_interval_seconds": 4,
        "max_parallel_tasks": 3,
        "enabled_media_kinds": ["image"],
        "enabled": True,
    }


def _artifact(artifact_id: str, *, image: bool = False) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        logical_id=(
            f"project/project_vr/visual_references/test/{artifact_id}"
            if image
            else f"logical/{artifact_id}"
        ),
        version_no=1,
        is_current=True,
        superseded_by_artifact_id=None,
        artifact_type=ArtifactType.IMAGE if image else ArtifactType.JSON,
        artifact_role=None,
        path=f"tests/{artifact_id}.{'png' if image else 'json'}",
        content_hash=f"hash_{artifact_id}",
        sha256=("a" * 64 if image else f"sha_{artifact_id}"),
        mime_type="image/png" if image else "application/json",
        size_bytes=1,
        width=1024 if image else None,
        height=1024 if image else None,
        duration_sec=None,
        provider_result_url=None,
        provider_source=None,
        source_job_id=None,
        status=ArtifactStatus.READY,
        created_at=NOW,
        deleted_at=None,
    )


def _seed(
    factory: sessionmaker[Session],
    *,
    task_status: str = "queued",
    operation_status: str = "queued",
    unit_status: str | None = None,
) -> None:
    frozen = _snapshot()
    options = {"aspect_ratio": "1:1"}
    with factory() as session:
        session.add(Channel(
            channel_id="channel_vr",
            name="VR",
            language="en",
            niche="test",
            audience="test",
            channel_dna_json={},
            channel_context_json={},
            created_at=NOW,
            updated_at=NOW,
        ))
        session.add_all([_artifact("artifact_style"), _artifact("artifact_image", image=True)])
        session.add(Setting(key=SETTINGS_KEY, value_json={**frozen, "api_key_configured": True}, updated_at=NOW))
        session.add(Setting(key=SECRET_KEY, value_json={"glabs_webhook": "test-key"}, updated_at=NOW))
        session.flush()
        session.add(Project(
            project_id="project_vr",
            channel_id="channel_vr",
            name="Visual Reference V2",
            description=None,
            channel_context_json={},
            channel_dna_json={},
            style_bible_artifact_id="artifact_style",
            created_at=NOW,
            updated_at=NOW,
        ))
        session.flush()
        session.add(VisualReferencePromptRevision(
            prompt_revision_id="vr_prompt_1",
            project_id="project_vr",
            anchor_type="character",
            anchor_id="char_001",
            revision_no=1,
            revision_kind="compiled",
            is_current=1,
            source_style_bible_artifact_id="artifact_style",
            source_definition_hash="definition_hash",
            prompt="Primitive stickman ancient human portrait",
            negative_prompt="No text, no watermark",
            created_at=NOW,
        ))
        session.add(VisualReferenceWorkingOperation(
            operation_id="vr_operation_1",
            project_id="project_vr",
            operation_type="generate_anchor",
            status=operation_status,
            source_style_bible_artifact_id="artifact_style",
            expected_visual_reference_set_artifact_id=None,
            provider_settings_snapshot_json=frozen,
            provider_settings_snapshot_hash=canonical_hash(frozen),
            total_anchor_count=1,
            completed_count=0,
            failed_count=0,
            cancelled_count=0,
            next_reconcile_at=NOW,
            last_progress_at=NOW,
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
            created_at=NOW,
            updated_at=NOW,
            completed_at=None,
        ))
        session.flush()
        resolved_unit_status = unit_status or ("queued" if task_status == "queued" else "running")
        session.add(VisualReferenceOperationAnchor(
            operation_anchor_id="vr_anchor_op_1",
            operation_id="vr_operation_1",
            project_id="project_vr",
            anchor_type="character",
            anchor_id="char_001",
            version=1,
            status=resolved_unit_status,
            candidate_count=1,
            prompt_revision_id="vr_prompt_1",
            prompt="Primitive stickman ancient human portrait",
            negative_prompt="No text, no watermark",
            generation_options_json=options,
            generation_options_hash=canonical_hash(options),
            source_definition_hash="definition_hash",
            error_code=None,
            error_message=None,
            error_details_json=None,
            created_at=NOW,
            updated_at=NOW,
            completed_at=None,
        ))
        session.add(VisualReferenceOperationTask(
            operation_task_id="vr_task_1",
            operation_anchor_id="vr_anchor_op_1",
            candidate_count=1,
            candidate_index=1,
            status=task_status,
            attempt_no=1,
            media_task_id=None,
            submit_attempt_token=None,
            submit_started_at=None,
            next_reconcile_at=NOW,
            error_code=None,
            error_message=None,
            error_details_json=None,
            created_at=NOW,
            updated_at=NOW,
            completed_at=None,
        ))
        session.flush()
        session.add(VisualReferenceAnchorState(
            project_id="project_vr",
            anchor_type="character",
            anchor_id="char_001",
            version=1,
            resolution_status="not_generated",
            review_status="unreviewed",
            current_prompt_revision_id="vr_prompt_1",
            selected_candidate_id=None,
            approved_candidate_id=None,
            active_operation_anchor_id="vr_anchor_op_1",
            last_error_json=None,
            last_progress_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        ))
        session.commit()


def _link_completed_transport(factory: sessionmaker[Session]) -> str:
    with factory() as session:
        operation = session.get(VisualReferenceWorkingOperation, "vr_operation_1")
        unit = session.get(VisualReferenceOperationAnchor, "vr_anchor_op_1")
        task = session.get(VisualReferenceOperationTask, "vr_task_1")
        assert operation is not None and unit is not None and task is not None
        batch = MediaBatch(
            batch_id="media_batch_vr_completed",
            purpose="style_bible_visual_reference",
            project_id="project_vr",
            style_bible_revision_id="artifact_style",
            domain_revision_id=operation.operation_id,
            total=1,
            created_at=NOW,
            updated_at=NOW,
        )
        media = MediaTask(
            media_task_id="media_task_vr_completed",
            batch_id=batch.batch_id,
            purpose=batch.purpose,
            project_id="project_vr",
            style_bible_revision_id="artifact_style",
            domain_revision_id=operation.operation_id,
            entity_type="character",
            entity_id="char_001",
            candidate_index=1,
            status="completed",
            external_provider="glabs_webhook",
            external_task_id="vrprovider1",
            provider_status="completed",
            provider_submitted_at=NOW,
            provider_error_code=None,
            error_code=None,
            error=None,
            error_detail=None,
            prompt=unit.prompt,
            negative_prompt=unit.negative_prompt,
            model="nano_banana_2",
            aspect_ratio="1:1",
            upscale_json=[],
            reference_images_json=[],
            storage_namespace="project/project_vr/visual_references/vr_prompt_1/character/char_001",
            metadata_json={
                "visual_reference_v2": True,
                "visual_reference_operation_id": operation.operation_id,
                "visual_reference_operation_anchor_id": unit.operation_anchor_id,
                "visual_reference_operation_task_id": task.operation_task_id,
                "anchor_type": unit.anchor_type,
                "anchor_id": unit.anchor_id,
                "candidate_index": 1,
                "prompt_revision_id": unit.prompt_revision_id,
                "source_definition_hash": unit.source_definition_hash,
                "generation_options_hash": unit.generation_options_hash,
                "provider_settings_snapshot_hash": operation.provider_settings_snapshot_hash,
                "source_style_bible_artifact_id": operation.source_style_bible_artifact_id,
            },
            result_json=[{
                "artifact_id": "artifact_image",
                "storage_uri": "tests/artifact_image.png",
                "mime_type": "image/png",
            }],
            storage_uri="tests/artifact_image.png",
            mime_type="image/png",
            source_result_url=None,
            selected=False,
            locked=False,
            created_at=NOW,
            updated_at=NOW,
            completed_at=NOW,
            poll_count=0,
            poll_revision=0,
            poll_claim_token=None,
            poll_claimed_at=None,
            poll_claim_expires_at=None,
        )
        session.add(batch)
        session.add(media)
        task.media_task_id = media.media_task_id
        session.commit()
        return media.media_task_id


def test_shared_visual_reference_provider_stages_exact_transport_and_submits(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory)
    coordinator = VisualReferenceCoordinator(factory)
    lease = coordinator.claim_due_operation(owner="vr-provider", now_iso=NOW, lease_seconds=30)
    assert lease is not None
    client = PendingClient()

    result = VisualReferenceDurableProviderExecutor(factory).submit_task(
        lease,
        operation_task_id="vr_task_1",
        client=client,
        now_iso=NOW,
    )

    assert result.v2_task_status == "waiting_provider"
    assert result.media_status == "submitted"
    assert len(client.submit_payloads) == 1
    payload = client.submit_payloads[0]
    assert payload["model"] == "nano_banana_2"
    assert payload["aspect_ratio"] == "1:1"
    assert payload["reference_images"] == []
    assert payload["prompt"] == "Primitive stickman ancient human portrait\n\nAvoid: No text, no watermark"
    assert "negative_prompt" not in payload
    assert "style_weight" not in payload
    with factory() as session:
        task = session.get(VisualReferenceOperationTask, "vr_task_1")
        assert task is not None and task.media_task_id is not None
        media = session.get(MediaTask, task.media_task_id)
        assert media is not None
        assert media.metadata_json["visual_reference_operation_task_id"] == "vr_task_1"
        assert media.domain_revision_id == "vr_operation_1"
        assert media.style_bible_revision_id == "artifact_style"


def test_shared_visual_reference_provider_preserves_ambiguous_submit_fence(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory)
    lease = VisualReferenceCoordinator(factory).claim_due_operation(owner="vr-provider", now_iso=NOW, lease_seconds=30)
    assert lease is not None

    result = VisualReferenceDurableProviderExecutor(factory).submit_task(
        lease,
        operation_task_id="vr_task_1",
        client=AmbiguousSubmitClient(),
        now_iso=NOW,
    )

    assert result.v2_task_status == "submit_outcome_unknown"
    with factory() as session:
        task = session.get(VisualReferenceOperationTask, "vr_task_1")
        assert task is not None and task.status == "submit_outcome_unknown"
        media = session.get(MediaTask, task.media_task_id)
        assert media is not None and media.external_task_id is None
        unit = session.get(VisualReferenceOperationAnchor, "vr_anchor_op_1")
        assert unit is not None and unit.status == "failed"


def test_completed_transport_materializes_immutable_candidate_and_updates_anchor_state(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory, task_status="waiting_provider", operation_status="running", unit_status="running")
    media_task_id = _link_completed_transport(factory)

    result = VisualReferenceProviderExecutionService(factory).reconcile_task("vr_task_1", now_iso=NOW)

    assert result.candidate_id is not None
    assert result.v2_task_status == "completed"
    with factory() as session:
        candidates = list(session.scalars(select(VisualReferenceCandidate)))
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.source_operation_task_id == "vr_task_1"
        assert candidate.media_task_id == media_task_id
        assert candidate.media_artifact_id == "artifact_image"
        state = session.get(VisualReferenceAnchorState, ("project_vr", "character", "char_001"))
        assert state is not None
        assert state.selected_candidate_id == candidate.candidate_id
        assert state.approved_candidate_id is None
        assert state.resolution_status == "candidate_ready"
        assert state.review_status == "draft_selected"
        unit = session.get(VisualReferenceOperationAnchor, "vr_anchor_op_1")
        assert unit is not None and unit.status == "candidate_ready"
        operation = session.get(VisualReferenceWorkingOperation, "vr_operation_1")
        assert operation is not None and operation.status == "finalizing"


def test_completed_transport_rejects_style_bible_namespaced_media(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory, task_status="waiting_provider", operation_status="running", unit_status="running")
    _link_completed_transport(factory)
    with factory() as session:
        media_artifact = session.get(Artifact, "artifact_image")
        assert media_artifact is not None
        media_artifact.logical_id = (
            "project/project_vr/style_bible/artifact_style/characters/char_001/"
            "candidates/media_task_vr_completed/result_001"
        )
        session.commit()

    with pytest.raises(AppError) as raised:
        VisualReferenceProviderExecutionService(factory).reconcile_task("vr_task_1", now_iso=NOW)

    assert raised.value.error_code == "VISUAL_REFERENCE_V2_MEDIA_RESULT_INVALID"
    with factory() as session:
        assert session.scalar(select(VisualReferenceCandidate)) is None
        task = session.get(VisualReferenceOperationTask, "vr_task_1")
        assert task is not None and task.status != "completed"


def test_cancelled_owner_fences_late_completed_transport_from_materialization(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory, task_status="waiting_provider", operation_status="running", unit_status="running")
    _link_completed_transport(factory)
    coordinator = VisualReferenceCoordinator(factory)
    cancelled = coordinator.cancel_operation(operation_id="vr_operation_1", now_iso=NOW)
    assert cancelled["status"] == "cancelled"

    result = VisualReferenceProviderExecutionService(factory).reconcile_task("vr_task_1", now_iso=NOW)

    assert result.candidate_id is None
    with factory() as session:
        assert session.scalar(select(VisualReferenceCandidate)) is None
        state = session.get(VisualReferenceAnchorState, ("project_vr", "character", "char_001"))
        assert state is not None and state.active_operation_anchor_id is None
