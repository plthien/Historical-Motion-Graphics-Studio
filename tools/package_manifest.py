#!/usr/bin/env python3
"""Deterministic package-authority and integrity manifest generator.

The generator deliberately starts from a complete prior authority snapshot and
applies an explicit patch. It never derives package authority from a changed-file
inventory or a latest-patch-only allowlist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

AUTHORITY_COLLECTIONS = (
    "active_contracts",
    "active_docs",
    "governance_outputs",
    "recovery_tests",
    "historical_superseded_docs",
)

FORBIDDEN_PARTS = {
    ".cache",
    ".git",
    ".mypy_cache",
    ".npm",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "dist-check",
    "htmlcov",
    "node_modules",
}
FORBIDDEN_ROOTS = {
    "Artifacts",
    "artifacts",
    "local_artifacts",
    "diagnostics",
    "provider_diagnostics",
    "logs",
}
FORBIDDEN_FILENAMES = {
    ".env",
    ".coverage",
    "coverage.xml",
    "dump.rdb",
    "package-lock.cache",
}
FORBIDDEN_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".db-shm",
    ".db-wal",
    ".sqlite-shm",
    ".sqlite-wal",
    ".sqlite3-shm",
    ".sqlite3-wal",
    ".tsbuildinfo",
    ".zip",
}
VERIFY_IGNORED_RUNTIME_CACHE_PARTS = {
    "__pycache__",
}
VERIFY_IGNORED_RUNTIME_CACHE_SUFFIXES = {
    ".pyc",
    ".pyo",
}


class ManifestError(RuntimeError):
    """Controlled manifest-generation or verification failure."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Unable to load JSON file: {path}") from exc
    if not isinstance(value, dict):
        raise ManifestError(f"JSON root must be an object: {path}")
    return value


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _canonical_file_payload(path: Path) -> bytes:
    """Return the platform-neutral integrity payload for one package file.

    UTF-8 text is normalized to LF so Git checkout newline policy cannot change
    package identity between Windows and Linux. Binary/non-UTF-8 payloads remain
    byte-exact.
    """

    payload = path.read_bytes()
    if b"\x00" in payload:
        return payload
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload
    if "\r" not in text:
        return payload
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _validate_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError("Authority paths must be non-empty strings")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise ManifestError(f"Invalid package-relative path: {value!r}")
    return value


def _sorted_unique(values: Iterable[str]) -> list[str]:
    validated = [_validate_relative_path(value) for value in values]
    if len(validated) != len(set(validated)):
        duplicates = sorted({value for value in validated if validated.count(value) > 1})
        raise ManifestError(f"Duplicate authority paths: {duplicates}")
    return sorted(validated)


