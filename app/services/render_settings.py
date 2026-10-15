from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session

from app.api.errors import AppError
from app.api.render_schemas_v4 import RenderSettingsModel
from app.core.config import Settings, get_settings
from app.core.contracts import ContractPathResolver
from app.core.time import utc_now_iso
from app.db.models import Setting
from app.repositories import SettingsRepository

SETTINGS_KEY = "render_settings_v3"
REQUIRED_FILTERS = {
    "scale", "crop", "pad", "perspective", "fps", "setsar", "format", "concat", "xfade", "blend", "trim", "setpts",
    "atrim", "asetpts", "aresample", "pan", "volume", "afade", "adelay", "amix",
}


def default_render_settings() -> dict[str, Any]:
    return {
        "active_profile_id": "youtube_1080p",
        "profiles": [{
            "profile_id": "youtube_1080p", "display_name": "YouTube 1080p", "enabled": True,
            "width": 1920, "height": 1080,
            "frame_rate": {"numerator": 30, "denominator": 1},
            "video_codec": "libx264", "video_crf": 20, "video_preset": "medium",
            "pixel_format": "yuv420p", "audio_codec": "aac", "audio_bitrate_kbps": 192,
            "audio_sample_rate_hz": 48000, "audio_channels": 2,
            "subtitle_mode": "embedded_mov_text", "faststart": True,
            "deterministic_encoding": True, "encoder_threads": 1,
            "clipping_policy": "fail_on_detected_clipping",
        }],
    }


