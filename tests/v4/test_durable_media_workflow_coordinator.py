from __future__ import annotations

from pathlib import Path

from app.modules.media.durable_workflow.coordinator import DurableMediaCoordinator
from app.modules.v4.scene_asset_coordinator import SceneAssetCoordinator


def test_scene_asset_coordinator_is_compatibility_facade_over_shared_core() -> None:
    source = Path(__file__).resolve().parents[2] / "app" / "modules" / "v4" / "scene_asset_coordinator.py"
    text = source.read_text(encoding="utf-8")
    assert "DurableMediaCoordinator" in text
    assert "SceneAssetWorkflowRepositoryAdapter" in text
    assert "class SceneAssetCoordinator" in text


def test_shared_coordinator_has_no_scene_or_visual_reference_domain_imports() -> None:
    source = Path(__file__).resolve().parents[2] / "app" / "modules" / "media" / "durable_workflow" / "coordinator.py"
    text = source.read_text(encoding="utf-8").lower()
    assert "scene_asset" not in text
    assert "visual_reference" not in text
    assert DurableMediaCoordinator.__name__ == "DurableMediaCoordinator"
    assert SceneAssetCoordinator.__name__ == "SceneAssetCoordinator"
