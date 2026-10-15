from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.assembly_schemas_v4 import AssemblyBuildRequest, AssemblyPlanModel, AssemblyWorkspaceResponse
from app.api.errors import AppError
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactRole, ArtifactStatus, ArtifactType
from app.db.models import Artifact, Project
from app.modules.v4.assembly_motion import compile_visual_instruction, cut_instruction
from app.modules.v4.assembly_subtitles import (
    CUE_COMPILER_VERSION, MILLISECOND_QUANTIZER_VERSION, SRT_SERIALIZER_VERSION,
    UNITS_PER_SECOND, VTT_SERIALIZER_VERSION, canonical_json_hash,
    compile_subtitle_cues, quantize_subtitle_cues, quantized_cue_model_sha256,
    seconds_to_units, serialize_srt, serialize_vtt, units_to_seconds,
    validate_serialized_subtitles,
)
from app.modules.v4.contracts import V4ContractValidator
from app.modules.v4.production_artifact_loader import ProductionArtifactLoaderV4
from app.modules.v4.scene_asset_manifest import flatten_manifest_entries
from app.modules.v4.voice_audio import validate_audio_artifact_bytes
from app.repositories import ArtifactRepository, AssemblyOutputSetCommitGuard, ProjectRepository
from app.services.artifacts import ArtifactService, StagedArtifact, cleanup_staged_artifact_safely, rollback_safely


COMPILER_VERSION = "assembly_plan_compiler_v1"


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _payload_hash(payload: Any) -> str:
    return canonical_json_hash(payload)


