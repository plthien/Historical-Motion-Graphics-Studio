from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.api.errors import AppError
from app.core.config import get_settings
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactStatus, ArtifactType
from app.db.models import Artifact, Project
from app.db.scene_asset_beat_state import SceneAssetBeatState
from app.db.scene_asset_candidate import SceneAssetCandidate
from app.db.scene_asset_prompt_revision import SceneAssetPromptRevision
from app.db.session import build_session_factory
from app.modules.v4.prompt_composer_service import PromptComposerService
from app.modules.v4.scene_asset_prompt_v2 import SceneAssetPromptV2Service
from app.modules.v4.scene_asset_workspace_v2_runtime import SceneAssetWorkspaceV2Service
from app.modules.v4.scene_breakdown_service import SceneBreakdownServiceV4
from app.services.artifacts import ArtifactService
from tests.v4.combined_scene_breakdown_support import generate_combined_pair


def _approval_request(result: dict[str, object], *, aspect_ratio: str = "16:9") -> dict[str, str]:
    return {
        "expected_scene_plan_artifact_id": str(result["scene_plan_artifact_id"]),
        "expected_visual_plan_artifact_id": str(result["visual_plan_artifact_id"]),
        "aspect_ratio": aspect_ratio,
    }


def _logical_revision_count(session, project_id: str) -> int:
    logical_ids = (
        f"project/{project_id}/scene_plan",
        f"project/{project_id}/visual_plan",
        f"project/{project_id}/prompt_bundle",
        f"project/{project_id}/beat_timing_map",
    )


def _seed_approved_scene_asset_candidates(session, project: Project) -> list[tuple[str, str]]:
    bundle = PromptComposerService(session).get_current_prompt_bundle(project.project_id)["artifact"]
    seeded: list[tuple[str, str]] = []
    for item in bundle["prompt_items"][:2]:
        beat_id = str(item["beat_id"])
        revision = session.query(SceneAssetPromptRevision).filter_by(
            project_id=project.project_id,
            beat_id=beat_id,
            is_current=True,
        ).one()
        artifact_id = generate_id("artifact")
        candidate_id = generate_id("scene_asset_candidate")
        session.add(
            Artifact(
                artifact_id=artifact_id,
                logical_id=f"project/{project.project_id}/scene_assets/{beat_id}/{artifact_id}",
                version_no=1,
                is_current=True,
                artifact_type=ArtifactType.IMAGE,
                artifact_role=None,
                path=f"tests/{artifact_id}.png",
                content_hash=f"hash_{artifact_id}",
                sha256="a" * 64,
                mime_type="image/png",
                size_bytes=1,
                width=1280,
                height=720,
                status=ArtifactStatus.READY,
                created_at=utc_now_iso(),
            )
        )
        session.flush()
        session.add(
            SceneAssetCandidate(
                candidate_id=candidate_id,
                project_id=project.project_id,
                scene_id=str(item["scene_id"]),
                beat_id=beat_id,
                artifact_id=artifact_id,
                source_kind="uploaded",
                operation_beat_id=None,
                operation_task_id=None,
                source_prompt_revision_id=revision.prompt_revision_id,
                source_prompt_bundle_artifact_id=revision.source_prompt_bundle_artifact_id,
                source_prompt_item_id=revision.source_prompt_item_id,
                source_prompt_hash=revision.source_prompt_hash,
                continuity_snapshot_json=None,
                created_at=utc_now_iso(),
            )
        )
        session.flush()
        session.add(
            SceneAssetBeatState(
                project_id=project.project_id,
                beat_id=beat_id,
                scene_id=str(item["scene_id"]),
                version=3,
                resolution_status="approved",
                review_status="approved",
                current_draft_candidate_id=candidate_id,
                selected_candidate_id=candidate_id,
                active_operation_beat_id=None,
                continuity_source_scene_id=None,
                continuity_source_beat_id=None,
                continuity_source_candidate_id=None,
                last_error_json=None,
                last_progress_at=utc_now_iso(),
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
            )
        )
        seeded.append((beat_id, candidate_id))
    session.commit()
    return seeded
    return int(
        session.scalar(select(func.count()).select_from(Artifact).where(Artifact.logical_id.in_(logical_ids)))
        or 0
    )


def test_approve_atomically_publishes_approved_pair_and_prompt_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch, approve=True)
    factory = build_session_factory(get_settings())
    with factory() as session:
        project = session.get(Project, result["project_id"])
        assert project is not None
        assert project.scene_plan_artifact_id == result["scene_plan_artifact_id"]
        assert project.visual_plan_artifact_id == result["visual_plan_artifact_id"]
        assert project.prompt_bundle_artifact_id is not None

        current = PromptComposerService(session).get_current_prompt_bundle(result["project_id"])
        bundle = current["artifact"]
        assert current["artifact_id"] == project.prompt_bundle_artifact_id
        assert bundle["status"] == "approved"
        assert bundle["revision_operation"] == "automatic_compilation"
        assert bundle["parent_prompt_bundle_revision_id"] is None
        assert bundle["source_scene_plan_revision_id"] == result["scene_plan_artifact_id"]
        assert bundle["source_visual_plan_revision_id"] == result["visual_plan_artifact_id"]
        assert bundle["options"]["aspect_ratio"] == "16:9"
        beat_count = sum(len(scene["beat_plans"]) for scene in result["visual_plan"]["scenes"])
        assert bundle["source_beat_count"] == beat_count
        assert bundle["prompt_item_count"] == beat_count
        assert len(bundle["prompt_items"]) == beat_count

        rows = list(
            session.scalars(
                select(Artifact).where(
                    Artifact.artifact_id.in_(
                        [
                            result["scene_plan_artifact_id"],
                            result["visual_plan_artifact_id"],
                            project.prompt_bundle_artifact_id,
                            project.beat_timing_map_artifact_id,
                        ]
                    )
                )
            )
        )
        assert len(rows) == 4
        assert all(row.is_current for row in rows)
        assert all(row.superseded_by_artifact_id is None for row in rows)


