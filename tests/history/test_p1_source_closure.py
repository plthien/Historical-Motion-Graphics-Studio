"""Current production tree only; P0 reference experiment and History governance excluded."""
from __future__ import annotations
import ast
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
LEGACY_TERMS=("app.modules.v4","scene_assets","scene_breakdown","prompt_bundle","visual_reference",
              "youtube_ai_studio","durable_media_workflow","assembly_service","render_coordinator_runner")

def test_single_history_runtime_and_no_old_runtime_modules():
    allowed={p.relative_to(ROOT).as_posix() for p in (ROOT/"app").rglob("*.py")}
    assert allowed
    assert all(p=="app/main.py" or p=="app/__init__.py" or p.startswith("app/history/") for p in allowed)
    for path in (ROOT/"app").rglob("*.py"):
        tree=ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                imports=[a.name for a in node.names]
            elif isinstance(node,ast.ImportFrom):
                imports=[node.module or ""]
            else:continue
            assert not any(imported.startswith(("app.modules","app.db","app.services","app.repositories")) for imported in imports)

def test_old_contracts_migrations_workers_and_root_manifest_absent():
    assert not (ROOT/"MANIFEST.json").exists()
    assert not (ROOT/"docs/32_CODING_RULES.md").exists()
    assert not (ROOT/"docs/99_SOURCE_OF_TRUTH.md").exists()
    assert not (ROOT/"app/modules").exists()
    assert not (ROOT/"kokoro").exists()
    assert not (ROOT/"scripts/windows/start_durable_media_workflow.bat").exists()
    assert not (ROOT/"scripts/windows/start_durable_render.bat").exists()
    migrations=list((ROOT/"alembic/versions").glob("*.py"))
    assert sorted(p.name for p in migrations)==["001_history_initial.py","002_history_authoring_p2.py"]
    for p in (ROOT/"contracts").rglob("*"):
        assert not any(term in p.name.lower() for term in ("beat","scene","ytb","youtube","v4"))
    assert not any(p.name.startswith("test_v4") for p in (ROOT/"tests").rglob("*"))

def test_documentation_authority_and_root_config():
    for name in ("AGENTS.md","00_START_HERE.md","docs/00_INDEX.md","docs/history/CODING_RULES.md",
                 ".agents/skills/README.md","docs/history/P1_ACCEPTANCE_GATES.md"):
        assert (ROOT/name).is_file()
    for name in ("README.md","00_START_HERE.md","docs/00_INDEX.md"):
        content=(ROOT/name).read_text(encoding="utf-8")
        assert "Historical Motion Graphics Studio" in content
        assert "CODING_RULES.md" in content
    for forbidden in ("openai","anthropic","gemini","ollama","langchain"):
        assert forbidden not in (ROOT/"requirements.txt").read_text().lower()
        assert forbidden not in (ROOT/"package.json").read_text().lower()
