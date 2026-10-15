#!/usr/bin/env python3
"""Validate repository-specific History Studio coding guidance (not a release manifest)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
    "clean-cut-migration",
    "external-chatgpt-authoring",
    "historical-asset-research",
    "remotion-motion-graphics",
    "voice-alignment",
    "implementation-audit",
)
AGENT_ROLES = (
    "architecture-agent.md",
    "backend-agent.md",
    "frontend-agent.md",
    "remotion-agent.md",
    "historical-assets-agent.md",
    "voice-agent.md",
    "qa-audit-agent.md",
)
REQUIRED_FILES = (
    "AGENTS.md",
    "docs/history/CODING_RULES.md",
    "docs/history/HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md",
    "docs/history/phases/README.md",
    "docs/history/agents/README.md",
    ".agents/skills/README.md",
    "00_START_HERE.md",
    "docs/00_INDEX.md",
    "docs/history/CODEX_P0_ON_NEW_MAIN_PROMPT.md",
    "docs/history/CODEX_P1_STRICT_CLEAN_CUT_PROMPT.md",
)
FORBIDDEN_FILES = (
    "docs/32_CODING_RULES.md",
    "docs/64_DUAL_OFFICIAL_LLM_IMPLEMENTATION_PLAN_2_PHASES.md",
    "docs/65_DUAL_OFFICIAL_LLM_CODEX_PROMPTS_2_PHASES.md",
    "MANIFEST.json",
    "tools/package_manifest.py",
    ".github/workflows/refresh-release-manifest.yml",
)
FORBIDDEN_DIRS = (
    "tools/manifest_authority",
)
ENTRYPOINTS = (
    "AGENTS.md",
    "00_START_HERE.md",
    "docs/00_INDEX.md",
    "docs/history/phases/README.md",
)


def audit(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for path in REQUIRED_FILES:
        if not (root / path).is_file():
            errors.append(f"Missing required active guidance: {path}")
    for path in FORBIDDEN_FILES:
        if (root / path).exists():
            errors.append(f"Forbidden obsolete guidance or manifest: {path}")
    for path in FORBIDDEN_DIRS:
        if (root / path).exists():
            errors.append(f"Forbidden obsolete directory: {path}")
    for slug in SKILLS:
        path = root / ".agents" / "skills" / slug / "SKILL.md"
        if not path.is_file():
            errors.append(f"Missing NEW skill: {path.relative_to(root)}")
            continue
        contents = path.read_text(encoding="utf-8")
        if not contents.startswith("---\n") or f"name: {slug}" not in contents.split("---", 2)[1]:
            errors.append(f"Invalid SKILL.md YAML frontmatter/name: {slug}")
        if "CODING_RULES.md" not in contents or "AGENTS.md" not in contents:
            errors.append(f"Skill does not direct agent to NEW governance: {slug}")
    for name in AGENT_ROLES:
        path = root / "docs" / "history" / "agents" / name
        if not path.is_file():
            errors.append(f"Missing NEW agent: {name}")
            continue
        if "CODING_RULES.md" not in path.read_text(encoding="utf-8"):
            errors.append(f"Agent lacks NEW coding rule authority: {name}")
    for name in ENTRYPOINTS:
        path = root / name
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            if "CODING_RULES.md" not in text or (
                "AGENTS.md" not in text and name != "AGENTS.md"
            ):
                errors.append(f"Entrypoint missing NEW governance reference: {name}")
    code_rules = root / "docs/history/CODING_RULES.md"
    if code_rules.is_file():
        text = code_rules.read_text(encoding="utf-8")
        for marker in ("NO LLM API", "P1", ".agents/skills", "Remotion", "MANIFEST.json"):
            if marker not in text:
                errors.append(f"NEW coding rules missing required authority marker: {marker}")
    for workflow in (root / ".github/workflows").glob("*.yml"):
        content = workflow.read_text(encoding="utf-8")
        for code in ("tools/package_manifest.py", "git add MANIFEST.json",
                     "git diff --exit-code -- MANIFEST.json"):
            if code in content:
                errors.append(f"Obsolete manifest action in workflow {workflow.name}: {code}")
    return errors


def main() -> int:
    errors = audit()
    if errors:
        for item in errors:
            print("HISTORY_GOVERNANCE_ERROR: " + item, file=sys.stderr)
        return 1
    print("History governance PASS: only new coding rules/agents/skills are active; obsolete rules and root manifest authority removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
