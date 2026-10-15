from __future__ import annotations

from copy import deepcopy
import inspect
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import AppError
from app.db import models as db_models  # noqa: F401
from app.db import scene_asset_working_models as working_models  # noqa: F401
from app.db.base import Base
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, Channel, Job, Project, SceneAssetOperation
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_candidate import SceneAssetCandidate
from app.db.scene_asset_working_operation import SceneAssetWorkingOperation
from app.db.session import create_sqlalchemy_engine
from app.main import create_app
from app.modules.v4.scene_asset_prompt_revision import SceneAssetPromptRevisionService
from app.modules.v4.scene_asset_runtime_v2 import SceneAssetRuntimeV2


NOW = "2026-08-16T12:00:00+00:00"


def _factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_sqlalchemy_engine(f"sqlite:///{(tmp_path / 'phase_d.sqlite3').resolve()}")
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
        status=ArtifactStatus.READY,
        created_at=NOW,
    )


def _seed_project(factory: sessionmaker[Session]) -> None:
    ids = [
        "artifact_script", "artifact_analysis", "artifact_style", "artifact_scene",
        "artifact_visual", "artifact_prompt",
    ]
    with factory() as session:
        session.add(Channel(
            channel_id="channel_v2",
            name="V2",
            language="en",
            niche="test",
            audience="test",
            channel_dna_json={},
            channel_context_json={},
            created_at=NOW,
            updated_at=NOW,
        ))
        session.add_all([_artifact(value) for value in ids])
        session.flush()
        session.add(Project(
            project_id="project_v2",
            channel_id="channel_v2",
            name="V2",
            description=None,
            channel_context_json={},
            channel_dna_json={},
            script_artifact_id="artifact_script",
            script_analysis_artifact_id="artifact_analysis",
            style_bible_artifact_id="artifact_style",
            scene_plan_artifact_id="artifact_scene",
            visual_plan_artifact_id="artifact_visual",
            prompt_bundle_artifact_id="artifact_prompt",
            created_at=NOW,
            updated_at=NOW,
        ))
        session.flush()
        SceneAssetPromptRevisionService(session).publish_approved_bundle("project_v2", _upstream()[2])
        session.commit()


def _upstream() -> tuple[dict, dict, dict]:
    prompt_items = []
    beat_plans = []
    for index in (1, 2):
        beat_id = f"beat_{index:03d}"
        prompt_items.append({
            "prompt_item_id": f"prompt_{index:03d}",
            "scene_id": f"scene_{index:03d}",
            "beat_id": beat_id,
            "beat_order": index,
            "local_beat_order": 1,
            "continuity_group_id": f"continuity_{index:03d}",
            "visual_priority": "normal",
            "target_asset_role": "beat_visual",
            "prompt_origin": "compiled",
            "image_prompt": f"prompt {index}",
            "negative_prompt": "no text",
            "prompt_hash": "sha256:" + str(index) * 64,
            "generation_options": {
                "schema_version": "1.0",
                "prompt_language": "en",
                "aspect_ratio": "16:9",
                "target_asset_role": "beat_visual",
                "image_count": 1,
            },
            "reference_bindings": [],
            "continuity_reference_requirement": None,
            "source_provenance": {
                "representation_family": "character_environment_lived",
                "selected_character_refs": [],
                "selected_location_refs": [],
                "selected_prop_refs": [],
                "selected_recurring_visual_subject_refs": [],
                "selected_motif_refs": [],
                "selected_native_visual_reference_ids": [],
            },
        })
        beat_plans.append({"beat_id": beat_id})
    return (
        {"sections": []},
        {
            "scenes": [
                {"scene_id": "scene_001", "section_id": "sec_001", "beat_plans": [beat_plans[0]]},
                {"scene_id": "scene_002", "section_id": "sec_002", "beat_plans": [beat_plans[1]]},
            ]
        },
        {
            "status": "approved",
            "prompt_bundle_revision_id": "artifact_prompt",
            "revision_operation": "full_recompilation",
            "source_visual_reference_approval_set_artifact_id": None,
            "prompt_items": prompt_items,
        },
    )


def _patch_runtime(
    runtime: SceneAssetRuntimeV2,
    monkeypatch: pytest.MonkeyPatch,
    upstream: tuple[dict, dict, dict] | None = None,
) -> None:
    current_upstream = upstream or _upstream()
    monkeypatch.setattr(runtime, "_load_upstream", lambda project: current_upstream)
    monkeypatch.setattr(runtime, "_provider_snapshot", lambda: {
        "provider": "glabs_webhook",
        "default_image_model": "nano_banana_2",
        "default_image_aspect_ratio": "1:1",
        "default_upscale": [],
        "poll_interval_seconds": 4,
        "max_parallel_tasks": 3,
        "enabled_media_kinds": ["image"],
        "enabled": True,
    })


