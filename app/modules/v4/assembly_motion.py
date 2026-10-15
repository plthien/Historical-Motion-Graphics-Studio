from __future__ import annotations

from typing import Any

VERSION = "assembly_visual_motion_v1"
TRANSITION_VERSION = "assembly_transition_v1"


def _crop(x: float, y: float, width: float, height: float) -> dict[str, float]:
    return {"x": round(x, 6), "y": round(y, 6), "width": round(width, 6), "height": round(height, 6)}


def compile_visual_instruction(*, camera_movement: str, framing: str, camera_angle: str) -> dict[str, Any]:
    start = _crop(0.0, 0.0, 1.0, 1.0)
    end = dict(start)
    keyframes: list[dict[str, Any]] = []
    motion = camera_movement if camera_movement not in {"none", "static"} else "static"
    if camera_movement == "slow_push_in": end = _crop(0.04, 0.04, 0.92, 0.92)
    elif camera_movement == "slow_pull_out": start, end = _crop(0.04, 0.04, 0.92, 0.92), start
    elif camera_movement == "pan_left": start, end = _crop(0.08, 0.0, 0.92, 1.0), _crop(0.0, 0.0, 0.92, 1.0)
    elif camera_movement == "pan_right": start, end = _crop(0.0, 0.0, 0.92, 1.0), _crop(0.08, 0.0, 0.92, 1.0)
    elif camera_movement == "tilt_up": start, end = _crop(0.0, 0.08, 1.0, 0.92), _crop(0.0, 0.0, 1.0, 0.92)
    elif camera_movement == "tilt_down": start, end = _crop(0.0, 0.0, 1.0, 0.92), _crop(0.0, 0.08, 1.0, 0.92)
    elif camera_movement == "tracking": start, end = _crop(0.0, 0.02, 0.96, 0.96), _crop(0.04, 0.02, 0.96, 0.96)
    elif camera_movement == "parallax": start, end = _crop(0.01, 0.01, 0.98, 0.98), _crop(0.03, 0.02, 0.96, 0.96)
    elif camera_movement == "subtle_handheld":
        pattern = [(0.0,0.01,0.01),(0.25,0.012,0.008),(0.5,0.008,0.012),(0.75,0.011,0.009),(1.0,0.01,0.01)]
        keyframes = [{"offset": o, "crop": _crop(x,y,0.98,0.98)} for o,x,y in pattern]
        start, end = keyframes[0]["crop"], keyframes[-1]["crop"]
    if not keyframes:
        keyframes = [{"offset": 0.0, "crop": start}, {"offset": 1.0, "crop": end}]
    return {
        "compiler_version": VERSION, "fit_mode": "cover", "anchor_x": 0.5, "anchor_y": 0.5,
        "start_crop": start, "end_crop": end, "motion_type": motion, "motion_keyframes": keyframes,
        "hold_mode": "hold", "background_fill": "technical_black",
        "source_camera_movement": camera_movement, "source_framing": framing, "source_camera_angle": camera_angle,
    }


def cut_instruction() -> dict[str, Any]:
    return {"compiler_version": TRANSITION_VERSION, "transition_type": "cut", "duration_time_units": 0,
            "previous_visual_artifact_id": None, "next_visual_artifact_id": None,
            "transition_intent": None, "source_visual_hint": None, "destination_visual_hint": None}


