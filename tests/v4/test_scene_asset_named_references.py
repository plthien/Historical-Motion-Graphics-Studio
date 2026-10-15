from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from app.api.errors import AppError
from app.core.config import get_settings
from app.db.enums import ArtifactType
from app.db.session import build_session_factory
from app.modules.media.media_task_service import (
    MediaTaskService,
    NormalizedImageGenerationCommand,
    PERSISTED_SCENE_ASSET_PURPOSE,
)
from app.modules.v4.prompt_composer import _approved_reference_semantics
from app.modules.v4.scene_asset_provider_execution import SceneAssetProviderExecutionService
from app.repositories import ArtifactRepository
from app.services.artifacts import ArtifactService
from app.services.media_settings import default_media_provider_settings
from tests.v4.test_style_bible_consumer import seed_project


def _png() -> bytes:
    image = Image.new("RGB", (32, 32), (64, 96, 128))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class CaptureClient:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def submit_image(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        return {"task_id": f"provider-{len(self.payloads)}", "status": "pending"}


def _provider_settings() -> dict[str, Any]:
    settings = default_media_provider_settings()
    settings.update(
        {
            "enabled": True,
            "api_key": "named-reference-test",
            "base_url": "http://127.0.0.1:8765",
            "poll_interval_seconds": 1,
            "max_parallel_tasks": 5,
        }
    )
    return settings


def test_prompt_reference_semantics_use_canonical_anchor_tags() -> None:
    beat = {
        "selected_reference_bindings": [
            {"anchor_type": "character", "anchor_id": "char_001"},
            {"anchor_type": "location", "anchor_id": "loc_001"},
        ]
    }
    lines = _approved_reference_semantics(
        beat,
        characters={"char_001": {"label": "Reconstructed child", "description": "stable child identity"}},
        locations={"loc_001": {"label": "Ice Age camp", "description": "stable camp identity"}},
        props={},
        recurring_subjects={},
    )
    assert len(lines) == 2
    assert "@char_001" in lines[0]
    assert "@loc_001" in lines[1]
    assert "supplied approved reference image" not in "\n".join(lines)


def test_scene_asset_reference_descriptors_preserve_anchor_id_not_storage_name() -> None:
    beat = SimpleNamespace(
        reference_bindings_json=[
            {
                "anchor_id": "char_001",
                "media_artifact_id": "artifact_000000000101",
                "media_sha256": "1" * 64,
                "mime_type": "image/png",
            },
            {
                "anchor_id": "loc_001",
                "media_artifact_id": "artifact_000000000102",
                "media_sha256": "2" * 64,
                "mime_type": "image/webp",
            },
        ],
        continuity_snapshot_json=None,
    )
    descriptors = SceneAssetProviderExecutionService._reference_descriptors(beat)
    assert descriptors == [
        {
            "reference_tag": "char_001",
            "artifact_id": "artifact_000000000101",
            "sha256": "1" * 64,
            "mime_type": "image/png",
        },
        {
            "reference_tag": "loc_001",
            "artifact_id": "artifact_000000000102",
            "sha256": "2" * 64,
            "mime_type": "image/webp",
        },
    ]


def test_media_provider_receives_named_references_independent_of_physical_filename() -> None:
    project_id, _, _ = seed_project()
    factory = build_session_factory(get_settings())
    with factory() as session:
        artifacts = ArtifactService(session, ArtifactRepository(session), get_settings())
        char = artifacts.write_artifact(
            logical_id=f"project/{project_id}/style_bible/test/characters/char_001/reference",
            artifact_type=ArtifactType.IMAGE,
            filename="result_001_v001.png",
            content=_png(),
        ).artifact
        loc = artifacts.write_artifact(
            logical_id=f"project/{project_id}/style_bible/test/locations/loc_001/reference",
            artifact_type=ArtifactType.IMAGE,
            filename="result_001_v001.png",
            content=_png(),
        ).artifact
        assert Path(char.path).name == Path(loc.path).name == "result_001_v001.png"

        command = NormalizedImageGenerationCommand(
            purpose=PERSISTED_SCENE_ASSET_PURPOSE,
            project_id=project_id,
            domain_revision_id="operation_named_refs",
            style_bible_revision_id=None,
            entity_type="beat",
            entity_id="beat_001",
            candidate_count=1,
            prompt=(
                "Approved reference identity: Use @char_001 as the exact character identity authority. "
                "Use @loc_001 as the exact location identity authority."
            ),
            negative_prompt="",
            model="nano_banana_pro",
            aspect_ratio="16:9",
            upscale=tuple(),
            reference_images=(
                {
                    "reference_tag": "char_001",
                    "artifact_id": char.artifact_id,
                    "sha256": char.sha256,
                    "mime_type": char.mime_type,
                },
                {
                    "reference_tag": "loc_001",
                    "artifact_id": loc.artifact_id,
                    "sha256": loc.sha256,
                    "mime_type": loc.mime_type,
                },
            ),
            storage_namespace=f"project/{project_id}/scene_assets/prompt_bundle_named_refs/beat_001",
            metadata={},
        )
        client = CaptureClient()
        response = MediaTaskService(session).submit_internal_image_generation(
            command,
            provider_settings=_provider_settings(),
            client=client,
            submit_immediately=True,
        )
        assert response["tasks"]
        assert len(client.payloads) == 1
        references = client.payloads[0]["reference_images"]
        assert [item["name"] for item in references] == ["char_001.png", "loc_001.png"]
        assert all(item["data"].startswith("data:image/png;base64,") for item in references)
        assert all("result_001_v001" not in item["name"] for item in references)


def test_scene_asset_named_reference_tags_must_match_prompt_exactly() -> None:
    project_id, _, _ = seed_project()
    factory = build_session_factory(get_settings())
    with factory() as session:
        artifacts = ArtifactService(session, ArtifactRepository(session), get_settings())
        image = artifacts.write_artifact(
            logical_id=f"project/{project_id}/style_bible/test/characters/char_001/reference",
            artifact_type=ArtifactType.IMAGE,
            filename="visual_reference_upload.png",
            content=_png(),
        ).artifact
        command = NormalizedImageGenerationCommand(
            purpose=PERSISTED_SCENE_ASSET_PURPOSE,
            project_id=project_id,
            domain_revision_id="operation_bad_named_refs",
            style_bible_revision_id=None,
            entity_type="beat",
            entity_id="beat_001",
            candidate_count=1,
            prompt="Use @loc_001 as the reference.",
            negative_prompt="",
            model="nano_banana_pro",
            aspect_ratio="16:9",
            upscale=tuple(),
            reference_images=(
                {
                    "reference_tag": "char_001",
                    "artifact_id": image.artifact_id,
                    "sha256": image.sha256,
                    "mime_type": image.mime_type,
                },
            ),
            storage_namespace=f"project/{project_id}/scene_assets/prompt_bundle_bad_refs/beat_001",
            metadata={},
        )
        with pytest.raises(AppError) as exc:
            MediaTaskService(session).submit_internal_image_generation(
                command,
                provider_settings=_provider_settings(),
                client=CaptureClient(),
                submit_immediately=True,
            )
        assert exc.value.error_code == "MEDIA_REFERENCE_IMAGE_INVALID"
        assert exc.value.details == {
            "expected_reference_tags": ["char_001"],
            "prompt_reference_tags": ["loc_001"],
        }
