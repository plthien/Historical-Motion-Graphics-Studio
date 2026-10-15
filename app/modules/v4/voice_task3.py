from __future__ import annotations

from datetime import timedelta
import hashlib
import json
import math
import re
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import AppError
from app.core.config import get_settings
from app.core.ids import generate_id
from app.core.time import utc_now_iso
from app.db.enums import ArtifactStatus, ArtifactType, JobStatus
from app.db.models import Channel, VoiceAlignmentTask
from app.modules.media.durable_workflow.timing import parse_iso, utc_now
from app.modules.v4.durable_voice_service import DurableVoiceService
from app.modules.v4.elevenlabs_client import ElevenLabsAmbiguousSubmission, ElevenLabsHttpClient
from app.modules.v4.elevenlabs_credentials import build_runtime_elevenlabs_credential_resolver
from app.modules.v4.script_word_budget import SCRIPT_SECTION_SEPARATOR
from app.modules.v4.voice_audio import CANONICAL_MIME, CANONICAL_SAMPLE_RATE, assemble_canonical_wav, validate_canonical_wav_bytes
from app.modules.v4.voice_first_policy import FORCED_ALIGNMENT_POLICY_VERSION, VOICE_ARCHITECTURE_VERSION
from app.modules.v4.voice_service import canonical_hash
from app.modules.v4.voice_spoken_text import compile_voice_spoken_text
from app.services.artifacts import StagedArtifact, cleanup_staged_artifact_safely


NARRATION_WORD_TIMING_MAP_SCHEMA_VERSION = "narration_word_timing_map_v1"
ALIGNMENT_SAMPLE_ROUNDING_POLICY = "nonnegative_half_up_samples_v1"
ALIGNMENT_BOUNDARY_TOLERANCE_SAMPLES = 2
ALIGNMENT_PROVIDER = "elevenlabs"
_WORD_RE = re.compile(r"\S+")


def _operation_generation_provider(operation: Any) -> str | None:
    provider_identity = getattr(operation, "provider_identity_json", None)
    if not isinstance(provider_identity, dict):
        return None
    value = provider_identity.get("provider_key") or provider_identity.get("provider")
    return str(value) if isinstance(value, str) and value else None


def _operation_generation_voice_id(operation: Any) -> str | None:
    provider_identity = getattr(operation, "provider_identity_json", None)
    if not isinstance(provider_identity, dict):
        return None
    value = provider_identity.get("voice_id")
    return str(value) if isinstance(value, str) and value else None


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def seconds_to_samples(value: float, *, sample_rate_hz: int = CANONICAL_SAMPLE_RATE) -> int:
    """Canonical Task-3 time conversion: non-negative half-up integer samples."""
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise AppError(
            "VOICE_ALIGNMENT_OUT_OF_RANGE",
            "Forced Alignment returned an invalid timestamp.",
            502,
            retryable=False,
        )
    return int(math.floor(float(value) * sample_rate_hz + 0.5))


