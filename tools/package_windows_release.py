#!/usr/bin/env python3
"""Create a deterministic Windows-safe release ZIP under the short ``yas3`` root."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from package_manifest import audit_archive_paths, verify

FIXED_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
DEFAULT_ROOT_NAME = "yas3"
DEFAULT_MAX_ENTRY_LENGTH = 180


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

    verify(
        root,
        root / "tools/manifest_authority/external_authoring_baseline.json",
        root / "tools/manifest_authority/external_authoring_patch.json",
        root / "MANIFEST.json",
    )
    manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    relative_paths = [record["path"] for record in manifest["package_files"]] + ["MANIFEST.json"]
    archive_paths = [f"{root_name}/{relative}" for relative in relative_paths]

    audit = audit_archive_paths(archive_paths)
    if any(audit.values()):
        raise RuntimeError(f"Archive path audit failed: {audit}")

    longest = max(archive_paths, key=len)
    if len(longest) > max_entry_length:
        raise RuntimeError(
            f"Archive entry is too long for the Windows release policy: {len(longest)} > "
            f"{max_entry_length}: {longest}"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, archive_name in zip(relative_paths, archive_paths, strict=True):
            archive.writestr(_zip_info(archive_name), (root / relative).read_bytes())

    with zipfile.ZipFile(output) as archive:
        written_names = archive.namelist()
        if written_names != archive_paths:
            raise RuntimeError("Written archive entries do not match the manifest order")
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