def test_phase_d_submission_writes_only_v2_runtime_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _factory(tmp_path)
    _seed_project(factory)
    with factory() as session:
        runtime = SceneAssetRuntimeV2(session)
        _patch_runtime(runtime, monkeypatch)
        result = runtime.submit_generation("project_v2", {"operation_type": "generate_all", "candidate_count": 1})
        assert result["total_beat_count"] == 2
        assert result["scheduled_beat_ids"] == ["beat_001", "beat_002"]

    with factory() as session:
        assert int(session.scalar(select(func.count()).select_from(SceneAssetWorkingOperation)) or 0) == 1
        assert int(session.scalar(select(func.count()).select_from(Job)) or 0) == 0
        assert int(session.scalar(select(func.count()).select_from(SceneAssetOperation)) or 0) == 0
        assert int(session.execute(text("SELECT COUNT(*) FROM scene_asset_beat_claims")).scalar_one()) == 0
        assert int(session.execute(text("SELECT COUNT(*) FROM scene_asset_beat_operation_leases")).scalar_one()) == 0
        states = list(session.scalars(select(SceneAssetBeatState).order_by(SceneAssetBeatState.beat_id)))
        assert len(states) == 2
        assert all(state.active_operation_beat_id is not None for state in states)


def test_generate_all_ignores_historical_candidates_from_previous_prompt_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory(tmp_path)
    _seed_project(factory)
    with factory() as session:
        project = session.get(Project, "project_v2")
        assert project is not None
        old_bundle = _upstream()[2]
        for index, item in enumerate(old_bundle["prompt_items"], start=1):
            revision = SceneAssetPromptRevisionService(session).current(
                project.project_id,
                str(item["beat_id"]),
            )
            artifact_id = f"artifact_old_image_{index:03d}"
            session.add(
                Artifact(
                    artifact_id=artifact_id,
                    logical_id=f"logical/{artifact_id}",
                    version_no=1,
                    is_current=True,
                    artifact_type=ArtifactType.IMAGE,
                    artifact_role=None,
                    path=f"tests/{artifact_id}.png",
                    content_hash=f"hash_{artifact_id}",
                    sha256=str(index) * 64,
                    mime_type="image/png",
                    size_bytes=1,
                    width=1280,
                    height=720,
                    status=ArtifactStatus.READY,
                    created_at=NOW,
                )
            )
            session.flush()
            session.add(
                SceneAssetCandidate(
                    candidate_id=f"scene_asset_candidate_{index:012x}",
                    project_id=project.project_id,
                    scene_id=str(item["scene_id"]),
                    beat_id=str(item["beat_id"]),
                    artifact_id=artifact_id,
                    source_kind="uploaded",
                    operation_beat_id=None,
                    operation_task_id=None,
                    source_prompt_revision_id=revision.prompt_revision_id,
                    source_prompt_bundle_artifact_id=revision.source_prompt_bundle_artifact_id,
                    source_prompt_item_id=revision.source_prompt_item_id,
                    source_prompt_hash=revision.source_prompt_hash,
                    continuity_snapshot_json=None,
                    created_at=NOW,
                )
            )
        session.add(_artifact("artifact_prompt_new"))
        session.flush()

        new_upstream = deepcopy(_upstream())
        new_bundle = new_upstream[2]
        new_bundle["prompt_bundle_revision_id"] = "artifact_prompt_new"
        for index, item in enumerate(new_bundle["prompt_items"], start=1):
            item["image_prompt"] = f"new prompt {index}"
            item["prompt_hash"] = "sha256:" + str(index + 2) * 64
        project.prompt_bundle_artifact_id = "artifact_prompt_new"
        SceneAssetPromptRevisionService(session).publish_approved_bundle(
            project.project_id,
            new_bundle,
        )
        session.commit()

        runtime = SceneAssetRuntimeV2(session)
        _patch_runtime(runtime, monkeypatch, new_upstream)
        result = runtime.submit_generation(
            project.project_id,
            {"operation_type": "generate_all", "candidate_count": 1},
        )

        assert result["scheduled_beat_ids"] == ["beat_001", "beat_002"]
        assert result["blocked_beat_ids"] == []
        assert session.query(SceneAssetCandidate).count() == 2