def validate_character_alignment(
    payload: Any,
    *,
    transcript: str,
    sample_count: int,
    sample_rate_hz: int = CANONICAL_SAMPLE_RATE,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate ElevenLabs characters against exact final audio and transcript."""
    if not isinstance(payload, dict):
        raise AppError(
            "VOICE_ALIGNMENT_RESPONSE_INVALID",
            "Forced Alignment returned a malformed response.",
            502,
            retryable=False,
        )
    characters = payload.get("characters")
    if transcript and (not isinstance(characters, list) or not characters):
        raise AppError(
            "VOICE_ALIGNMENT_RESPONSE_INVALID",
            "Forced Alignment returned no character timing.",
            502,
            retryable=False,
        )
    if not isinstance(characters, list):
        characters = []

    reconstructed: list[str] = []
    result: list[dict[str, Any]] = []
    spoken_cursor = 0
    previous_start = 0
    previous_end = 0
    for index, raw in enumerate(characters):
        if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
            raise AppError(
                "VOICE_ALIGNMENT_RESPONSE_INVALID",
                "Forced Alignment character timing is malformed.",
                502,
                retryable=False,
            )
        text = str(raw["text"])
        start = raw.get("start")
        end = raw.get("end")
        if (
            not isinstance(start, (int, float))
            or isinstance(start, bool)
            or not isinstance(end, (int, float))
            or isinstance(end, bool)
        ):
            raise AppError(
                "VOICE_ALIGNMENT_OUT_OF_RANGE",
                "Forced Alignment returned non-numeric timing.",
                502,
                retryable=False,
            )
        start_sample = seconds_to_samples(float(start), sample_rate_hz=sample_rate_hz)
        end_sample = seconds_to_samples(float(end), sample_rate_hz=sample_rate_hz)
        if end_sample < start_sample or start_sample < previous_start or end_sample < previous_end:
            raise AppError(
                "VOICE_ALIGNMENT_OUT_OF_RANGE",
                "Forced Alignment timing is not monotonic.",
                502,
                retryable=False,
            )
        if (
            start_sample > sample_count + ALIGNMENT_BOUNDARY_TOLERANCE_SAMPLES
            or end_sample > sample_count + ALIGNMENT_BOUNDARY_TOLERANCE_SAMPLES
        ):
            raise AppError(
                "VOICE_ALIGNMENT_OUT_OF_RANGE",
                "Forced Alignment timing exceeds the exact final section audio.",
                502,
                retryable=False,
            )
        # Only clamp tiny provider floating-point boundary noise.
        start_sample = min(start_sample, sample_count)
        end_sample = min(end_sample, sample_count)
        spoken_start = spoken_cursor
        spoken_end = spoken_start + len(text)
        result.append(
            {
                "character_index": index,
                "text": text,
                "spoken_start": spoken_start,
                "spoken_end": spoken_end,
                "start_sample": start_sample,
                "end_sample": end_sample,
            }
        )
        reconstructed.append(text)
        spoken_cursor = spoken_end
        previous_start = start_sample
        previous_end = end_sample

    if "".join(reconstructed) != transcript:
        raise AppError(
            "VOICE_ALIGNMENT_TEXT_MISMATCH",
            "Forced Alignment text does not reconstruct the canonical spoken transcript.",
            502,
            retryable=False,
        )

    vendor_words = payload.get("words") if isinstance(payload.get("words"), list) else []
    raw_loss = payload.get("loss")
    telemetry: dict[str, Any] = {
        "provider_loss": (
            float(raw_loss)
            if isinstance(raw_loss, (int, float))
            and not isinstance(raw_loss, bool)
            and math.isfinite(float(raw_loss))
            else None
        ),
        "provider_word_count": len(vendor_words),
        "provider_word_losses": [
            float(item["loss"])
            for item in vendor_words
            if isinstance(item, dict)
            and isinstance(item.get("loss"), (int, float))
            and not isinstance(item.get("loss"), bool)
            and math.isfinite(float(item["loss"]))
        ],
    }
    return result, telemetry


def _spoken_boundary_to_authored(
    offset: int,
    *,
    mapping: list[dict[str, Any]],
    authored_section_start: int,
    authored_length: int,
    spoken_length: int,
    for_end: bool,
) -> int:
    if not 0 <= offset <= spoken_length:
        raise AppError(
            "VOICE_ALIGNMENT_RESPONSE_INVALID",
            "Spoken word boundary is outside the canonical section.",
            500,
            retryable=False,
        )
    authored_cursor = 0
    spoken_cursor = 0
    for item in sorted(mapping, key=lambda value: int(value.get("spoken_start", -1))):
        authored_start = int(item["section_authored_start"])
        authored_end = int(item["section_authored_end"])
        spoken_start = int(item["spoken_start"])
        spoken_end = int(item["spoken_end"])
        if offset <= spoken_start:
            return authored_section_start + authored_cursor + (offset - spoken_cursor)
        if spoken_start < offset < spoken_end:
            return authored_section_start + (authored_end if for_end else authored_start)
        if offset == spoken_end:
            return authored_section_start + authored_end
        authored_cursor = authored_end
        spoken_cursor = spoken_end
    mapped = authored_section_start + authored_cursor + (offset - spoken_cursor)
    if mapped > authored_section_start + authored_length:
        raise AppError(
            "VOICE_ALIGNMENT_RESPONSE_INVALID",
            "Spoken word lineage exceeds the authored section.",
            500,
            retryable=False,
        )
    return mapped


def build_project_word_timing(
    *,
    transcript: str,
    character_timing: list[dict[str, Any]],
    authored_spoken_mapping: list[dict[str, Any]],
    authored_section_start: int,
    authored_length: int,
    section_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build project-owned words from canonical text, never vendor word tokens."""
    words: list[dict[str, Any]] = []
    for local_index, match in enumerate(_WORD_RE.finditer(transcript)):
        spoken_start, spoken_end = match.span()
        covered = [
            item
            for item in character_timing
            if int(item["spoken_end"]) > spoken_start and int(item["spoken_start"]) < spoken_end
        ]
        if not covered:
            raise AppError(
                "VOICE_ALIGNMENT_RESPONSE_INVALID",
                "A canonical spoken word has no character timing coverage.",
                502,
                retryable=False,
            )
        words.append(
            {
                "section_word_index": local_index,
                "text": match.group(0),
                "spoken_start": spoken_start,
                "spoken_end": spoken_end,
                "authored_start": _spoken_boundary_to_authored(
                    spoken_start,
                    mapping=authored_spoken_mapping,
                    authored_section_start=authored_section_start,
                    authored_length=authored_length,
                    spoken_length=len(transcript),
                    for_end=False,
                ),
                "authored_end": _spoken_boundary_to_authored(
                    spoken_end,
                    mapping=authored_spoken_mapping,
                    authored_section_start=authored_section_start,
                    authored_length=authored_length,
                    spoken_length=len(transcript),
                    for_end=True,
                ),
                "start_sample": int(covered[0]["start_sample"]),
                "end_sample": int(covered[-1]["end_sample"]),
            }
        )
    _resolve_collapsed_words_from_aligned_separators(
        words,
        character_timing=character_timing,
    )
    _validate_project_owned_word_ranges(
        words,
        section_id=section_id,
        sample_start_field="start_sample",
        sample_end_field="end_sample",
    )
    return words


def _resolve_collapsed_words_from_aligned_separators(
    words: list[dict[str, Any]],
    *,
    character_timing: list[dict[str, Any]],
) -> None:
    """Give a collapsed lexical token its measured adjacent separator cell.

    Forced Alignment can place a very short spoken token at one exact sample
    boundary while assigning its acoustic cell to the adjacent whitespace
    character. This projection uses only that provider-measured separator
    interval. It never adds synthetic samples and never changes a positive
    neighboring word range.
    """
    for index, word in enumerate(words):
        start_sample = int(word["start_sample"])
        end_sample = int(word["end_sample"])
        if end_sample != start_sample:
            continue

        spoken_start = int(word["spoken_start"])
        spoken_end = int(word["spoken_end"])
        previous_spoken_end = (
            int(words[index - 1]["spoken_end"])
            if index > 0
            else 0
        )
        next_spoken_start = (
            int(words[index + 1]["spoken_start"])
            if index + 1 < len(words)
            else max(
                (int(item.get("spoken_end", 0)) for item in character_timing),
                default=spoken_end,
            )
        )

        right_separators = [
            item
            for item in character_timing
            if isinstance(item.get("text"), str)
            and str(item["text"]).isspace()
            and int(item["spoken_start"]) >= spoken_end
            and int(item["spoken_end"]) <= next_spoken_start
        ]
        if right_separators:
            right_start = min(int(item["start_sample"]) for item in right_separators)
            right_end = max(int(item["end_sample"]) for item in right_separators)
            if index + 1 < len(words):
                right_end = min(right_end, int(words[index + 1]["start_sample"]))
            if right_start <= start_sample < right_end:
                word["end_sample"] = right_end
                continue

        left_separators = [
            item
            for item in character_timing
            if isinstance(item.get("text"), str)
            and str(item["text"]).isspace()
            and int(item["spoken_start"]) >= previous_spoken_end
            and int(item["spoken_end"]) <= spoken_start
        ]
        if left_separators:
            left_start = min(int(item["start_sample"]) for item in left_separators)
            left_end = max(int(item["end_sample"]) for item in left_separators)
            if index > 0:
                left_start = max(left_start, int(words[index - 1]["end_sample"]))
            if left_start < end_sample <= left_end:
                word["start_sample"] = left_start


def _validate_project_owned_word_ranges(
    words: list[dict[str, Any]],
    *,
    section_id: str | None,
    sample_start_field: str,
    sample_end_field: str,
    total_sample_count: int | None = None,
    initial_state: tuple[int, int] = (-1, -1),
) -> tuple[int, int]:
    previous_sample_start, previous_sample_end = initial_state
    for fallback_index, item in enumerate(words):
        section_word_index = item.get("section_word_index", fallback_index)
        required = (
            "authored_start",
            "authored_end",
            sample_start_field,
            sample_end_field,
        )
        if any(
            not isinstance(item.get(field), int) or isinstance(item.get(field), bool)
            for field in required
        ):
            raise AppError(
                "VOICE_ALIGNMENT_OUT_OF_RANGE",
                "Project-owned word timing fields must be exact integers.",
                502,
                retryable=False,
                details={
                    "section_id": section_id,
                    "section_word_index": section_word_index,
                    "text": item.get("text"),
                },
            )
        authored_start = int(item["authored_start"])
        authored_end = int(item["authored_end"])
        start_sample = int(item[sample_start_field])
        end_sample = int(item[sample_end_field])
        details = {
            "section_id": section_id,
            "section_word_index": section_word_index,
            "text": item.get("text"),
            "authored_start": authored_start,
            "authored_end": authored_end,
            "start_sample": start_sample,
            "end_sample": end_sample,
        }
        if (
            authored_start < 0
            or authored_end <= authored_start
            or start_sample < 0
            or end_sample <= start_sample
            or (
                total_sample_count is not None
                and end_sample > total_sample_count
            )
        ):
            raise AppError(
                "VOICE_ALIGNMENT_OUT_OF_RANGE",
                "Project-owned word resolved to an invalid positive range.",
                502,
                retryable=False,
                details=details,
            )
        if (
            start_sample < previous_sample_start
            or end_sample < previous_sample_end
        ):
            raise AppError(
                "VOICE_ALIGNMENT_OUT_OF_RANGE",
                "Project-owned word timing is not monotonic.",
                502,
                retryable=False,
                details=details,
            )
        previous_sample_start = start_sample
        previous_sample_end = end_sample
    return previous_sample_start, previous_sample_end


def _validate_published_word_timing_map(timing_map: dict[str, Any]) -> None:
    total_sample_count = timing_map.get("total_sample_count")
    sections = timing_map.get("sections")
    if (
        not isinstance(total_sample_count, int)
        or isinstance(total_sample_count, bool)
        or total_sample_count <= 0
        or not isinstance(sections, list)
    ):
        raise AppError(
            "VOICE_ALIGNMENT_OUT_OF_RANGE",
            "Project-owned word timing authority is malformed.",
            502,
            retryable=False,
        )
    state = (-1, -1)
    for section in sections:
        if not isinstance(section, dict) or not isinstance(section.get("words"), list):
            raise AppError(
                "VOICE_ALIGNMENT_OUT_OF_RANGE",
                "Project-owned word timing section is malformed.",
                502,
                retryable=False,
            )
        state = _validate_project_owned_word_ranges(
            section["words"],
            section_id=str(section.get("section_id") or "") or None,
            sample_start_field="global_start_sample",
            sample_end_field="global_end_sample",
            total_sample_count=total_sample_count,
            initial_state=state,
        )


class VoiceTask3AlignmentService:
    """Durable owner for final-audio alignment and operation-owned draft Voice candidates."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        client_factory: Callable[[], ElevenLabsHttpClient] | None = None,
        stale_submit_seconds: int = 150,
    ) -> None:
        self.session_factory = session_factory
        self.client_factory = client_factory
        self.stale_submit_seconds = stale_submit_seconds

    def reconcile_operation(self, operation_id: str) -> bool:
        prepared: tuple[str, str, str, int, str, str, str] | None = None
        with self.session_factory() as session:
            service = DurableVoiceService(session)
            operation = service.operations.get(operation_id)
            if operation is None or operation.status != "finalizing":
                session.rollback()
                return False
            final_sections = operation.result_json.get("final_sections") if isinstance(operation.result_json, dict) else None
            if not isinstance(final_sections, list) or not final_sections:
                session.rollback()
                return False
            job = service.jobs.get(operation.job_id)
            if job is None:
                raise AppError(
                    "VOICE_OPERATION_INTEGRITY_INVALID",
                    "Voice operation Job is missing.",
                    409,
                    retryable=False,
                )
            project = service._project_required(operation.project_id)
            if (
                project.script_artifact_id != operation.script_artifact_id
                or project.narration_audio_artifact_id != operation.expected_narration_audio_artifact_id
                or project.narration_word_timing_map_artifact_id
                != operation.expected_narration_word_timing_map_artifact_id
            ):
                self._terminalize_superseded(operation, job)
                session.commit()
                return True
            if service._cancellation_requested(job, operation):
                now = utc_now_iso()
                for task in self._tasks(session, operation_id):
                    if task.status not in {"completed", "cancelled"}:
                        task.status = "cancelled"
                        task.completed_at = now
                        task.updated_at = now
                service._terminalize_cancelled(job, operation)
                session.commit()
                return True

            tasks = self._tasks(session, operation_id)
            if not tasks:
                self._create_tasks(session, operation, final_sections)
                now = utc_now_iso()
                operation.result_json = {
                    **operation.result_json,
                    "task_3_status": "alignment",
                    "alignment_status": "queued",
                }
                operation.next_reconcile_at = now
                operation.updated_at = now
                job.status = JobStatus.RUNNING
                job.progress = 82
                job.updated_at = now
                session.commit()
                return True

            unknown = [task for task in tasks if task.status == "submit_outcome_unknown"]
            if unknown:
                now = utc_now_iso()
                operation.result_json = {
                    **operation.result_json,
                    "task_3_status": "alignment",
                    "alignment_status": "submit_outcome_unknown",
                    "alignment_submit_outcome_unknown_section_ids": [task.section_id for task in unknown],
                }
                # Hard park: durable scheduler only claims operations with a due timestamp.
                operation.next_reconcile_at = None
                operation.updated_at = now
                job.status = JobStatus.RUNNING
                job.progress = 86
                job.updated_at = now
                session.commit()
                return True

            submitting = next((task for task in tasks if task.status == "submitting"), None)
            if submitting is not None:
                stale_at = parse_iso(str(submitting.submit_started_at)) + timedelta(seconds=self.stale_submit_seconds)
                if utc_now() >= stale_at:
                    self._mark_unknown(submitting, reason="worker_recovery_after_unproven_submission")
                    operation.result_json = {
                        **operation.result_json,
                        "alignment_status": "submit_outcome_unknown",
                        "alignment_submit_outcome_unknown_section_ids": [submitting.section_id],
                    }
                    operation.next_reconcile_at = None
                else:
                    operation.next_reconcile_at = stale_at.isoformat()
                operation.updated_at = utc_now_iso()
                session.commit()
                return True

            failed = next((task for task in tasks if task.status == "failed"), None)
            if failed is not None:
                self._terminalize_failed(operation, job, failed)
                session.commit()
                return True

            if all(task.status == "completed" for task in tasks):
                return self._finalize_candidate(session, service, operation, job, final_sections, tasks)

            now_dt = utc_now()
            queued = next(
                (
                    task
                    for task in tasks
                    if task.status == "queued"
                    and (task.next_reconcile_at is None or parse_iso(task.next_reconcile_at) <= now_dt)
                ),
                None,
            )
            if queued is None:
                due = [parse_iso(task.next_reconcile_at) for task in tasks if task.status == "queued" and task.next_reconcile_at]
                operation.next_reconcile_at = min(due).isoformat() if due else (now_dt + timedelta(seconds=1)).isoformat()
                operation.updated_at = utc_now_iso()
                session.commit()
                return True

            token = uuid4().hex
            now = utc_now_iso()
            queued.status = "submitting"
            queued.submit_attempt_token = token
            queued.execution_attempt_token = token
            queued.submit_started_at = now
            queued.execution_started_at = now
            queued.provider_submitted_at = now
            queued.provider_status = "submitting"
            queued.updated_at = now
            queued.state_revision += 1
            operation.result_json = {
                **operation.result_json,
                "task_3_status": "alignment",
                "alignment_status": "submitting",
            }
            operation.next_reconcile_at = (now_dt + timedelta(seconds=self.stale_submit_seconds)).isoformat()
            operation.updated_at = now
            job.status = JobStatus.RUNNING
            job.progress = 84
            job.updated_at = now
            prepared = (
                queued.voice_alignment_task_id,
                token,
                queued.section_audio_artifact_id,
                queued.section_audio_sample_count,
                queued.section_audio_sha256,
                queued.spoken_transcript_sha256,
                queued.spoken_transcript,
            )
            session.commit()

        if prepared is None:
            return True
        task_id, token, artifact_id, sample_count, audio_sha, transcript_sha, transcript = prepared
        client: ElevenLabsHttpClient | None = None
        try:
            with self.session_factory() as session:
                service = DurableVoiceService(session)
                artifact = service.artifact_rows.get(artifact_id)
                if artifact is None or artifact.status != ArtifactStatus.READY or artifact.deleted_at is not None:
                    raise AppError(
                        "VOICE_ALIGNMENT_NOT_READY",
                        "Final section audio Artifact is unavailable for alignment.",
                        409,
                        retryable=False,
                    )
                audio_bytes = service.artifacts.resolve_file_path(artifact).read_bytes()
            if _sha256_bytes(audio_bytes) != audio_sha:
                raise AppError(
                    "VOICE_ALIGNMENT_NOT_READY",
                    "Final section audio bytes do not match the frozen alignment SHA.",
                    409,
                    retryable=False,
                )
            info = validate_canonical_wav_bytes(audio_bytes)
            if info.sample_rate_hz != CANONICAL_SAMPLE_RATE or info.sample_count != sample_count:
                raise AppError(
                    "VOICE_ALIGNMENT_NOT_READY",
                    "Final section audio sample identity changed before alignment.",
                    409,
                    retryable=False,
                )
            if hashlib.sha256(transcript.encode("utf-8")).hexdigest() != transcript_sha or "<" in transcript or ">" in transcript:
                raise AppError(
                    "VOICE_ALIGNMENT_NOT_READY",
                    "Canonical spoken transcript identity changed before alignment.",
                    409,
                    retryable=False,
                )
            client = self._client()
            response = client.forced_alignment(audio_bytes=audio_bytes, text=transcript)
            characters, loss = validate_character_alignment(
                response.payload,
                transcript=transcript,
                sample_count=sample_count,
                sample_rate_hz=CANONICAL_SAMPLE_RATE,
            )
        except ElevenLabsAmbiguousSubmission:
            self._accept_unknown(task_id, token)
            return True
        except AppError as exc:
            self._accept_error(task_id, token, exc)
            return True
        finally:
            if client is not None:
                client.close()

        self._accept_success(
            task_id,
            token,
            result={
                "schema_version": 1,
                "sample_rounding_policy": ALIGNMENT_SAMPLE_ROUNDING_POLICY,
                "sample_rate_hz": CANONICAL_SAMPLE_RATE,
                "section_audio_sha256": audio_sha,
                "spoken_transcript_sha256": transcript_sha,
                "characters": characters,
            },
            loss=loss,
        )
        return True

    def retry_unknown(self, *, project_id: str, job_id: str) -> int:
        """Explicit user recovery only; never called by the scheduler."""
        with self.session_factory() as session:
            service = DurableVoiceService(session)
            operation = service.operations.get_by_job_id(job_id)
            if operation is None or operation.project_id != project_id or operation.status != "finalizing":
                raise AppError(
                    "VOICE_JOB_NOT_FOUND",
                    "Voice alignment Job is not actionable.",
                    404,
                    retryable=False,
                )
            tasks = [task for task in self._tasks(session, operation.operation_id) if task.status == "submit_outcome_unknown"]
            if not tasks:
                raise AppError(
                    "VOICE_ALIGNMENT_NOT_READY",
                    "No ambiguous Forced Alignment submission is awaiting explicit retry.",
                    409,
                    retryable=False,
                )
            now = utc_now_iso()
            for task in tasks:
                task.status = "queued"
                task.retry_count += 1
                task.last_retry_at = now
                task.next_reconcile_at = now
                task.submit_attempt_token = None
                task.execution_attempt_token = None
                task.submit_started_at = None
                task.execution_started_at = None
                task.provider_status = "explicit_retry_queued"
                task.error_code = None
                task.error_message = None
                task.error_details_json = None
                task.updated_at = now
                task.state_revision += 1
            operation.result_json = {
                **operation.result_json,
                "alignment_status": "queued",
                "alignment_submit_outcome_unknown_section_ids": [],
            }
            operation.next_reconcile_at = now
            operation.updated_at = now
            job = service.jobs.get(job_id)
            if job is not None:
                job.status = JobStatus.RUNNING
                job.progress = 84
                job.error_code = None
                job.error_message = None
                job.updated_at = now
            session.commit()
            return len(tasks)

    def _client(self) -> ElevenLabsHttpClient:
        if self.client_factory is not None:
            return self.client_factory()
        settings = get_settings()
        api_key = build_runtime_elevenlabs_credential_resolver(settings).require_api_key()
        return ElevenLabsHttpClient(
            api_key=api_key,
            api_base_url=settings.elevenlabs_api_base_url,
            max_response_bytes=max(settings.tts_provider_max_output_bytes * 2 + 1_048_576, 1_048_576),
            timeout_seconds=float(settings.tts_provider_timeout_seconds),
        )

    @staticmethod
    def _tasks(session: Session, operation_id: str) -> list[VoiceAlignmentTask]:
        stmt = (
            select(VoiceAlignmentTask)
            .where(VoiceAlignmentTask.operation_id == operation_id)
            .order_by(VoiceAlignmentTask.section_order, VoiceAlignmentTask.voice_alignment_task_id)
        )
        return list(session.scalars(stmt))

    def _create_tasks(self, session: Session, operation, final_sections: list[dict[str, Any]]) -> None:
        now = utc_now_iso()
        for expected_order, section in enumerate(final_sections, 1):
            if not isinstance(section, dict) or int(section.get("section_order") or -1) != expected_order:
                raise AppError(
                    "VOICE_OPERATION_INTEGRITY_INVALID",
                    "Final Voice section order is invalid for alignment.",
                    409,
                    retryable=False,
                )
            transcript = section.get("spoken_transcript")
            transcript_sha = section.get("spoken_transcript_sha256")
            if (
                not isinstance(transcript, str)
                or not isinstance(transcript_sha, str)
                or hashlib.sha256(transcript.encode("utf-8")).hexdigest() != transcript_sha
                or "<" in transcript
                or ">" in transcript
            ):
                raise AppError(
                    "VOICE_OPERATION_INTEGRITY_INVALID",
                    "Final Voice section transcript identity is invalid for alignment.",
                    409,
                    retryable=False,
                )
            session.add(
                VoiceAlignmentTask(
                    voice_alignment_task_id=generate_id("voice_alignment_task"),
                    operation_id=operation.operation_id,
                    job_id=operation.job_id,
                    project_id=operation.project_id,
                    section_id=str(section["section_id"]),
                    section_order=expected_order,
                    section_audio_artifact_id=str(section["section_audio_artifact_id"]),
                    section_audio_sha256=str(section["audio_sha256"]),
                    section_audio_sample_count=int(section["sample_count"]),
                    sample_rate_hz=int(section["sample_rate_hz"]),
                    spoken_transcript=transcript,
                    spoken_transcript_sha256=transcript_sha,
                    provider=ALIGNMENT_PROVIDER,
                    provider_policy_version=FORCED_ALIGNMENT_POLICY_VERSION,
                    alignment_policy_version=FORCED_ALIGNMENT_POLICY_VERSION,
                    status="queued",
                    attempt_no=1,
                    submit_attempt_token=None,
                    submit_started_at=None,
                    execution_attempt_token=None,
                    execution_started_at=None,
                    provider_submitted_at=None,
                    provider_status=None,
                    retry_count=0,
                    max_attempts=3,
                    last_retry_at=None,
                    next_reconcile_at=now,
                    result_json=None,
                    loss_telemetry_json=None,
                    error_code=None,
                    error_message=None,
                    error_details_json=None,
                    created_at=now,
                    updated_at=now,
                    completed_at=None,
                )
            )
        session.flush()

    @staticmethod
    def _mark_unknown(task: VoiceAlignmentTask, *, reason: str) -> None:
        now = utc_now_iso()
        task.status = "submit_outcome_unknown"
        task.provider_status = "submit_outcome_unknown"
        task.error_code = "VOICE_ALIGNMENT_SUBMIT_OUTCOME_UNKNOWN"
        task.error_message = "Forced Alignment may have been submitted; automatic resubmission is disabled."
        task.error_details_json = {"reason": reason}
        task.next_reconcile_at = None
        task.updated_at = now
        task.state_revision += 1

    def _accept_unknown(self, task_id: str, token: str) -> None:
        with self.session_factory() as session:
            task = session.get(VoiceAlignmentTask, task_id)
            if task is None or task.status != "submitting" or task.execution_attempt_token != token:
                session.rollback()
                return
            self._mark_unknown(task, reason="transport_outcome_ambiguous")
            operation = DurableVoiceService(session).operations.get(task.operation_id)
            if operation is not None and operation.status == "finalizing":
                operation.result_json = {
                    **operation.result_json,
                    "alignment_status": "submit_outcome_unknown",
                    "alignment_submit_outcome_unknown_section_ids": [task.section_id],
                }
                operation.next_reconcile_at = None
                operation.updated_at = utc_now_iso()
            session.commit()

    def _accept_error(self, task_id: str, token: str, exc: AppError) -> None:
        with self.session_factory() as session:
            service = DurableVoiceService(session)
            task = session.get(VoiceAlignmentTask, task_id)
            if task is None or task.status != "submitting" or task.execution_attempt_token != token:
                session.rollback()
                return
            operation = service.operations.get(task.operation_id)
            if operation is None or operation.status != "finalizing":
                session.rollback()
                return
            now_dt = utc_now()
            now = now_dt.isoformat()
            next_retry_count = task.retry_count + 1
            if exc.retryable and next_retry_count < task.max_attempts:
                task.status = "queued"
                task.retry_count = next_retry_count
                task.last_retry_at = now
                task.next_reconcile_at = (now_dt + timedelta(seconds=min(30, 2 ** next_retry_count))).isoformat()
                task.submit_attempt_token = None
                task.execution_attempt_token = None
                task.submit_started_at = None
                task.execution_started_at = None
                task.provider_status = "known_retryable_failure"
                operation.next_reconcile_at = task.next_reconcile_at
            else:
                task.status = "failed"
                task.error_code = "VOICE_ALIGNMENT_FAILED"
                task.error_message = exc.message
                task.error_details_json = {"cause_error_code": exc.error_code, **dict(exc.details or {})}
                task.completed_at = now
                task.next_reconcile_at = None
                operation.next_reconcile_at = now
            task.updated_at = now
            task.state_revision += 1
            operation.updated_at = now
            session.commit()

    def _accept_success(self, task_id: str, token: str, *, result: dict[str, Any], loss: dict[str, Any]) -> None:
        with self.session_factory() as session:
            service = DurableVoiceService(session)
            task = session.get(VoiceAlignmentTask, task_id)
            if task is None or task.status != "submitting" or task.execution_attempt_token != token:
                session.rollback()
                return
            operation = service.operations.get(task.operation_id)
            if operation is None or operation.status != "finalizing" or operation.project_id != task.project_id:
                session.rollback()
                return
            final_sections = operation.result_json.get("final_sections") if isinstance(operation.result_json, dict) else None
            frozen = next(
                (
                    item
                    for item in final_sections or []
                    if isinstance(item, dict) and item.get("section_id") == task.section_id
                ),
                None,
            )
            if (
                not isinstance(frozen, dict)
                or frozen.get("section_audio_artifact_id") != task.section_audio_artifact_id
                or frozen.get("audio_sha256") != task.section_audio_sha256
                or int(frozen.get("sample_count") or -1) != task.section_audio_sample_count
                or frozen.get("spoken_transcript_sha256") != task.spoken_transcript_sha256
            ):
                session.rollback()
                return
            now = utc_now_iso()
            task.status = "completed"
            task.provider_status = "completed"
            task.result_json = result
            task.loss_telemetry_json = loss
            task.error_code = None
            task.error_message = None
            task.error_details_json = None
            task.next_reconcile_at = None
            task.completed_at = now
            task.updated_at = now
            task.state_revision += 1
            operation.result_json = {**operation.result_json, "alignment_status": "running"}
            operation.next_reconcile_at = now
            operation.updated_at = now
            session.commit()

    def _finalize_candidate(
        self,
        session: Session,
        service: DurableVoiceService,
        operation,
        job,
        final_sections: list[dict[str, Any]],
        tasks: list[VoiceAlignmentTask],
    ) -> bool:
        staged: list[StagedArtifact] = []
        try:
            project = service._project_required(operation.project_id)
            script, _ = service._load_sources(project)
            compiled = compile_voice_spoken_text(script)
            if compiled.compilation_sha256 != operation.request_json.get("spoken_compilation_sha256"):
                raise AppError(
                    "VOICE_CANDIDATE_STALE",
                    "Voice candidate spoken compilation is stale.",
                    409,
                    retryable=False,
                )
            generation_provider = _operation_generation_provider(operation)
            generation_voice_id = _operation_generation_voice_id(operation)
            requires_channel_voice_profile = generation_provider == "elevenlabs"
            channel_profile_sha = self._channel_profile_hash(session, project.channel_id)
            candidate_channel_profile_sha = channel_profile_sha if requires_channel_voice_profile else None
            if requires_channel_voice_profile and channel_profile_sha != operation.generation_settings_snapshot_json.get("channel_voice_profile_sha256"):
                raise AppError(
                    "VOICE_CANDIDATE_STALE",
                    "Channel Voice configuration changed before candidate finalization.",
                    409,
                    retryable=False,
                )

            script_sections = script.get("script_sections") if isinstance(script, dict) else None
            if not isinstance(script_sections, list) or len(script_sections) != len(final_sections):
                raise AppError(
                    "VOICE_OPERATION_INTEGRITY_INVALID",
                    "Script section coverage changed before Voice candidate finalization.",
                    409,
                    retryable=False,
                )
            compiled_by_section = {str(item["section_id"]): item for item in compiled.sections}
            tasks_by_section = {task.section_id: task for task in tasks}
            section_wavs: list[bytes] = []
            timing_sections: list[dict[str, Any]] = []
            global_sample_cursor = 0
            authored_section_start = 0
            global_word_index = 0
            final_section_identities: list[str] = []

            for index, (script_section, final_section) in enumerate(zip(script_sections, final_sections, strict=True), 1):
                if not isinstance(script_section, dict) or not isinstance(final_section, dict):
                    raise AppError(
                        "VOICE_OPERATION_INTEGRITY_INVALID",
                        "Voice candidate section is malformed.",
                        409,
                        retryable=False,
                    )
                section_id = str(final_section["section_id"])
                task = tasks_by_section.get(section_id)
                compiled_section = compiled_by_section.get(section_id)
                if task is None or task.status != "completed" or not isinstance(task.result_json, dict) or compiled_section is None:
                    raise AppError(
                        "VOICE_ALIGNMENT_NOT_READY",
                        "Voice candidate requires complete final-audio alignment for every section.",
                        409,
                        retryable=False,
                    )
                if int(final_section.get("section_order") or -1) != index or task.section_order != index:
                    raise AppError(
                        "VOICE_OPERATION_INTEGRITY_INVALID",
                        "Voice candidate section order is invalid.",
                        409,
                        retryable=False,
                    )
                transcript = str(final_section["spoken_transcript"])
                if transcript != compiled_section["canonical_spoken_text"] or task.spoken_transcript != transcript:
                    raise AppError(
                        "VOICE_ALIGNMENT_TEXT_MISMATCH",
                        "Aligned transcript does not match canonical spoken narration.",
                        409,
                        retryable=False,
                    )
                artifact = service.artifact_rows.get(str(final_section["section_audio_artifact_id"]))
                if artifact is None or artifact.status != ArtifactStatus.READY or artifact.sha256 != final_section["audio_sha256"]:
                    raise AppError(
                        "VOICE_ALIGNMENT_NOT_READY",
                        "Final section audio lineage is unavailable.",
                        409,
                        retryable=False,
                    )
                wav_bytes = service.artifacts.resolve_file_path(artifact).read_bytes()
                if _sha256_bytes(wav_bytes) != final_section["audio_sha256"]:
                    raise AppError(
                        "VOICE_ALIGNMENT_NOT_READY",
                        "Final section WAV bytes changed after alignment.",
                        409,
                        retryable=False,
                    )
                info = validate_canonical_wav_bytes(wav_bytes)
                if info.sample_count != int(final_section["sample_count"]) or info.sample_rate_hz != CANONICAL_SAMPLE_RATE:
                    raise AppError(
                        "VOICE_ALIGNMENT_NOT_READY",
                        "Final section WAV sample identity changed after alignment.",
                        409,
                        retryable=False,
                    )
                characters = task.result_json.get("characters")
                if not isinstance(characters, list):
                    raise AppError(
                        "VOICE_ALIGNMENT_RESPONSE_INVALID",
                        "Completed alignment is missing character timing.",
                        409,
                        retryable=False,
                    )
                authored_narration = str(script_section.get("narration") or "")
                words = build_project_word_timing(
                    transcript=transcript,
                    character_timing=characters,
                    authored_spoken_mapping=list(compiled_section.get("authored_spoken_mapping") or []),
                    authored_section_start=authored_section_start,
                    authored_length=len(authored_narration),
                    section_id=section_id,
                )
                for word in words:
                    word["word_index"] = global_word_index
                    word["section_id"] = section_id
                    word["global_start_sample"] = global_sample_cursor + int(word["start_sample"])
                    word["global_end_sample"] = global_sample_cursor + int(word["end_sample"])
                    global_word_index += 1
                timing_sections.append(
                    {
                        "section_id": section_id,
                        "section_order": index,
                        "authored_section_start": authored_section_start,
                        "authored_section_end": authored_section_start + len(authored_narration),
                        "section_audio_artifact_id": artifact.artifact_id,
                        "section_audio_sha256": info.sha256,
                        "final_section_identity_sha256": str(final_section["final_section_identity_sha256"]),
                        "sample_count": info.sample_count,
                        "global_start_sample": global_sample_cursor,
                        "global_end_sample": global_sample_cursor + info.sample_count,
                        "canonical_spoken_text": transcript,
                        "canonical_spoken_text_sha256": task.spoken_transcript_sha256,
                        "authored_spoken_mapping": compiled_section["authored_spoken_mapping"],
                        "character_timing": [
                            {
                                **item,
                                "global_start_sample": global_sample_cursor + int(item["start_sample"]),
                                "global_end_sample": global_sample_cursor + int(item["end_sample"]),
                            }
                            for item in characters
                        ],
                        "words": words,
                        "alignment_task_id": task.voice_alignment_task_id,
                        "alignment_loss_telemetry": task.loss_telemetry_json,
                    }
                )
                section_wavs.append(wav_bytes)
                global_sample_cursor += info.sample_count
                authored_section_start += len(authored_narration)
                if index < len(script_sections):
                    authored_section_start += len(SCRIPT_SECTION_SEPARATOR)
                final_section_identities.append(str(final_section["final_section_identity_sha256"]))

            full_audio, full_info = assemble_canonical_wav(section_wavs)
            if full_info.sample_count != global_sample_cursor or full_info.sample_rate_hz != CANONICAL_SAMPLE_RATE:
                raise AppError(
                    "VOICE_OPERATION_INTEGRITY_INVALID",
                    "Full narration sample identity does not equal exact section concatenation.",
                    409,
                    retryable=False,
                )
            _validate_published_word_timing_map(
                {
                    "total_sample_count": full_info.sample_count,
                    "sections": timing_sections,
                }
            )
            audio_staged = service.artifacts.stage_artifact_revision(
                logical_id=f"project/{operation.project_id}/voice/narration-audio",
                artifact_type=ArtifactType.AUDIO,
                filename="narration.wav",
                content=full_audio,
                source_job_id=operation.job_id,
                provider_source="voice_task3_candidate",
                content_hash=full_info.sha256,
            )
            # ArtifactService MIME inference is platform-dependent (for example,
            # Python may report audio/x-wav). Canonical Voice artifacts own the
            # stricter application contract and must publish the exact MIME claim
            # consumed by downstream byte validation.
            audio_staged.artifact.mime_type = CANONICAL_MIME
            audio_staged.artifact.duration_sec = full_info.duration_seconds
            staged.append(audio_staged)

            canonical_spoken_narration = str(compiled.spoken_script.get("full_script") or "")
            timing_map = {
                "schema_version": NARRATION_WORD_TIMING_MAP_SCHEMA_VERSION,
                "architecture_version": VOICE_ARCHITECTURE_VERSION,
                "source_script_artifact_id": operation.script_artifact_id,
                "source_narration_audio_artifact_id": audio_staged.artifact_id,
                "sample_rate_hz": CANONICAL_SAMPLE_RATE,
                "total_sample_count": full_info.sample_count,
                "canonical_spoken_narration": canonical_spoken_narration,
                "canonical_spoken_narration_sha256": hashlib.sha256(canonical_spoken_narration.encode("utf-8")).hexdigest(),
                "spoken_compilation_sha256": compiled.compilation_sha256,
                "sections": timing_sections,
                "generation_provider": generation_provider,
                "generation_voice_id": generation_voice_id,
                "alignment_provider": ALIGNMENT_PROVIDER,
                "alignment_policy_version": FORCED_ALIGNMENT_POLICY_VERSION,
                "sample_rounding_policy": ALIGNMENT_SAMPLE_ROUNDING_POLICY,
                "alignment_summary": {
                    "section_count": len(timing_sections),
                    "completed_alignment_task_ids": [task.voice_alignment_task_id for task in tasks],
                    "loss_telemetry": [task.loss_telemetry_json for task in tasks],
                },
            }
            timing_bytes = _json_bytes(timing_map)
            timing_sha = _sha256_bytes(timing_bytes)
            timing_staged = service.artifacts.stage_artifact_revision(
                logical_id=f"project/{operation.project_id}/voice/narration-word-timing-map",
                artifact_type=ArtifactType.JSON,
                filename="narration_word_timing_map.json",
                content=timing_bytes,
                source_job_id=operation.job_id,
                provider_source="voice_task3_candidate",
                content_hash=timing_sha,
            )
            staged.append(timing_staged)

            candidate_identity = canonical_hash(
                {
                    "architecture_version": VOICE_ARCHITECTURE_VERSION,
                    "operation_id": operation.operation_id,
                    "source_script_artifact_id": operation.script_artifact_id,
                    "narration_audio_artifact_id": audio_staged.artifact_id,
                    "narration_audio_sha256": full_info.sha256,
                    "narration_word_timing_map_artifact_id": timing_staged.artifact_id,
                    "narration_word_timing_map_sha256": timing_sha,
                    "total_sample_count": full_info.sample_count,
                    "voice_preset": operation.voice_preset,
                    "generation_provider": generation_provider,
                    "generation_voice_id": generation_voice_id,
                    "provider_identity_sha256": operation.provider_identity_sha256,
                    "generation_settings_snapshot_sha256": operation.generation_settings_snapshot_sha256,
                    "channel_voice_profile_sha256": candidate_channel_profile_sha,
                    "spoken_compilation_sha256": compiled.compilation_sha256,
                    "final_section_identity_sha256": final_section_identities,
                }
            )
            now = utc_now_iso()
            operation.result_json = {
                **operation.result_json,
                "task_3_status": "reviewable",
                "alignment_status": "completed",
                "alignment_submit_outcome_unknown_section_ids": [],
                "draft_candidate": {
                    "candidate_identity_sha256": candidate_identity,
                    "narration_audio_artifact_id": audio_staged.artifact_id,
                    "narration_audio_sha256": full_info.sha256,
                    "narration_word_timing_map_artifact_id": timing_staged.artifact_id,
                    "narration_word_timing_map_sha256": timing_sha,
                    "source_script_artifact_id": operation.script_artifact_id,
                    "sample_rate_hz": CANONICAL_SAMPLE_RATE,
                    "total_sample_count": full_info.sample_count,
                    "channel_voice_profile_sha256": candidate_channel_profile_sha,
                    "voice_preset": operation.voice_preset,
                    "generation_provider": generation_provider,
                    "generation_voice_id": generation_voice_id,
                    "provider_identity_sha256": operation.provider_identity_sha256,
                    "generation_settings_snapshot_sha256": operation.generation_settings_snapshot_sha256,
                    "spoken_compilation_sha256": compiled.compilation_sha256,
                    "final_section_identity_sha256": final_section_identities,
                    "reviewable": True,
                },
            }
            operation.status = "completed"
            operation.error_code = None
            operation.error_message = None
            operation.error_details_json = None
            operation.completed_at = now
            operation.next_reconcile_at = None
            operation.reconcile_owner = None
            operation.reconcile_token = None
            operation.reconcile_lease_expires_at = None
            operation.updated_at = now
            job.status = JobStatus.COMPLETED
            job.retryable = False
            job.progress = 100
            job.error_code = None
            job.error_message = None
            job.error_details_json = None
            job.completed_at = now
            job.finished_at = now
            job.updated_at = now
            job.heartbeat_at = now
            session.commit()
            staged.clear()
            return True
        except Exception:
            session.rollback()
            import logging

            logger = logging.getLogger(__name__)
            for item in reversed(staged):
                cleanup_staged_artifact_safely(item, logger=logger)
            raise

    @staticmethod
    def _channel_profile_hash(session: Session, channel_id: str) -> str | None:
        channel = session.get(Channel, channel_id)
        profile = channel.voice_profile_json if channel is not None else None
        return canonical_hash(profile) if isinstance(profile, dict) else None

    @staticmethod
    def _terminalize_superseded(operation, job) -> None:
        now = utc_now_iso()
        operation.status = "superseded"
        operation.error_code = "VOICE_CANDIDATE_STALE"
        operation.error_message = "Voice operation Script or approved Voice pair lineage became stale before candidate completion."
        operation.completed_at = now
        operation.next_reconcile_at = None
        operation.updated_at = now
        job.status = JobStatus.FAILED
        job.retryable = False
        job.error_code = operation.error_code
        job.error_message = operation.error_message
        job.completed_at = now
        job.finished_at = now
        job.updated_at = now

    @staticmethod
    def _terminalize_failed(operation, job, task: VoiceAlignmentTask) -> None:
        now = utc_now_iso()
        operation.status = "failed"
        operation.error_code = task.error_code or "VOICE_ALIGNMENT_FAILED"
        operation.error_message = task.error_message or "Forced Alignment failed."
        operation.error_details_json = task.error_details_json
        operation.completed_at = now
        operation.next_reconcile_at = None
        operation.updated_at = now
        job.status = JobStatus.FAILED
        job.retryable = False
        job.progress = 86
        job.error_code = operation.error_code
        job.error_message = operation.error_message
        job.error_details_json = operation.error_details_json
        job.completed_at = now
        job.finished_at = now
        job.updated_at = now
