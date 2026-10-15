from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from threading import Lock
import time
from typing import Any

from app.api.errors import AppError
from app.modules.v4.host_owned_process import HostOwnedProcess
from app.modules.v4.voice_audio import deterministic_fake_wav, validate_canonical_wav_file
from kokoro.runtime_response_calibration import (
    KOKORO_FLOW_VERSION,
    validate_runtime_response_calibration,
)
from app.modules.v4.voice_provider_contracts import (
    VoiceExecutionControl,
    VoiceProviderCancelResult,
    VoiceProviderCapabilities,
    VoiceProviderExecutionResult,
    VoiceProviderHandle,
    VoiceProviderPacingDirective,
    VoiceProviderPacingObservation,
    VoiceProviderPacingRecommendation,
    VoiceProviderPacingRequest,
    VoiceProviderPollResult,
    VoiceProviderProfile,
    VoiceProviderReadiness,
    VoiceProviderReconcileResult,
    VoiceProviderSubmission,
    VoiceProviderVoice,
    VoiceSynthesisRequest,
)


logger = logging.getLogger(__name__)


class KokoroProviderAdapter:
    provider_key = "kokoro_local"
    _READY_MARKER_FILENAME = ".yas3_kokoro_ready.json"
    _REQUIREMENTS_FILENAME = "requirements.txt"
    _RUNTIME_RESPONSE_SOURCE_FILENAME = "runtime_response_calibration.py"
    _MODEL_FILENAME = "kokoro-v1.0.onnx"
    _VOICES_FILENAME = "voices-v1.0.bin"
    _EXPECTED_MODEL_SHA256 = "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"
    _EXPECTED_VOICES_SHA256 = "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"
    _WRAPPER_CONTRACT = "kokoro_command_v3"
    _INCOMPLETE_SETUP_CODE = "TTS_KOKORO_SETUP_INCOMPLETE"
    _INCOMPLETE_SETUP_MESSAGE = "Local Kokoro setup is incomplete. Run kokoro\\setup_kokoro.bat."
    _RATE_NORMALIZATION_WPM = 260.0
    _PACING_QUALITY_FENCE_RELATIVE = 0.05
    _PACING_RATE_EPSILON = 0.000_001
    _catalog_cache_lock = Lock()
    _catalog_cache_identity: tuple[Any, ...] | None = None
    _catalog_cache_voices: tuple[VoiceProviderVoice, ...] = ()
    _ASSET_HASH_CHUNK_BYTES = 1024 * 1024
    _asset_fingerprint_lock = Lock()
    _asset_fingerprint_cache: dict[tuple[str, int, int, int, int], str] = {}

    def __init__(self, settings=None) -> None:
        from app.core.config import get_settings

        self.settings = settings or get_settings()
        self._active_process_lock = Lock()
        self._active_processes: dict[tuple[str, str], HostOwnedProcess] = {}
        self._interrupt_reasons: dict[tuple[str, str], str] = {}

    def capabilities(self) -> VoiceProviderCapabilities:
        return VoiceProviderCapabilities(
            execution_model="blocking",
            supports_idempotency=False,
            supports_submission_lookup=False,
            supports_cancel=True,
            supports_execution_interrupt=True,
            supports_word_timing=False,
            supports_character_timing=False,
            supports_speaking_rate=True,
            pacing_control="relative_multiplier",
            pacing_value_range=(0.5, 2.0),
            supports_language_selection=True,
            supported_output_formats=("wav_pcm_s16le",),
            supported_sample_rates=(48_000,),
        )

    def pacing_directive(self, request: VoiceProviderPacingRequest) -> VoiceProviderPacingDirective:
        capabilities = self.capabilities()
        native_wpm_at_rate_1 = self._RATE_NORMALIZATION_WPM / float(request.base_native_speaking_rate)
        beta = float(request.speed_response_exponent)
        value = float(request.base_native_speaking_rate) * (
            float(request.target_narration_wpm) / self._RATE_NORMALIZATION_WPM
        )
        force_bound = False
        if (
            request.actual_word_count is not None
            and request.target_duration_seconds is not None
            and math.isfinite(native_wpm_at_rate_1)
            and native_wpm_at_rate_1 > 0
        ):
            target_speech_duration = float(request.target_duration_seconds) - float(request.fixed_duration_seconds)
            speech_duration_at_rate_1 = 60.0 * int(request.actual_word_count) / native_wpm_at_rate_1
            if (
                math.isfinite(target_speech_duration)
                and target_speech_duration > self._PACING_RATE_EPSILON
                and math.isfinite(beta)
                and beta > 0
            ):
                value = (speech_duration_at_rate_1 / target_speech_duration) ** (1.0 / beta)
            else:
                _, maximum = capabilities.pacing_value_range or (None, None)
                if maximum is not None:
                    value = float(maximum)
                    force_bound = True
        bounded, clamped = _clamp_pacing_value(value, capabilities.pacing_value_range)
        return VoiceProviderPacingDirective(
            control_model="relative_multiplier",
            value=round(bounded, 6),
            target_narration_wpm=request.target_narration_wpm,
            policy_version=KOKORO_FLOW_VERSION,
            clamped=clamped or force_bound,
            value_range=capabilities.pacing_value_range,
            speed_response_exponent=beta,
            response_model_source=request.response_model_source,
            response_model_identity=request.response_model_identity,
        )

    def recommend_pacing(self, observation: VoiceProviderPacingObservation) -> VoiceProviderPacingRecommendation:
        capabilities = self.capabilities()
        if observation.current_control_model != "relative_multiplier" or observation.current_provider_pacing_value is None:
            return VoiceProviderPacingRecommendation(
                status="unsupported",
                control_model=capabilities.pacing_control,
                recommended_value=None,
                policy_version=KOKORO_FLOW_VERSION,
                value_range=capabilities.pacing_value_range,
                reason="Kokoro feedback requires a relative-multiplier observation.",
                speed_response_exponent_used=1.0,
                response_model_source=observation.response_model_source,
                response_model_identity=observation.response_model_identity,
            )
        raw = float(observation.measured_raw_wpm)
        current = float(observation.current_provider_pacing_value)
        if not math.isfinite(raw) or raw <= 0 or not math.isfinite(current) or current <= 0:
            return VoiceProviderPacingRecommendation(
                status="unsupported",
                control_model=capabilities.pacing_control,
                recommended_value=None,
                policy_version=KOKORO_FLOW_VERSION,
                value_range=capabilities.pacing_value_range,
                reason="Kokoro feedback observation is not finite.",
                speed_response_exponent_used=1.0,
                response_model_source=observation.response_model_source,
                response_model_identity=observation.response_model_identity,
            )
        duration_inputs_ready = (
            observation.actual_word_count is not None
            and observation.measured_raw_duration_seconds is not None
            and observation.target_duration_seconds is not None
        )
        if not duration_inputs_ready:
            return VoiceProviderPacingRecommendation(
                status="unsupported",
                control_model=capabilities.pacing_control,
                recommended_value=None,
                policy_version=KOKORO_FLOW_VERSION,
                value_range=capabilities.pacing_value_range,
                reason="kokoro_duration_model_invalid",
                speed_response_exponent_used=1.0,
                response_model_source=observation.response_model_source,
                response_model_identity=observation.response_model_identity,
            )
        words = int(observation.actual_word_count or 0)
        measured_duration = float(observation.measured_raw_duration_seconds or 0.0)
        target_duration = float(observation.target_duration_seconds or 0.0)
        fixed_duration = float(observation.fixed_duration_seconds)
        speech_current = measured_duration - fixed_duration
        speech_target = target_duration - fixed_duration
        if (
            words <= 0
            or not math.isfinite(measured_duration)
            or not math.isfinite(target_duration)
            or not math.isfinite(fixed_duration)
            or measured_duration <= 0
            or target_duration <= 0
            or fixed_duration < 0
            or speech_current <= self._PACING_RATE_EPSILON
        ):
            return VoiceProviderPacingRecommendation(
                status="unsupported",
                control_model=capabilities.pacing_control,
                recommended_value=None,
                policy_version=KOKORO_FLOW_VERSION,
                value_range=capabilities.pacing_value_range,
                reason="kokoro_duration_model_invalid",
                speed_response_exponent_used=1.0,
                response_model_source=observation.response_model_source,
                response_model_identity=observation.response_model_identity,
            )
        minimum, maximum = capabilities.pacing_value_range or (None, None)
        if minimum is None or maximum is None:
            return VoiceProviderPacingRecommendation(
                status="unsupported",
                control_model=capabilities.pacing_control,
                recommended_value=None,
                policy_version=KOKORO_FLOW_VERSION,
                value_range=capabilities.pacing_value_range,
                reason="kokoro_provider_bounds_missing",
                speed_response_exponent_used=1.0,
                response_model_source=observation.response_model_source,
                response_model_identity=observation.response_model_identity,
            )
        min_rate, max_rate = float(minimum), float(maximum)
        speech_material_at_rate_1 = current * speech_current
        if (
            not math.isfinite(min_rate)
            or not math.isfinite(max_rate)
            or min_rate <= 0
            or max_rate <= 0
            or min_rate > max_rate
            or not math.isfinite(speech_material_at_rate_1)
            or speech_material_at_rate_1 <= self._PACING_RATE_EPSILON
        ):
            return VoiceProviderPacingRecommendation(
                status="unsupported",
                control_model=capabilities.pacing_control,
                recommended_value=None,
                policy_version=KOKORO_FLOW_VERSION,
                value_range=capabilities.pacing_value_range,
                reason="kokoro_duration_model_invalid",
                speed_response_exponent_used=1.0,
                response_model_source=observation.response_model_source,
                response_model_identity=observation.response_model_identity,
            )

        def predicted_wpm(rate: float) -> float:
            return 60.0 * words / ((speech_material_at_rate_1 / rate) + fixed_duration)

        wpm_low = float(observation.target_narration_wpm) * (1.0 - self._PACING_QUALITY_FENCE_RELATIVE)
        wpm_high = float(observation.target_narration_wpm) * (1.0 + self._PACING_QUALITY_FENCE_RELATIVE)
        reachable_low = min(predicted_wpm(min_rate), predicted_wpm(max_rate))
        reachable_high = max(predicted_wpm(min_rate), predicted_wpm(max_rate))
        if reachable_high < wpm_low or reachable_low > wpm_high:
            return VoiceProviderPacingRecommendation(
                status="unsupported",
                control_model=capabilities.pacing_control,
                recommended_value=None,
                policy_version=KOKORO_FLOW_VERSION,
                value_range=capabilities.pacing_value_range,
                reason="provider_pacing_limit_reached",
                speed_response_exponent_used=1.0,
                response_model_source=observation.response_model_source,
                response_model_identity=observation.response_model_identity,
            )
        if speech_target > self._PACING_RATE_EPSILON:
            value = speech_material_at_rate_1 / speech_target
        else:
            accept_wpm = wpm_low if raw < float(observation.target_narration_wpm) else wpm_high
            accept_duration = 60.0 * words / accept_wpm
            accept_speech_duration = accept_duration - fixed_duration
            if not math.isfinite(accept_speech_duration) or accept_speech_duration <= self._PACING_RATE_EPSILON:
                return VoiceProviderPacingRecommendation(
                    status="unsupported",
                    control_model=capabilities.pacing_control,
                    recommended_value=None,
                    policy_version=KOKORO_FLOW_VERSION,
                    value_range=capabilities.pacing_value_range,
                    reason="provider_pacing_limit_reached",
                    speed_response_exponent_used=1.0,
                    response_model_source=observation.response_model_source,
                    response_model_identity=observation.response_model_identity,
                )
            value = speech_material_at_rate_1 / accept_speech_duration
        bounded, clamped = _clamp_pacing_value(value, capabilities.pacing_value_range)
        rounded = round(bounded, 6)
        currently_outside_quality = abs(raw / float(observation.target_narration_wpm) - 1.0) > self._PACING_QUALITY_FENCE_RELATIVE + 1e-12
        if currently_outside_quality and abs(rounded - current) <= self._PACING_RATE_EPSILON:
            return VoiceProviderPacingRecommendation(
                status="unsupported",
                control_model=capabilities.pacing_control,
                recommended_value=None,
                policy_version=KOKORO_FLOW_VERSION,
                value_range=capabilities.pacing_value_range,
                reason="provider_pacing_no_progress",
                speed_response_exponent_used=1.0,
                response_model_source=observation.response_model_source,
                response_model_identity=observation.response_model_identity,
            )
        return VoiceProviderPacingRecommendation(
            status="supported",
            control_model="relative_multiplier",
            recommended_value=rounded,
            policy_version=KOKORO_FLOW_VERSION,
            clamped=clamped,
            value_range=capabilities.pacing_value_range,
            speed_response_exponent_used=1.0,
            response_model_source=observation.response_model_source,
            response_model_identity=observation.response_model_identity,
        )

    def list_voices(self, profile: VoiceProviderProfile | None = None) -> tuple[VoiceProviderVoice, ...]:
        _ = profile
        root, parsed, _resolved_executable, _entry_path = self._ready_runtime_command()
        identity = self._runtime_identity(root, parsed)
        with self._catalog_cache_lock:
            if identity == self._catalog_cache_identity:
                return self._catalog_cache_voices
            voices = self._discover_voice_catalog(root, parsed)
            self.__class__._catalog_cache_identity = identity
            self.__class__._catalog_cache_voices = voices
            return voices

    def runtime_response_calibration(self, profile: VoiceProviderProfile | None = None) -> dict[str, Any]:
        _ = profile
        root, _parsed, _resolved_executable, _entry_path = self._ready_runtime_command()
        calibration = self._validated_runtime_response_calibration(self._load_ready_marker(root))
        if calibration is None:
            raise AppError(self._INCOMPLETE_SETUP_CODE, self._INCOMPLETE_SETUP_MESSAGE, 503, retryable=False)
        return calibration

    @staticmethod
    def _runtime_subprocess_env() -> dict[str, str]:
        env = os.environ.copy()
        for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE"):
            env.pop(key, None)
        env["PYTHONNOUSERSITE"] = "1"
        return env

    def _discover_voice_catalog(self, root: Path, parsed_command: list[str]) -> tuple[VoiceProviderVoice, ...]:
        try:
            completed = subprocess.run(
                [*self._resolve_command_for_spawn(parsed_command, root), "--list-voices", "--format", "json"],
                cwd=root,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=min(max(1, self.settings.tts_provider_timeout_seconds), 30),
                env=self._runtime_subprocess_env(),
                check=False,
            )
        except (FileNotFoundError, OSError) as exc:
            raise AppError("TTS_PROVIDER_UNAVAILABLE", "The configured Kokoro executable is unavailable.", 503, retryable=True) from exc
        except subprocess.TimeoutExpired as exc:
            raise AppError("TTS_KOKORO_VOICES_UNAVAILABLE", "Kokoro voice catalog discovery timed out.", 504, retryable=True) from exc
        if completed.returncode != 0:
            raise AppError(
                "TTS_KOKORO_VOICES_UNAVAILABLE",
                "Kokoro voice catalog discovery failed.",
                503,
                retryable=True,
                details=self._provider_failure_diagnostics(completed.stderr),
            )
        try:
            payload = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise AppError("TTS_KOKORO_VOICE_CATALOG_INVALID", "Kokoro voice catalog discovery returned invalid JSON.", 502, retryable=True) from exc
        return self._parse_voice_catalog(payload)

    def readiness(self, profile: VoiceProviderProfile) -> VoiceProviderReadiness:
        command_text = self.settings.kokoro_command.strip()
        if not command_text:
            return VoiceProviderReadiness(status="blocked", issue_code="TTS_KOKORO_COMMAND_NOT_CONFIGURED", message="Kokoro command is not configured.")
        try:
            command = self._parse_command(command_text)
        except ValueError:
            return VoiceProviderReadiness(status="blocked", issue_code="TTS_KOKORO_COMMAND_INVALID", message="Kokoro command could not be parsed.")
        if not command:
            return VoiceProviderReadiness(status="blocked", issue_code="TTS_KOKORO_COMMAND_NOT_CONFIGURED", message="Kokoro command is not configured.")
        root = self.settings.resolve_configured_path(self.settings.kokoro_base_path)
        if not root.is_dir():
            return VoiceProviderReadiness(status="blocked", issue_code="TTS_KOKORO_BASE_PATH_INVALID", message="Kokoro base directory is unavailable.")
        if profile.requested_output_format not in self.capabilities().supported_output_formats:
            return VoiceProviderReadiness(status="blocked", issue_code="VOICE_PROVIDER_OUTPUT_FORMAT_UNSUPPORTED", message="Kokoro does not support the requested Voice output format.")
        if profile.requested_sample_rate_hz not in self.capabilities().supported_sample_rates:
            return VoiceProviderReadiness(status="blocked", issue_code="VOICE_PROVIDER_SAMPLE_RATE_UNSUPPORTED", message="Kokoro does not support the requested Voice sample rate.")
        runtime_issue = self._runtime_setup_readiness(root)
        if runtime_issue is not None:
            return runtime_issue
        try:
            self._validate_configured_runtime_command(root, command)
            voices = self.list_voices(profile)
        except AppError as exc:
            return VoiceProviderReadiness(status="blocked", issue_code=exc.error_code, message=exc.message, details=dict(exc.details or {}))
        if not voices:
            return VoiceProviderReadiness(status="blocked", issue_code="TTS_KOKORO_VOICE_CATALOG_EMPTY", message="Kokoro did not report any available voices.")
        if profile.voice_id not in {voice.voice_id for voice in voices}:
            return VoiceProviderReadiness(status="blocked", issue_code="TTS_VOICE_INVALID", message="The selected Kokoro voice is not available in the runtime catalog.")
        return VoiceProviderReadiness(status="ready")

    def execution_identity(self, profile: VoiceProviderProfile) -> dict[str, Any]:
        _ = profile
        root, parsed, resolved_executable, entry_path = self._ready_runtime_command()
        return self._execution_identity_snapshot(root, parsed, resolved_executable, entry_path)

    def _ready_runtime_command(self) -> tuple[Path, list[str], Path, Path]:
        command_text = self.settings.kokoro_command.strip()
        if not command_text:
            raise AppError("TTS_KOKORO_COMMAND_NOT_CONFIGURED", "Kokoro command is not configured.", 409, retryable=False)
        try:
            parsed = self._parse_command(command_text)
        except ValueError as exc:
            raise AppError("TTS_KOKORO_COMMAND_INVALID", "Kokoro command could not be parsed.", 409, retryable=False) from exc
        if not parsed:
            raise AppError("TTS_KOKORO_COMMAND_NOT_CONFIGURED", "Kokoro command is not configured.", 409, retryable=False)
        root = self.settings.resolve_configured_path(self.settings.kokoro_base_path)
        if not root.is_dir():
            raise AppError("TTS_KOKORO_BASE_PATH_INVALID", "Kokoro base directory is unavailable.", 503, retryable=True)
        runtime_issue = self._runtime_setup_readiness(root)
        if runtime_issue is not None:
            raise AppError(runtime_issue.issue_code or self._INCOMPLETE_SETUP_CODE, runtime_issue.message or self._INCOMPLETE_SETUP_MESSAGE, 503, retryable=True)
        resolved_executable, entry_path = self._validate_configured_runtime_command(root, parsed)
        return root, parsed, resolved_executable, entry_path

    @staticmethod
    def _parse_command(command_text: str) -> list[str]:
        command = shlex.split(command_text, posix=os.name != "nt")
        if os.name == "nt":
            return [part.strip().strip('"').strip("'") for part in command if part.strip()]
        return command

    @classmethod
    def _resolve_executable(cls, executable: str, root: Path) -> Path | None:
        if cls._executable_uses_path(executable):
            candidate = Path(executable)
            candidate = candidate if candidate.is_absolute() else root / candidate
            return candidate if cls._is_executable_file(candidate) else None
        resolved = shutil.which(executable)
        if resolved is not None:
            return Path(resolved)
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            if entry:
                candidate = Path(entry) / executable
                if cls._is_executable_file(candidate):
                    return candidate
        return None

    @staticmethod
    def _executable_uses_path(executable: str) -> bool:
        return Path(executable).is_absolute() or "/" in executable or "\\" in executable

    @classmethod
    def _resolve_command_for_spawn(cls, command: list[str], root: Path) -> list[str]:
        if not command or not cls._executable_uses_path(command[0]):
            return command
        resolved = cls._resolve_executable(command[0], root)
        if resolved is None:
            raise FileNotFoundError(command[0])
        return [str(resolved), *command[1:]]

    @staticmethod
    def _is_executable_file(path: Path) -> bool:
        return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))

    @classmethod
    def _runtime_setup_readiness(cls, root: Path) -> VoiceProviderReadiness | None:
        expected_python = cls._expected_venv_python(root)
        if not expected_python.is_file():
            return cls._incomplete_setup_readiness()
        requirements_path = root / cls._REQUIREMENTS_FILENAME
        wrapper_path = root / "kokoro_cli.py"
        response_source_path = root / cls._RUNTIME_RESPONSE_SOURCE_FILENAME
        model_path = root / cls._MODEL_FILENAME
        voices_path = root / cls._VOICES_FILENAME
        marker_path = root / cls._READY_MARKER_FILENAME
        if (
            not requirements_path.is_file()
            or not wrapper_path.is_file()
            or not response_source_path.is_file()
            or not model_path.is_file()
            or not voices_path.is_file()
            or not marker_path.is_file()
        ):
            return cls._incomplete_setup_readiness()
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return cls._incomplete_setup_readiness()
        if not isinstance(marker, dict):
            return cls._incomplete_setup_readiness()
        expected = {
            "flow_version": KOKORO_FLOW_VERSION,
            "requirements_sha256": cls._file_sha256(requirements_path),
            "wrapper_source_sha256": cls._file_sha256(wrapper_path),
            "runtime_response_calibration_source_sha256": cls._file_sha256(response_source_path),
            "model_sha256": cls._EXPECTED_MODEL_SHA256,
            "voices_sha256": cls._EXPECTED_VOICES_SHA256,
            "wrapper_contract": cls._WRAPPER_CONTRACT,
            "canonical_sample_rate_hz": 48_000,
            "output_format": "wav_pcm_s16le",
        }
        if any(marker.get(key) != value for key, value in expected.items()):
            return cls._incomplete_setup_readiness()
        try:
            model_identity = cls._runtime_asset_identity(model_path)
            voices_identity = cls._runtime_asset_identity(voices_path)
            python_executable_sha256 = cls._file_sha256(expected_python)
            runtime_packages = cls._installed_runtime_packages(root)
        except (AppError, OSError):
            return cls._incomplete_setup_readiness()
        if (
            model_identity["sha256"] != cls._EXPECTED_MODEL_SHA256
            or voices_identity["sha256"] != cls._EXPECTED_VOICES_SHA256
            or marker.get("python_executable_sha256") != python_executable_sha256
            or marker.get("runtime_packages") != runtime_packages
        ):
            return cls._incomplete_setup_readiness()
        python_version = marker.get("python_version")
        if not isinstance(python_version, str) or not (python_version == "3.12" or python_version.startswith("3.12.")):
            return cls._incomplete_setup_readiness()
        if cls._validated_runtime_response_calibration(marker) is None:
            return cls._incomplete_setup_readiness()
        return None

    @classmethod
    def _incomplete_setup_readiness(cls) -> VoiceProviderReadiness:
        return VoiceProviderReadiness(status="blocked", issue_code=cls._INCOMPLETE_SETUP_CODE, message=cls._INCOMPLETE_SETUP_MESSAGE)

    @staticmethod
    def _expected_venv_python(root: Path) -> Path:
        if os.name == "nt":
            return root / ".venv" / "Scripts" / "python.exe"
        return root / ".venv" / "bin" / "python"

    @staticmethod
    def _runtime_site_packages(root: Path) -> Path:
        if os.name == "nt":
            return root / ".venv" / "Lib" / "site-packages"
        return root / ".venv" / "lib" / "python3.12" / "site-packages"

    @classmethod
    def _installed_runtime_packages(cls, root: Path) -> dict[str, str]:
        site_packages = cls._runtime_site_packages(root)
        if not site_packages.is_dir():
            raise AppError(cls._INCOMPLETE_SETUP_CODE, cls._INCOMPLETE_SETUP_MESSAGE, 503, retryable=False)
        found: dict[str, str] = {}
        try:
            for metadata_path in site_packages.glob("*.dist-info/METADATA"):
                name = None
                version = None
                for line in metadata_path.read_text(encoding="utf-8").splitlines():
                    if name is None and line.startswith("Name:"):
                        name = line.partition(":")[2].strip()
                    elif version is None and line.startswith("Version:"):
                        version = line.partition(":")[2].strip()
                    if name is not None and version is not None:
                        break
                if not name:
                    continue
                normalized = re.sub(r"[-_.]+", "-", name.lower())
                if not version or normalized in found:
                    raise AppError(cls._INCOMPLETE_SETUP_CODE, cls._INCOMPLETE_SETUP_MESSAGE, 503, retryable=False)
                found[normalized] = version
        except (OSError, UnicodeError) as exc:
            raise AppError(cls._INCOMPLETE_SETUP_CODE, cls._INCOMPLETE_SETUP_MESSAGE, 503, retryable=False) from exc
        if not found:
            raise AppError(cls._INCOMPLETE_SETUP_CODE, cls._INCOMPLETE_SETUP_MESSAGE, 503, retryable=False)
        return dict(sorted(found.items()))

    @staticmethod
    def _same_runtime_path(left: Path, right: Path) -> bool:
        return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))

    @classmethod
    def _validate_configured_runtime_command(cls, root: Path, command: list[str]) -> tuple[Path, Path]:
        if len(command) != 2 or not cls._executable_uses_path(command[0]):
            raise AppError(cls._INCOMPLETE_SETUP_CODE, cls._INCOMPLETE_SETUP_MESSAGE, 503, retryable=False)
        resolved_executable = cls._resolve_executable(command[0], root)
        entry_path = Path(command[1])
        entry_path = entry_path if entry_path.is_absolute() else root / entry_path
        expected_executable = cls._expected_venv_python(root)
        expected_entry_path = root / "kokoro_cli.py"
        if (
            resolved_executable is None
            or not entry_path.is_file()
            or not expected_executable.is_file()
            or not expected_entry_path.is_file()
            or not cls._same_runtime_path(resolved_executable, expected_executable)
            or not cls._same_runtime_path(entry_path, expected_entry_path)
        ):
            raise AppError(cls._INCOMPLETE_SETUP_CODE, cls._INCOMPLETE_SETUP_MESSAGE, 503, retryable=False)
        return resolved_executable, entry_path

    @staticmethod
    def _file_sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @classmethod
    def _load_ready_marker(cls, root: Path) -> dict[str, Any]:
        try:
            marker = json.loads((root / cls._READY_MARKER_FILENAME).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return marker if isinstance(marker, dict) else {}

    @staticmethod
    def _validated_runtime_response_calibration(marker: dict[str, Any]) -> dict[str, Any] | None:
        return validate_runtime_response_calibration(marker.get("runtime_response_calibration"))

    @staticmethod
    def _path_identity(path: Path) -> tuple[str, int | None, int | None]:
        try:
            stat = path.stat()
        except OSError:
            return (path.name, None, None)
        return (path.name, stat.st_size, stat.st_mtime_ns)

    @classmethod
    def _runtime_identity(cls, root: Path, parsed_command: list[str]) -> tuple[Any, ...]:
        marker = cls._load_ready_marker(root)
        marker_keys = ("flow_version", "python_version", "python_executable_sha256", "requirements_sha256", "wrapper_source_sha256", "runtime_response_calibration_source_sha256", "model_sha256", "voices_sha256", "wrapper_contract", "canonical_sample_rate_hz", "output_format")
        runtime_packages = marker.get("runtime_packages")
        runtime_package_identity = tuple(sorted(runtime_packages.items())) if isinstance(runtime_packages, dict) else None
        calibration = cls._validated_runtime_response_calibration(marker)
        calibration_sha256 = cls._sha256_json(calibration) if calibration is not None else None
        return (
            str(root.resolve()),
            tuple(parsed_command),
            tuple((key, marker.get(key)) for key in marker_keys),
            runtime_package_identity,
            calibration_sha256,
            cls._path_identity(root / cls._READY_MARKER_FILENAME),
            cls._path_identity(root / "kokoro_cli.py"),
            cls._path_identity(root / cls._MODEL_FILENAME),
            cls._path_identity(root / cls._VOICES_FILENAME),
        )

    @staticmethod
    def _sha256_text(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _sha256_json(cls, value: Any) -> str:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return cls._sha256_text(rendered)

    @classmethod
    def _safe_path_digest(cls, path: Path) -> str:
        try:
            rendered = str(path.resolve())
        except OSError:
            rendered = str(path)
        return cls._sha256_text(rendered)

    @classmethod
    def _executable_identity(cls, path: Path) -> dict[str, Any]:
        try:
            stat = path.stat()
        except OSError:
            return {"name": path.name, "path_sha256": cls._safe_path_digest(path), "size_bytes": None, "mtime_ns": None}
        return {"name": path.name, "path_sha256": cls._safe_path_digest(path), "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}

    @classmethod
    def _asset_cache_key(cls, path: Path, stat: os.stat_result) -> tuple[str, int, int, int, int]:
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        return (resolved, int(stat.st_size), int(stat.st_mtime_ns), int(getattr(stat, "st_dev", 0) or 0), int(getattr(stat, "st_ino", 0) or 0))

    @classmethod
    def _stream_sha256(cls, path: Path, *, chunk_size: int = _ASSET_HASH_CHUNK_BYTES) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(chunk_size):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _runtime_asset_identity(cls, path: Path) -> dict[str, Any]:
        try:
            stat = path.stat()
        except OSError as exc:
            raise AppError(cls._INCOMPLETE_SETUP_CODE, cls._INCOMPLETE_SETUP_MESSAGE, 503, retryable=True) from exc
        cache_key = cls._asset_cache_key(path, stat)
        with cls._asset_fingerprint_lock:
            digest = cls._asset_fingerprint_cache.get(cache_key)
            if digest is None:
                try:
                    digest = cls._stream_sha256(path)
                except OSError as exc:
                    raise AppError(cls._INCOMPLETE_SETUP_CODE, cls._INCOMPLETE_SETUP_MESSAGE, 503, retryable=True) from exc
                cls._asset_fingerprint_cache[cache_key] = digest
        return {"name": path.name, "size_bytes": stat.st_size, "sha256": digest}

    @classmethod
    def _execution_identity_snapshot(cls, root: Path, parsed_command: list[str], resolved_executable: Path, entry_script: Path | None) -> dict[str, Any]:
        marker = cls._load_ready_marker(root)
        marker_keys = ("flow_version", "python_version", "python_executable_sha256", "requirements_sha256", "wrapper_source_sha256", "runtime_response_calibration_source_sha256", "model_sha256", "voices_sha256", "wrapper_contract", "canonical_sample_rate_hz", "output_format")
        calibration = cls._validated_runtime_response_calibration(marker)
        if calibration is None:
            raise AppError(cls._INCOMPLETE_SETUP_CODE, cls._INCOMPLETE_SETUP_MESSAGE, 503, retryable=False)
        entry_identity = None
        if entry_script is not None:
            entry_identity = {"name": entry_script.name, "path_sha256": cls._safe_path_digest(entry_script), "sha256": cls._file_sha256(entry_script)}
        return {
            "schema_version": 1,
            "provider_key": cls.provider_key,
            "flow_version": KOKORO_FLOW_VERSION,
            "command_sha256": cls._sha256_json(parsed_command),
            "executable": cls._executable_identity(resolved_executable),
            "entry_script": entry_identity,
            "ready_marker": {
                **{key: marker.get(key) for key in marker_keys},
                "runtime_packages": marker.get("runtime_packages"),
            },
            "runtime_response_calibration_sha256": cls._sha256_json(calibration),
            "runtime_assets": {
                "model": cls._runtime_asset_identity(root / cls._MODEL_FILENAME),
                "voices": cls._runtime_asset_identity(root / cls._VOICES_FILENAME),
            },
            "wrapper_source_sha256": cls._file_sha256(root / "kokoro_cli.py"),
        }


    @staticmethod
    def _execution_key(segment_task_id: str, execution_attempt_token: str) -> tuple[str, str]:
        return (segment_task_id, execution_attempt_token)

    @staticmethod
    def _stop_owned_process(process: HostOwnedProcess) -> None:
        try:
            if process.poll() is None:
                try:
                    process.terminate_tree(exit_code=1)
                except OSError:
                    return
                try:
                    process.wait(timeout=2.0)
                    return
                except subprocess.TimeoutExpired:
                    pass
                try:
                    process.kill_root()
                    process.wait(timeout=2.0)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        finally:
            process.release()

    def interrupt_execution(self, *, segment_task_id: str, execution_attempt_token: str, reason: str) -> bool:
        if reason not in {"cancelled", "timeout"}:
            raise ValueError("Unsupported Voice execution interrupt reason.")
        key = self._execution_key(segment_task_id, execution_attempt_token)
        with self._active_process_lock:
            process = self._active_processes.get(key)
            if process is None:
                return False
            self._interrupt_reasons[key] = reason
        logger.warning("Kokoro subprocess interrupt task_id=%s attempt_token=%s reason=%s pid=%s", segment_task_id, execution_attempt_token, reason, process.pid)
        self._stop_owned_process(process)
        return True

    def _register_process(self, key: tuple[str, str], process: HostOwnedProcess) -> None:
        with self._active_process_lock:
            if key in self._active_processes:
                raise AppError("VOICE_PROVIDER_EXECUTION_DUPLICATE", "Kokoro already owns a process for this Voice execution attempt.", 409, retryable=True)
            self._active_processes[key] = process

    def _interrupt_reason(self, key: tuple[str, str]) -> str | None:
        with self._active_process_lock:
            return self._interrupt_reasons.get(key)

    def _unregister_process(self, key: tuple[str, str], process: HostOwnedProcess | None) -> None:
        with self._active_process_lock:
            if process is not None and self._active_processes.get(key) is process:
                self._active_processes.pop(key, None)
            self._interrupt_reasons.pop(key, None)

    def submit(self, request: VoiceSynthesisRequest, control: VoiceExecutionControl) -> VoiceProviderSubmission:
        if request.voice_id not in {voice.voice_id for voice in self.list_voices()}:
            raise AppError("TTS_VOICE_INVALID", "The selected Kokoro voice is not available in the runtime catalog.", 422, retryable=False)
        output_path = request.attempt_output_path
        root = self.settings.resolve_configured_path(self.settings.kokoro_base_path)
        root.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        input_path = output_path.with_suffix(".txt")
        succeeded = False
        key = self._execution_key(request.segment_task_id, request.execution_attempt_token)
        stderr_capture = tempfile.TemporaryFile(mode="w+b")
        try:
            payloads = request.provider_options.get("_kokoro_segment_payloads")
            payload = payloads.get(request.segment_task_id) if isinstance(payloads, dict) else None
            pieces = payload.get("pieces") if isinstance(payload, dict) else None
            if isinstance(pieces, list) and pieces:
                self._synthesize_piece_plan(
                    request=request,
                    control=control,
                    root=root,
                    output_path=output_path,
                    pieces=pieces,
                    key=key,
                )
            else:
                input_path.write_text(request.exact_text, encoding="utf-8")
                self._run_process(
                    request=request,
                    control=control,
                    root=root,
                    command=self._build_command(request, input_path, output_path),
                    key=key,
                    stderr_capture=stderr_capture,
                )
            info = validate_canonical_wav_file(output_path, maximum_bytes=self.settings.tts_provider_max_output_bytes)
            succeeded = True
            return VoiceProviderSubmission(
                status="completed",
                result=VoiceProviderExecutionResult(
                    operation_id=request.operation_id,
                    segment_task_id=request.segment_task_id,
                    execution_attempt_token=request.execution_attempt_token,
                    provider_key=self.provider_key,
                    provider_task_id=None,
                    audio_file_path=output_path,
                    source_mime_type="audio/wav",
                    sample_rate_hz=info.sample_rate_hz,
                    channels=info.channels,
                    sample_width_bytes=info.sample_width_bytes,
                    duration_seconds=info.duration_seconds,
                    sanitized_metadata={"command_contract": self._WRAPPER_CONTRACT},
                ),
            )
        except FileNotFoundError as exc:
            raise AppError("TTS_PROVIDER_UNAVAILABLE", "The configured Kokoro executable is unavailable.", 503, retryable=True) from exc
        finally:
            stderr_capture.close()
            if input_path.exists():
                input_path.unlink()
            if not succeeded and output_path.exists():
                output_path.unlink()

    def _run_process(
        self,
        *,
        request: VoiceSynthesisRequest,
        control: VoiceExecutionControl,
        root: Path,
        command: list[str],
        key: tuple[str, str],
        stderr_capture,
    ) -> HostOwnedProcess:
        process = self._start_owned_process(
            command,
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=stderr_capture,
            bufsize=0,
            env=self._runtime_subprocess_env(),
        )
        try:
            self._register_process(key, process)
            while process.poll() is None:
                if control.cancellation_requested():
                    self._stop_owned_process(process)
                    raise AppError("VOICE_OPERATION_CANCELLED", "Voice generation was cancelled.", 409, retryable=False)
                if control.deadline_reached():
                    self._stop_owned_process(process)
                    raise AppError("TTS_PROVIDER_TIMEOUT", "Kokoro synthesis timed out.", 504, retryable=True)
                time.sleep(0.1)
            process.wait(timeout=2.0)
            interrupt_reason = self._interrupt_reason(key)
            if interrupt_reason == "cancelled":
                raise AppError("VOICE_OPERATION_CANCELLED", "Voice generation was cancelled.", 409, retryable=False)
            if interrupt_reason == "timeout":
                raise AppError("TTS_PROVIDER_TIMEOUT", "Kokoro synthesis timed out.", 504, retryable=True)
            if process.returncode != 0:
                stderr_capture.flush()
                stderr_capture.seek(0)
                raise AppError("TTS_PROVIDER_FAILED", "Kokoro synthesis failed.", 502, retryable=True, details=self._provider_failure_diagnostics(stderr_capture.read(64 * 1024)))
            return process
        finally:
            pending_error = sys.exc_info()[0] is not None
            try:
                if process.poll() is None:
                    self._stop_owned_process(process)
                else:
                    process.release()
            except Exception:
                if not pending_error:
                    raise
                logger.warning(
                    "Kokoro subprocess cleanup failed while preserving provider error task_id=%s attempt_token=%s pid=%s",
                    request.segment_task_id,
                    request.execution_attempt_token,
                    process.pid,
                    exc_info=True,
                )
            self._unregister_process(key, process)

    def _start_owned_process(
        self,
        command: list[str],
        *,
        cwd: Path,
        stdout: Any,
        stderr: Any,
        bufsize: int,
        env: dict[str, str],
    ) -> HostOwnedProcess:
        return HostOwnedProcess.start(
            command,
            cwd=cwd,
            stdout=stdout,
            stderr=stderr,
            bufsize=bufsize,
            env=env,
        )

    def _synthesize_piece_plan(
        self,
        *,
        request: VoiceSynthesisRequest,
        control: VoiceExecutionControl,
        root: Path,
        output_path: Path,
        pieces: list[Any],
        key: tuple[str, str],
    ) -> None:
        frame_bytes = 2
        frames = bytearray()
        for index, piece in enumerate(pieces):
            if not isinstance(piece, dict):
                raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Kokoro delivery piece is malformed.", 409, retryable=False)
            kind = piece.get("kind")
            if kind == "silence":
                seconds = float(piece.get("duration_seconds") or 0.0)
                if seconds < 0:
                    raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Kokoro delivery pause is invalid.", 409, retryable=False)
                frames.extend(b"\x00" * int(round(48_000 * seconds)) * frame_bytes)
                continue
            if kind != "text":
                raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Kokoro delivery piece kind is invalid.", 409, retryable=False)
            text = str(piece.get("text") or "")
            if not text.strip():
                continue
            input_path = output_path.with_suffix(f".part{index}.txt")
            part_output = output_path.with_suffix(f".part{index}.wav")
            stderr_capture = tempfile.TemporaryFile(mode="w+b")
            try:
                input_path.write_text(text, encoding="utf-8")
                self._run_process(
                    request=request,
                    control=control,
                    root=root,
                    command=self._build_command(request, input_path, part_output),
                    key=key,
                    stderr_capture=stderr_capture,
                )
                info = validate_canonical_wav_file(part_output, maximum_bytes=self.settings.tts_provider_max_output_bytes)
                with wave.open(str(part_output), "rb") as wav:
                    frames.extend(wav.readframes(info.sample_count))
            finally:
                stderr_capture.close()
                input_path.unlink(missing_ok=True)
                part_output.unlink(missing_ok=True)
        if not frames:
            raise AppError("VOICE_SPOKEN_TEXT_INVALID", "Kokoro delivery plan produced no audio.", 409, retryable=False)
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(48_000)
            wav.writeframes(bytes(frames))

    def poll(self, handle: VoiceProviderHandle, control: VoiceExecutionControl) -> VoiceProviderPollResult:
        _ = (handle, control)
        raise AppError("VOICE_PROVIDER_POLL_UNSUPPORTED", "Kokoro is a blocking provider and cannot be polled.", 409, retryable=False)

    def reconcile_submission(self, request: VoiceSynthesisRequest, handle: VoiceProviderHandle | None, control: VoiceExecutionControl) -> VoiceProviderReconcileResult:
        _ = (request, handle, control)
        return VoiceProviderReconcileResult(status="replay_safe")

    def cancel(self, handle: VoiceProviderHandle | None, control: VoiceExecutionControl) -> VoiceProviderCancelResult:
        _ = (handle, control)
        return VoiceProviderCancelResult(status="accepted")

    def _build_command(self, request: VoiceSynthesisRequest, input_path: Path, output_path: Path) -> list[str]:
        if request.pacing is None or request.pacing.control_model != "relative_multiplier" or request.pacing.value is None:
            raise AppError("VOICE_PROVIDER_PACING_INVALID", "Kokoro requires relative-multiplier pacing.", 409, retryable=False)
        root, base, _resolved_executable, _entry_path = self._ready_runtime_command()
        command = self._resolve_command_for_spawn(base, root)
        return [
            *command,
            "--input-file", str(input_path),
            "--output-file", str(output_path),
            "--voice", request.voice_id,
            "--language", request.language,
            "--speaking-rate", str(request.pacing.value),
            "--sample-rate", str(request.requested_sample_rate_hz),
            "--output-format", request.requested_output_format,
        ]

    @staticmethod
    def _provider_failure_diagnostics(stderr: bytes | bytearray | None) -> dict[str, Any]:
        payload = bytes(stderr or b"")
        return {"provider_diagnostics": {"stderr_captured": bool(payload), "stderr_bytes": len(payload)}}

    @staticmethod
    def _parse_voice_catalog(payload: Any) -> tuple[VoiceProviderVoice, ...]:
        if not isinstance(payload, dict) or not isinstance(payload.get("voices"), list):
            raise AppError("TTS_KOKORO_VOICE_CATALOG_INVALID", "Kokoro voice catalog discovery returned an invalid payload.", 502, retryable=True)
        voices: list[VoiceProviderVoice] = []
        seen: set[str] = set()
        for item in payload["voices"]:
            if not isinstance(item, dict):
                raise AppError("TTS_KOKORO_VOICE_CATALOG_INVALID", "Kokoro voice catalog contains an invalid voice record.", 502, retryable=True)
            voice_id = str(item.get("voice_id") or "").strip()
            display_name = str(item.get("display_name") or "").strip()
            language = str(item.get("language") or "").strip()
            if not voice_id or not display_name or not language:
                raise AppError("TTS_KOKORO_VOICE_CATALOG_INVALID", "Kokoro voice catalog contains an incomplete voice record.", 502, retryable=True)
            if voice_id in seen:
                raise AppError("TTS_KOKORO_VOICE_CATALOG_INVALID", "Kokoro voice catalog contains duplicate voice IDs.", 502, retryable=True)
            seen.add(voice_id)
            voices.append(VoiceProviderVoice(voice_id=voice_id, display_name=display_name, language=language))
        return tuple(voices)


def _clamp_pacing_value(value: float, bounds: tuple[float, float] | None) -> tuple[float, bool]:
    if bounds is None:
        return value, False
    minimum, maximum = float(bounds[0]), float(bounds[1])
    clamped = min(maximum, max(minimum, value))
    return clamped, abs(clamped - value) > 0.000_001


class FakeImmediateProvider:
    provider_key = "fake_immediate"

    def capabilities(self) -> VoiceProviderCapabilities:
        return VoiceProviderCapabilities(
            execution_model="synchronous",
            supports_idempotency=True,
        )

    def pacing_directive(self, request: VoiceProviderPacingRequest) -> VoiceProviderPacingDirective:
        value = float(request.base_native_speaking_rate) * (
            float(request.target_narration_wpm) / float(request.planned_average_wpm)
        )
        return VoiceProviderPacingDirective(
            control_model="relative_multiplier",
            value=round(value, 6),
            target_narration_wpm=request.target_narration_wpm,
            policy_version="fake_relative_multiplier_v1",
            value_range=self.capabilities().pacing_value_range,
        )

    def recommend_pacing(self, observation: VoiceProviderPacingObservation) -> VoiceProviderPacingRecommendation:
        capabilities = self.capabilities()
        if observation.current_control_model != "relative_multiplier" or observation.current_provider_pacing_value is None:
            return VoiceProviderPacingRecommendation(
                status="unsupported",
                control_model=capabilities.pacing_control,
                recommended_value=None,
                policy_version="fake_relative_multiplier_feedback_v1",
                value_range=capabilities.pacing_value_range,
            )
        value = float(observation.current_provider_pacing_value) * float(observation.target_narration_wpm) / float(observation.measured_raw_wpm)
        bounded, clamped = _clamp_pacing_value(value, capabilities.pacing_value_range)
        return VoiceProviderPacingRecommendation(
            status="supported",
            control_model="relative_multiplier",
            recommended_value=round(bounded, 6),
            policy_version="fake_relative_multiplier_feedback_v1",
            clamped=clamped,
            value_range=capabilities.pacing_value_range,
        )

    def list_voices(self, profile: VoiceProviderProfile | None = None) -> tuple[VoiceProviderVoice, ...]:
        _ = profile
        return (VoiceProviderVoice(voice_id="fake_voice", display_name="Fake Voice", language="en"),)

    def readiness(self, profile: VoiceProviderProfile) -> VoiceProviderReadiness:
        _ = profile
        return VoiceProviderReadiness(status="ready")

    def execution_identity(self, profile: VoiceProviderProfile) -> dict[str, Any]:
        _ = profile
        return {"schema_version": 1, "provider_key": self.provider_key, "test_runtime": "stable"}

    def submit(self, request: VoiceSynthesisRequest, control: VoiceExecutionControl) -> VoiceProviderSubmission:
        if control.cancellation_requested():
            raise AppError("VOICE_OPERATION_CANCELLED", "Voice generation was cancelled.", 409, retryable=False)
        return VoiceProviderSubmission(status="completed", result=_fake_result(self.provider_key, request))

    def poll(self, handle: VoiceProviderHandle, control: VoiceExecutionControl) -> VoiceProviderPollResult:
        _ = (handle, control)
        raise AppError("VOICE_PROVIDER_POLL_UNSUPPORTED", "Immediate provider cannot be polled.", 409, retryable=False)

    def reconcile_submission(
        self,
        request: VoiceSynthesisRequest,
        handle: VoiceProviderHandle | None,
        control: VoiceExecutionControl,
    ) -> VoiceProviderReconcileResult:
        _ = (request, handle, control)
        return VoiceProviderReconcileResult(status="replay_safe")

    def cancel(self, handle: VoiceProviderHandle | None, control: VoiceExecutionControl) -> VoiceProviderCancelResult:
        _ = (handle, control)
        return VoiceProviderCancelResult(status="unsupported")


class FakeBlockingProvider(FakeImmediateProvider):
    provider_key = "fake_blocking"

    def capabilities(self) -> VoiceProviderCapabilities:
        return VoiceProviderCapabilities(
            execution_model="blocking",
            supports_idempotency=True,
            supports_cancel=True,
        )


class FakeAsyncProvider:
    provider_key = "fake_async"

    def __init__(self) -> None:
        self._requests: dict[str, VoiceSynthesisRequest] = {}

    def capabilities(self) -> VoiceProviderCapabilities:
        return VoiceProviderCapabilities(
            execution_model="asynchronous",
            supports_idempotency=True,
            supports_submission_lookup=True,
            supports_cancel=True,
        )

    def pacing_directive(self, request: VoiceProviderPacingRequest) -> VoiceProviderPacingDirective:
        value = float(request.base_native_speaking_rate) * (
            float(request.target_narration_wpm) / float(request.planned_average_wpm)
        )
        return VoiceProviderPacingDirective(
            control_model="relative_multiplier",
            value=round(value, 6),
            target_narration_wpm=request.target_narration_wpm,
            policy_version="fake_relative_multiplier_v1",
            value_range=self.capabilities().pacing_value_range,
        )

    def recommend_pacing(self, observation: VoiceProviderPacingObservation) -> VoiceProviderPacingRecommendation:
        capabilities = self.capabilities()
        if observation.current_control_model != "relative_multiplier" or observation.current_provider_pacing_value is None:
            return VoiceProviderPacingRecommendation(
                status="unsupported",
                control_model=capabilities.pacing_control,
                recommended_value=None,
                policy_version="fake_relative_multiplier_feedback_v1",
                value_range=capabilities.pacing_value_range,
            )
        value = float(observation.current_provider_pacing_value) * float(observation.target_narration_wpm) / float(observation.measured_raw_wpm)
        bounded, clamped = _clamp_pacing_value(value, capabilities.pacing_value_range)
        return VoiceProviderPacingRecommendation(
            status="supported",
            control_model="relative_multiplier",
            recommended_value=round(bounded, 6),
            policy_version="fake_relative_multiplier_feedback_v1",
            clamped=clamped,
            value_range=capabilities.pacing_value_range,
        )

    def list_voices(self, profile: VoiceProviderProfile | None = None) -> tuple[VoiceProviderVoice, ...]:
        _ = profile
        return (VoiceProviderVoice(voice_id="fake_voice", display_name="Fake Voice", language="en"),)

    def readiness(self, profile: VoiceProviderProfile) -> VoiceProviderReadiness:
        _ = profile
        return VoiceProviderReadiness(status="ready")

    def execution_identity(self, profile: VoiceProviderProfile) -> dict[str, Any]:
        _ = profile
        return {"schema_version": 1, "provider_key": self.provider_key, "test_runtime": "stable"}

    def submit(self, request: VoiceSynthesisRequest, control: VoiceExecutionControl) -> VoiceProviderSubmission:
        _ = control
        task_id = f"fake-async:{request.idempotency_key}"
        self._requests[task_id] = request
        return VoiceProviderSubmission(
            status="pending",
            handle=VoiceProviderHandle(provider_key=self.provider_key, provider_task_id=task_id),
        )

    def poll(self, handle: VoiceProviderHandle, control: VoiceExecutionControl) -> VoiceProviderPollResult:
        _ = control
        request = self._requests.get(handle.provider_task_id)
        if request is None:
            raise AppError("VOICE_PROVIDER_TASK_NOT_FOUND", "Fake async provider task was not found.", 404, retryable=False)
        return VoiceProviderPollResult(status="completed", handle=handle, result=_fake_result(self.provider_key, request, handle.provider_task_id))

    def reconcile_submission(
        self,
        request: VoiceSynthesisRequest,
        handle: VoiceProviderHandle | None,
        control: VoiceExecutionControl,
    ) -> VoiceProviderReconcileResult:
        _ = control
        task_id = handle.provider_task_id if handle is not None else f"fake-async:{request.idempotency_key}"
        if task_id not in self._requests:
            return VoiceProviderReconcileResult(status="replay_safe")
        resolved_handle = handle or VoiceProviderHandle(provider_key=self.provider_key, provider_task_id=task_id)
        return VoiceProviderReconcileResult(status="pending", handle=resolved_handle)

    def cancel(self, handle: VoiceProviderHandle | None, control: VoiceExecutionControl) -> VoiceProviderCancelResult:
        _ = control
        if handle is None:
            return VoiceProviderCancelResult(status="not_found")
        self._requests.pop(handle.provider_task_id, None)
        return VoiceProviderCancelResult(status="cancelled")


def _fake_result(provider_key: str, request: VoiceSynthesisRequest, provider_task_id: str | None = None) -> VoiceProviderExecutionResult:
    output_path = request.attempt_output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rate = request.pacing.value if request.pacing is not None and request.pacing.value is not None else request.speaking_rate
    output_path.write_bytes(deterministic_fake_wav(request.exact_text, speaking_rate=float(rate)))
    info = validate_canonical_wav_file(output_path)
    return VoiceProviderExecutionResult(
        operation_id=request.operation_id,
        segment_task_id=request.segment_task_id,
        execution_attempt_token=request.execution_attempt_token,
        provider_key=provider_key,
        provider_task_id=provider_task_id or f"{provider_key}:{request.idempotency_key}",
        audio_file_path=output_path,
        source_mime_type="audio/wav",
        sample_rate_hz=info.sample_rate_hz,
        channels=info.channels,
        sample_width_bytes=info.sample_width_bytes,
        duration_seconds=info.duration_seconds,
        sanitized_metadata={"deterministic": True},
    )
