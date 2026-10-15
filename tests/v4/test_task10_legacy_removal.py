from __future__ import annotations

import ast
import re
from pathlib import Path

from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_IDENTIFIERS = (
    "story_context_artifact_id",
    "source_story_context_revision_id",
    "act_id",
    "act_context",
    "carry_over_from_previous_act",
    "narrative_structure.acts",
    "act_regeneration",
    "scene_breakdown_act",
)


def _text_files(*roots: str):
    suffixes = {".py", ".json", ".md", ".ts", ".tsx", ".js", ".yaml", ".yml"}
    for root_name in roots:
        root = ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in suffixes:
                yield path


def test_legacy_runtime_contract_and_prompt_trees_are_physically_absent() -> None:
    assert not (ROOT / "app/modules/v3").exists()
    assert not (ROOT / "contracts/schemas/v3").exists()
    assert not (ROOT / "contracts/prompts/v3").exists()
    assert not (ROOT / "app/api/routes/v3.py").exists()
    assert not (ROOT / "app/api/routes/active_v3.py").exists()


def test_active_runtime_contracts_and_frontend_have_no_removed_identifiers() -> None:
    matches: list[str] = []
    for path in _text_files("app", "contracts", "src"):
        if path == ROOT / "contracts/openapi/youtube_ai_studio_api.openapi.yaml":
            pass
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_IDENTIFIERS:
            pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])")
            if pattern.search(text):
                matches.append(f"{path.relative_to(ROOT)}:{token}")
    assert matches == []


def test_active_python_runtime_has_no_v3_module_imports() -> None:
    violations: list[str] = []
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.startswith("app.modules.v3") for name in names):
                violations.append(str(path.relative_to(ROOT)))
    assert violations == []


def test_openapi_exposes_no_superseded_creative_routes() -> None:
    paths = set(create_app().openapi()["paths"])
    forbidden = (
        "/api/v3/projects/{project_id}/story-analyzer",
        "/api/v3/projects/{project_id}/style-bible",
        "/api/v3/projects/{project_id}/scene-breakdown",
        "/api/v3/projects/{project_id}/visual-planner",
        "/api/v3/projects/{project_id}/prompt-bundle",
        "/api/v3/projects/{project_id}/scene-assets",
        "/api/v3/projects/{project_id}/voice",
        "/api/v3/projects/{project_id}/assembly",
        "/api/v3/projects/{project_id}/render",
    )
    assert not any(path.startswith(forbidden) for path in paths)


def test_independent_visual_planner_runtime_and_stale_paired_response_names_are_absent() -> None:
    assert not (ROOT / "contracts/prompts/v4/visual_planner").exists()
    assert not (ROOT / "contracts/schemas/v4/scene_plan_revision_response_v1.schema.json").exists()
    assert (ROOT / "contracts/schemas/v4/scene_breakdown_revision_response_v1.schema.json").is_file()

    active_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in _text_files("app", "contracts/schemas/v4", "src")
        if path != ROOT / "contracts/openapi/youtube_ai_studio_api.openapi.yaml"
    )
    for token in (
        "visual_planner_plan",
        "visual_planner_v4_generate",
        "visual_planner_v4_regenerate_scene",
        "visual_planner_v4_regenerate_failed_scene",
        "VisualPlannerPanel",
        "ScenePlanArtifactResponseV4",
        "ScenePlanReadResponseV4",
        "ApproveScenePlanRequestV4",
    ):
        assert token not in active_text

    paths = set(create_app().openapi()["paths"])
    assert not any("/visual-planner" in path for path in paths)
    assert {path for path in paths if path.endswith("/visual-plan")} == {
        "/api/v4/projects/{project_id}/visual-plan"
    }
