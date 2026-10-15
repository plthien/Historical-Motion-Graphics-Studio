from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import hashlib
from typing import Any

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import JobStatus
from app.db.models import Channel, Job, MediaBatch, MediaTask, Project, StyleBibleVisualOperation
from app.db.session import build_session_factory
from app.api.errors import AppError
from app.modules.media.bounded_http_transport import BoundedTransportDeadline, BoundedTransportHttpError
from app.modules.media.glabs_webhook_client import DownloadMetadata
from app.modules.media.glabs_webhook_client import GLabsWebhookClient
from app.modules.media.media_ownership_recovery import recover_media_ownership
from app.modules.media.media_task_service import (
    PERSISTED_SCENE_ASSET_PURPOSE,
    PERSISTED_STYLE_BIBLE_VISUAL_REFERENCE_PURPOSE,
    MediaTaskService,
)
from app.modules.media.transport_policy import MediaTransportPolicy
from app.repositories import DownloadOwnerSnapshot
from app.services.media_settings import default_media_provider_settings


_ACTIVE_TEST_MEDIA_STATUSES = {"queued", "submitted", "provider_pending", "provider_running", "downloading"}


@pytest.fixture(autouse=True)
def _release_media_capacity_created_by_test() -> Any:
    """Keep synthetic reliability tasks from leaking global provider capacity."""
    factory = build_session_factory(get_settings())
    with factory() as session:
        existing_ids = set(session.scalars(select(MediaTask.media_task_id)))
    yield
    now = utc_now_iso()
    with factory() as session:
        stmt = select(MediaTask)
        if existing_ids:
            stmt = stmt.where(MediaTask.media_task_id.not_in(existing_ids))
        for task in session.scalars(stmt):
            if task.status not in _ACTIVE_TEST_MEDIA_STATUSES:
                continue
            task.status = "failed"
            task.error_code = "TEST_MEDIA_TASK_RELEASED"
            task.error = "Synthetic media reliability task released after test."
            task.error_detail = None
            task.poll_claim_token = None
            task.poll_claimed_at = None
            task.poll_claim_expires_at = None
            task.updated_at = now
            task.completed_at = now
        session.commit()


def _now(offset_seconds: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset_seconds)).isoformat()


def _png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
        b"\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
        b"\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )


class CompletingMediaClient:
    def __init__(self) -> None:
        self.submitted: list[dict[str, Any]] = []
        self.status_calls: list[str] = []
        self.content = _png_bytes()

    def submit_image(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.submitted.append(dict(payload))
        return {"task_id": f"external-{len(self.submitted)}", "status": "pending"}

    def get_status(self, external_task_id: str) -> dict[str, Any]:
        self.status_calls.append(external_task_id)
        return {
            "task_id": external_task_id,
            "status": "completed",
            "results": [f"http://127.0.0.1:8765/{external_task_id}.png"],
        }

    def get_result(self, external_task_id: str) -> dict[str, Any]:
        return self.get_status(external_task_id)

    def download_file(self, url: str, destination: Path, max_bytes: int) -> DownloadMetadata:
        destination.write_bytes(self.content)
        return DownloadMetadata(
            mime_type="image/png",
            size_bytes=len(self.content),
            sha256=hashlib.sha256(self.content).hexdigest(),
        )


class PendingMediaClient(CompletingMediaClient):
    def get_status(self, external_task_id: str) -> dict[str, Any]:
        self.status_calls.append(external_task_id)
        return {"task_id": external_task_id, "status": "pending", "results": []}


class TransientTimeoutMediaClient(CompletingMediaClient):
    def get_status(self, external_task_id: str) -> dict[str, Any]:
        self.status_calls.append(external_task_id)
        raise AppError(
            "MEDIA_PROVIDER_REQUEST_TIMEOUT",
            "Media provider request timed out.",
            504,
            retryable=True,
        )


class CapturingTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def request_json(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return {"task_id": "ABCDEF12", "status": "pending"}


class Http400Transport:
    def request_json(self, **kwargs: Any) -> dict[str, object]:
        raise BoundedTransportHttpError(400, b'{"error":"invalid argument"}')


class DeadlineTransport:
    def request_json(self, **kwargs: Any) -> dict[str, object]:
        raise BoundedTransportDeadline("synthetic control-plane deadline")


def _provider_settings(**overrides: Any) -> dict[str, Any]:
    settings = default_media_provider_settings()
    settings.update(
        {
            "enabled": True,
            "api_key": "media-reliability-test-key",
            "base_url": "http://127.0.0.1:8765",
            "poll_interval_seconds": 1,
            "max_parallel_tasks": 3,
        }
    )
    settings.update(overrides)
    return settings


def test_provider_submit_uses_separate_request_and_response_byte_limits() -> None:
    transport = CapturingTransport()
    policy = MediaTransportPolicy(
        control_plane_max_request_bytes=5_242_880,
        control_plane_max_response_bytes=262_144,
    )
    client = GLabsWebhookClient(
        _provider_settings(),
        policy=policy,
        transport=transport,  # type: ignore[arg-type]
    )

    prompt = "x" * 300_000
    response = client.submit_image(
        {
            "prompt": prompt,
            "model": "nano_banana_2",
            "aspect_ratio": "16:9",
            "reference_images": [],
            "upscale": [],
        }
    )

    assert response["task_id"] == "abcdef12"
    assert len(transport.calls) == 1
    assert transport.calls[0]["max_request_bytes"] == 5_242_880
    assert transport.calls[0]["max_response_bytes"] == 262_144
    assert len(transport.calls[0]["body"]) > 262_144


def _project(session: Any) -> Project:
    now = utc_now_iso()
    channel = Channel(
        channel_id=generate_id("channel"),
        name="Media Reliability",
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
        name="Media Reliability",
        description=None,
        channel_context_json={},
        channel_dna_json={},
        created_at=now,
        updated_at=now,
    )
    session.add(project)
    session.flush()
    return project


def _batch(session: Any, project: Project, *, purpose: str = "style_bible_visual_reference") -> MediaBatch:
    now = utc_now_iso()
    batch = MediaBatch(
        batch_id=generate_id("media_batch"),
        purpose=purpose,
        project_id=project.project_id,
        style_bible_revision_id=None,
        domain_revision_id=generate_id("domain"),
        total=0,
        created_at=now,
        updated_at=now,
    )
    session.add(batch)
    session.flush()
    return batch


def _task(
    session: Any,
    batch: MediaBatch,
    project: Project,
    *,
    status: str = "queued",
    created_at: str | None = None,
    candidate_index: int = 1,
    external_task_id: str | None = None,
    provider_submitted_at: str | None = None,
    storage_namespace: str | None = None,
    external_provider: str = "glabs_webhook",
) -> MediaTask:
    now = utc_now_iso()
    task = MediaTask(
        media_task_id=generate_id("media_task"),
        batch_id=batch.batch_id,
        purpose=batch.purpose,
        project_id=project.project_id,
        style_bible_revision_id=None,
        domain_revision_id=batch.domain_revision_id,
        entity_type="beat",
        entity_id=generate_id("beat"),
        candidate_index=candidate_index,
        status=status,
        external_provider=external_provider,
        external_task_id=external_task_id,
        provider_status="running" if status == "provider_running" else None,
        provider_submitted_at=provider_submitted_at,
        provider_error_code=None,
        error_code=None,
        error=None,
        error_detail=None,
        prompt="A reliable image prompt.",
        negative_prompt="",
        model="nano_banana_2",
        aspect_ratio="16:9",
        upscale_json=[],
        reference_images_json=[],
        storage_namespace=storage_namespace,
        metadata_json={},
        result_json=[],
        storage_uri=None,
        mime_type=None,
        source_result_url=None,
        selected=False,
        locked=False,
        created_at=created_at or now,
        updated_at=created_at or now,
        completed_at=None,
        poll_count=0,
        poll_revision=0,
        poll_claim_token=None,
        poll_claimed_at=None,
        poll_claim_expires_at=None,
    )
    session.add(task)
    batch.total += 1
    session.flush()
    return task


def test_clean_batch_uses_batch_scoped_parallel_waves() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project = _project(session)
        batch = _batch(session, project)
        for index in range(1, 7):
            _task(session, batch, project, candidate_index=index)
        session.commit()

        client = CompletingMediaClient()
        tasks = MediaTaskService(session).execute_batch_to_terminal(
            batch.batch_id,
            provider_settings=_provider_settings(max_parallel_tasks=3),
            client=client,
            max_cycles=5,
        )

        assert len(tasks) == 6
        assert all(task.status == "completed" for task in tasks)
        assert len(client.submitted) == 6


def test_provider_active_tasks_keep_global_capacity_without_age_expiry() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project = _project(session)
        old_batch = _batch(session, project)
        for index in range(1, 4):
            _task(
                session,
                old_batch,
                project,
                status="provider_running",
                candidate_index=index,
                external_task_id=f"active-{index}",
                provider_submitted_at=_now(-3600),
            )
        new_batch = _batch(session, project)
        new_tasks = [_task(session, new_batch, project, candidate_index=index) for index in range(1, 3)]
        session.commit()

        client = CompletingMediaClient()
        MediaTaskService(session).advance_batch_once(
            new_batch.batch_id,
            provider_settings=_provider_settings(max_parallel_tasks=3),
            client=client,
        )
        session.expire_all()
        refreshed = [session.get(MediaTask, task.media_task_id) for task in new_tasks]
        assert all(task is not None and task.status == "queued" for task in refreshed)
        assert client.submitted == []


def test_provider_submit_capacity_is_global_across_batches() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project = _project(session)
        provider = generate_id("provider")
        batches = [_batch(session, project, purpose="scene_asset_generation") for _ in range(3)]
        tasks = [_task(session, batch, project, external_provider=provider) for batch in batches]
        session.commit()

        client = PendingMediaClient()
        service = MediaTaskService(session)
        settings = _provider_settings(max_parallel_tasks=2)
        for batch in batches:
            service.advance_batch_once(batch.batch_id, provider_settings=settings, client=client)

        assert len(client.submitted) == 2
        session.expire_all()
        refreshed = [session.get(MediaTask, task.media_task_id) for task in tasks]
        assert [task.status for task in refreshed if task is not None].count("queued") == 1
        assert [task.status for task in refreshed if task is not None].count("submitted") == 2

        refreshed[0].status = "completed"
        refreshed[0].completed_at = utc_now_iso()
        refreshed[0].updated_at = refreshed[0].completed_at
        session.commit()
        service.advance_batch_once(batches[2].batch_id, provider_settings=settings, client=client)

        assert len(client.submitted) == 3


def test_queued_task_waits_indefinitely_while_global_capacity_is_full(monkeypatch: Any) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project = _project(session)
        provider = generate_id("provider")
        blocker_batch = _batch(session, project)
        _task(
            session,
            blocker_batch,
            project,
            status="provider_running",
            external_provider=provider,
            external_task_id="blocker",
            provider_submitted_at=_now(-3600),
            created_at=_now(-7200),
        )
        batch = _batch(session, project)
        task = _task(
            session,
            batch,
            project,
            created_at=_now(-7200),
            external_provider=provider,
        )
        session.commit()

        monkeypatch.setattr("app.modules.media.media_task_service.utc_now_iso", lambda: _now(0))
        MediaTaskService(session)._refresh_task(
            task.media_task_id,
            provider_settings_override=_provider_settings(max_parallel_tasks=1),
            client_override=PendingMediaClient(),
        )
        session.expire_all()
        refreshed = session.get(MediaTask, task.media_task_id)
        assert refreshed is not None
        assert refreshed.status == "queued"
        assert refreshed.error_code is None
        assert refreshed.completed_at is None


def test_global_queue_progress_does_not_false_starve_deep_task(monkeypatch: Any) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project = _project(session)
        provider = generate_id("provider")
        created = _now(-70)
        earlier: list[MediaTask] = []
        for index in range(5):
            batch = _batch(session, project, purpose="scene_asset_generation")
            earlier.append(
                _task(
                    session,
                    batch,
                    project,
                    status="provider_running" if index >= 3 else "completed",
                    created_at=created,
                    candidate_index=index + 1,
                    external_task_id=f"ahead-{index}",
                    provider_submitted_at=_now(-10) if index >= 3 else _now(-60),
                    external_provider=provider,
                )
            )
            if index < 3:
                earlier[-1].completed_at = _now(-40)
        target_batch = _batch(session, project, purpose="scene_asset_generation")
        target = _task(
            session,
            target_batch,
            project,
            created_at=_now(-69),
            external_provider=provider,
        )
        session.commit()

        monkeypatch.setattr("app.modules.media.media_task_service.utc_now_iso", lambda: _now(0))
        service = MediaTaskService(session)
        service._refresh_task(
            target.media_task_id,
            provider_settings_override=_provider_settings(max_parallel_tasks=2),
            client_override=PendingMediaClient(),
        )
        session.expire_all()
        refreshed = session.get(MediaTask, target.media_task_id)

        assert refreshed is not None
        assert refreshed.status == "queued"
        assert refreshed.error_code is None


def test_provider_control_plane_deadline_is_retryable_request_timeout() -> None:
    client = GLabsWebhookClient(
        _provider_settings(),
        transport=DeadlineTransport(),  # type: ignore[arg-type]
    )

    with pytest.raises(AppError) as exc:
        client.get_status("abcdef12")

    assert exc.value.error_code == "MEDIA_PROVIDER_REQUEST_TIMEOUT"
    assert exc.value.retryable is True


def test_provider_http_400_maps_to_invalid_request() -> None:
    client = GLabsWebhookClient(
        _provider_settings(),
        transport=Http400Transport(),  # type: ignore[arg-type]
    )

    with pytest.raises(AppError) as exc:
        client.submit_image(
            {
                "prompt": "A valid test prompt.",
                "model": "nano_banana_2",
                "aspect_ratio": "16:9",
                "reference_images": [],
                "upscale": [],
            }
        )

    assert exc.value.error_code == "MEDIA_PROVIDER_INVALID_REQUEST"
    assert exc.value.details["status_code"] == 400


def test_short_queue_wait_does_not_fail_before_starvation_threshold() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project = _project(session)
        batch = _batch(session, project)
        task = _task(session, batch, project, created_at=_now(-5), external_provider=generate_id("provider"))
        session.commit()

        MediaTaskService(session)._refresh_task(
            task.media_task_id,
            provider_settings_override=_provider_settings(),
            client_override=CompletingMediaClient(),
        )
        session.expire_all()
        refreshed = session.get(MediaTask, task.media_task_id)

        assert refreshed is not None
        assert refreshed.status == "submitted"
        assert refreshed.error_code is None


def test_provider_running_past_old_timeout_keeps_polling(monkeypatch: Any) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project = _project(session)
        batch = _batch(session, project)
        task = _task(
            session,
            batch,
            project,
            status="provider_running",
            external_task_id="still-running",
            provider_submitted_at=_now(-86_400),
        )
        session.commit()
        monkeypatch.setattr("app.modules.media.media_task_service.utc_now_iso", lambda: _now(0))
        client = PendingMediaClient()

        MediaTaskService(session)._refresh_task(
            task.media_task_id,
            provider_settings_override=_provider_settings(),
            client_override=client,
        )
        session.expire_all()
        refreshed = session.get(MediaTask, task.media_task_id)
        assert refreshed is not None
        assert refreshed.status == "provider_running"
        assert refreshed.error_code is None
        assert client.status_calls == ["still-running"]


def test_transient_status_poll_timeout_keeps_task_active_and_releases_claim() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project = _project(session)
        batch = _batch(session, project)
        task = _task(
            session,
            batch,
            project,
            status="provider_running",
            external_task_id="transient-timeout",
            provider_submitted_at=_now(-10),
            external_provider=generate_id("provider"),
        )
        session.commit()

        MediaTaskService(session)._refresh_task(
            task.media_task_id,
            provider_settings_override=_provider_settings(),
            client_override=TransientTimeoutMediaClient(),
        )
        session.expire_all()
        refreshed = session.get(MediaTask, task.media_task_id)
        assert refreshed is not None
        assert refreshed.status == "provider_running"
        assert refreshed.error_code is None
        assert refreshed.poll_claim_token is None
        assert refreshed.completed_at is None


def test_provider_completed_after_old_timeout_is_materialized(monkeypatch: Any) -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project = _project(session)
        batch = _batch(session, project)
        task = _task(
            session,
            batch,
            project,
            status="provider_running",
            external_task_id="completed-late",
            provider_submitted_at=_now(-86_400),
        )
        session.commit()
        monkeypatch.setattr("app.modules.media.media_task_service.utc_now_iso", lambda: _now(0))

        MediaTaskService(session)._refresh_task(
            task.media_task_id,
            provider_settings_override=_provider_settings(),
            client_override=CompletingMediaClient(),
        )
        session.expire_all()
        refreshed = session.get(MediaTask, task.media_task_id)
        assert refreshed is not None
        assert refreshed.status == "completed"
        assert refreshed.result_json


def test_explicit_visual_reference_namespace_wins_over_style_bible_revision_on_download() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = MediaTaskService(session)
        owner = DownloadOwnerSnapshot(
            media_task_id="media_task_vr_namespace",
            batch_id="media_batch_vr_namespace",
            purpose=PERSISTED_STYLE_BIBLE_VISUAL_REFERENCE_PURPOSE,
            project_id="project_vr_namespace",
            external_task_id="external-vr-namespace",
            provider_submitted_at="2026-08-18T00:00:00+00:00",
            claimed_downloading_at="2026-08-18T00:00:00+00:00",
            style_bible_revision_id="artifact_style_revision",
            domain_revision_id="visual_reference_operation_vr_namespace",
            storage_namespace="project/project_vr_namespace/visual_references/prompt_1/character/char_001",
            entity_type="character",
            entity_id="char_001",
        )

        logical_id = service._logical_id_for_download_owner(owner, index=1)

        assert logical_id == (
            "project/project_vr_namespace/visual_references/prompt_1/character/char_001/"
            "media_task_vr_namespace/result_001"
        )
        assert "/style_bible/" not in logical_id


def test_scene_asset_namespace_remains_scene_assets_even_with_style_revision() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = MediaTaskService(session)
        owner = DownloadOwnerSnapshot(
            media_task_id="media_task_scene_namespace",
            batch_id="media_batch_scene_namespace",
            purpose=PERSISTED_SCENE_ASSET_PURPOSE,
            project_id="project_scene_namespace",
            external_task_id="external-scene-namespace",
            provider_submitted_at="2026-08-18T00:00:00+00:00",
            claimed_downloading_at="2026-08-18T00:00:00+00:00",
            style_bible_revision_id="artifact_style_revision",
            domain_revision_id="scene_asset_operation_scene_namespace",
            storage_namespace="project/project_scene_namespace/scene_assets/prompt_bundle/beat_001",
            entity_type="beat",
            entity_id="beat_001",
        )

        logical_id = service._logical_id_for_download_owner(owner, index=1)

        assert logical_id == (
            "project/project_scene_namespace/scene_assets/prompt_bundle/beat_001/"
            "media_task_scene_namespace/result_001"
        )


def test_legacy_style_bible_path_is_preserved_without_explicit_namespace() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = MediaTaskService(session)
        owner = DownloadOwnerSnapshot(
            media_task_id="media_task_legacy_style",
            batch_id="media_batch_legacy_style",
            purpose=PERSISTED_STYLE_BIBLE_VISUAL_REFERENCE_PURPOSE,
            project_id="project_legacy_style",
            external_task_id="external-legacy-style",
            provider_submitted_at="2026-08-18T00:00:00+00:00",
            claimed_downloading_at="2026-08-18T00:00:00+00:00",
            style_bible_revision_id="artifact_style_revision",
            domain_revision_id="legacy_style_domain",
            storage_namespace=None,
            entity_type="character",
            entity_id="char_001",
        )

        assert service._logical_id_for_download_owner(owner, index=1) == (
            "project/project_legacy_style/style_bible/artifact_style_revision/"
            "characters/char_001/candidates/media_task_legacy_style/result_001"
        )


def test_storage_namespace_validation_is_purpose_aware() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        service = MediaTaskService(session)
        service._validate_storage_namespace(
            "project/project_namespace/visual_references/prompt_1/character/char_001",
            project_id="project_namespace",
            purpose=PERSISTED_STYLE_BIBLE_VISUAL_REFERENCE_PURPOSE,
        )
        service._validate_storage_namespace(
            "project/project_namespace/scene_assets/prompt_bundle/beat_001",
            project_id="project_namespace",
            purpose=PERSISTED_SCENE_ASSET_PURPOSE,
        )
        with pytest.raises(AppError) as visual_error:
            service._validate_storage_namespace(
                "project/project_namespace/scene_assets/prompt_bundle/beat_001",
                project_id="project_namespace",
                purpose=PERSISTED_STYLE_BIBLE_VISUAL_REFERENCE_PURPOSE,
            )
        with pytest.raises(AppError) as scene_error:
            service._validate_storage_namespace(
                "project/project_namespace/visual_references/prompt_1/character/char_001",
                project_id="project_namespace",
                purpose=PERSISTED_SCENE_ASSET_PURPOSE,
            )
        assert visual_error.value.error_code == "MEDIA_IMAGE_REQUEST_INVALID"
        assert scene_error.value.error_code == "MEDIA_IMAGE_REQUEST_INVALID"


def test_active_visual_reference_owner_is_not_terminalized_by_recovery() -> None:
    factory = build_session_factory(get_settings())
    with factory() as session:
        project = _project(session)
        batch = _batch(session, project, purpose="style_bible_visual_reference")
        task = _task(
            session,
            batch,
            project,
            status="provider_running",
            external_task_id="active-provider-task",
            provider_submitted_at=_now(-10),
        )
        job = Job(
            job_id=generate_id("job"),
            job_type="visual_reference_v4_generate",
            status=JobStatus.RUNNING,
            progress=10,
            artifact_type="style_bible_visual_reference_set",
            target_type="project",
            target_id=project.project_id,
            attempt=1,
            retryable=True,
            error_code=None,
            error_message=None,
            error_details_json=None,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
            started_at=utc_now_iso(),
            finished_at=None,
            heartbeat_at=utc_now_iso(),
            completed_at=None,
        )
        session.add(job)
        operation = StyleBibleVisualOperation(
            operation_id=generate_id("operation"),
            job_id=job.job_id,
            project_id=project.project_id,
            operation_type="full_generation",
            status="generating",
            script_artifact_id="script",
            script_analysis_artifact_id="analysis",
            style_bible_artifact_id="style",
            expected_visual_reference_set_artifact_id=None,
            media_batch_id=batch.batch_id,
            request_json={},
            result_json={},
            error_code=None,
            error_message=None,
            error_details_json=None,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
            completed_at=None,
        )
        session.add(operation)
        session.commit()

    recover_media_ownership(factory, get_settings())

    with factory() as session:
        refreshed = session.get(MediaTask, task.media_task_id)
        assert refreshed is not None
        assert refreshed.status == "provider_running"
        assert refreshed.error_code is None
