from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import AppError
from app.db import models as db_models  # noqa: F401
from app.db import scene_asset_working_models as working_models  # noqa: F401
from app.db.base import Base
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, Channel, Job, MediaBatch, MediaTask, Project, SceneAssetOperation, Setting
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_candidate import SceneAssetCandidate
from app.db.scene_asset_operation_beat import SceneAssetOperationBeat
from app.db.scene_asset_operation_task import SceneAssetOperationTask
from app.db.scene_asset_prompt_revision import SceneAssetPromptRevision
from app.db.scene_asset_working_operation import SceneAssetWorkingOperation
from app.db.session import create_sqlalchemy_engine
from app.modules.v4.scene_asset_coordinator import SceneAssetCoordinator
from app.modules.v4.scene_asset_manifest import canonical_hash
from app.modules.v4.scene_asset_provider_execution import SceneAssetProviderExecutionService
from app.services.media_settings import SECRET_KEY, SETTINGS_KEY


NOW = "2026-08-16T11:00:00+00:00"
PROMPT_REVISION_ID = "scene_asset_prompt_revision_aaaaaaaaaaaa"


class PendingClient:
    def __init__(self) -> None:
        self.submit_payloads: list[dict[str, object]] = []

    def submit_image(self, payload: dict[str, object]) -> dict[str, object]:
        self.submit_payloads.append(dict(payload))
        return {"task_id": "deadbeef", "status": "pending"}


class AmbiguousSubmitClient:
    def submit_image(self, payload: dict[str, object]) -> dict[str, object]:
        raise AppError(
            "MEDIA_PROVIDER_REQUEST_TIMEOUT",
            "Media provider request timed out.",
            504,
            retryable=True,
        )


def _factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_sqlalchemy_engine(f"sqlite:///{(tmp_path / 'scene_asset_provider_v2.sqlite3').resolve()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _artifact(artifact_id: str, *, image: bool = False) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        logical_id=(f"project/project_v2/scene_assets/test/{artifact_id}" if image else f"logical/{artifact_id}"),
        version_no=1,
        is_current=True,
        artifact_type=ArtifactType.IMAGE if image else ArtifactType.JSON,
        artifact_role=None,
        path=f"tests/{artifact_id}.{'png' if image else 'json'}",
        content_hash=f"hash_{artifact_id}",
        sha256=("a" * 64 if image else f"sha_{artifact_id}"),
        mime_type="image/png" if image else "application/json",
        size_bytes=1,
        width=1280 if image else None,
        height=720 if image else None,
        status=ArtifactStatus.READY,
        created_at=NOW,
    )


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


