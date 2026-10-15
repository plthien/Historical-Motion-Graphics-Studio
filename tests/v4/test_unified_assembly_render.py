from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.api.assembly_schemas_v4 import AssemblyPlanModel, VisualInstructionModel
from app.api.render_schemas_v4 import RenderCommandGraphModel, RenderFrameQuantizationModel
from app.modules.v4.assembly_motion import compile_visual_instruction
from app.modules.v4.render_frame_quantization import quantize_render_frames
from app.modules.v4.render_graph import compile_ffmpeg_render_graph
from app.services.render_settings import default_render_settings


def _profile() -> dict:
    profile = deepcopy(default_render_settings()["profiles"][0])
    profile.update({
        "profile_id": "test",
        "display_name": "Test",
        "width": 426,
        "height": 240,
        "frame_rate": {"numerator": 30, "denominator": 1},
        "video_preset": "ultrafast",
        "subtitle_mode": "embedded_mov_text",
    })
    return profile


def _visual_instruction() -> dict:
    return compile_visual_instruction(camera_movement="slow_push_in", framing="wide", camera_angle="eye_level")


def test_visual_instruction_is_single_image_path() -> None:
    payload = _visual_instruction()
    assert payload["fit_mode"] == "cover"
    assert payload["hold_mode"] == "hold"
    assert payload["background_fill"] == "technical_black"
    VisualInstructionModel.model_validate(payload)
    with pytest.raises(TypeError):
        compile_visual_instruction(  # type: ignore[call-arg]
            strategy="generated_still",
            camera_movement="static",
            framing="wide",
            camera_angle="eye_level",
        )


def test_frame_quantization_has_no_asset_classification() -> None:
    payload = quantize_render_frames(
        total_duration_time_units=2_000_000,
        beats=[
            {"scene_id": "scene_1", "beat_id": "beat_1", "beat_order": 1, "start_time_units": 0, "end_time_units": 1_000_000},
            {"scene_id": "scene_1", "beat_id": "beat_2", "beat_order": 2, "start_time_units": 1_000_000, "end_time_units": 2_000_000},
        ],
        frame_rate_numerator=30,
        frame_rate_denominator=1,
    )
    assert payload["schema_version"] == "3.0"
    assert all("asset_strategy" not in item for item in payload["intervals"])
    RenderFrameQuantizationModel.model_validate(payload)
    invalid = deepcopy(payload)
    invalid.pop("frame_quantization_sha256")
    invalid["intervals"][0]["asset_strategy"] = "generated_still"
    invalid["frame_quantization_sha256"] = "0" * 64
    with pytest.raises(ValidationError):
        RenderFrameQuantizationModel.model_validate(invalid)


def test_render_graph_emits_only_visual_clips_without_classification() -> None:
    visual = _visual_instruction()
    assembly = {
        "beats": [
            {
                "scene_id": "scene_1",
                "beat_id": "beat_1",
                "beat_order": 1,
                "selected_visual_artifact_id": "artifact_image_1",
                "visual_instruction": visual,
            }
        ],
        "narration_track": {"artifact_id": "artifact_audio"},
        "background_audio_track": None,
        "sfx_track": [],
        "source_subtitle_srt_artifact_id": "artifact_srt",
        "source_subtitle_vtt_artifact_id": "artifact_vtt",
    }
    quantized = quantize_render_frames(
        total_duration_time_units=1_000_000,
        beats=[{"scene_id": "scene_1", "beat_id": "beat_1", "beat_order": 1, "start_time_units": 0, "end_time_units": 1_000_000}],
        frame_rate_numerator=30,
        frame_rate_denominator=1,
    )
    descriptors = [
        {"input_index": 0, "artifact_id": "artifact_image_1"},
        {"input_index": 1, "artifact_id": "artifact_audio"},
        {"input_index": 2, "artifact_id": "artifact_srt"},
    ]
    graph = compile_ffmpeg_render_graph(
        assembly_plan=assembly,
        render_profile=_profile(),
        frame_quantization=quantized,
        input_descriptors=descriptors,
    )
    assert graph["schema_version"] == "3.0"
    assert graph["visual_nodes"] == [
        {
            "node_type": "visual_clip",
            "scene_id": "scene_1",
            "beat_id": "beat_1",
            "beat_order": 1,
            "artifact_id": "artifact_image_1",
            "frame_count": 30,
            "motion": graph["visual_nodes"][0]["motion"],
        }
    ]
    assert "asset_strategy" not in graph["visual_nodes"][0]
    RenderCommandGraphModel.model_validate(graph)


def test_assembly_contract_strictly_rejects_removed_fields() -> None:
    schema = AssemblyPlanModel.model_json_schema()
    beat_schema = schema["$defs"]["AssemblyBeatModel"]
    assert "asset_strategy" not in beat_schema["properties"]
    assert "visual_kind" not in beat_schema["properties"]
    assert beat_schema["properties"]["selected_visual_artifact_id"]["type"] == "string"
