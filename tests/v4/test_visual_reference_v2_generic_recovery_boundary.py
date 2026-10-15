from __future__ import annotations

from app.core.config import get_settings
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.models import Channel, MediaBatch, MediaTask, Project
from app.db.session import build_session_factory
from app.modules.media.media_ownership_recovery import recover_media_ownership


def _seed_v2_transport(*, status: str, external_task_id: str | None):
    settings = get_settings()
    factory = build_session_factory(settings)
    now = utc_now_iso()
    with factory() as session:
        channel = Channel(
            channel_id=generate_id("channel"),
            name="VR V2 recovery",
            language="en",
            niche="tests",
            audience="tests",
            channel_dna_json={},
            channel_context_json={},
            created_at=now,
            updated_at=now,
        )
        session.add(channel)
        project = Project(
            project_id=generate_id("project"),
            channel_id=channel.channel_id,
            name="VR V2 recovery",
            description=None,
            channel_context_json={},
            channel_dna_json={},
            created_at=now,
            updated_at=now,
        )
        session.add(project)
        session.flush()
        batch = MediaBatch(
            batch_id=generate_id("media_batch"),
            purpose="style_bible_visual_reference",
            project_id=project.project_id,
            style_bible_revision_id=None,
            domain_revision_id=generate_id("domain"),
            total=1,
            created_at=now,
            updated_at=now,
        )
        session.add(batch)
        session.flush()
        task = MediaTask(
            media_task_id=generate_id("media_task"),
            batch_id=batch.batch_id,
            purpose=batch.purpose,
            project_id=project.project_id,
            style_bible_revision_id=None,
            domain_revision_id=batch.domain_revision_id,
            entity_type="character",
            entity_id="char_001",
            candidate_index=1,
            status=status,
            external_provider="glabs_webhook",
            external_task_id=external_task_id,
            provider_status="completed" if status == "downloading" else None,
            provider_submitted_at=now,
            provider_error_code=None,
            error_code=None,
            error=None,
            error_detail=None,
            prompt="Visual Reference V2 recovery boundary.",
            negative_prompt="",
            model="nano_banana_2",
            aspect_ratio="1:1",
            upscale_json=[],
            reference_images_json=[],
            storage_namespace=f"project/{project.project_id}/visual_reference_v2/test",
            metadata_json={
                "visual_reference_v2": True,
                "operation_task_id": generate_id("operation_task"),
            },
            result_json=[],
            storage_uri=None,
            mime_type=None,
            source_result_url=None,
            selected=False,
            locked=False,
            created_at=now,
            updated_at=now,
            completed_at=None,
            poll_count=0,
            poll_revision=0,
            poll_claim_token=None,
            poll_claimed_at=None,
            poll_claim_expires_at=None,
        )
        session.add(task)
        session.commit()
        task_id = task.media_task_id
    return factory, settings, task_id


def test_generic_recovery_does_not_fail_v2_ambiguous_submit() -> None:
    factory, settings, task_id = _seed_v2_transport(
        status="submitted",
        external_task_id=None,
    )

    recover_media_ownership(factory, settings)

    with factory() as session:
        task = session.get(MediaTask, task_id)
        assert task is not None
        assert task.status == "submitted"
        assert task.error_code is None
        assert task.completed_at is None


def test_generic_recovery_does_not_rewind_v2_downloading_transport() -> None:
    factory, settings, task_id = _seed_v2_transport(
        status="downloading",
        external_task_id="external-v2-download",
    )

    recover_media_ownership(factory, settings)

    with factory() as session:
        task = session.get(MediaTask, task_id)
        assert task is not None
        assert task.status == "downloading"
        assert task.external_task_id == "external-v2-download"
        assert task.error_code is None
        assert task.completed_at is None