def _seed(
    factory: sessionmaker[Session],
    *,
    task_status: str = "queued",
    operation_status: str = "queued",
) -> None:
    frozen = _snapshot()
    options = {
        "schema_version": "1.0",
        "prompt_language": "en",
        "aspect_ratio": "16:9",
        "target_asset_role": "beat_visual",
        "image_count": 1,
    }
    prompt_hash = "sha256:" + "b" * 64
    with factory() as session:
        session.add(
            Channel(
                channel_id="channel_v2",
                name="V2",
                language="en",
                niche="test",
                audience="test",
                channel_dna_json={},
                channel_context_json={},
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add_all([_artifact("artifact_source"), _artifact("artifact_prompt")])
        session.add(Setting(key=SETTINGS_KEY, value_json={**frozen, "api_key_configured": True}, updated_at=NOW))
        session.add(Setting(key=SECRET_KEY, value_json={"glabs_webhook": "test-key"}, updated_at=NOW))
        session.flush()
        session.add(
            Project(
                project_id="project_v2",
                channel_id="channel_v2",
                name="V2",
                description=None,
                channel_context_json={},
                channel_dna_json={},
                script_artifact_id="artifact_source",
                script_analysis_artifact_id="artifact_source",
                style_bible_artifact_id="artifact_source",
                scene_plan_artifact_id="artifact_source",
                visual_plan_artifact_id="artifact_source",
                prompt_bundle_artifact_id="artifact_prompt",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            SceneAssetPromptRevision(
                prompt_revision_id=PROMPT_REVISION_ID,
                project_id="project_v2",
                scene_id="scene_001",
                beat_id="beat_001",
                revision_no=1,
                revision_kind="compiled",
                is_current=True,
                source_prompt_bundle_artifact_id="artifact_prompt",
                source_prompt_item_id="prompt_001",
                source_prompt_hash=prompt_hash,
                image_prompt="Ancient human crossing a rocky valley.",
                negative_prompt="No text, no watermark.",
                created_at=NOW,
            )
        )
        session.flush()
        session.add(
            SceneAssetWorkingOperation(
                operation_id="operation_v2",
                project_id="project_v2",
                operation_type="generate_beat",
                status=operation_status,
                source_script_artifact_id="artifact_source",
                source_script_analysis_artifact_id="artifact_source",
                source_style_bible_artifact_id="artifact_source",
                source_scene_plan_artifact_id="artifact_source",
                source_visual_plan_artifact_id="artifact_source",
                source_prompt_bundle_artifact_id="artifact_prompt",
                source_reference_approval_set_artifact_id=None,
                provider_settings_snapshot_json=frozen,
                provider_settings_snapshot_hash=canonical_hash(frozen),
                total_beat_count=1,
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
            )
        )
        session.flush()
        session.add(
            SceneAssetOperationBeat(
                operation_beat_id="operation_beat_v2",
                operation_id="operation_v2",
                project_id="project_v2",
                scene_id="scene_001",
                beat_id="beat_001",
                version=1,
                status="queued" if task_status == "queued" else "running",
                candidate_count=1,
                prompt_revision_id=PROMPT_REVISION_ID,
                prompt_bundle_artifact_id="artifact_prompt",
                prompt_item_id="prompt_001",
                prompt_hash=prompt_hash,
                image_prompt="Ancient human crossing a rocky valley.",
                negative_prompt="No text, no watermark.",
                generation_options_json=options,
                generation_options_hash=canonical_hash(options),
                reference_bindings_json=[],
                reference_bindings_hash=canonical_hash([]),
                continuity_snapshot_json=None,
                error_code=None,
                error_message=None,
                error_details_json=None,
                created_at=NOW,
                updated_at=NOW,
                completed_at=None,
            )
        )
        session.flush()
        session.add(
            SceneAssetBeatState(
                project_id="project_v2",
                beat_id="beat_001",
                scene_id="scene_001",
                version=1,
                resolution_status="not_generated",
                review_status="unreviewed",
                current_draft_candidate_id=None,
                selected_candidate_id=None,
                active_operation_beat_id="operation_beat_v2",
                continuity_source_scene_id=None,
                continuity_source_beat_id=None,
                continuity_source_candidate_id=None,
                last_error_json=None,
                last_progress_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            SceneAssetOperationTask(
                operation_task_id="operation_task_v2",
                operation_beat_id="operation_beat_v2",
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
            )
        )
        session.commit()


def _transport_metadata(operation: SceneAssetWorkingOperation, beat: SceneAssetOperationBeat) -> dict[str, object]:
    return {
        "scene_asset_v2": True,
        "scene_asset_operation_id": operation.operation_id,
        "scene_asset_operation_beat_id": beat.operation_beat_id,
        "scene_asset_operation_task_id": "operation_task_v2",
        "scene_id": beat.scene_id,
        "beat_id": beat.beat_id,
        "candidate_index": 1,
        "prompt_revision_id": beat.prompt_revision_id,
        "prompt_bundle_artifact_id": beat.prompt_bundle_artifact_id,
        "prompt_item_id": beat.prompt_item_id,
        "prompt_hash": beat.prompt_hash,
        "reference_binding_snapshot_hash": beat.reference_bindings_hash,
        "generation_settings_snapshot_hash": beat.generation_options_hash,
        "provider_settings_snapshot_hash": operation.provider_settings_snapshot_hash,
    }


def _add_transport(
    session: Session,
    *,
    status: str,
    external_task_id: str | None,
    provider_submitted_at: str | None,
    result_json: list[dict[str, object]] | None = None,
) -> None:
    operation = session.get(SceneAssetWorkingOperation, "operation_v2")
    beat = session.get(SceneAssetOperationBeat, "operation_beat_v2")
    task = session.get(SceneAssetOperationTask, "operation_task_v2")
    assert operation is not None and beat is not None and task is not None
    session.add(
        MediaBatch(
            batch_id="media_batch_v2",
            purpose="scene_asset_generation",
            project_id="project_v2",
            style_bible_revision_id=None,
            domain_revision_id="operation_v2",
            total=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()
    session.add(
        MediaTask(
            media_task_id="media_task_v2",
            batch_id="media_batch_v2",
            purpose="scene_asset_generation",
            project_id="project_v2",
            style_bible_revision_id=None,
            domain_revision_id="operation_v2",
            entity_type="beat",
            entity_id="beat_001",
            candidate_index=1,
            status=status,
            external_provider="glabs_webhook",
            external_task_id=external_task_id,
            provider_status=("completed" if status == "completed" else None),
            provider_submitted_at=provider_submitted_at,
            provider_error_code=None,
            error_code=None,
            error=None,
            error_detail=None,
            prompt=beat.image_prompt,
            negative_prompt=beat.negative_prompt,
            model="nano_banana_2",
            aspect_ratio="16:9",
            upscale_json=[],
            reference_images_json=[],
            storage_namespace="project/project_v2/scene_assets/artifact_prompt/beat_001",
            metadata_json=_transport_metadata(operation, beat),
            result_json=result_json or [],
            storage_uri=("tests/artifact_image_v2.png" if status == "completed" else None),
            mime_type=("image/png" if status == "completed" else None),
            source_result_url=("http://127.0.0.1:8765/result.png" if status == "completed" else None),
            selected=False,
            locked=False,
            created_at=NOW,
            updated_at=NOW,
            completed_at=(NOW if status == "completed" else None),
            poll_count=(1 if status == "completed" else 0),
            poll_revision=(1 if status == "completed" else 0),
        )
    )
    session.flush()
    task.media_task_id = "media_task_v2"


def test_submit_uses_frozen_v2_input_and_media_is_transport_only(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory)
    coordinator = SceneAssetCoordinator(factory)
    lease = coordinator.claim_due_operation(owner="phase_c", now_iso=NOW, lease_seconds=30)
    assert lease is not None
    client = PendingClient()

    result = SceneAssetProviderExecutionService(factory).submit_task(
        lease,
        operation_task_id="operation_task_v2",
        client=client,
        now_iso=NOW,
    )

    assert result.v2_task_status == "waiting_provider"
    assert result.media_status == "submitted"
    assert len(client.submit_payloads) == 1
    payload = client.submit_payloads[0]
    assert payload["model"] == "nano_banana_2"
    assert payload["aspect_ratio"] == "16:9"
    assert payload["reference_images"] == []
    assert "Ancient human crossing a rocky valley." in str(payload["prompt"])
    assert "Avoid: No text, no watermark." in str(payload["prompt"])
    assert "negative_prompt" not in payload
    assert "style_weight" not in payload

    with factory() as session:
        task = session.get(SceneAssetOperationTask, "operation_task_v2")
        operation = session.get(SceneAssetWorkingOperation, "operation_v2")
        assert task is not None and task.media_task_id is not None
        media = session.get(MediaTask, task.media_task_id)
        assert media is not None
        assert media.domain_revision_id == "operation_v2"
        assert media.metadata_json["scene_asset_operation_task_id"] == "operation_task_v2"
        assert media.metadata_json["prompt_revision_id"] == PROMPT_REVISION_ID
        assert media.metadata_json["provider_settings_snapshot_hash"] == operation.provider_settings_snapshot_hash
        assert operation is not None and operation.reconcile_token is None
        assert int(session.scalar(select(func.count()).select_from(Job)) or 0) == 0
        assert int(session.scalar(select(func.count()).select_from(SceneAssetOperation)) or 0) == 0


def test_ambiguous_submit_never_silently_requeues_or_resubmits(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory)
    lease = SceneAssetCoordinator(factory).claim_due_operation(owner="phase_c", now_iso=NOW, lease_seconds=30)
    assert lease is not None

    result = SceneAssetProviderExecutionService(factory).submit_task(
        lease,
        operation_task_id="operation_task_v2",
        client=AmbiguousSubmitClient(),
        now_iso=NOW,
    )
    assert result.v2_task_status == "submit_outcome_unknown"
    assert result.operation_status in {"finalizing", "failed"}

    with factory() as session:
        task = session.get(SceneAssetOperationTask, "operation_task_v2")
        assert task is not None and task.status == "submit_outcome_unknown"
        assert task.completed_at is not None
        media = session.get(MediaTask, task.media_task_id)
        assert media is not None and media.external_task_id is None


def test_completed_transport_materializes_exact_candidate_and_lineage(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory, task_status="waiting_provider", operation_status="running")
    with factory() as session:
        session.add(_artifact("artifact_image_v2", image=True))
        _add_transport(
            session,
            status="completed",
            external_task_id="deadbeef",
            provider_submitted_at=NOW,
            result_json=[{
                "artifact_id": "artifact_image_v2",
                "mime_type": "image/png",
                "sha256": "a" * 64,
                "storage_uri": "tests/artifact_image_v2.png",
            }],
        )
        session.commit()

    result = SceneAssetProviderExecutionService(factory).reconcile_task("operation_task_v2", now_iso=NOW)
    assert result.candidate_id is not None
    assert result.v2_task_status == "completed"
    assert result.operation_status == "finalizing"

    with factory() as session:
        candidate = session.scalar(
            select(SceneAssetCandidate).where(SceneAssetCandidate.operation_task_id == "operation_task_v2")
        )
        state = session.get(SceneAssetBeatState, ("project_v2", "beat_001"))
        beat = session.get(SceneAssetOperationBeat, "operation_beat_v2")
        assert candidate is not None
        assert candidate.artifact_id == "artifact_image_v2"
        assert candidate.source_prompt_revision_id == PROMPT_REVISION_ID
        assert candidate.source_prompt_bundle_artifact_id == "artifact_prompt"
        assert candidate.source_prompt_item_id == "prompt_001"
        assert candidate.source_prompt_hash == "sha256:" + "b" * 64
        assert state is not None
        assert state.current_draft_candidate_id == candidate.candidate_id
        assert state.resolution_status == "candidate_ready"
        assert state.review_status == "current_draft"
        assert state.active_operation_beat_id is None
        assert beat is not None and beat.status == "candidate_ready"


def test_stale_queued_bridge_reuses_same_media_identity_with_exact_v2_metadata(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory, task_status="submitting", operation_status="running")
    with factory() as session:
        _add_transport(
            session,
            status="queued",
            external_task_id=None,
            provider_submitted_at=None,
        )
        task = session.get(SceneAssetOperationTask, "operation_task_v2")
        assert task is not None
        task.submit_attempt_token = "submit_attempt_v2"
        task.submit_started_at = "2026-08-16T10:00:00+00:00"
        session.commit()

    result = SceneAssetProviderExecutionService(factory).recover_stale_submission(
        operation_task_id="operation_task_v2",
        stale_before_iso="2026-08-16T10:30:00+00:00",
        now_iso=NOW,
    )
    assert result.media_task_id == "media_task_v2"
    assert result.v2_task_status == "submitting"
    with factory() as session:
        task = session.get(SceneAssetOperationTask, "operation_task_v2")
        assert task is not None and task.media_task_id == "media_task_v2"
        assert task.status == "submitting"
        assert task.next_reconcile_at == NOW
