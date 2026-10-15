from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import Settings
from app.db.sqlite_pragmas import SQLitePragmaProfile, apply_sqlite_pragmas, read_sqlite_pragmas
from app.modules.v4.release_hardening import audit_long_video_release_candidate
from app.modules.v4.render_ffmpeg import MAX_FILTER_SCRIPT_BYTES, build_filter_script
from app.modules.v4.render_frame_quantization import quantize_render_frames
from app.modules.v4.render_graph import compile_ffmpeg_render_graph


def _fixture() -> dict[str, dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    scenes: list[dict[str, Any]] = []
    visual_scenes: list[dict[str, Any]] = []
    manifest_scenes: list[dict[str, Any]] = []
    prompt_items: list[dict[str, Any]] = []
    timing_beats: list[dict[str, Any]] = []
    assembly_beats: list[dict[str, Any]] = []
    input_descriptors: list[dict[str, Any]] = []
    beat_order = 0
    scene_order = 0
    for section_order in range(1, 11):
        section_id = f"sec_{section_order:03d}"
        sections.append({
            "section_id": section_id,
            "section_order": section_order,
            "section_title": f"Section {section_order}",
            "section_role": "development",
        })
        for section_scene in range(1, 5):
            scene_order += 1
            scene_id = f"scene_{scene_order:03d}"
            scene_beats: list[dict[str, Any]] = []
            visual_beats: list[dict[str, Any]] = []
            manifest_entries: list[dict[str, Any]] = []
            for _ in range(3):
                beat_order += 1
                beat_id = f"beat_{beat_order:03d}"
                start_units = (beat_order - 1) * 4_000_000
                end_units = beat_order * 4_000_000
                artifact_id = f"artifact_visual_{beat_order:03d}"
                scene_beats.append({
                    "beat_id": beat_id,
                    "beat_order": beat_order,
                    "script_quote": f"Narration for beat {beat_order}.",
                    "computed_duration_seconds": 4.0,
                })
                visual_beats.append({
                    "beat_id": beat_id,
                    "beat_order": beat_order,
                    "visual_concept": f"Release-scale image concept {beat_order}",
                    "subject_action": "Hold a clear production-ready visual state.",
                    "environment_emphasis": "Use the approved documentary environment.",
                    "visible_text": [],
                })
                prompt_items.append({
                    "prompt_item_id": f"prompt_item_{beat_order:03d}",
                    "beat_id": beat_id,
                    "beat_order": beat_order,
                    "image_prompt": f"Prompt {beat_order}",
                    "negative_prompt": "No watermark or extra text.",
                    "prompt_hash": f"sha256:{'c' * 61}{beat_order % 10:03d}",
                    "reference_bindings": [],
                    "generation_options": {"aspect_ratio": "16:9"},
                })
                manifest_entries.append({
                    "beat_id": beat_id,
                    "beat_order": beat_order,
                    "resolution_status": "approved",
                    "review_status": "approved",
                    "selected_asset_artifact_id": artifact_id,
                    "candidate_asset_artifact_ids": [artifact_id],
                    "source_prompt_item_id": f"prompt_item_{beat_order:03d}",
                    "source_prompt_hash": f"sha256:{'c' * 61}{beat_order % 10:03d}",
                })
                timing_beats.append({
                    "beat_id": beat_id,
                    "beat_order": beat_order,
                    "sample_start": (beat_order - 1) * 192_000,
                    "sample_end": beat_order * 192_000,
                })
                assembly_beats.append({
                    "section_id": section_id,
                    "scene_id": scene_id,
                    "beat_id": beat_id,
                    "beat_order": beat_order,
                    "selected_visual_artifact_id": artifact_id,
                    "selected_visual_sha256": f"{'a' * 61}{beat_order % 10:03d}",
                    "selected_visual_mime_type": "image/png",
                    "selected_visual_width": 320,
                    "selected_visual_height": 180,
                    "start_time_units": start_units,
                    "end_time_units": end_units,
                    "visual_instruction": {
                        "motion_type": "static",
                        "fit_mode": "cover",
                        "anchor_x": 0.5,
                        "anchor_y": 0.5,
                        "motion_keyframes": [
                            {"offset": 0.0, "crop": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}},
                            {"offset": 1.0, "crop": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}},
                        ],
                    },
                    "transition_instruction": {"transition_type": "cut", "duration_time_units": 0},
                })
                input_descriptors.append({
                    "input_index": beat_order - 1,
                    "kind": "visual",
                    "artifact_id": artifact_id,
                    "mime_type": "image/png",
                    "sha256": f"{'a' * 61}{beat_order % 10:03d}",
                    "width": 320,
                    "height": 180,
                })
            scenes.append({
                "section_id": section_id,
                "scene_id": scene_id,
                "scene_order": scene_order,
                "scene_purpose": "Scale fixture",
                "beats": scene_beats,
            })
            visual_scenes.append({"section_id": section_id, "scene_id": scene_id, "scene_order": scene_order, "beat_plans": visual_beats})
            manifest_scenes.append({"section_id": section_id, "scene_id": scene_id, "scene_order": scene_order, "entries": manifest_entries})

    scene_plan = {
        "schema_version": "2.0",
        "status": "approved",
        "sections": sections,
        "scenes": scenes,
        "beat_count": beat_order,
        "scene_count": scene_order,
        "total_expected_duration_seconds": 480.0,
    }
    visual_plan = {"schema_version": "3.0", "status": "approved", "scenes": visual_scenes, "beat_count": beat_order}
    prompt_bundle = {"schema_version": "3.0", "status": "approved", "prompt_items": prompt_items, "prompt_item_count": beat_order}
    manifest = {"schema_version": "3.0", "status": "approved", "scenes": manifest_scenes, "beat_count": beat_order}
    timing = {"schema_version": "2.0", "status": "approved", "sample_rate_hz": 48_000, "beats": timing_beats}
    assembly = {
        "schema_version": "3.0",
        "status": "approved",
        "total_duration_time_units": 480_000_000,
        "beats": assembly_beats,
        "narration_track": {
            "artifact_id": "artifact_narration",
            "mime_type": "audio/wav",
            "sha256": "b" * 64,
            "byte_count": 1,
            "sample_rate_hz": 48_000,
            "channels": 1,
            "sample_count": 23_040_000,
            "duration_time_units": 480_000_000,
        },
        "background_audio_track": None,
        "sfx_track": [],
        "source_subtitle_srt_artifact_id": "artifact_srt",
        "source_subtitle_vtt_artifact_id": "artifact_vtt",
    }
    input_descriptors.append({
        "input_index": beat_order,
        "kind": "narration",
        "artifact_id": "artifact_narration",
        "mime_type": "audio/wav",
        "sha256": "b" * 64,
    })
    return {
        "scene_plan": scene_plan,
        "visual_plan": visual_plan,
        "prompt_bundle": prompt_bundle,
        "scene_asset_manifest": manifest,
        "timing_map": timing,
        "assembly_plan": assembly,
        "input_descriptors": input_descriptors,
    }


