from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any, Final, Literal


VideoPacingMode = Literal["fast", "medium", "slow"]
Density = Literal["high", "medium", "low"]

VIDEO_PACING_MODES: Final[tuple[str, ...]] = ("fast", "medium", "slow")
PACING_DENSITIES: Final[tuple[str, ...]] = ("high", "medium", "low")
VIDEO_PACING_POLICY: Final[str] = (
    "Binding authoring target for realized semantic visual-state cadence and "
    "post-segmentation conformance rescan. Preferred ranges never authorize "
    "punctuation splitting, beat quotas, or invented semantic boundaries."
)


class VideoPacingError(ValueError):
    pass


def validate_video_pacing_mode(value: Any) -> str:
    if not isinstance(value, str) or value not in VIDEO_PACING_MODES:
        raise VideoPacingError("pacing_mode must be one of: fast, medium, slow")
    return value


def validate_preferred_hold_seconds(value: Any) -> tuple[float, float]:
    """Return the canonical finite ``0 < min < max`` hold range."""

    if not isinstance(value, dict) or set(value) != {"min", "max"}:
        raise VideoPacingError("preferred_hold_seconds must contain exactly min and max")
    minimum = value.get("min")
    maximum = value.get("max")
    if (
        isinstance(minimum, bool)
        or isinstance(maximum, bool)
        or not isinstance(minimum, (int, float))
        or not isinstance(maximum, (int, float))
    ):
        raise VideoPacingError("preferred hold range values must be numeric")
    minimum = float(minimum)
    maximum = float(maximum)
    if (
        not isfinite(minimum)
        or not isfinite(maximum)
        or minimum <= 0
        or maximum <= minimum
    ):
        raise VideoPacingError("preferred hold range is invalid")
    return minimum, maximum


def validate_pacing_profiles(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(VIDEO_PACING_MODES):
        raise VideoPacingError("pacing_profiles must contain exactly: fast, medium, slow")
    normalized: dict[str, Any] = {}
    for mode in VIDEO_PACING_MODES:
        mode_profile = value.get(mode)
        if not isinstance(mode_profile, dict) or set(mode_profile) != set(PACING_DENSITIES):
            raise VideoPacingError(f"pacing_profiles.{mode} must contain exactly: high, medium, low")
        normalized_mode: dict[str, Any] = {}
        for density in PACING_DENSITIES:
            density_profile = mode_profile.get(density)
            if not isinstance(density_profile, dict) or set(density_profile) != {"preferred_hold_seconds"}:
                raise VideoPacingError(
                    f"pacing_profiles.{mode}.{density} must contain exactly preferred_hold_seconds"
                )
            minimum, maximum = validate_preferred_hold_seconds(
                density_profile.get("preferred_hold_seconds")
            )
            normalized_mode[density] = {
                "preferred_hold_seconds": {"min": minimum, "max": maximum}
            }
        normalized[mode] = normalized_mode
    return normalized


def resolve_video_pacing_profile(pacing_profiles: Any, mode: Any) -> dict[str, Any]:
    normalized_mode = validate_video_pacing_mode(mode)
    profiles = validate_pacing_profiles(pacing_profiles)
    return {
        "mode": normalized_mode,
        "profile_name": normalized_mode,
        "policy": VIDEO_PACING_POLICY,
        "density_profiles": deepcopy(profiles[normalized_mode]),
    }


def preferred_hold_range(profile: dict[str, Any], density: Any) -> tuple[float, float]:
    if density not in PACING_DENSITIES:
        raise VideoPacingError("pacing density must be one of: high, medium, low")
    try:
        hold = profile["density_profiles"][density]["preferred_hold_seconds"]
    except (KeyError, TypeError) as exc:
        raise VideoPacingError("visual pacing profile is malformed") from exc
    return validate_preferred_hold_seconds(hold)


__all__ = [
    "Density",
    "PACING_DENSITIES",
    "VIDEO_PACING_MODES",
    "VIDEO_PACING_POLICY",
    "VideoPacingError",
    "VideoPacingMode",
    "preferred_hold_range",
    "resolve_video_pacing_profile",
    "validate_pacing_profiles",
    "validate_preferred_hold_seconds",
    "validate_video_pacing_mode",
]
