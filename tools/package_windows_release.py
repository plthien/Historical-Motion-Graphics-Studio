#!/usr/bin/env python3
"""Create a deterministic Windows-safe source ZIP without a root release manifest.

Inventory derives from explicit source directories, not MANIFEST.json. Generated
user data, model weights, caches and secrets are never release contents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath

FIXED_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
DEFAULT_ROOT_NAME = "yas3"  # donor launcher name until the P1 history cutover
DEFAULT_MAX_ENTRY_LENGTH = 180

SOURCE_ROOTS = frozenset({
    "alembic", "app", "contracts", "docs", "kokoro", "scripts",
    "src", "tools",
})
ROOT_SOURCE_FILES = frozenset({
    "README.md", "00_START_HERE.md", "index.html", "start.bat",
    "alembic.ini", "package.json", "package-lock.json", ".npmrc",
    "pytest.ini", "requirements.txt", "postcss.config.js",
    "tailwind.config.js", "tsconfig.json", "vite.config.ts",
})
SKIP_PARTS = frozenset({
    ".git", ".github", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".npm",
    ".cache", "build", "dist", "dist-check", "coverage", "htmlcov",
    "artifacts", "Artifacts", "local_artifacts", "diagnostics", "logs",
    "history_data", "data", "output", "outputs",
})
SKIP_NAMES = frozenset({
    "MANIFEST.json", ".env", ".env.local", ".env.production",
    ".coverage", "coverage.xml", "dump.rdb", ".yas3_kokoro_ready.json",
})
SKIP_SUFFIXES = frozenset({
    ".db", ".sqlite", ".sqlite3", ".sqlite-wal", ".sqlite-shm",
    ".db-wal", ".db-shm", ".pyc", ".pyo", ".tsbuildinfo",
    ".zip", ".mp4", ".wav", ".mp3", ".onnx", ".bin", ".pem", ".key",
})
SOURCE_SUFFIXES = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".md", ".txt", ".json", ".yaml", ".yml", ".html", ".css",
    ".bat", ".ps1", ".ini", ".toml", ".sh", ".svg",
})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root-name", default=DEFAULT_ROOT_NAME)
    parser.add_argument("--max-entry-length", type=int, default=DEFAULT_MAX_ENTRY_LENGTH)
    return parser


def _validate_root_name(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or len(path.parts) != 1 or value in {".", ".."}:
        raise ValueError("root-name must be one safe directory name")
    if value != path.as_posix() or ":" in value or "\\" in value:
        raise ValueError("root-name must be a portable POSIX directory name")
    return value


def _is_releasable(relative: PurePosixPath) -> bool:
    if not relative.parts:
        return False
    if relative.name in SKIP_NAMES or relative.name.startswith(".env"):
        return False
    if any(part in SKIP_PARTS for part in relative.parts):
        return False
    if relative.suffix.lower() in SKIP_SUFFIXES:
        return False
    if len(relative.parts) == 1:
        return relative.name in ROOT_SOURCE_FILES
    return relative.parts[0] in SOURCE_ROOTS and relative.suffix.lower() in SOURCE_SUFFIXES


def _collect_release_paths(root: Path) -> list[str]:
    """Collect safe, known source paths directly; never load any manifest file."""
    if not root.is_dir():
        raise FileNotFoundError(f"Release source root does not exist: {root}")
    result: list[str] = []
    for file in root.rglob("*"):
        path = file.relative_to(root)
        relative = PurePosixPath(path.as_posix())
        if file.is_symlink():
            if _is_releasable(relative):
                raise ValueError(f"Release input symlink is not permitted: {relative}")
            continue
        if not file.is_file() or not _is_releasable(relative):
            continue
        if path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
            raise ValueError(f"Invalid relative archive path: {relative}")
        result.append(relative.as_posix())
    # Case-insensitive Windows and ZIP extraction: reject colliding paths.
    if len(set(p.casefold() for p in result)) != len(result):
        raise ValueError("Duplicate case-insensitive archive paths")
    return sorted(result)


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def build_release(root: Path, output: Path, root_name: str, max_entry_length: int) -> dict[str, object]:
    root = root.resolve()
    output = output.resolve()
    root_name = _validate_root_name(root_name)
    relative_paths = _collect_release_paths(root)
    if not relative_paths:
        raise RuntimeError("Cannot build an empty release archive")
    archive_paths = [f"{root_name}/{relative}" for relative in relative_paths]
    longest = max(archive_paths, key=len)
    if len(longest) > max_entry_length:
        raise RuntimeError(
            f"Archive entry is too long for the Windows release policy: "
            f"{len(longest)} > {max_entry_length}: {longest}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, name in zip(relative_paths, archive_paths, strict=True):
            archive.writestr(_zip_info(name), (root / relative).read_bytes())
    with zipfile.ZipFile(output) as archive:
        if archive.namelist() != archive_paths:
            raise RuntimeError("Archive entry list changed during packaging")
        if archive.testzip() is not None:
            raise RuntimeError("ZIP CRC verification failed")
    payload = output.read_bytes()
    return {
        "output": str(output),
        "entry_count": len(archive_paths),
        "root_name": root_name,
        "longest_entry_length": len(longest),
        "longest_entry": longest,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def main() -> int:
    args = _parser().parse_args()
    result = build_release(args.root, args.output, args.root_name, args.max_entry_length)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