def merge_authority(
    baseline: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Merge a complete baseline authority with explicit removals/additions."""

    metadata = patch.get("metadata")
    if not isinstance(metadata, dict):
        raise ManifestError("Patch metadata must be an object")

    classification = patch.get("classification")
    if not isinstance(classification, list) or not all(
        isinstance(item, str) and item for item in classification
    ):
        raise ManifestError("Patch classification must be a non-empty string list")

    remove_all = patch.get("remove_from_all_authority_collections", [])
    if not isinstance(remove_all, list):
        raise ManifestError("remove_from_all_authority_collections must be a list")
    remove_all_set = set(_sorted_unique(remove_all))

    remove_by_collection = patch.get("remove", {})
    add_by_collection = patch.get("add", {})
    if not isinstance(remove_by_collection, dict) or not isinstance(add_by_collection, dict):
        raise ManifestError("Patch remove/add sections must be objects")

    merged: dict[str, Any] = {
        "name": metadata.get("name"),
        "version": metadata.get("version"),
        "baseline": metadata.get("baseline"),
        "classification": list(classification),
        "migration_head": metadata.get("migration_head"),
        "manifest_note": metadata.get("manifest_note"),
    }
    for key in ("name", "version", "baseline", "migration_head", "manifest_note"):
        if not isinstance(merged[key], str) or not merged[key]:
            raise ManifestError(f"Patch metadata field {key!r} must be a non-empty string")

    for collection in AUTHORITY_COLLECTIONS:
        baseline_values = baseline.get(collection, [])
        if not isinstance(baseline_values, list):
            raise ManifestError(f"Baseline {collection} must be a list")
        collection_remove = remove_by_collection.get(collection, [])
        collection_add = add_by_collection.get(collection, [])
        if not isinstance(collection_remove, list) or not isinstance(collection_add, list):
            raise ManifestError(f"Patch {collection} remove/add values must be lists")

        removed = remove_all_set | set(_sorted_unique(collection_remove))
        values = [
            _validate_relative_path(value)
            for value in baseline_values
            if _validate_relative_path(value) not in removed
        ]
        values.extend(_sorted_unique(collection_add))
        # Stable deterministic ordering and uniqueness after merging.
        merged[collection] = sorted(set(values))

    return merged


def _is_forbidden_path(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    if path.parts and path.parts[0] == "kokoro":
        if path.name == ".yas3_kokoro_ready.json":
            return True
        if path.suffix.lower() in {".onnx", ".wav"}:
            return True
        if path.name.startswith("voices-") and path.suffix.lower() == ".bin":
            return True
    if any(part in FORBIDDEN_PARTS for part in path.parts):
        return True
    if path.parts and path.parts[0] in FORBIDDEN_ROOTS:
        return True
    if path.name in FORBIDDEN_FILENAMES:
        return True
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return True
    return False


def _is_unsafe_archive_path(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/")
    path = PurePosixPath(normalized)
    return (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or ":" in path.parts[0]
        or normalized != path.as_posix()
    )


def audit_archive_paths(paths: Iterable[str]) -> dict[str, list[str]]:
    seen: set[str] = set()
    duplicate: list[str] = []
    unsafe: list[str] = []
    forbidden: list[str] = []
    for value in paths:
        normalized = value.replace("\\", "/")
        lower = normalized.lower()
        if lower in seen:
            duplicate.append(normalized)
        seen.add(lower)
        if _is_unsafe_archive_path(value):
            unsafe.append(normalized)
            continue
        if _is_forbidden_path(normalized):
            forbidden.append(normalized)
    return {
        "duplicate_paths": sorted(duplicate),
        "unsafe_paths": sorted(unsafe),
        "forbidden_paths": sorted(forbidden),
    }


def collect_package_files(root: Path, manifest_relative_path: str = "MANIFEST.json") -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ManifestError(f"Package symlinks are not allowed: {path.relative_to(root)}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == manifest_relative_path:
            continue
        if _is_forbidden_path(relative):
            continue
        payload = _canonical_file_payload(path)
        records.append(
            {
                "path": relative,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return records


def collect_forbidden_paths(root: Path) -> list[str]:
    forbidden: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        posix = PurePosixPath(relative)
        if (
            any(part in VERIFY_IGNORED_RUNTIME_CACHE_PARTS for part in posix.parts)
            or posix.suffix.lower() in VERIFY_IGNORED_RUNTIME_CACHE_SUFFIXES
        ):
            continue
        if _is_forbidden_path(relative):
            forbidden.append(relative)
    return forbidden


def build_manifest(root: Path, baseline_path: Path, patch_path: Path) -> dict[str, Any]:
    baseline = _load_json(baseline_path)
    patch = _load_json(patch_path)
    manifest = merge_authority(baseline, patch)
    package_files = collect_package_files(root)
    manifest["package_file_count_excluding_manifest"] = len(package_files)
    manifest["package_files"] = package_files
    verify_manifest_object(root, manifest)
    return manifest


def verify_manifest_object(root: Path, manifest: dict[str, Any]) -> None:
    package_files = manifest.get("package_files")
    if not isinstance(package_files, list):
        raise ManifestError("package_files must be a list")

    paths: list[str] = []
    for record in package_files:
        if not isinstance(record, dict):
            raise ManifestError("Each package_files entry must be an object")
        path_value = _validate_relative_path(record.get("path"))
        if path_value == "MANIFEST.json":
            raise ManifestError("MANIFEST.json must not index itself")
        if _is_forbidden_path(path_value):
            raise ManifestError(f"Forbidden path indexed by manifest: {path_value}")
        paths.append(path_value)
        disk_path = root / path_value
        if not disk_path.is_file():
            raise ManifestError(f"Indexed path does not exist: {path_value}")
        payload = _canonical_file_payload(disk_path)
        if record.get("size_bytes") != len(payload):
            raise ManifestError(f"Size mismatch for {path_value}")
        if record.get("sha256") != hashlib.sha256(payload).hexdigest():
            raise ManifestError(f"SHA-256 mismatch for {path_value}")

    if len(paths) != len(set(paths)):
        raise ManifestError("Duplicate package_files paths")
    if manifest.get("package_file_count_excluding_manifest") != len(package_files):
        raise ManifestError("package_file_count_excluding_manifest mismatch")

    actual_records = collect_package_files(root)
    actual_paths = [record["path"] for record in actual_records]
    if paths != actual_paths:
        missing = sorted(set(actual_paths) - set(paths))
        phantom = sorted(set(paths) - set(actual_paths))
        raise ManifestError(f"Integrity index path mismatch; missing={missing}, phantom={phantom}")

    indexed = set(paths)
    for collection in AUTHORITY_COLLECTIONS:
        values = manifest.get(collection)
        if not isinstance(values, list):
            raise ManifestError(f"{collection} must be a list")
        if values != sorted(values):
            raise ManifestError(f"{collection} is not deterministically sorted")
        if len(values) != len(set(values)):
            raise ManifestError(f"{collection} contains duplicate paths")
        for value in values:
            relative = _validate_relative_path(value)
            if relative not in indexed:
                raise ManifestError(f"{collection} path is not indexed: {relative}")
            if not (root / relative).is_file():
                raise ManifestError(f"{collection} path does not exist: {relative}")


def generate(root: Path, baseline_path: Path, patch_path: Path, output_path: Path) -> bytes:
    manifest = build_manifest(root, baseline_path, patch_path)
    rendered = _canonical_bytes(manifest)
    output_path.write_bytes(rendered)
    return rendered


def verify(root: Path, baseline_path: Path, patch_path: Path, manifest_path: Path) -> None:
    forbidden = collect_forbidden_paths(root)
    if forbidden:
        raise ManifestError(f"Forbidden generated/local files remain in package tree: {forbidden}")
    actual = _canonical_file_payload(manifest_path)
    expected = _canonical_bytes(build_manifest(root, baseline_path, patch_path))
    if actual != expected:
        raise ManifestError("MANIFEST.json is not the deterministic output of the authority generator")
    verify_manifest_object(root, json.loads(actual))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "verify", "summary"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("tools/manifest_authority/external_authoring_baseline.json"),
    )
    parser.add_argument(
        "--patch",
        type=Path,
        default=Path("tools/manifest_authority/external_authoring_patch.json"),
    )
    parser.add_argument("--manifest", type=Path, default=Path("MANIFEST.json"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    baseline = args.baseline if args.baseline.is_absolute() else root / args.baseline
    patch = args.patch if args.patch.is_absolute() else root / args.patch
    manifest_path = args.manifest if args.manifest.is_absolute() else root / args.manifest
    try:
        if args.command == "generate":
            rendered = generate(root, baseline, patch, manifest_path)
            print(f"generated {manifest_path} ({len(rendered)} bytes)")
        elif args.command == "verify":
            verify(root, baseline, patch, manifest_path)
            print("manifest verification passed")
        else:
            manifest = build_manifest(root, baseline, patch)
            summary = {
                collection: len(manifest[collection]) for collection in AUTHORITY_COLLECTIONS
            }
            summary["package_files"] = manifest["package_file_count_excluding_manifest"]
            print(json.dumps(summary, indent=2))
    except (ManifestError, OSError, ValueError, TypeError) as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