@pytest.mark.parametrize("operation", ["manual_edit", "beat_recompilation"])
def test_real_beat_prompt_mutation_keeps_sibling_workspace_candidate_current(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    result = generate_combined_pair(monkeypatch, approve=True)
    factory = build_session_factory(get_settings())
    with factory() as session:
        project = session.get(Project, result["project_id"])
        assert project is not None
        (target_beat_id, target_candidate_id), (sibling_beat_id, sibling_candidate_id) = (
            _seed_approved_scene_asset_candidates(session, project)
        )
        target_state = session.get(SceneAssetBeatState, (project.project_id, target_beat_id))
        target_revision = session.query(SceneAssetPromptRevision).filter_by(
            project_id=project.project_id,
            beat_id=target_beat_id,
            is_current=True,
        ).one()
        assert target_state is not None

        prompt_service = SceneAssetPromptV2Service(session)
        if operation == "manual_edit":
            prompt_service.edit(
                project.project_id,
                target_beat_id,
                expected_beat_version=target_state.version,
                expected_prompt_revision_id=target_revision.prompt_revision_id,
                image_prompt=target_revision.image_prompt + " with a deliberate local change",
                negative_prompt=target_revision.negative_prompt,
            )
        else:
            prompt_service.restore(
                project.project_id,
                target_beat_id,
                expected_beat_version=target_state.version,
                expected_prompt_revision_id=target_revision.prompt_revision_id,
            )

        workspace = SceneAssetWorkspaceV2Service(session).workspace(project.project_id)
        projected = {
            beat["beat_id"]: beat
            for scene in workspace["scenes"]
            for beat in scene["beats"]
        }
        target = projected[target_beat_id]
        sibling = projected[sibling_beat_id]

        assert target_candidate_id not in {
            candidate["candidate_id"] for candidate in target["candidates"]
        }
        assert target["candidates"] == []
        assert target["current_draft_candidate_id"] is None
        assert target["selected_candidate_id"] is None
        assert target["resolution_status"] == "not_generated"
        assert target["review_status"] == "unreviewed"
        assert target["can_generate"] is True
        assert target["can_regenerate"] is False
        assert target["can_review"] is False
        assert target["can_approve"] is False

        assert [candidate["candidate_id"] for candidate in sibling["candidates"]] == [
            sibling_candidate_id
        ]
        assert sibling["current_draft_candidate_id"] == sibling_candidate_id
        assert sibling["selected_candidate_id"] == sibling_candidate_id
        assert sibling["resolution_status"] == "approved"
        assert sibling["review_status"] == "approved"
        assert sibling["can_generate"] is False
        assert sibling["can_regenerate"] is False
        assert sibling["can_review"] is False
        assert sibling["can_approve"] is False


def test_prompt_compilation_failure_leaves_draft_pair_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    factory = build_session_factory(get_settings())
    with factory() as session:
        before_count = _logical_revision_count(session, result["project_id"])

    def fail_compile(*args, **kwargs):
        raise AppError(
            "PROMPT_COMPOSER_COMPILATION_FAILED",
            "Synthetic compilation failure.",
            409,
            retryable=False,
        )

    monkeypatch.setattr(PromptComposerService, "build_automatic_approved_bundle", fail_compile)
    with factory() as session:
        with pytest.raises(AppError) as exc:
            SceneBreakdownServiceV4(session).approve(
                result["project_id"],
                _approval_request(result),
            )
    assert exc.value.error_code == "PROMPT_COMPOSER_COMPILATION_FAILED"

    with factory() as session:
        project = session.get(Project, result["project_id"])
        assert project is not None
        assert project.scene_plan_artifact_id == result["scene_plan_artifact_id"]
        assert project.visual_plan_artifact_id == result["visual_plan_artifact_id"]
        assert project.prompt_bundle_artifact_id is None
        assert project.beat_timing_map_artifact_id is None
        assert _logical_revision_count(session, result["project_id"]) == before_count


def test_failure_after_staging_rolls_back_rows_and_cleans_all_three_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    factory = build_session_factory(get_settings())
    ids = iter(
        [
            "artifact_a1a1a1a1a1a1",
            "artifact_b2b2b2b2b2b2",
            "artifact_c3c3c3c3c3c3",
            "artifact_444444444444",
        ]
    )
    monkeypatch.setattr(ArtifactService, "preallocate_artifact_id", lambda self: next(ids))

    def fail_after_stage(self, **kwargs):
        raise AppError(
            "SYNTHETIC_ATOMIC_STAGE_FAILURE",
            "Synthetic failure after all artifacts were staged.",
            500,
            retryable=False,
        )

    monkeypatch.setattr(
        SceneBreakdownServiceV4,
        "_after_scene_visual_prompt_atomic_stage",
        fail_after_stage,
    )
    settings = get_settings()
    artifact_root = settings.resolve_configured_path(settings.artifact_root)
    before_files = (
        {path.relative_to(artifact_root).as_posix() for path in artifact_root.rglob("*") if path.is_file()}
        if artifact_root.exists()
        else set()
    )
    with factory() as session:
        before_count = _logical_revision_count(session, result["project_id"])
        with pytest.raises(AppError) as exc:
            SceneBreakdownServiceV4(session).approve(
                result["project_id"],
                _approval_request(result, aspect_ratio="9:16"),
            )
    assert exc.value.error_code == "SYNTHETIC_ATOMIC_STAGE_FAILURE"

    expected_ids = {
        "artifact_a1a1a1a1a1a1",
        "artifact_b2b2b2b2b2b2",
        "artifact_c3c3c3c3c3c3",
        "artifact_444444444444",
    }
    with factory() as session:
        project = session.get(Project, result["project_id"])
        assert project is not None
        assert project.scene_plan_artifact_id == result["scene_plan_artifact_id"]
        assert project.visual_plan_artifact_id == result["visual_plan_artifact_id"]
        assert project.prompt_bundle_artifact_id is None
        assert project.beat_timing_map_artifact_id is None
        assert _logical_revision_count(session, result["project_id"]) == before_count
        assert session.scalar(
            select(func.count()).select_from(Artifact).where(Artifact.artifact_id.in_(expected_ids))
        ) == 0

    after_files = (
        {path.relative_to(artifact_root).as_posix() for path in artifact_root.rglob("*") if path.is_file()}
        if artifact_root.exists()
        else set()
    )
    assert after_files == before_files


def test_atomic_cas_loss_keeps_draft_pair_and_cleans_staged_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.repositories import ProjectRepository

    result = generate_combined_pair(monkeypatch)
    factory = build_session_factory(get_settings())
    ids = iter(
        [
            "artifact_d4d4d4d4d4d4",
            "artifact_e5e5e5e5e5e5",
            "artifact_f6f6f6f6f6f6",
            "artifact_777777777777",
        ]
    )
    monkeypatch.setattr(ArtifactService, "preallocate_artifact_id", lambda self: next(ids))
    monkeypatch.setattr(
        ProjectRepository,
        "compare_and_set_scene_visual_prompt_bundle_v4",
        lambda self, **kwargs: False,
    )

    with factory() as session:
        before_count = _logical_revision_count(session, result["project_id"])
        with pytest.raises(AppError) as exc:
            SceneBreakdownServiceV4(session).approve(
                result["project_id"],
                _approval_request(result),
            )
    assert exc.value.error_code == "SCENE_VISUAL_PROMPT_BUNDLE_REVISION_CONFLICT"

    expected_ids = {
        "artifact_d4d4d4d4d4d4",
        "artifact_e5e5e5e5e5e5",
        "artifact_f6f6f6f6f6f6",
        "artifact_777777777777",
    }
    with factory() as session:
        project = session.get(Project, result["project_id"])
        assert project is not None
        assert project.scene_plan_artifact_id == result["scene_plan_artifact_id"]
        assert project.visual_plan_artifact_id == result["visual_plan_artifact_id"]
        assert project.prompt_bundle_artifact_id is None
        assert _logical_revision_count(session, result["project_id"]) == before_count
        assert session.scalar(
            select(func.count()).select_from(Artifact).where(Artifact.artifact_id.in_(expected_ids))
        ) == 0


def test_approve_requires_explicit_aspect_ratio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch)
    factory = build_session_factory(get_settings())
    with factory() as session:
        with pytest.raises(AppError) as exc:
            SceneBreakdownServiceV4(session).approve(
                result["project_id"],
                {
                    "expected_scene_plan_artifact_id": result["scene_plan_artifact_id"],
                    "expected_visual_plan_artifact_id": result["visual_plan_artifact_id"],
                },
            )
    assert exc.value.error_code == "SCENE_PLAN_APPROVE_REQUEST_INVALID"

    with factory() as session:
        project = session.get(Project, result["project_id"])
        assert project is not None
        assert project.scene_plan_artifact_id == result["scene_plan_artifact_id"]
        assert project.visual_plan_artifact_id == result["visual_plan_artifact_id"]
        assert project.prompt_bundle_artifact_id is None


