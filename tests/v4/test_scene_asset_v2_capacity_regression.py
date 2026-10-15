from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.db import models as db_models  # noqa: F401
from app.db import scene_asset_working_models as working_models  # noqa: F401
from app.db.base import Base
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, Channel, MediaBatch, MediaTask, Project, Setting
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_operation_beat import SceneAssetOperationBeat
from app.db.scene_asset_operation_task import SceneAssetOperationTask
from app.db.scene_asset_prompt_revision import SceneAssetPromptRevision
from app.db.scene_asset_working_operation import SceneAssetWorkingOperation
from app.db.session import create_sqlalchemy_engine
from app.modules.v4.scene_asset_coordinator import SceneAssetCoordinator
from app.modules.v4.scene_asset_manifest import canonical_hash
from app.modules.v4.scene_asset_provider_execution import SceneAssetProviderExecutionService
from app.modules.v4.scene_asset_workspace_v2 import SceneAssetWorkspaceV2Service
from app.services.media_settings import SECRET_KEY, SETTINGS_KEY


NOW = "2026-08-17T04:00:00+00:00"
FUTURE = "2099-08-17T04:00:00+00:00"
PROMPT_REVISION_ID = "scene_asset_prompt_revision_capacity"


class PendingClient:
    def __init__(self) -> None:
        self.submit_payloads: list[dict[str, object]] = []

    def submit_image(self, payload: dict[str, object]) -> dict[str, object]:
        self.submit_payloads.append(dict(payload))
        return {"task_id": "cafebabe", "status": "pending"}


def _factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_sqlalchemy_engine(f"sqlite:///{(tmp_path / 'scene_asset_capacity.sqlite3').resolve()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _artifact(artifact_id: str) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        logical_id=f"logical/{artifact_id}",
        version_no=1,
        is_current=True,
        artifact_type=ArtifactType.JSON,
        artifact_role=None,
        path=f"tests/{artifact_id}.json",
        content_hash=f"hash_{artifact_id}",
        sha256=f"sha_{artifact_id}",
        mime_type="application/json",
        size_bytes=1,
        width=None,
        height=None,
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
        "max_parallel_tasks": 1,
        "enabled_media_kinds": ["image"],
        "enabled": True,
    }