def _sqlite_probe() -> dict[str, Any]:
    settings = Settings(root_dir=ROOT)
    profile = SQLitePragmaProfile.from_settings(settings)
    with tempfile.TemporaryDirectory(prefix="task9_sqlite_") as directory:
        path = Path(directory) / "probe.sqlite3"
        connection = sqlite3.connect(path)
        try:
            apply_sqlite_pragmas(connection, profile)
            pragmas = read_sqlite_pragmas(connection)
        finally:
            connection.close()
    return {"profile": asdict(profile), "observed": pragmas}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic Task 9 scale and local-runtime release gate.")
    parser.add_argument("--require-ffmpeg", action="store_true")
    args = parser.parse_args()

    fixture = _fixture()
    report = audit_long_video_release_candidate(
        scene_plan=fixture["scene_plan"],
        visual_plan=fixture["visual_plan"],
        prompt_bundle=fixture["prompt_bundle"],
        scene_asset_manifest=fixture["scene_asset_manifest"],
        timing_map=fixture["timing_map"],
        assembly_plan=fixture["assembly_plan"],
    )
    profile = {
        "profile_id": "task9_probe_320p",
        "display_name": "Task 9 probe",
        "width": 320,
        "height": 180,
        "frame_rate": {"numerator": 30, "denominator": 1},
        "pixel_format": "yuv420p",
        "video_codec": "libx264",
        "video_preset": "ultrafast",
        "video_crf": 30,
        "audio_codec": "aac",
        "audio_bitrate": "96k",
        "subtitle_mode": "sidecar_only",
    }
    quantized = quantize_render_frames(
        total_duration_time_units=480_000_000,
        beats=fixture["assembly_plan"]["beats"],
        frame_rate_numerator=30,
        frame_rate_denominator=1,
    )
    graph = compile_ffmpeg_render_graph(
        assembly_plan=fixture["assembly_plan"],
        render_profile=profile,
        frame_quantization=quantized,
        input_descriptors=fixture["input_descriptors"],
    )
    indexes = {item["artifact_id"]: index for index, item in enumerate(fixture["input_descriptors"])}
    indexes["artifact_narration"] = len(indexes)
    filter_script = build_filter_script(graph, input_index_by_artifact=indexes)
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    payload = {
        **report.to_dict(),
        "render_compile": {
            "total_frames": quantized["total_frame_count"],
            "interval_count": len(quantized["intervals"]),
            "filter_script_bytes": len(filter_script.encode("utf-8")),
            "filter_script_limit_bytes": MAX_FILTER_SCRIPT_BYTES,
        },
        "sqlite": _sqlite_probe(),
        "executables": {"ffmpeg": ffmpeg, "ffprobe": ffprobe},
    }
    payload["ready"] = bool(
        payload["ready"]
        and payload["render_compile"]["filter_script_bytes"] <= MAX_FILTER_SCRIPT_BYTES
        and (not args.require_ffmpeg or (ffmpeg and ffprobe))
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