def canonical_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class RenderSettingsService:
    def __init__(self, session: Session, repository: SettingsRepository, *, runtime_settings: Settings | None = None, resolver: ContractPathResolver | None = None) -> None:
        self.session = session
        self.repository = repository
        self.runtime = runtime_settings or get_settings()
        self.resolver = resolver or ContractPathResolver.from_project_root()

    def _validate_schema(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = self.runtime.root_dir / "contracts" / "schemas" / "settings" / "render_settings.schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.path))
        if errors:
            raise AppError("RENDER_PROFILE_INVALID", "Render settings are invalid.", 400, retryable=False, details={"errors": [item.message for item in errors[:20]]})
        try:
            return RenderSettingsModel.model_validate(payload).model_dump(mode="json")
        except Exception as exc:
            raise AppError("RENDER_PROFILE_INVALID", "Render settings are invalid.", 400, retryable=False) from exc

    def get_settings(self) -> dict[str, Any]:
        row = self.repository.get(SETTINGS_KEY)
        payload = deepcopy(row.value_json) if row and isinstance(row.value_json, dict) else default_render_settings()
        return self._validate_schema(payload)

    def put_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        validated = self._validate_schema(deepcopy(payload))
        now = utc_now_iso()
        row = self.repository.get(SETTINGS_KEY)
        if row is None:
            self.repository.upsert(Setting(key=SETTINGS_KEY, value_json=validated, updated_at=now))
        else:
            row.value_json = validated
            row.updated_at = now
        self.session.commit()
        return deepcopy(validated)

    def get_profile(self, profile_id: str | None = None) -> dict[str, Any]:
        settings = self.get_settings()
        selected = profile_id or settings["active_profile_id"]
        profile = next((item for item in settings["profiles"] if item["profile_id"] == selected), None)
        if profile is None or not profile.get("enabled"):
            raise AppError("RENDER_PROFILE_INVALID", "The selected render profile does not exist or is disabled.", 409, retryable=False, details={"profile_id": selected})
        return deepcopy(profile)

    @staticmethod
    def _run_identity(executable: str, args: list[str], *, timeout: int = 5) -> tuple[str, str]:
        resolved = shutil.which(executable) if not Path(executable).is_absolute() else executable
        if not resolved or not Path(resolved).is_file():
            raise FileNotFoundError(executable)
        completed = subprocess.run([resolved, *args], check=True, capture_output=True, text=True, timeout=timeout, shell=False, env={"PATH": os.environ.get("PATH", "")})
        first = (completed.stdout or completed.stderr).splitlines()[0].strip()
        identity = f"{Path(resolved).resolve()}::{first}"
        return identity, first

    def readiness(self, profile_id: str | None = None, *, required_input_codecs: set[str] | None = None) -> dict[str, Any]:
        issues: list[dict[str, str]] = []
        try:
            profile = self.get_profile(profile_id)
        except AppError as exc:
            return {"status": "not_ready", "profile_id": profile_id, "ffmpeg_version": None, "ffprobe_version": None, "capabilities_sha256": None, "issues": [{"error_code": exc.error_code, "message": exc.message}]}
        if self.runtime.render_provider_mode.strip().lower() != "ffmpeg_local":
            return {"status": "disabled", "profile_id": profile["profile_id"], "ffmpeg_version": None, "ffprobe_version": None, "capabilities_sha256": None, "issues": [{"error_code": "RENDER_PROVIDER_DISABLED", "message": "Local FFmpeg rendering is disabled."}]}
        try:
            ffmpeg_identity, ffmpeg_version = self._run_identity(self.runtime.ffmpeg_path, ["-version"])
        except Exception:
            issues.append({"error_code": "RENDER_FFMPEG_NOT_FOUND", "message": "Configured FFmpeg executable is unavailable."})
            ffmpeg_identity = ffmpeg_version = ""
        try:
            ffprobe_identity, ffprobe_version = self._run_identity(self.runtime.ffprobe_path, ["-version"])
        except Exception:
            issues.append({"error_code": "RENDER_FFPROBE_NOT_FOUND", "message": "Configured FFprobe executable is unavailable."})
            ffprobe_identity = ffprobe_version = ""
        capabilities: dict[str, Any] = {"ffmpeg_identity": ffmpeg_identity, "ffprobe_identity": ffprobe_identity, "encoders": [], "muxers": [], "filters": [], "decoders": []}
        if ffmpeg_identity:
            resolved = ffmpeg_identity.split("::", 1)[0]
            env = {"PATH": os.environ.get("PATH", "")}
            try:
                encoders = subprocess.run([resolved, "-hide_banner", "-encoders"], check=True, capture_output=True, text=True, timeout=10, shell=False, env=env).stdout
                muxers = subprocess.run([resolved, "-hide_banner", "-muxers"], check=True, capture_output=True, text=True, timeout=10, shell=False, env=env).stdout
                filters = subprocess.run([resolved, "-hide_banner", "-filters"], check=True, capture_output=True, text=True, timeout=10, shell=False, env=env).stdout
                decoders = subprocess.run([resolved, "-hide_banner", "-decoders"], check=True, capture_output=True, text=True, timeout=10, shell=False, env=env).stdout
                capabilities.update({"encoders": encoders.splitlines(), "muxers": muxers.splitlines(), "filters": filters.splitlines(), "decoders": decoders.splitlines()})
                required_encoders = {"libx264", "aac"} | ({"mov_text"} if profile["subtitle_mode"] == "embedded_mov_text" else set())
                for name in sorted(required_encoders):
                    if name not in encoders:
                        issues.append({"error_code": "RENDER_ENCODER_UNAVAILABLE", "message": f"Required encoder '{name}' is unavailable.", "capability": name})
                if " mp4 " not in f" {muxers} ":
                    issues.append({"error_code": "RENDER_MUXER_UNAVAILABLE", "message": "Required MP4 muxer is unavailable.", "capability": "mp4"})
                for name in sorted(REQUIRED_FILTERS):
                    if not any(line.split() and name in line.split()[:3] for line in filters.splitlines()):
                        issues.append({"error_code": "RENDER_FILTER_UNAVAILABLE", "message": f"Required filter '{name}' is unavailable.", "capability": name})
                for codec in sorted(required_input_codecs or set()):
                    if codec not in decoders and codec not in ffmpeg_version.lower():
                        issues.append({"error_code": "RENDER_INPUT_DECODER_UNAVAILABLE", "message": f"Required input decoder '{codec}' is unavailable.", "capability": codec})
            except Exception:
                issues.append({"error_code": "RENDER_CAPABILITY_DISCOVERY_FAILED", "message": "FFmpeg capability discovery failed."})
        cap_hash = canonical_hash(capabilities) if ffmpeg_identity and ffprobe_identity else None
        return {"status": "ready" if not issues else "not_ready", "profile_id": profile["profile_id"], "ffmpeg_version": ffmpeg_version or None, "ffprobe_version": ffprobe_version or None, "capabilities_sha256": cap_hash, "issues": issues}

    def identity_snapshot_owned(
        self,
        profile_id: str | None = None,
        *,
        required_input_codecs: set[str] | None = None,
        operation_deadline_monotonic: float,
        cancellation_requested: Callable[[], bool],
        cancel_grace_seconds: int,
        started_callback_factory: Callable[[str], Callable[[int], None] | None] | None = None,
        finished_callback: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Discover runtime identity under the operation-owned lifecycle.

        Global HTTP readiness may use short bounded synchronous probes, but a
        worker operation must share its durable cancellation callback and one
        overall deadline across every FFmpeg/FFprobe subprocess.
        """
        from app.modules.v4.render_ffmpeg import run_owned_process

        profile = self.get_profile(profile_id)
        if self.runtime.render_provider_mode.strip().lower() != "ffmpeg_local":
            raise AppError("RENDER_NOT_READY", "Local FFmpeg renderer is not ready.", 409, retryable=False, details={"issues": [{"error_code": "RENDER_PROVIDER_DISABLED", "message": "Local FFmpeg rendering is disabled."}]})

        def resolve(executable: str, *, code: str) -> str:
            resolved = shutil.which(executable) if not Path(executable).is_absolute() else executable
            if not resolved or not Path(resolved).is_file():
                raise AppError(code, "Configured local media executable is unavailable.", 409, retryable=False)
            return str(Path(resolved).resolve())

        ffmpeg = resolve(self.runtime.ffmpeg_path, code="RENDER_FFMPEG_NOT_FOUND")
        ffprobe = resolve(self.runtime.ffprobe_path, code="RENDER_FFPROBE_NOT_FOUND")

        def capture(executable: str, args: list[str], process_kind: str) -> tuple[str, str]:
            result = run_owned_process(
                [executable, *args],
                operation_deadline_monotonic=operation_deadline_monotonic,
                cancellation_requested=cancellation_requested,
                process_kind=process_kind,
                cancel_grace_seconds=cancel_grace_seconds,
                started_callback=started_callback_factory(process_kind) if started_callback_factory else None,
                finished_callback=finished_callback,
            )
            if result["return_code"] != 0:
                raise AppError("RENDER_CAPABILITY_DISCOVERY_FAILED", "FFmpeg capability discovery failed.", 409, retryable=True, details={"process_kind": process_kind})
            output = result["stdout"] or result["stderr"]
            first = output.splitlines()[0].strip() if output.splitlines() else ""
            return output, first

        ffmpeg_version_output, ffmpeg_version = capture(ffmpeg, ["-version"], "runtime_ffmpeg_version")
        _, ffprobe_version = capture(ffprobe, ["-version"], "runtime_ffprobe_version")
        encoders, _ = capture(ffmpeg, ["-hide_banner", "-encoders"], "runtime_ffmpeg_encoders")
        muxers, _ = capture(ffmpeg, ["-hide_banner", "-muxers"], "runtime_ffmpeg_muxers")
        filters, _ = capture(ffmpeg, ["-hide_banner", "-filters"], "runtime_ffmpeg_filters")
        decoders, _ = capture(ffmpeg, ["-hide_banner", "-decoders"], "runtime_ffmpeg_decoders")

        issues: list[dict[str, str]] = []
        required_encoders = {"libx264", "aac"} | ({"mov_text"} if profile["subtitle_mode"] == "embedded_mov_text" else set())
        for name in sorted(required_encoders):
            if name not in encoders:
                issues.append({"error_code": "RENDER_ENCODER_UNAVAILABLE", "message": f"Required encoder '{name}' is unavailable.", "capability": name})
        if " mp4 " not in f" {muxers} ":
            issues.append({"error_code": "RENDER_MUXER_UNAVAILABLE", "message": "Required MP4 muxer is unavailable.", "capability": "mp4"})
        for name in sorted(REQUIRED_FILTERS):
            if not any(line.split() and name in line.split()[:3] for line in filters.splitlines()):
                issues.append({"error_code": "RENDER_FILTER_UNAVAILABLE", "message": f"Required filter '{name}' is unavailable.", "capability": name})
        for codec in sorted(required_input_codecs or set()):
            if codec not in decoders and codec not in ffmpeg_version.lower():
                issues.append({"error_code": "RENDER_INPUT_DECODER_UNAVAILABLE", "message": f"Required input decoder '{codec}' is unavailable.", "capability": codec})
        if issues:
            raise AppError("RENDER_NOT_READY", "Local FFmpeg renderer is not ready.", 409, retryable=False, details={"issues": issues})

        capabilities = {
            "ffmpeg_identity": f"{ffmpeg}::{ffmpeg_version}",
            "ffprobe_identity": f"{ffprobe}::{ffprobe_version}",
            "encoders": encoders.splitlines(),
            "muxers": muxers.splitlines(),
            "filters": filters.splitlines(),
            "decoders": decoders.splitlines(),
        }
        return {
            "ffmpeg_path_identity": capabilities["ffmpeg_identity"],
            "ffmpeg_version": ffmpeg_version,
            "ffmpeg_capabilities_sha256": canonical_hash(capabilities),
            "ffprobe_path_identity": capabilities["ffprobe_identity"],
            "ffprobe_version": ffprobe_version,
        }

    def identity_snapshot(self, profile_id: str | None = None, *, required_input_codecs: set[str] | None = None) -> dict[str, Any]:
        readiness = self.readiness(profile_id, required_input_codecs=required_input_codecs)
        if readiness["status"] != "ready":
            raise AppError("RENDER_NOT_READY", "Local FFmpeg renderer is not ready.", 409, retryable=False, details={"issues": readiness["issues"]})
        ffmpeg_identity, ffmpeg_version = self._run_identity(self.runtime.ffmpeg_path, ["-version"])
        ffprobe_identity, ffprobe_version = self._run_identity(self.runtime.ffprobe_path, ["-version"])
        return {
            "ffmpeg_path_identity": ffmpeg_identity,
            "ffmpeg_version": ffmpeg_version,
            "ffmpeg_capabilities_sha256": readiness["capabilities_sha256"],
            "ffprobe_path_identity": ffprobe_identity,
            "ffprobe_version": ffprobe_version,
        }