def test_generate_all_schedules_ready_source_and_blocks_cross_section_dependent_without_deadlock(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _seed_project(factory)
    with factory() as session:
        runtime = SceneAssetRuntimeV2(session)
        project = session.get(Project, "project_v2")
        assert project is not None

        _scene_plan, visual_plan, prompt_bundle = _upstream()
        prompt_items = list(prompt_bundle["prompt_items"])
        prompt_items[1] = {
            **prompt_items[1],
            "continuity_group_id": "continuity_001",
            "continuity_reference_requirement": {
                "reference_tag": "prev_beat",
                "source_beat_id": "beat_001",
                "relation": "same_setup",
            },
        }
        runtime._ensure_beat_states(project, prompt_items)
        session.flush()

        section_by_beat = {
            str(beat["beat_id"]): str(scene["section_id"])
            for scene in visual_plan["scenes"]
            for beat in scene["beat_plans"]
        }
        scheduled, blocked, snapshots = runtime._schedule(
            project=project,
            operation_type="generate_all",
            prompt_items=prompt_items,
            prompt_by_beat={
                str(item["beat_id"]): item
                for item in prompt_items
            },
            prompt_revision_by_beat={
                str(item["beat_id"]): runtime._current_prompt_revision(project, item)
                for item in prompt_items
            },
            section_by_beat=section_by_beat,
            section_id=None,
            target_beat_id=None,
        )

        assert section_by_beat["beat_001"] == "sec_001"
        assert section_by_beat["beat_002"] == "sec_002"
        assert scheduled == ["beat_001"]
        assert blocked == ["beat_002"]
        assert snapshots == {}

        source_revision = runtime._current_prompt_revision(project, prompt_items[0])
        session.add(
            Artifact(
                artifact_id="artifact_source_image",
                logical_id="project/project_v2/scene_assets/beat_001/source",
                version_no=1,
                is_current=True,
                artifact_type=ArtifactType.IMAGE,
                artifact_role=None,
                path="tests/artifact_source_image.png",
                content_hash="hash_source_image",
                sha256="a" * 64,
                mime_type="image/png",
                size_bytes=1,
                width=1280,
                height=720,
                status=ArtifactStatus.READY,
                created_at=NOW,
            )
        )
        session.flush()
        session.add(
            SceneAssetCandidate(
                candidate_id="scene_asset_candidate_source",
                project_id=project.project_id,
                scene_id="scene_001",
                beat_id="beat_001",
                artifact_id="artifact_source_image",
                source_kind="uploaded",
                operation_beat_id=None,
                operation_task_id=None,
                source_prompt_revision_id=source_revision.prompt_revision_id,
                source_prompt_bundle_artifact_id=source_revision.source_prompt_bundle_artifact_id,
                source_prompt_item_id=source_revision.source_prompt_item_id,
                source_prompt_hash=source_revision.source_prompt_hash,
                continuity_snapshot_json=None,
                created_at=NOW,
            )
        )
        session.flush()
        source = session.get(SceneAssetBeatState, (project.project_id, "beat_001"))
        assert source is not None
        source.resolution_status = "approved"
        source.review_status = "approved"
        source.current_draft_candidate_id = "scene_asset_candidate_source"
        source.selected_candidate_id = "scene_asset_candidate_source"
        session.flush()

        scheduled, blocked, snapshots = runtime._schedule(
            project=project,
            operation_type="generate_all",
            prompt_items=prompt_items,
            prompt_by_beat={str(item["beat_id"]): item for item in prompt_items},
            prompt_revision_by_beat={
                str(item["beat_id"]): runtime._current_prompt_revision(project, item)
                for item in prompt_items
            },
            section_by_beat=section_by_beat,
            section_id=None,
            target_beat_id=None,
        )

        assert scheduled == ["beat_002"]
        assert blocked == []
        assert snapshots["beat_002"]["source_beat_id"] == "beat_001"
        assert snapshots["beat_002"]["source_kind"] == "approved"


def test_semantic_continuity_without_compiled_requirement_does_not_block_scheduling(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    _seed_project(factory)
    with factory() as session:
        runtime = SceneAssetRuntimeV2(session)
        project = session.get(Project, "project_v2")
        assert project is not None
        _scene_plan, visual_plan, prompt_bundle = _upstream()
        prompt_items = list(prompt_bundle["prompt_items"])
        visual_plan["scenes"][1]["beat_plans"][0].update(
            {
                "continuity_with_previous": "same_setup",
                "previous_beat_image_reference_required": False,
            }
        )
        # The semantic relation remains upstream in the Visual Plan; runtime
        # intentionally consumes only the compiled requirement, which is absent.
        runtime._ensure_beat_states(project, prompt_items)
        session.flush()
        source = session.get(SceneAssetBeatState, (project.project_id, "beat_001"))
        assert source is not None
        assert source.resolution_status == "not_generated"
        assert visual_plan["scenes"][1]["beat_plans"][0]["continuity_with_previous"] == "same_setup"
        assert visual_plan["scenes"][1]["beat_plans"][0]["previous_beat_image_reference_required"] is False

        scheduled, blocked, snapshots = runtime._schedule(
            project=project,
            operation_type="generate_all",
            prompt_items=prompt_items,
            prompt_by_beat={str(item["beat_id"]): item for item in prompt_items},
            prompt_revision_by_beat={
                str(item["beat_id"]): runtime._current_prompt_revision(project, item)
                for item in prompt_items
            },
            section_by_beat={
                str(beat["beat_id"]): str(scene["section_id"])
                for scene in visual_plan["scenes"]
                for beat in scene["beat_plans"]
            },
            section_id=None,
            target_beat_id=None,
        )

        assert scheduled == ["beat_001", "beat_002"]
        assert blocked == []
        assert snapshots == {}


def test_phase_d_same_beat_conflicts_but_different_beat_can_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _factory(tmp_path)
    _seed_project(factory)
    with factory() as session:
        first = SceneAssetRuntimeV2(session)
        _patch_runtime(first, monkeypatch)
        first.submit_generation(
            "project_v2", {"candidate_count": 1}, target_beat_id="beat_001", target_mode="generate_beat"
        )

    with factory() as session:
        same = SceneAssetRuntimeV2(session)
        _patch_runtime(same, monkeypatch)
        with pytest.raises(AppError) as busy:
            same.submit_generation(
                "project_v2", {"candidate_count": 1}, target_beat_id="beat_001", target_mode="generate_beat"
            )
        assert busy.value.error_code in {"SCENE_ASSET_V2_BEAT_BUSY", "SCENE_ASSET_NO_ELIGIBLE_BEATS"}

    with factory() as session:
        different = SceneAssetRuntimeV2(session)
        _patch_runtime(different, monkeypatch)
        result = different.submit_generation(
            "project_v2", {"candidate_count": 1}, target_beat_id="beat_002", target_mode="generate_beat"
        )
        assert result["scheduled_beat_ids"] == ["beat_002"]


def test_phase_d_cancel_is_v2_local_and_creates_no_legacy_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _factory(tmp_path)
    _seed_project(factory)
    with factory() as session:
        runtime = SceneAssetRuntimeV2(session)
        _patch_runtime(runtime, monkeypatch)
        submitted = runtime.submit_generation(
            "project_v2", {"candidate_count": 1}, target_beat_id="beat_001", target_mode="generate_beat"
        )
        operation_id = submitted["operation_id"]
    with factory() as session:
        result = SceneAssetRuntimeV2(session).cancel_operation("project_v2", operation_id)
        assert result["status"] == "cancelled"
    with factory() as session:
        operation = session.get(SceneAssetWorkingOperation, operation_id)
        assert operation is not None and operation.status == "cancelled"
        assert int(session.scalar(select(func.count()).select_from(Job)) or 0) == 0
        assert int(session.scalar(select(func.count()).select_from(SceneAssetOperation)) or 0) == 0


def test_scene_asset_v2_routes_shadow_legacy_generation_and_execution_routes() -> None:
    app = create_app()
    paths = (
        "/api/v4/projects/{project_id}/scene-assets/jobs",
        "/api/v4/projects/{project_id}/scene-assets/execution-status",
        "/api/v4/projects/{project_id}/scene-assets/beats/{beat_id}/generate/jobs",
        "/api/v4/projects/{project_id}/scene-assets/beats/{beat_id}/retry/jobs",
        "/api/v4/projects/{project_id}/scene-assets/beats/{beat_id}/regenerate/jobs",
    )
    for path in paths:
        first = next(route for route in app.routes if getattr(route, "path", None) == path)
        assert first.endpoint.__module__ == "app.api.routes.scene_assets_v2"


def test_phase_d_runtime_has_no_rq_job_or_legacy_scene_asset_operation_dependency() -> None:
    source = inspect.getsource(SceneAssetRuntimeV2)
    assert "queue.enqueue" not in source
    assert "WorkerJobPayload" not in source
    assert "SceneAssetOperationRepository" not in source
    assert "SceneAssetBeatClaimRepository" not in source
    assert "SceneAssetBeatOperationLeaseRepository" not in source