def test_existing_manual_prompt_bundle_revision_routes_still_work_after_auto_compilation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = generate_combined_pair(monkeypatch, approve=True)
    factory = build_session_factory(get_settings())
    with factory() as session:
        project = session.get(Project, result["project_id"])
        assert project is not None and project.prompt_bundle_artifact_id is not None
        service = PromptComposerService(session)
        recompiled = service.recompile_full(
            result["project_id"],
            {
                "style_bible_artifact_id": project.style_bible_artifact_id,
                "visual_plan_artifact_id": project.visual_plan_artifact_id,
                "expected_prompt_bundle_artifact_id": project.prompt_bundle_artifact_id,
                "options": {
                    "schema_version": "1.0",
                    "prompt_language": "en",
                    "aspect_ratio": "9:16",
                    "target_asset_role": "beat_visual",
                    "image_count": 1,
                },
            },
        )
        assert recompiled["artifact"]["status"] == "draft_ready"
        assert recompiled["artifact"]["parent_prompt_bundle_revision_id"] is not None
        assert recompiled["artifact"]["options"]["aspect_ratio"] == "9:16"

    with factory() as session:
        project = session.get(Project, result["project_id"])
        assert project is not None and project.prompt_bundle_artifact_id is not None
        approved = PromptComposerService(session).approve(
            result["project_id"],
            {
                "style_bible_artifact_id": project.style_bible_artifact_id,
                "visual_plan_artifact_id": project.visual_plan_artifact_id,
                "expected_prompt_bundle_artifact_id": project.prompt_bundle_artifact_id,
            },
        )
        assert approved["artifact"]["status"] == "approved"
        assert approved["artifact"]["revision_operation"] == "approval"