class AssemblyService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.artifact_rows = ArtifactRepository(session)
        self.artifacts = ArtifactService(session, self.artifact_rows)
        self.validator = V4ContractValidator()
        self.loader = ProductionArtifactLoaderV4(self.artifacts, self.validator)

    def _project_required(self, project_id: str) -> Project:
        project = self.projects.get(project_id)
        if project is None:
            raise AppError("PROJECT_NOT_FOUND", f"Project '{project_id}' was not found.", 404, retryable=False)
        return project

    def _load_inputs(self, project: Project) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Artifact, dict[str, Any], dict[str, Any]]:
        required = {
            "script_artifact_id": project.script_artifact_id,
            "scene_plan_artifact_id": project.scene_plan_artifact_id,
            "scene_asset_manifest_artifact_id": project.scene_asset_manifest_artifact_id,
            "narration_audio_artifact_id": project.narration_audio_artifact_id,
            "narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
            "beat_timing_map_artifact_id": project.beat_timing_map_artifact_id,
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise AppError("ASSEMBLY_NOT_READY", "Approved Scene Plan, Scene Assets, Voice pair, and BeatTimingMap are required.", 409, retryable=False, details={"missing": missing})
        script = self.loader.load_script(project, str(project.script_artifact_id), require_current=True, require_downstream_ready=True)
        scene_plan = self.loader.load_approved_scene_plan(project)
        manifest = self.loader.load_scene_asset_manifest(project, str(project.scene_asset_manifest_artifact_id), require_current=True, require_approved=True)
        audio = self.loader.load_approved_narration_audio(project)
        word_timing = self.loader.load_narration_word_timing_map(project, str(project.narration_word_timing_map_artifact_id), require_current=True)
        beat_timing = self.loader.load_beat_timing_map(project, str(project.beat_timing_map_artifact_id), require_current=True)
        if (
            word_timing.get("source_script_artifact_id") != project.script_artifact_id
            or word_timing.get("source_narration_audio_artifact_id") != audio.artifact_id
        ):
            raise AppError("ASSEMBLY_UPSTREAM_STALE", "NarrationWordTimingMap does not match the current approved Voice pair.", 409, retryable=False)
        if (
            beat_timing.get("source_script_revision_id") != project.script_artifact_id
            or beat_timing.get("source_scene_plan_revision_id") != project.scene_plan_artifact_id
            or beat_timing.get("source_narration_audio_artifact_id") != audio.artifact_id
            or beat_timing.get("source_narration_word_timing_map_artifact_id") != project.narration_word_timing_map_artifact_id
        ):
            raise AppError("ASSEMBLY_UPSTREAM_STALE", "BeatTimingMap does not match current Script, Scene Plan, and Voice authorities.", 409, retryable=False)
        if manifest.get("source_scene_plan_revision_id") != project.scene_plan_artifact_id:
            raise AppError("ASSEMBLY_UPSTREAM_STALE", "The current Scene Asset Manifest does not match the current Scene Plan.", 409, retryable=False)
        sample_rate = int(word_timing.get("sample_rate_hz") or 0)
        total_samples = int(word_timing.get("total_sample_count") or 0)
        if (
            sample_rate != 48_000
            or total_samples <= 0
            or int(beat_timing.get("sample_rate_hz") or 0) != sample_rate
            or int(beat_timing.get("total_narration_sample_count") or 0) != total_samples
        ):
            raise AppError("ASSEMBLY_TIMING_AUTHORITY_MISMATCH", "BeatTimingMap and NarrationWordTimingMap do not share the exact 48kHz narration timeline.", 409, retryable=False)
        raw_audio = self.artifacts.resolve_file_path(audio).read_bytes()
        audio_info = validate_audio_artifact_bytes(
            raw_audio,
            declared_mime_type=audio.mime_type,
            expected_size_bytes=audio.size_bytes,
            expected_sha256=audio.sha256,
        )
        if audio_info.sample_rate_hz != sample_rate or audio_info.channels != 1 or audio_info.sample_count != total_samples:
            raise AppError("ASSEMBLY_VOICE_AUDIO_TIMING_MISMATCH", "Approved narration bytes do not match NarrationWordTimingMap samples.", 409, retryable=False)
        return script, scene_plan, manifest, audio, word_timing, beat_timing

    def readiness(self, project_id: str) -> dict[str, Any]:
        project = self._project_required(project_id)
        issues: list[dict[str, str]] = []
        try:
            self._load_inputs(project)
        except AppError as exc:
            issues.append({"error_code": exc.error_code, "message": exc.message})
        return {"status": "ready" if not issues else "not_ready", "issues": issues}

    def _visual_artifact(self, project: Project, entry: dict[str, Any]) -> Artifact:
        artifact_id = entry.get("selected_asset_artifact_id")
        if not isinstance(artifact_id, str):
            raise AppError("ASSEMBLY_VISUAL_ASSET_MISSING", "Every renderable beat requires an exact selected visual Artifact.", 409, retryable=False, details={"beat_id": entry.get("beat_id")})
        artifact = self.artifacts.get_required(artifact_id)
        if artifact.status != ArtifactStatus.READY or artifact.deleted_at is not None or artifact.artifact_type != ArtifactType.IMAGE:
            raise AppError("ASSEMBLY_VISUAL_ASSET_INVALID", "Selected visual Artifact is unavailable or not image-compatible.", 409, retryable=False, details={"artifact_id": artifact_id})
        if not artifact.logical_id.startswith(f"project/{project.project_id}/"):
            raise AppError("ASSEMBLY_VISUAL_ASSET_PROJECT_MISMATCH", "Selected visual Artifact belongs to another project.", 409, retryable=False)
        raw = self.artifacts.resolve_file_path(artifact).read_bytes()
        if not artifact.sha256 or _sha(raw) != artifact.sha256 or artifact.sha256 != entry.get("sha256") or artifact.mime_type != entry.get("mime_type"):
            raise AppError("ASSEMBLY_VISUAL_ASSET_INTEGRITY_INVALID", "Selected visual Artifact integrity does not match the approved manifest.", 409, retryable=False, details={"artifact_id": artifact_id})
        if artifact.width != entry.get("width") or artifact.height != entry.get("height"):
            raise AppError("ASSEMBLY_VISUAL_ASSET_INTEGRITY_INVALID", "Selected visual dimensions do not match the approved manifest.", 409, retryable=False)
        return artifact

    def _optional_audio(
        self,
        project: Project,
        binding: dict[str, Any],
        *,
        total_units: int,
        binding_kind: str = "background_music",
    ) -> dict[str, Any]:
        artifact_id = str(binding.get("artifact_id", ""))
        details = {"artifact_id": artifact_id, "binding_kind": binding_kind}
        try:
            artifact = self.artifacts.get_required(artifact_id)
        except AppError as exc:
            raise AppError(
                "ASSEMBLY_OPTIONAL_AUDIO_INVALID",
                "Optional audio Artifact does not exist.",
                409,
                retryable=False,
                details={**details, "validation_field": "artifact_id"},
            ) from exc
        if artifact.status != ArtifactStatus.READY or artifact.deleted_at is not None or artifact.artifact_type != ArtifactType.AUDIO:
            raise AppError(
                "ASSEMBLY_OPTIONAL_AUDIO_INVALID",
                "Optional audio binding is unavailable or not audio.",
                409,
                retryable=False,
                details={**details, "validation_field": "artifact_state"},
            )
        if not artifact.logical_id.startswith(f"project/{project.project_id}/"):
            raise AppError(
                "ASSEMBLY_OPTIONAL_AUDIO_PROJECT_MISMATCH",
                "Optional audio belongs to another project.",
                409,
                retryable=False,
                details=details,
            )
        try:
            path = self.artifacts.resolve_file_path(artifact)
            raw = path.read_bytes()
        except (AppError, OSError) as exc:
            code = "ASSEMBLY_OPTIONAL_AUDIO_BYTES_MISSING" if not isinstance(exc, AppError) or exc.error_code == "ARTIFACT_FILE_MISSING" else "ASSEMBLY_OPTIONAL_AUDIO_INVALID"
            raise AppError(
                code,
                "Optional audio bytes are missing or inaccessible.",
                409,
                retryable=False,
                details={**details, "validation_field": "bytes"},
            ) from exc
        try:
            info = validate_audio_artifact_bytes(
                raw,
                declared_mime_type=artifact.mime_type,
                expected_size_bytes=artifact.size_bytes,
                expected_sha256=artifact.sha256,
            )
        except AppError as exc:
            code = {
                "AUDIO_HASH_MISMATCH": "ASSEMBLY_OPTIONAL_AUDIO_HASH_MISMATCH",
                "AUDIO_FORMAT_UNSUPPORTED": "ASSEMBLY_OPTIONAL_AUDIO_FORMAT_UNSUPPORTED",
                "AUDIO_BYTES_MISSING": "ASSEMBLY_OPTIONAL_AUDIO_BYTES_MISSING",
            }.get(exc.error_code, "ASSEMBLY_OPTIONAL_AUDIO_INVALID")
            field = {
                "AUDIO_HASH_MISMATCH": "sha256",
                "AUDIO_SIZE_MISMATCH": "size_bytes",
                "AUDIO_FORMAT_UNSUPPORTED": "mime_type_or_format",
                "AUDIO_BYTES_MISSING": "bytes",
            }.get(exc.error_code, "audio_bytes")
            raise AppError(
                code,
                "Optional audio immutable-byte validation failed.",
                422,
                retryable=False,
                details={**details, "validation_field": field},
            ) from exc
        if artifact.duration_sec is not None:
            tolerance = 1.0 / info.sample_rate_hz
            if abs(float(artifact.duration_sec) - info.duration_seconds) > tolerance:
                raise AppError(
                    "ASSEMBLY_OPTIONAL_AUDIO_INVALID",
                    "Optional audio duration metadata does not match the validated bytes.",
                    422,
                    retryable=False,
                    details={**details, "validation_field": "duration_sec"},
                )
        if binding["start_time_units"] < 0 or binding["end_time_units"] > total_units or binding["end_time_units"] <= binding["start_time_units"]:
            raise AppError(
                "ASSEMBLY_OPTIONAL_AUDIO_RANGE_INVALID",
                "Optional audio interval is outside the Assembly timeline.",
                422,
                retryable=False,
                details=details,
            )
        return {
            **binding,
            "validator_version": info.validator_version,
            "mime_type": info.mime_type,
            "codec": info.codec,
            "container": info.container,
            "sample_rate_hz": info.sample_rate_hz,
            "channels": info.channels,
            "sample_width_bytes": info.sample_width_bytes,
            "sample_count": info.sample_count,
            "duration_seconds": info.duration_seconds,
            "byte_count": info.byte_count,
            "sha256": info.sha256,
        }

    def _revalidate_optional_audio_bindings(self, project: Project, payload: dict[str, Any]) -> None:
        total_units = int(payload["total_duration_time_units"])
        frozen_fields = (
            "artifact_id", "validator_version", "mime_type", "codec", "container",
            "sample_rate_hz", "channels", "sample_width_bytes", "sample_count",
            "duration_seconds", "byte_count", "sha256",
        )
        background = payload.get("background_audio_track")
        if background:
            current = self._optional_audio(
                project, dict(background), total_units=total_units, binding_kind="background_music"
            )
            if any(current.get(field) != background.get(field) for field in frozen_fields):
                raise AppError(
                    "ASSEMBLY_OPTIONAL_AUDIO_HASH_MISMATCH",
                    "Optional background-music bytes changed after validation.",
                    409,
                    retryable=False,
                    details={"artifact_id": background.get("artifact_id"), "binding_kind": "background_music", "validation_field": "frozen_identity"},
                )
        for track in payload.get("sfx_track", []):
            candidate = dict(track)
            candidate["end_time_units"] = int(candidate["start_time_units"]) + int(candidate["duration_time_units"])
            current = self._optional_audio(
                project, candidate, total_units=total_units, binding_kind="sound_effect"
            )
            if any(current.get(field) != track.get(field) for field in frozen_fields):
                raise AppError(
                    "ASSEMBLY_OPTIONAL_AUDIO_HASH_MISMATCH",
                    "Optional sound-effect bytes changed after validation.",
                    409,
                    retryable=False,
                    details={"artifact_id": track.get("artifact_id"), "binding_kind": "sound_effect", "validation_field": "frozen_identity"},
                )

    def compile_plan(self, *, project: Project, request: dict[str, Any], assembly_id: str, srt_id: str, vtt_id: str, status: str = "draft_ready", revision_operation: str = "build", parent_id: str | None = None) -> tuple[dict[str, Any], bytes, bytes]:
        parsed = AssemblyBuildRequest.model_validate(request).model_dump(mode="json")
        script, scene_plan, manifest, audio, word_timing, beat_timing = self._load_inputs(project)
        expected = {
            "expected_scene_plan_artifact_id": project.scene_plan_artifact_id,
            "expected_scene_asset_manifest_artifact_id": project.scene_asset_manifest_artifact_id,
            "expected_narration_audio_artifact_id": project.narration_audio_artifact_id,
            "expected_narration_word_timing_map_artifact_id": project.narration_word_timing_map_artifact_id,
            "expected_beat_timing_map_artifact_id": project.beat_timing_map_artifact_id,
            "expected_assembly_plan_artifact_id": project.assembly_plan_artifact_id,
            "expected_subtitle_srt_artifact_id": project.subtitle_srt_artifact_id,
            "expected_subtitle_vtt_artifact_id": project.subtitle_vtt_artifact_id,
        }
        for key, value in expected.items():
            if parsed.get(key) != value:
                raise AppError("ASSEMBLY_PUBLICATION_CONFLICT", "Assembly input or current output changed before build.", 409, retryable=False, details={"field": key})
        cues, cue_warnings = compile_subtitle_cues(full_script=str(script["full_script"]), word_timing_map=word_timing, beat_timing_map=beat_timing)
        sample_rate_hz = int(word_timing["sample_rate_hz"])
        total_samples = int(word_timing["total_sample_count"])
        total_units = int(total_samples * UNITS_PER_SECOND // sample_rate_hz)
        quantized_cues = quantize_subtitle_cues(cues, total_duration_units=total_units)
        quantized_hash = quantized_cue_model_sha256(quantized_cues)
        srt_bytes = serialize_srt(quantized_cues, total_duration_units=total_units)
        vtt_bytes = serialize_vtt(quantized_cues, total_duration_units=total_units)
        validate_serialized_subtitles(
            srt_bytes=srt_bytes, vtt_bytes=vtt_bytes, quantized_cues=quantized_cues,
            total_duration_units=total_units,
        )
        scene_plan_beats = [(scene, beat) for scene in scene_plan["scenes"] for beat in scene["beats"]]
        manifest_entries = flatten_manifest_entries(manifest)
        timing_beats = list(beat_timing["beats"])
        if len(scene_plan_beats) != len(manifest_entries) or len(scene_plan_beats) != len(timing_beats):
            raise AppError("ASSEMBLY_BEAT_COVERAGE_INVALID", "Scene Plan, Scene Assets, and Voice timing must cover the same beats.", 409, retryable=False)
        # BeatTimingMap visual intervals are the sole visual timing authority.
        # Convert exact integer sample boundaries directly; never reconstruct from float seconds.

        beats: list[dict[str, Any]] = []
        for index, ((scene, beat), entry, beat_timing) in enumerate(zip(scene_plan_beats, manifest_entries, timing_beats, strict=True)):
            keys = ("scene_id", "beat_id", "beat_order", "local_beat_order")
            if any(entry[k] != beat_timing[k] or entry[k] != (scene["scene_id"] if k == "scene_id" else beat[k]) for k in keys):
                raise AppError("ASSEMBLY_BEAT_IDENTITY_INVALID", "Scene Plan, Scene Asset, and narration beat identities differ.", 409, retryable=False, details={"index": index})
            if list(beat["resolved_script_span"]) != [beat_timing["source_start"], beat_timing["source_end"]]:
                raise AppError("ASSEMBLY_BEAT_SPAN_INVALID", "Narration beat Script span differs from the approved Scene Plan.", 409, retryable=False, details={"beat_id": beat["beat_id"]})
            artifact = self._visual_artifact(project, entry)
            start_units = int(int(beat_timing["visual_start_sample"]) * UNITS_PER_SECOND // sample_rate_hz)
            end_units = int(int(beat_timing["visual_end_sample"]) * UNITS_PER_SECOND // sample_rate_hz)
            if end_units <= start_units:
                raise AppError("ASSEMBLY_TIMELINE_INVALID", "Beat timing must have positive duration.", 409, retryable=False)
            treatment = entry.get("treatment") or {}
            visual = compile_visual_instruction(camera_movement=str(treatment.get("camera_movement", "none")), framing=str(treatment.get("framing", "")), camera_angle=str(treatment.get("camera_angle", "")))
            cue_ids = [cue["cue_id"] for cue in cues if beat["beat_id"] in cue["beat_ids"]]
            planned = float(entry["computed_duration_seconds"])
            actual = units_to_seconds(end_units-start_units)
            beats.append({
                "scene_id": scene["scene_id"], "scene_order": scene["scene_order"], "beat_id": beat["beat_id"], "beat_order": beat["beat_order"],
                "local_beat_order": beat["local_beat_order"], "continuity_group_id": entry["continuity_group_id"],
                "source_start": beat_timing["source_start"], "source_end": beat_timing["source_end"],
                "start_time_units": start_units, "end_time_units": end_units, "duration_time_units": end_units-start_units,
                "start_seconds": units_to_seconds(start_units), "end_seconds": units_to_seconds(end_units), "duration_seconds": actual,
                "planned_duration_seconds": planned, "duration_delta_seconds": round(actual-planned, 6),
                "selected_visual_artifact_id": artifact.artifact_id,
                "selected_visual_mime_type": artifact.mime_type, "selected_visual_sha256": artifact.sha256,
                "selected_visual_width": artifact.width, "selected_visual_height": artifact.height,
                "visual_instruction": visual, "transition_instruction": cut_instruction(), "subtitle_cue_ids": cue_ids,
                "source_scene_asset_entry_sha256": _payload_hash(entry), "source_narration_beat_timing_sha256": _payload_hash(beat_timing),
            })
        # Exact order and continuous narration-time coverage.
        if beats[0]["start_time_units"] != 0 or beats[-1]["end_time_units"] != total_units:
            raise AppError("ASSEMBLY_TIMELINE_COVERAGE_INVALID", "Visual timeline must start at zero and end at narration duration.", 409, retryable=False)
        for left, right in zip(beats, beats[1:]):
            if left["end_time_units"] != right["start_time_units"]:
                raise AppError("ASSEMBLY_TIMELINE_COVERAGE_INVALID", "Visual timeline contains a gap or overlap.", 409, retryable=False)
        scenes: list[dict[str, Any]] = []
        beat_by_id = {beat["beat_id"]: beat for beat in beats}
        for scene in scene_plan["scenes"]:
            scene_beats = [beat_by_id[beat["beat_id"]] for beat in scene["beats"]]
            start_u = scene_beats[0]["start_time_units"]
            end_u = scene_beats[-1]["end_time_units"]
            source_start, source_end = [int(value) for value in scene["resolved_script_span"]]
            scenes.append({"scene_id": scene["scene_id"], "scene_order": scene["scene_order"], "source_start": source_start, "source_end": source_end, "start_time_units": start_u, "end_time_units": end_u, "duration_time_units": end_u-start_u, "beat_ids": [b["beat_id"] for b in scene["beats"]]})
        background = self._optional_audio(project, parsed["background_music"], total_units=total_units, binding_kind="background_music") if parsed.get("background_music") else None
        sfx: list[dict[str, Any]] = []
        scene_by_id = {s["scene_id"]: s for s in scenes}; beat_by_id = {b["beat_id"]: b for b in beats}
        for binding in parsed.get("sound_effects", []):
            resolved = dict(binding)
            anchor = binding["anchor_type"]
            if anchor == "scene_start":
                owner = scene_by_id.get(binding.get("scene_id"));
                if owner is None: raise AppError("ASSEMBLY_SFX_ANCHOR_INVALID", "SFX scene anchor does not exist.", 422, retryable=False)
                resolved["start_time_units"] = owner["start_time_units"] + binding["start_time_units"]
            elif anchor in {"beat_start", "beat_end"}:
                owner = beat_by_id.get(binding.get("beat_id"));
                if owner is None: raise AppError("ASSEMBLY_SFX_ANCHOR_INVALID", "SFX beat anchor does not exist.", 422, retryable=False)
                base = owner["start_time_units"] if anchor == "beat_start" else owner["end_time_units"]
                resolved["start_time_units"] = base + binding["start_time_units"]
            resolved["end_time_units"] = resolved["start_time_units"] + binding["duration_time_units"]
            resolved = self._optional_audio(project, resolved, total_units=total_units, binding_kind="sound_effect")
            resolved.pop("end_time_units", None)
            sfx.append(resolved)
        input_identity = {"script": project.script_artifact_id, "scene_plan": project.scene_plan_artifact_id, "scene_assets": project.scene_asset_manifest_artifact_id, "audio": audio.artifact_id, "word_timing": project.narration_word_timing_map_artifact_id, "beat_timing": project.beat_timing_map_artifact_id, "optional_audio": {"background": background, "sfx": sfx}}
        cue_hash = _payload_hash(cues)
        qc = {
            "lineage_status": "pass", "scene_coverage_status": "pass", "beat_coverage_status": "pass", "visual_asset_status": "pass",
            "visual_timeline_status": "pass", "transition_status": "pass", "narration_track_status": "pass", "duration_status": "pass",
            "subtitle_text_coverage_status": "pass", "subtitle_timing_status": "pass", "srt_serialization_status": "pass", "vtt_serialization_status": "pass",
            "optional_audio_status": "pass" if background or sfx else "not_configured", "publication_status": "pending", "warnings": cue_warnings, "errors": [],
        }
        payload = {
            "artifact_type": "assembly_plan", "schema_version": "3.0", "project_id": project.project_id,
            "assembly_plan_revision_id": assembly_id, "parent_assembly_plan_revision_id": parent_id,
            "revision_operation": revision_operation, "status": status,
            "source_script_artifact_id": str(project.script_artifact_id), "source_scene_plan_artifact_id": str(project.scene_plan_artifact_id),
            "source_scene_asset_manifest_artifact_id": str(project.scene_asset_manifest_artifact_id), "source_narration_audio_artifact_id": audio.artifact_id,
            "source_narration_word_timing_map_artifact_id": str(project.narration_word_timing_map_artifact_id),
            "source_beat_timing_map_artifact_id": str(project.beat_timing_map_artifact_id),
            "source_subtitle_srt_artifact_id": srt_id, "source_subtitle_vtt_artifact_id": vtt_id,
            "compiler_version": COMPILER_VERSION, "compiler_input_sha256": _payload_hash(input_identity),
            "timeline_timebase": {"units_per_second": UNITS_PER_SECOND, "unit": "microsecond"},
            "total_duration_seconds": units_to_seconds(total_units), "total_duration_time_units": total_units,
            "visual_track": {"compiler_version": COMPILER_VERSION, "coverage_start_time_units": 0, "coverage_end_time_units": total_units, "beat_ids": [b["beat_id"] for b in beats]},
            "narration_track": {"artifact_id": audio.artifact_id, "mime_type": audio.mime_type or "audio/wav", "sha256": str(audio.sha256), "sample_rate_hz": sample_rate_hz, "channels": 1, "sample_count": total_samples, "duration_time_units": total_units, "duration_seconds": units_to_seconds(total_units), "start_time_units": 0, "end_time_units": total_units, "gain_db": 0},
            "background_audio_track": background, "sfx_track": sfx,
            "subtitle_track": {"cue_compiler_version": CUE_COMPILER_VERSION, "cue_model_sha256": cue_hash, "millisecond_quantizer_version": MILLISECOND_QUANTIZER_VERSION, "quantized_cue_model_sha256": quantized_hash, "serialized_timeline_end_ms": (total_units + 999) // 1000, "srt_serializer_version": SRT_SERIALIZER_VERSION, "vtt_serializer_version": VTT_SERIALIZER_VERSION, "cue_count": len(cues), "srt_sha256": _sha(srt_bytes), "vtt_sha256": _sha(vtt_bytes)},
            "scenes": scenes, "beats": beats, "subtitle_cues": cues, "backend_qc": qc, "created_at": utc_now_iso(),
        }
        return AssemblyPlanModel.model_validate(payload).model_dump(mode="json"), srt_bytes, vtt_bytes

    def publish_assembly_output_set(self, *, project: Project, payload: dict[str, Any], srt_bytes: bytes | None, vtt_bytes: bytes | None, expected_assembly_id: str | None, expected_srt_id: str | None, expected_vtt_id: str | None, reuse_srt: Artifact | None = None, reuse_vtt: Artifact | None = None, before_commit: Callable[[], None] | None = None) -> tuple[Artifact, Artifact, Artifact]:
        staged_plan: StagedArtifact | None = None; staged_srt: StagedArtifact | None = None; staged_vtt: StagedArtifact | None = None
        try:
            self.session.refresh(project)
            if (project.assembly_plan_artifact_id, project.subtitle_srt_artifact_id, project.subtitle_vtt_artifact_id) != (expected_assembly_id, expected_srt_id, expected_vtt_id):
                raise AppError("ASSEMBLY_PUBLICATION_CONFLICT", "Assembly output set changed before publication.", 409, retryable=False)
            if (project.script_artifact_id != payload["source_script_artifact_id"] or project.scene_plan_artifact_id != payload["source_scene_plan_artifact_id"] or project.scene_asset_manifest_artifact_id != payload["source_scene_asset_manifest_artifact_id"] or project.narration_audio_artifact_id != payload["source_narration_audio_artifact_id"] or project.narration_word_timing_map_artifact_id != payload["source_narration_word_timing_map_artifact_id"] or project.beat_timing_map_artifact_id != payload["source_beat_timing_map_artifact_id"]):
                raise AppError("ASSEMBLY_UPSTREAM_STALE", "Assembly upstream lineage changed before publication.", 409, retryable=False)
            self._revalidate_optional_audio_bindings(project, payload)
            old_plan = self.artifact_rows.get(expected_assembly_id) if expected_assembly_id else None
            old_srt = self.artifact_rows.get(expected_srt_id) if expected_srt_id else None
            old_vtt = self.artifact_rows.get(expected_vtt_id) if expected_vtt_id else None
            if reuse_srt is None:
                assert srt_bytes is not None
                staged_srt = self.artifacts.stage_artifact_revision(logical_id=f"project/{project.project_id}/subtitles/srt", artifact_type=ArtifactType.SRT, artifact_role=ArtifactRole.SUBTITLE_SRT, filename="subtitles.srt", content=srt_bytes, artifact_id=payload["source_subtitle_srt_artifact_id"], content_hash=canonical_json_hash({"quantizer_version": payload["subtitle_track"]["millisecond_quantizer_version"], "quantized_cue_model_sha256": payload["subtitle_track"]["quantized_cue_model_sha256"], "serializer_version": payload["subtitle_track"]["srt_serializer_version"], "bytes_sha256": _sha(srt_bytes)}))
                new_srt = staged_srt.artifact; new_srt.mime_type = "application/x-subrip"
            else:
                new_srt = reuse_srt
            if reuse_vtt is None:
                assert vtt_bytes is not None
                staged_vtt = self.artifacts.stage_artifact_revision(logical_id=f"project/{project.project_id}/subtitles/vtt", artifact_type=ArtifactType.VTT, artifact_role=ArtifactRole.SUBTITLE_VTT, filename="subtitles.vtt", content=vtt_bytes, artifact_id=payload["source_subtitle_vtt_artifact_id"], content_hash=canonical_json_hash({"quantizer_version": payload["subtitle_track"]["millisecond_quantizer_version"], "quantized_cue_model_sha256": payload["subtitle_track"]["quantized_cue_model_sha256"], "serializer_version": payload["subtitle_track"]["vtt_serializer_version"], "bytes_sha256": _sha(vtt_bytes)}))
                new_vtt = staged_vtt.artifact; new_vtt.mime_type = "text/vtt"
            else:
                new_vtt = reuse_vtt
            if payload["source_subtitle_srt_artifact_id"] != new_srt.artifact_id or payload["source_subtitle_vtt_artifact_id"] != new_vtt.artifact_id:
                raise AppError("ASSEMBLY_SUBTITLE_LINK_INVALID", "Assembly subtitle linkage is invalid.", 422, retryable=False)
            payload["backend_qc"]["publication_status"] = "pass"
            AssemblyPlanModel.model_validate(payload)
            self.validator.validate("assembly_plan_v1.schema.json", payload, error_code="ASSEMBLY_PLAN_INVALID")
            staged_plan = self.artifacts.stage_artifact_revision(logical_id=f"project/{project.project_id}/assembly_plan", artifact_type=ArtifactType.JSON, artifact_role=ArtifactRole.PLAN_JSON, filename="assembly_plan.json", content=_json_bytes(payload), artifact_id=payload["assembly_plan_revision_id"], content_hash=_payload_hash(payload))
            new_plan = staged_plan.artifact; new_plan.mime_type = "application/json"
            won = self.projects.compare_and_set_assembly_output_set(
                project_id=project.project_id, new_assembly_plan_artifact_id=new_plan.artifact_id,
                new_subtitle_srt_artifact_id=new_srt.artifact_id, new_subtitle_vtt_artifact_id=new_vtt.artifact_id,
                guard=AssemblyOutputSetCommitGuard(
                    script_artifact_id=str(project.script_artifact_id), scene_plan_artifact_id=str(project.scene_plan_artifact_id),
                    scene_asset_manifest_artifact_id=str(project.scene_asset_manifest_artifact_id), narration_audio_artifact_id=str(project.narration_audio_artifact_id),
                    narration_word_timing_map_artifact_id=str(project.narration_word_timing_map_artifact_id), beat_timing_map_artifact_id=str(project.beat_timing_map_artifact_id), previous_assembly_plan_artifact_id=expected_assembly_id,
                    previous_subtitle_srt_artifact_id=expected_srt_id, previous_subtitle_vtt_artifact_id=expected_vtt_id,
                ), updated_at=utc_now_iso())
            if not won:
                raise AppError("ASSEMBLY_PUBLICATION_CONFLICT", "Assembly output set changed before publication.", 409, retryable=False)
            for old, new in ((old_plan,new_plan),(old_srt,new_srt),(old_vtt,new_vtt)):
                if old is not None and old.artifact_id != new.artifact_id:
                    old.is_current = False; old.superseded_by_artifact_id = new.artifact_id
                if not self.artifact_rows.promote_exact_current_artifact(
                    logical_id=new.logical_id,
                    new_artifact_id=new.artifact_id,
                ):
                    raise AppError("ASSEMBLY_PUBLICATION_INVARIANT_FAILED", "Assembly output revision could not be promoted.", 500, retryable=False)
            self.session.flush()
            self.session.expire(project, ["assembly_plan_artifact_id", "subtitle_srt_artifact_id", "subtitle_vtt_artifact_id"])
            self.session.refresh(project, attribute_names=["assembly_plan_artifact_id", "subtitle_srt_artifact_id", "subtitle_vtt_artifact_id"])
            if (project.assembly_plan_artifact_id, project.subtitle_srt_artifact_id, project.subtitle_vtt_artifact_id) != (new_plan.artifact_id, new_srt.artifact_id, new_vtt.artifact_id):
                raise AppError("ASSEMBLY_PUBLICATION_INVARIANT_FAILED", "Assembly Project pointers do not match the promoted output set.", 500, retryable=False)
            for artifact in (new_plan,new_srt,new_vtt):
                rows = list(self.session.scalars(select(Artifact).where(Artifact.logical_id == artifact.logical_id, Artifact.is_current.is_(True))))
                if len(rows) != 1 or rows[0].artifact_id != artifact.artifact_id:
                    raise AppError("ASSEMBLY_PUBLICATION_INVARIANT_FAILED", "Assembly output has multiple current revisions.", 500, retryable=False)
            if before_commit: before_commit()
            self.session.commit()
            return new_plan,new_srt,new_vtt
        except Exception:
            rollback_safely(self.session, logger=__import__("logging").getLogger(__name__))
            for staged in (staged_plan, staged_srt, staged_vtt): cleanup_staged_artifact_safely(staged, logger=__import__("logging").getLogger(__name__))
            raise

    def build(self, project_id: str, request: dict[str, Any]) -> dict[str, Any]:
        project = self._project_required(project_id)
        assembly_id, srt_id, vtt_id = generate_id("artifact"), generate_id("artifact"), generate_id("artifact")
        operation = "rebuild" if project.assembly_plan_artifact_id else "build"
        payload,srt,vtt = self.compile_plan(project=project, request=request, assembly_id=assembly_id, srt_id=srt_id, vtt_id=vtt_id, revision_operation=operation, parent_id=project.assembly_plan_artifact_id)
        self.publish_assembly_output_set(project=project,payload=payload,srt_bytes=srt,vtt_bytes=vtt,expected_assembly_id=request.get("expected_assembly_plan_artifact_id"),expected_srt_id=request.get("expected_subtitle_srt_artifact_id"),expected_vtt_id=request.get("expected_subtitle_vtt_artifact_id"))
        return self.get_workspace(project_id)

    def _load_current_plan(self, project: Project) -> tuple[Artifact, Artifact, Artifact, dict[str, Any]]:
        if not project.assembly_plan_artifact_id or not project.subtitle_srt_artifact_id or not project.subtitle_vtt_artifact_id:
            raise AppError("ASSEMBLY_NOT_BUILT", "Assembly output has not been built.", 409, retryable=False)
        plan = self.artifacts.get_required(project.assembly_plan_artifact_id); srt = self.artifacts.get_required(project.subtitle_srt_artifact_id); vtt = self.artifacts.get_required(project.subtitle_vtt_artifact_id)
        for artifact in (plan,srt,vtt):
            if not artifact.is_current or artifact.superseded_by_artifact_id is not None or artifact.status != ArtifactStatus.READY or artifact.deleted_at is not None:
                raise AppError("ASSEMBLY_OUTPUT_NOT_CURRENT", "Assembly output is unavailable or not current.", 409, retryable=False)
        if plan.logical_id != f"project/{project.project_id}/assembly_plan" or srt.logical_id != f"project/{project.project_id}/subtitles/srt" or vtt.logical_id != f"project/{project.project_id}/subtitles/vtt":
            raise AppError("ASSEMBLY_OUTPUT_PROJECT_MISMATCH", "Assembly output belongs to another project.", 409, retryable=False)
        raw = self.artifacts.resolve_file_path(plan).read_bytes()
        if not plan.sha256 or _sha(raw) != plan.sha256:
            raise AppError("ASSEMBLY_PLAN_CORRUPT", "Assembly Plan bytes failed integrity validation.", 409, retryable=False)
        payload = json.loads(raw.decode("utf-8"))
        AssemblyPlanModel.model_validate(payload); self.validator.validate("assembly_plan_v1.schema.json", payload, error_code="ASSEMBLY_PLAN_INVALID")
        if payload["assembly_plan_revision_id"] != plan.artifact_id or payload["source_subtitle_srt_artifact_id"] != srt.artifact_id or payload["source_subtitle_vtt_artifact_id"] != vtt.artifact_id:
            raise AppError("ASSEMBLY_OUTPUT_LINK_INVALID", "Assembly output cross-linkage is invalid.", 409, retryable=False)
        total_units = int(payload["total_duration_time_units"])
        quantized_cues = quantize_subtitle_cues(payload["subtitle_cues"], total_duration_units=total_units)
        quantized_hash = quantized_cue_model_sha256(quantized_cues)
        track = payload["subtitle_track"]
        if (track.get("millisecond_quantizer_version") != MILLISECOND_QUANTIZER_VERSION
            or track.get("quantized_cue_model_sha256") != quantized_hash
            or track.get("serialized_timeline_end_ms") != (total_units + 999) // 1000):
            raise AppError("ASSEMBLY_SUBTITLE_INTEGRITY_INVALID", "Subtitle quantization metadata is invalid.", 409, retryable=False)
        expected_srt = serialize_srt(quantized_cues, total_duration_units=total_units)
        expected_vtt = serialize_vtt(quantized_cues, total_duration_units=total_units)
        actual_srt = self.artifacts.resolve_file_path(srt).read_bytes(); actual_vtt = self.artifacts.resolve_file_path(vtt).read_bytes()
        validate_serialized_subtitles(srt_bytes=actual_srt, vtt_bytes=actual_vtt, quantized_cues=quantized_cues, total_duration_units=total_units)
        if actual_srt != expected_srt or actual_vtt != expected_vtt:
            raise AppError("ASSEMBLY_SUBTITLE_BYTES_INVALID", "Subtitle bytes do not match the shared quantized cue model.", 409, retryable=False)
        expected_srt_content_hash = canonical_json_hash({"quantizer_version": MILLISECOND_QUANTIZER_VERSION, "quantized_cue_model_sha256": quantized_hash, "serializer_version": track["srt_serializer_version"], "bytes_sha256": _sha(actual_srt)})
        expected_vtt_content_hash = canonical_json_hash({"quantizer_version": MILLISECOND_QUANTIZER_VERSION, "quantized_cue_model_sha256": quantized_hash, "serializer_version": track["vtt_serializer_version"], "bytes_sha256": _sha(actual_vtt)})
        if (not srt.sha256 or _sha(actual_srt) != srt.sha256 or srt.sha256 != track["srt_sha256"] or srt.size_bytes != len(actual_srt) or srt.content_hash != expected_srt_content_hash
            or not vtt.sha256 or _sha(actual_vtt) != vtt.sha256 or vtt.sha256 != track["vtt_sha256"] or vtt.size_bytes != len(actual_vtt) or vtt.content_hash != expected_vtt_content_hash):
            raise AppError("ASSEMBLY_SUBTITLE_INTEGRITY_INVALID", "Subtitle Artifact hash, byte, or quantizer metadata is invalid.", 409, retryable=False)
        # Revalidate current upstream identities and immutable inputs.
        self._load_inputs(project)
        self._revalidate_optional_audio_bindings(project, payload)
        if any(payload[field] != expected for field,expected in {
            "source_script_artifact_id":project.script_artifact_id,"source_scene_plan_artifact_id":project.scene_plan_artifact_id,
            "source_scene_asset_manifest_artifact_id":project.scene_asset_manifest_artifact_id,"source_narration_audio_artifact_id":project.narration_audio_artifact_id,
            "source_narration_word_timing_map_artifact_id":project.narration_word_timing_map_artifact_id,"source_beat_timing_map_artifact_id":project.beat_timing_map_artifact_id}.items()):
            raise AppError("ASSEMBLY_UPSTREAM_STALE", "Assembly output lineage is stale.", 409, retryable=False)
        return plan,srt,vtt,payload

    def approve(self, project_id: str, request: dict[str, Any]) -> dict[str, Any]:
        project = self._project_required(project_id)
        expected = (request.get("expected_assembly_plan_artifact_id"),request.get("expected_subtitle_srt_artifact_id"),request.get("expected_subtitle_vtt_artifact_id"))
        if expected != (project.assembly_plan_artifact_id,project.subtitle_srt_artifact_id,project.subtitle_vtt_artifact_id):
            raise AppError("ASSEMBLY_PUBLICATION_CONFLICT", "Assembly output changed before approval.", 409, retryable=False)
        plan,srt,vtt,payload = self._load_current_plan(project)
        if payload["status"] == "approved": return self.get_workspace(project_id)
        approved = deepcopy(payload); approved_id = generate_id("artifact")
        approved.update({"assembly_plan_revision_id":approved_id,"parent_assembly_plan_revision_id":plan.artifact_id,"revision_operation":"approval","status":"approved","created_at":utc_now_iso()})
        approved["backend_qc"]["publication_status"] = "pending"
        self.publish_assembly_output_set(project=project,payload=approved,srt_bytes=None,vtt_bytes=None,expected_assembly_id=plan.artifact_id,expected_srt_id=srt.artifact_id,expected_vtt_id=vtt.artifact_id,reuse_srt=srt,reuse_vtt=vtt)
        return self.get_workspace(project_id)

    def get_workspace(self, project_id: str) -> dict[str, Any]:
        project = self._project_required(project_id); readiness = self.readiness(project_id)
        payload: dict[str, Any] | None = None
        errors: list[dict[str, Any]] = []
        if project.assembly_plan_artifact_id or project.subtitle_srt_artifact_id or project.subtitle_vtt_artifact_id:
            try: _,_,_,payload = self._load_current_plan(project)
            except AppError as exc: errors.append({"error_code":exc.error_code,"message":exc.message})
        response = {
            "project_id":project_id,"readiness":readiness,"current_assembly_plan_artifact_id":project.assembly_plan_artifact_id,
            "assembly_status":payload.get("status") if payload else None,"current_subtitle_srt_artifact_id":project.subtitle_srt_artifact_id,"current_subtitle_vtt_artifact_id":project.subtitle_vtt_artifact_id,
            "source_identities":{"script_artifact_id":project.script_artifact_id,"scene_plan_artifact_id":project.scene_plan_artifact_id,"scene_asset_manifest_artifact_id":project.scene_asset_manifest_artifact_id,"narration_audio_artifact_id":project.narration_audio_artifact_id,"narration_word_timing_map_artifact_id":project.narration_word_timing_map_artifact_id,"beat_timing_map_artifact_id":project.beat_timing_map_artifact_id},
            "total_duration_seconds":payload.get("total_duration_seconds") if payload else None,"scene_count":len(payload.get("scenes",[])) if payload else 0,"beat_count":len(payload.get("beats",[])) if payload else 0,"subtitle_cue_count":len(payload.get("subtitle_cues",[])) if payload else 0,
            "visual_timeline_summary":payload.get("visual_track",{}) if payload else {},"narration_track_summary":payload.get("narration_track",{}) if payload else {},
            "optional_audio_summary":{"background_music_configured":bool(payload and payload.get("background_audio_track")),"sfx_count":len(payload.get("sfx_track",[])) if payload else 0},
            "subtitle_summary":payload.get("subtitle_track",{}) if payload else {},"backend_qc":payload.get("backend_qc",{}) if payload else {},
            "scenes":payload.get("scenes",[]) if payload else [],"beats":payload.get("beats",[]) if payload else [],"subtitle_cues":payload.get("subtitle_cues",[]) if payload else [],
            "build_eligible":readiness["status"]=="ready","approval_eligible":bool(payload and payload.get("status")=="draft_ready" and not errors),
            "available_actions":(["build"] if payload is None else (["rebuild","approve"] if payload.get("status")=="draft_ready" else ["rebuild"])),"errors":errors,
        }
        return AssemblyWorkspaceResponse.model_validate(response).model_dump(mode="json")

    def resolve_subtitle(self, project_id: str, kind: str) -> Artifact:
        project = self._project_required(project_id); _,srt,vtt,_ = self._load_current_plan(project)
        if kind == "srt": return srt
        if kind == "vtt": return vtt
        raise AppError("ASSEMBLY_SUBTITLE_KIND_INVALID", "Unsupported subtitle kind.", 404, retryable=False)
