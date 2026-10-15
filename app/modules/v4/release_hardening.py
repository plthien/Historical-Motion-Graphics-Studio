from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class LongVideoScaleProfile:
    minimum_duration_seconds: float = 480.0
    minimum_section_count: int = 8
    maximum_section_count: int = 12
    minimum_beat_count: int = 80
    maximum_beat_count: int = 120
    maximum_artifact_json_bytes: int = 25 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ReleaseScaleReport:
    ready: bool
    profile: dict[str, Any]
    metrics: dict[str, Any]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "profile": self.profile,
            "metrics": self.metrics,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def _unique(values: Iterable[str], *, label: str, errors: list[str]) -> tuple[str, ...]:
    ordered = tuple(str(value) for value in values)
    if len(set(ordered)) != len(ordered):
        errors.append(f"{label} contains duplicate identities.")
    return ordered


def _scene_plan_beat_ids(scene_plan: dict[str, Any], errors: list[str]) -> tuple[str, ...]:
    return _unique(
        (
            str(beat.get("beat_id"))
            for scene in scene_plan.get("scenes", [])
            for beat in scene.get("beats", [])
        ),
        label="Scene Plan beats",
        errors=errors,
    )


def _visual_plan_beat_ids(visual_plan: dict[str, Any], errors: list[str]) -> tuple[str, ...]:
    return _unique(
        (
            str(beat.get("beat_id"))
            for scene in visual_plan.get("scenes", [])
            for beat in scene.get("beat_plans", [])
        ),
        label="Visual Plan beats",
        errors=errors,
    )


def _manifest_beat_ids(manifest: dict[str, Any], errors: list[str]) -> tuple[str, ...]:
    return _unique(
        (
            str(entry.get("beat_id"))
            for scene in manifest.get("scenes", [])
            for entry in scene.get("entries", scene.get("beats", []))
        ),
        label="Scene Asset Manifest beats",
        errors=errors,
    )


def _ordered_ids(items: list[dict[str, Any]], *, label: str, errors: list[str]) -> tuple[str, ...]:
    return _unique((str(item.get("beat_id")) for item in items), label=label, errors=errors)


def _json_size(value: dict[str, Any]) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def audit_long_video_release_candidate(
    *,
    scene_plan: dict[str, Any],
    visual_plan: dict[str, Any],
    prompt_bundle: dict[str, Any],
    scene_asset_manifest: dict[str, Any],
    timing_map: dict[str, Any],
    assembly_plan: dict[str, Any],
    profile: LongVideoScaleProfile | None = None,
) -> ReleaseScaleReport:
    """Audit one immutable V4 topology without adapting historical artifacts.

    This is intentionally a pure release gate. It does not load, mutate, repair,
    or convert artifacts and therefore cannot become a compatibility boundary.
    """

    active_profile = profile or LongVideoScaleProfile()
    errors: list[str] = []
    warnings: list[str] = []

    expected_versions = (
        ("Scene Plan", scene_plan, "2.0"),
        ("Visual Plan", visual_plan, "3.0"),
        ("Prompt Bundle", prompt_bundle, "3.0"),
        ("Scene Asset Manifest", scene_asset_manifest, "3.0"),
        ("Timing Map", timing_map, "2.0"),
        ("Assembly Plan", assembly_plan, "3.0"),
    )
    for label, artifact, expected_version in expected_versions:
        if artifact.get("schema_version") != expected_version:
            errors.append(f"{label} must use schema_version {expected_version}.")
        byte_count = _json_size(artifact)
        if byte_count > active_profile.maximum_artifact_json_bytes:
            errors.append(f"{label} exceeds the release JSON size bound.")

    section_count = len(scene_plan.get("sections", []))
    scene_count = len(scene_plan.get("scenes", []))
    scene_beat_ids = _scene_plan_beat_ids(scene_plan, errors)
    beat_count = len(scene_beat_ids)
    duration_seconds = float(scene_plan.get("total_expected_duration_seconds") or 0.0)

    if not active_profile.minimum_section_count <= section_count <= active_profile.maximum_section_count:
        errors.append(
            f"Section count {section_count} is outside the release profile "
            f"{active_profile.minimum_section_count}-{active_profile.maximum_section_count}."
        )
    if not active_profile.minimum_beat_count <= beat_count <= active_profile.maximum_beat_count:
        errors.append(
            f"Beat count {beat_count} is outside the release profile "
            f"{active_profile.minimum_beat_count}-{active_profile.maximum_beat_count}."
        )
    if duration_seconds < active_profile.minimum_duration_seconds:
        errors.append(
            f"Expected duration {duration_seconds:.3f}s is below the "
            f"{active_profile.minimum_duration_seconds:.3f}s release target."
        )

    ordered_sources = {
        "Scene Plan": scene_beat_ids,
        "Visual Plan": _visual_plan_beat_ids(visual_plan, errors),
        "Prompt Bundle": _ordered_ids(prompt_bundle.get("prompt_items", []), label="Prompt Bundle beats", errors=errors),
        "Scene Asset Manifest": _manifest_beat_ids(scene_asset_manifest, errors),
        "Timing Map": _ordered_ids(timing_map.get("beats", []), label="Timing Map beats", errors=errors),
        "Assembly Plan": _ordered_ids(assembly_plan.get("beats", []), label="Assembly Plan beats", errors=errors),
    }
    for label, ids in ordered_sources.items():
        if ids != scene_beat_ids:
            errors.append(f"{label} beat identity/order does not match the approved Scene Plan.")

    assembly_beats = assembly_plan.get("beats", [])
    if assembly_beats:
        if int(assembly_beats[0].get("start_time_units", -1)) != 0:
            errors.append("Assembly timeline must start at zero.")
        for left, right in zip(assembly_beats, assembly_beats[1:]):
            if int(left.get("end_time_units", -1)) != int(right.get("start_time_units", -2)):
                errors.append("Assembly beat timeline is not contiguous.")
                break
        expected_end = int(assembly_plan.get("total_duration_time_units", -1))
        if int(assembly_beats[-1].get("end_time_units", -2)) != expected_end:
            errors.append("Assembly final beat does not end at total_duration_time_units.")
        assembly_seconds = expected_end / 1_000_000
        if abs(assembly_seconds - duration_seconds) > 1.0:
            warnings.append(
                "Assembly duration differs from Scene Plan expected duration by more than one second; "
                "backend-owned voice timing remains authoritative."
            )

    metrics = {
        "section_count": section_count,
        "scene_count": scene_count,
        "beat_count": beat_count,
        "expected_duration_seconds": duration_seconds,
        "assembly_duration_seconds": int(assembly_plan.get("total_duration_time_units", 0)) / 1_000_000,
        "artifact_json_bytes": {
            "scene_plan": _json_size(scene_plan),
            "visual_plan": _json_size(visual_plan),
            "prompt_bundle": _json_size(prompt_bundle),
            "scene_asset_manifest": _json_size(scene_asset_manifest),
            "timing_map": _json_size(timing_map),
            "assembly_plan": _json_size(assembly_plan),
        },
    }
    return ReleaseScaleReport(
        ready=not errors,
        profile=asdict(active_profile),
        metrics=metrics,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )
