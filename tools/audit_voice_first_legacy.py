#!/usr/bin/env python3
"""Classify every Task 5 legacy-token hit in the exact candidate tree."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import subprocess


TOKENS = (
    "narration_timing_map_artifact_id",
    "weighted_text_alignment_v1",
    "with-timestamps",
    "VOICE_NOT_READY",
    "kokoro",
    "source_scene_plan_revision_id",
)

TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}


def _tracked_files(root: Path) -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
    return [root / value.decode("utf-8") for value in output.split(b"\0") if value]


def _classification(relative: str, token: str) -> str:
    lower = relative.lower()
    if relative == "tools/audit_voice_first_legacy.py":
        return "STATIC_AUDIT_TOOLING"
    if relative == "MANIFEST.json":
        return "GENERATED_MANIFEST_METADATA"
    if relative.startswith("alembic/versions/"):
        return "HISTORICAL_MIGRATION"
    if relative.startswith("tests/"):
        return "TEST_OR_REGRESSION_GUARD"
    if relative.startswith("docs/") or relative in {"00_START_HERE.md", "README.md"}:
        return "DOCUMENTATION_OR_SPEC"
    if relative.startswith("kokoro/"):
        return "NON_PRODUCTION_KOKORO_TOOLING"
    if relative.startswith(".github/"):
        return "CI_TOOLING"
    if relative.startswith("tools/"):
        return "NON_PRODUCTION_TOOLING"

    active_root = relative.startswith(("app/", "src/", "frontend/", "contracts/"))
    if token == "kokoro" and active_root:
        return "ACTIVE_KOKORO_PROVIDER_SURFACE"
    if token == "source_scene_plan_revision_id" and active_root:
        if "voice" in lower:
            return "ACTIVE_VOICE_LINEAGE_FORBIDDEN"
        return "SCENE_OR_DOWNSTREAM_LINEAGE"
    if active_root:
        return "ACTIVE_PRODUCTION_REVIEW_REQUIRED"
    return "REVIEW_REQUIRED"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    hits: list[tuple[str, str, str, int, str]] = []
    for path in _tracked_files(root):
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"Dockerfile"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        relative = path.relative_to(root).as_posix()
        for line_number, line in enumerate(text.splitlines(), 1):
            for token in TOKENS:
                if token.lower() in line.lower():
                    hits.append((token, _classification(relative, token), relative, line_number, line.strip()))

    counts = Counter(classification for _token, classification, _path, _line, _text in hits)
    print("VOICE_FIRST_LEGACY_AUDIT")
    print(f"TOTAL_HITS={len(hits)}")
    for classification in sorted(counts):
        print(f"CLASSIFICATION {classification}={counts[classification]}")
    for token, classification, relative, line_number, line in hits:
        print(f"HIT {token} | {classification} | {relative}:{line_number} | {line}")

    blockers = [
        hit
        for hit in hits
        if hit[1] in {
            "ACTIVE_VOICE_LINEAGE_FORBIDDEN",
            "ACTIVE_PRODUCTION_REVIEW_REQUIRED",
            "REVIEW_REQUIRED",
        }
    ]
    if blockers:
        print(f"BLOCKING_HITS={len(blockers)}")
        return 1
    print("BLOCKING_HITS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