def _seed(factory: sessionmaker[Session], *, task_status: str = "queued", operation_status: str = "queued") -> None:
    frozen = _snapshot()
    generation_options = {
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
                channel_id="channel_capacity",
                name="Capacity",
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
                project_id="project_capacity",
                channel_id="channel_capacity",
                name="Capacity",
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
                project_id="project_capacity",
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
                operation_id="operation_capacity",
                project_id="project_capacity",
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
                operation_beat_id="operation_beat_capacity",
                operation_id="operation_capacity",
                project_id="project_capacity",
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
                generation_options_json=generation_options,
                generation_options_hash=canonical_hash(generation_options),
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
                project_id="project_capacity",
                beat_id="beat_001",
                scene_id="scene_001",
                version=1,
                resolution_status="not_generated",
                review_status="unreviewed",
                current_draft_candidate_id=None,
                selected_candidate_id=None,
                active_operation_beat_id="operation_beat_capacity",
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
                operation_task_id="operation_task_capacity",
                operation_beat_id="operation_beat_capacity",
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


def _add_capacity_blocker(session: Session) -> MediaTask:
    batch = MediaBatch(
        batch_id="media_batch_blocker",
        purpose="style_bible_visual_reference",
        project_id="project_capacity",
        style_bible_revision_id=None,
        domain_revision_id="unrelated",
        total=1,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(batch)
    session.flush()
    blocker = MediaTask(
        media_task_id="media_task_blocker",
        batch_id=batch.batch_id,
        purpose=batch.purpose,
        project_id="project_capacity",
        style_bible_revision_id=None,
        domain_revision_id=batch.domain_revision_id,
        entity_type="character",
        entity_id="char_001",
        candidate_index=1,
        status="provider_running",
        external_provider="glabs_webhook",
        external_task_id="block000",
        provider_status="running",
        provider_submitted_at=NOW,
        provider_error_code=None,
        error_code=None,
        error=None,
        error_detail=None,
        prompt="Unrelated active provider task.",
        negative_prompt="",
        model="nano_banana_2",
        aspect_ratio="1:1",
        upscale_json=[],
        reference_images_json=[],
        storage_namespace=None,
        metadata_json={},
        result_json=[],
        storage_uri=None,
        mime_type=None,
        source_result_url=None,
        selected=False,
        locked=False,
        created_at=NOW,
        updated_at=NOW,
        completed_at=None,
        poll_count=0,
        poll_revision=0,
        poll_claim_token=None,
        poll_claimed_at=None,
        poll_claim_expires_at=None,
    )
    session.add(blocker)
    session.flush()
    return blocker


def _link_v2_transport(session: Session, *, status: str = "provider_running") -> MediaTask:
    operation = session.get(SceneAssetWorkingOperation, "operation_capacity")
    beat = session.get(SceneAssetOperationBeat, "operation_beat_capacity")
    task = session.get(SceneAssetOperationTask, "operation_task_capacity")
    assert operation is not None and beat is not None and task is not None
    batch = MediaBatch(
        batch_id="media_batch_v2_capacity",
        purpose="scene_asset_generation",
        project_id="project_capacity",
        style_bible_revision_id=None,
        domain_revision_id=operation.operation_id,
        total=1,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(batch)
    session.flush()
    media = MediaTask(
        media_task_id="media_task_v2_capacity",
        batch_id=batch.batch_id,
        purpose=batch.purpose,
        project_id="project_capacity",
        style_bible_revision_id=None,
        domain_revision_id=operation.operation_id,
        entity_type="beat",
        entity_id=beat.beat_id,
        candidate_index=1,
        status=status,
        external_provider="glabs_webhook",
        external_task_id="feedbeef" if status != "queued" else None,
        provider_status="running" if status == "provider_running" else None,
        provider_submitted_at=NOW if status != "queued" else None,
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
        storage_namespace=f"project/project_capacity/scene_assets/{PROMPT_REVISION_ID}/beat_001",
        metadata_json={
            "scene_asset_v2": True,
            "scene_asset_operation_id": operation.operation_id,
            "scene_asset_operation_beat_id": beat.operation_beat_id,
            "scene_asset_operation_task_id": task.operation_task_id,
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
        },
        result_json=[],
        storage_uri=None,
        mime_type=None,
        source_result_url=None,
        selected=False,
        locked=False,
        created_at=NOW,
        updated_at=NOW,
        completed_at=None,
        poll_count=0,
        poll_revision=0,
        poll_claim_token=None,
        poll_claimed_at=None,
        poll_claim_expires_at=None,
    )
    session.add(media)
    session.flush()
    task.media_task_id = media.media_task_id
    return media


def test_capacity_wait_defers_to_queued_then_reuses_exact_media_task(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory)
    with factory() as session:
        blocker = _add_capacity_blocker(session)
        session.commit()
        blocker_id = blocker.media_task_id

    coordinator = SceneAssetCoordinator(factory)
    lease = coordinator.claim_due_operation(owner="capacity-first", now_iso=NOW, lease_seconds=30)
    assert lease is not None
    client = PendingClient()
    service = SceneAssetProviderExecutionService(factory)

    first = service.submit_task(
        lease,
        operation_task_id="operation_task_capacity",
        client=client,
        now_iso=NOW,
    )

    assert first.v2_task_status == "queued"
    assert first.media_status == "queued"
    assert client.submit_payloads == []
    first_media_task_id = first.media_task_id
    assert first_media_task_id is not None

    with factory() as session:
        task = session.get(SceneAssetOperationTask, "operation_task_capacity")
        blocker = session.get(MediaTask, blocker_id)
        assert task is not None and blocker is not None
        assert task.status == "queued"
        assert task.media_task_id == first_media_task_id
        assert task.submit_attempt_token is None
        assert task.submit_started_at is None
        assert task.next_reconcile_at is not None
        blocker.status = "completed"
        blocker.provider_status = "completed"
        blocker.completed_at = NOW
        blocker.updated_at = NOW
        session.commit()

    retry_lease = coordinator.claim_due_operation(owner="capacity-retry", now_iso=FUTURE, lease_seconds=30)
    assert retry_lease is not None
    second = service.submit_task(
        retry_lease,
        operation_task_id="operation_task_capacity",
        client=client,
        now_iso=FUTURE,
    )

    assert second.media_task_id == first_media_task_id
    assert second.media_status == "submitted"
    assert second.v2_task_status == "waiting_provider"
    assert len(client.submit_payloads) == 1


def test_terminal_v2_owner_releases_provider_capacity_but_active_owner_is_preserved(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory, task_status="waiting_provider", operation_status="running")
    with factory() as session:
        media = _link_v2_transport(session)
        session.commit()
        media_id = media.media_task_id

    service = SceneAssetProviderExecutionService(factory)
    assert service.release_terminal_v2_provider_capacity(now_iso=NOW) == 0
    with factory() as session:
        active = session.get(MediaTask, media_id)
        assert active is not None and active.status == "provider_running"

        task = session.get(SceneAssetOperationTask, "operation_task_capacity")
        beat = session.get(SceneAssetOperationBeat, "operation_beat_capacity")
        operation = session.get(SceneAssetWorkingOperation, "operation_capacity")
        state = session.get(SceneAssetBeatState, ("project_capacity", "beat_001"))
        assert task is not None and beat is not None and operation is not None and state is not None
        task.status = "cancelled"
        task.completed_at = NOW
        task.next_reconcile_at = None
        beat.status = "cancelled"
        beat.completed_at = NOW
        operation.status = "cancelled"
        operation.cancel_requested_at = NOW
        operation.cancelled_at = NOW
        operation.completed_at = NOW
        operation.next_reconcile_at = None
        state.active_operation_beat_id = None
        session.commit()

    assert service.release_terminal_v2_provider_capacity(now_iso=NOW) == 1
    with factory() as session:
        released = session.get(MediaTask, media_id)
        assert released is not None
        assert released.status == "canceled"
        assert released.error_code == "SCENE_ASSET_V2_MEDIA_OWNER_TERMINAL"
        assert released.completed_at == NOW


def test_submitting_projects_as_queued_until_provider_acceptance(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed(factory, task_status="submitting", operation_status="running")
    with factory() as session:
        state = session.get(SceneAssetBeatState, ("project_capacity", "beat_001"))
        task = session.get(SceneAssetOperationTask, "operation_task_capacity")
        assert state is not None and task is not None
        workspace = SceneAssetWorkspaceV2Service(session)
        assert workspace._transient_resolution(state, "not_generated") == "queued"
        task.status = "waiting_provider"
        session.flush()
        assert workspace._transient_resolution(state, "not_generated") == "generating"
