#!/usr/bin/env python3
"""Fail when Redis/RQ remains in the active runtime or release wiring."""

from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_RUNTIME_FILES = (
    "app/worker.py",
    "app/infrastructure/redis.py",
    "app/modules/v4/render_worker.py",
    "app/modules/v4/render_recovery.py",
    "scripts/windows/start_worker.bat",
)

FORBIDDEN_RUNTIME_TOKENS = (
    "create_queue",
    "queue.enqueue",
    "app.worker",
    "RENDER_QUEUE_UNAVAILABLE",
    "REDIS_URL",
    "REDIS_QUEUE_NAME",
    "RQ_WORKER_MODE",
)

FORBIDDEN_IMPORT_ROOTS = {"redis", "rq"}


def _tracked_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [ROOT / raw.decode("utf-8") for raw in output.split(b"\0") if raw]


def _active_runtime_files() -> list[Path]:
    result: list[Path] = []
    for path in _tracked_files():
        relative = path.relative_to(ROOT).as_posix()
        if relative == "start.bat" or relative.startswith("app/") or relative.startswith("scripts/windows/"):
            result.append(path)
    return result


def _python_import_blockers(path: Path, source: str) -> list[str]:
    blockers: list[str] = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"PYTHON_PARSE_ERROR {path.relative_to(ROOT).as_posix()}:{exc.lineno}: {exc.msg}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in FORBIDDEN_IMPORT_ROOTS:
                    blockers.append(
                        f"FORBIDDEN_IMPORT {path.relative_to(ROOT).as_posix()}:{node.lineno}: import {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root in FORBIDDEN_IMPORT_ROOTS:
                blockers.append(
                    f"FORBIDDEN_IMPORT {path.relative_to(ROOT).as_posix()}:{node.lineno}: from {node.module} import ..."
                )
    return blockers


def _requirements_blockers() -> list[str]:
    path = ROOT / "requirements.txt"
    blockers: list[str] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)", line)
        if not match:
            continue
        package = match.group(1).lower().replace("_", "-")
        if package in FORBIDDEN_IMPORT_ROOTS:
            blockers.append(f"FORBIDDEN_DEPENDENCY requirements.txt:{line_number}: {raw.strip()}")
    return blockers


def _workflow_blockers() -> list[str]:
    path = ROOT / ".github" / "workflows" / "scene-assets-v2-candidate-audit.yml"
    source = path.read_text(encoding="utf-8")
    blockers: list[str] = []
    for line_number, line in enumerate(source.splitlines(), 1):
        lowered = line.lower()
        if re.match(r"^\s+redis:\s*$", lowered) or "image: redis:" in lowered:
            blockers.append(f"FORBIDDEN_CI_REDIS {path.relative_to(ROOT).as_posix()}:{line_number}: {line.strip()}")
    return blockers


def main() -> int:
    blockers: list[str] = []

    for relative in FORBIDDEN_RUNTIME_FILES:
        if (ROOT / relative).exists():
            blockers.append(f"FORBIDDEN_RUNTIME_FILE {relative}")

    blockers.extend(_requirements_blockers())
    blockers.extend(_workflow_blockers())

    for path in _active_runtime_files():
        if not path.is_file():
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeError:
            source = path.read_text(encoding="ascii")
        relative = path.relative_to(ROOT).as_posix()
        if path.suffix.lower() == ".py":
            blockers.extend(_python_import_blockers(path, source))
        for line_number, line in enumerate(source.splitlines(), 1):
            for token in FORBIDDEN_RUNTIME_TOKENS:
                if token in line:
                    blockers.append(
                        f"FORBIDDEN_RUNTIME_TOKEN {relative}:{line_number}: {token}: {line.strip()}"
                    )

    print("REDIS_RQ_CLOSURE_AUDIT")
    if blockers:
        for blocker in blockers:
            print(f"BLOCKER {blocker}")
        print(f"BLOCKING_HITS={len(blockers)}")
        return 1

    print("BLOCKING_HITS=0")
    print("Redis/RQ packages, imports, queue dispatch, worker surfaces, CI service wiring, and Windows worker launchers are absent from active runtime ownership.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
