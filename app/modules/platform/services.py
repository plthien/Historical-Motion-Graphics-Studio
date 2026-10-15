from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from typing import Any

from sqlalchemy import Text, cast, select
from sqlalchemy.orm import Session, load_only

from app.api.errors import AppError
from app.core.config import get_settings
from app.core.ids import generate_id
from app.core.external_authoring import extract_reserved_artifact_ids, parse_external_json, render_external_prompt, require_source_values, validation_success
from app.core.time import utc_now_iso
from app.db.enums import ArtifactRole, ArtifactType
from app.db.models import Channel, Project
from app.modules.platform.contracts import PlatformContractValidator
from app.modules.platform.invalidation import ProjectPipelineInvalidationService
from app.modules.platform.video_pacing import VideoPacingError, resolve_video_pacing_profile
from app.modules.platform.artifact_loader import ProjectArtifactLoader
from app.modules.v4.script_word_budget import (
    SCRIPT_SECTION_SEPARATOR,
    SECTION_COUNT_MODE_LLM_DECIDES,
    ScriptTimingError,
    build_authoring_constraints,
    build_computed_constraints,
    build_full_script_from_sections,
    build_script_section_word_counts,
    canonicalize_script_section_narration,
    count_narration_words,
    build_runtime_diagnostics,
)
from app.repositories import ArtifactRepository, ChannelRepository, ProjectRepository
from app.repositories.core import TopicIdeasCommitGuard
from app.services.artifacts import ArtifactService, cleanup_staged_artifact_safely, rollback_safely, StagedArtifact
from app.services.external_authoring_reservations import ExternalAuthoringReservationService, TOPIC_STAGE


logger = logging.getLogger(__name__)

SENSITIVE_SPOKEN_TOKEN_RE = re.compile(r"[0-9$€£¥%]")


def _require_narration_wpm_range(
    value: Any,
    *,
    error_code: str,
    field_path: str,
) -> dict[str, int]:
    if not isinstance(value, dict):
        raise AppError(
            error_code,
            "Narration WPM must be an inclusive {min, max} range.",
            422,
            retryable=False,
            details={
                "errors": [
                    {
                        "path": field_path,
                        "keyword": "type",
                        "message": "Narration WPM must be an object with integer min and max.",
                    }
                ]
            },
        )
    minimum = value.get("min")
    maximum = value.get("max")
    if (
        isinstance(minimum, bool)
        or isinstance(maximum, bool)
        or not isinstance(minimum, int)
        or not isinstance(maximum, int)
        or minimum < 80
        or maximum > 300
        or minimum >= maximum
    ):
        raise AppError(
            error_code,
            "Narration WPM range must satisfy integer 80 <= min < max <= 300.",
            422,
            retryable=False,
            details={
                "errors": [
                    {
                        "path": field_path,
                        "keyword": "range",
                        "message": "Narration WPM range must satisfy integer 80 <= min < max <= 300.",
                    }
                ]
            },
        )
    return {"min": minimum, "max": maximum}


def channel_context_from_dna(channel_dna: dict[str, Any]) -> dict[str, str]:
    channel = channel_dna.get("channel") if isinstance(channel_dna, dict) else None
    if not isinstance(channel, dict):
        raise AppError("CHANNEL_DNA_INVALID", "Channel DNA is missing channel context.", 422, retryable=False)
    return {
        "name": str(channel["name"]),
        "language": str(channel["language"]),
        "niche": str(channel["niche"]),
        "audience": str(channel["audience"]),
    }


def build_script_section_ranges(script_sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranges = []
    current_offset = 0
    for section in script_sections:
        narration = canonicalize_script_section_narration(section.get("narration", ""))
        if not narration:
            continue
        start_pos = current_offset
        end_pos = start_pos + len(narration)
        ranges.append({
            "section_id": section.get("section_id", ""),
            "start": start_pos,
            "end": end_pos,
        })
        current_offset = end_pos + len(SCRIPT_SECTION_SEPARATOR)
    return ranges


def validate_script_full_script_integrity(script_artifact: dict[str, Any], *, artifact_id: str | None = None) -> str:
    canonical_full_script = build_full_script_from_sections(script_artifact.get("script_sections", []))
    stored_full_script = str(script_artifact.get("full_script", "")).replace("\r\n", "\n").replace("\r", "\n")
    if stored_full_script != canonical_full_script:
        details: dict[str, Any] = {
            "artifact_id": artifact_id,
            "stored_length": len(stored_full_script),
            "canonical_length": len(canonical_full_script),
            "mismatch_category": _classify_script_structure_mismatch(stored_full_script, canonical_full_script),
        }
        raise AppError(
            "SCRIPT_STRUCTURE_MISMATCH",
            "The current Script artifact is structurally inconsistent. Regenerate or resave the Script before continuing.",
            409,
            retryable=False,
            details=details,
        )
    return canonical_full_script


def _classify_script_structure_mismatch(stored: str, canonical: str) -> str:
    if stored.startswith(canonical):
        return "unmatched_suffix"
    if stored.endswith(canonical):
        return "unmatched_prefix"
    if len(stored) == len(canonical):
        return "order_or_punctuation_mismatch"
    if canonical and stored and canonical not in stored and stored not in canonical:
        return "general_mismatch"
    if stored in canonical:
        return "missing_section_content"
    return "order_or_punctuation_mismatch"


def _find_unique_quote(narration: str, quote: str, *, error_code: str, field: str) -> int:
    matches = [match.start() for match in re.finditer(re.escape(quote), narration)]
    if len(matches) != 1:
        raise AppError(
            error_code,
            "External Script quote resolution must be unique.",
            422,
            retryable=False,
            details={"field": field, "match_count": len(matches)},
        )
    return matches[0]


def _resolve_section_anchor(narration: str, item: dict[str, Any], *, error_code: str) -> tuple[int, int]:
    anchor = str(item.get("anchor_quote") or "")
    context = str(item.get("context_quote") or "")
    context_start = _find_unique_quote(narration, context, error_code=error_code, field="context_quote")
    local_matches = [match.start() for match in re.finditer(re.escape(anchor), context)]
    if len(local_matches) != 1:
        raise AppError(
            error_code,
            "External Script anchor_quote must occur exactly once inside context_quote.",
            422,
            retryable=False,
            details={"field": "anchor_quote", "match_count": len(local_matches)},
        )
    start = context_start + local_matches[0]
    end = start + len(anchor)
    if narration[start:end] != anchor:
        raise AppError(
            error_code,
            "External Script anchor_quote does not resolve to section narration.",
            422,
            retryable=False,
        )
    return start, end


def _resolve_spoken_overrides_and_delivery_cues(sections: list[dict[str, Any]]) -> None:
    section_ranges = build_script_section_ranges(sections)
    section_global_start = {item["section_id"]: int(item["start"]) for item in section_ranges}
    for section in sections:
        section_id = str(section["section_id"])
        narration = canonicalize_script_section_narration(section.get("narration", ""))
        global_start = section_global_start.get(section_id, 0)
        raw_overrides = section.get("spoken_overrides")
        raw_cues = section.get("delivery_cues")
        if not isinstance(raw_overrides, list) or not isinstance(raw_cues, list):
            raise AppError(
                "SCRIPT_SPOKEN_OVERRIDE_INVALID",
                "Every Script section must include spoken_overrides and delivery_cues arrays.",
                422,
                retryable=False,
            )

        resolved_overrides: list[dict[str, Any]] = []
        for item in raw_overrides:
            if not isinstance(item, dict):
                raise AppError("SCRIPT_SPOKEN_OVERRIDE_INVALID", "Spoken overrides must be objects.", 422, retryable=False)
            spoken_text = str(item.get("spoken_text") or "")
            if "<" in spoken_text or ">" in spoken_text:
                raise AppError(
                    "SCRIPT_SPOKEN_OVERRIDE_INVALID",
                    "spoken_text must be plain spoken text without SSML/provider markup.",
                    422,
                    retryable=False,
                )
            local_start, local_end = _resolve_section_anchor(
                narration,
                item,
                error_code="SCRIPT_SPOKEN_OVERRIDE_INVALID",
            )
            resolved_overrides.append({
                "anchor_quote": item["anchor_quote"],
                "context_quote": item["context_quote"],
                "spoken_text": spoken_text,
                "source_start": global_start + local_start,
                "source_end": global_start + local_end,
            })

        resolved_overrides.sort(key=lambda item: (item["source_start"], item["source_end"], item["spoken_text"]))
        for index, item in enumerate(resolved_overrides, start=1):
            item["id"] = f"sov_{section_id}_{index:03d}"
        for left, right in zip(resolved_overrides, resolved_overrides[1:]):
            if int(left["source_end"]) > int(right["source_start"]):
                raise AppError(
                    "SCRIPT_SPOKEN_OVERRIDE_OVERLAP",
                    "Resolved spoken overrides cannot overlap.",
                    422,
                    retryable=False,
                    details={"section_id": section_id},
                )

        for match in SENSITIVE_SPOKEN_TOKEN_RE.finditer(narration):
            offset = global_start + match.start()
            covering = [
                item
                for item in resolved_overrides
                if int(item["source_start"]) <= offset < int(item["source_end"])
            ]
            if len(covering) != 1:
                raise AppError(
                    "SCRIPT_SPOKEN_OVERRIDE_REQUIRED",
                    "Every ASCII digit and currency/percent symbol in narration must be covered by exactly one spoken_override.",
                    422,
                    retryable=False,
                    details={"section_id": section_id, "source_offset": offset, "token": match.group(0)},
                )

        resolved_cues: list[dict[str, Any]] = []
        for item in raw_cues:
            if not isinstance(item, dict):
                raise AppError("SCRIPT_DELIVERY_CUE_BOUNDARY_INVALID", "Delivery cues must be objects.", 422, retryable=False)
            local_start, local_end = _resolve_section_anchor(
                narration,
                item,
                error_code="SCRIPT_DELIVERY_CUE_BOUNDARY_INVALID",
            )
            source_offset = global_start + local_end
            if any(int(override["source_start"]) < source_offset < int(override["source_end"]) for override in resolved_overrides):
                raise AppError(
                    "SCRIPT_DELIVERY_CUE_BOUNDARY_INVALID",
                    "Delivery cue boundary cannot land strictly inside a spoken override span.",
                    422,
                    retryable=False,
                    details={"section_id": section_id, "source_offset": source_offset},
                )
            resolved_cues.append({
                "anchor_quote": item["anchor_quote"],
                "context_quote": item["context_quote"],
                "kind": "pause_after",
                "pause_strength": item["pause_strength"],
                "source_offset": source_offset,
            })

        resolved_cues.sort(key=lambda item: (item["source_offset"], item["anchor_quote"]))
        for index, item in enumerate(resolved_cues, start=1):
            item["id"] = f"dvc_{section_id}_{index:03d}"

        section["spoken_overrides"] = resolved_overrides
        section["delivery_cues"] = resolved_cues


class ChannelService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.channels = ChannelRepository(session)
        self.validator = PlatformContractValidator()

    def list_channels(self, *, page: int, page_size: int) -> tuple[list[Channel], int]:
        offset = (page - 1) * page_size
        return self.channels.list_all(offset=offset, limit=page_size), self.channels.count_all()

    def list_channel_cards(self, *, page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
        offset = (page - 1) * page_size
        rows = self.session.execute(
            select(
                Channel.channel_id,
                Channel.name,
                Channel.language,
                Channel.niche,
                Channel.audience,
                Channel.channel_dna_json.is_not(None).label("has_channel_dna"),
                Channel.created_at,
                Channel.updated_at,
            )
            .order_by(Channel.created_at.desc())
            .offset(offset)
            .limit(page_size)
        ).all()
        return [
            {
                "channel_id": row.channel_id,
                "name": row.name,
                "language": row.language,
                "niche": row.niche,
                "audience": row.audience,
                "has_channel_dna": bool(row.has_channel_dna),
                "channel_context": {
                    "name": row.name,
                    "language": row.language,
                    "niche": row.niche,
                    "audience": row.audience,
                },
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in rows
        ], self.channels.count_all()

    def get_channel_card(self, channel_id: str) -> dict[str, Any]:
        row = self.session.execute(
            select(
                Channel.channel_id,
                Channel.name,
                Channel.language,
                Channel.niche,
                Channel.audience,
                Channel.channel_dna_json.is_not(None).label("has_channel_dna"),
                Channel.created_at,
                Channel.updated_at,
            ).where(Channel.channel_id == channel_id)
        ).one_or_none()
        if row is None:
            raise AppError("CHANNEL_NOT_FOUND", f"Channel '{channel_id}' was not found.", 404, retryable=False)
        return {
            "channel_id": row.channel_id,
            "name": row.name,
            "language": row.language,
            "niche": row.niche,
            "audience": row.audience,
            "has_channel_dna": bool(row.has_channel_dna),
            "channel_context": {
                "name": row.name,
                "language": row.language,
                "niche": row.niche,
                "audience": row.audience,
            },
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def get_required(self, channel_id: str) -> Channel:
        channel = self.channels.get(channel_id)
        if channel is None:
            raise AppError("CHANNEL_NOT_FOUND", f"Channel '{channel_id}' was not found.", 404, retryable=False)
        return channel

    def create_channel(self, payload: dict[str, Any]) -> Channel:
        now = utc_now_iso()
        context = {
            "name": str(payload["name"]),
            "language": str(payload["language"]),
            "niche": str(payload["niche"]),
            "audience": str(payload["audience"]),
        }
        channel = Channel(
            channel_id=generate_id("channel"),
            name=context["name"],
            language=context["language"],
            niche=context["niche"],
            audience=context["audience"],
            channel_dna_json=None,
            channel_context_json=context,
            voice_profile_json=None,
            created_at=now,
            updated_at=now,
        )
        self.channels.create(channel)
        self.session.commit()
        self.session.refresh(channel)
        return channel

    def validate_channel_dna(self, channel_dna: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        validated = self.validator.validate("channel_dna.schema.json", channel_dna, error_code="CHANNEL_DNA_V3_INVALID")
        validated["narration_wpm"] = _require_narration_wpm_range(
            validated.get("narration_wpm"),
            error_code="CHANNEL_DNA_V3_INVALID",
            field_path="$.narration_wpm",
        )
        return validated, channel_context_from_dna(validated)

    def save_channel_dna(self, channel_id: str, channel_dna: dict[str, Any]) -> Channel:
        channel = self.session.scalar(
            select(Channel)
            .options(
                load_only(
                    Channel.channel_id,
                    Channel.name,
                    Channel.language,
                    Channel.niche,
                    Channel.audience,
                    Channel.created_at,
                    Channel.updated_at,
                )
            )
            .where(Channel.channel_id == channel_id)
        )
        if channel is None:
            raise AppError("CHANNEL_NOT_FOUND", f"Channel '{channel_id}' was not found.", 404, retryable=False)
        validated, context = self.validate_channel_dna(channel_dna)
        channel.channel_dna_json = validated
        channel.channel_context_json = context
        channel.name = context["name"]
        channel.language = context["language"]
        channel.niche = context["niche"]
        channel.audience = context["audience"]
        channel.updated_at = utc_now_iso()
        self.session.commit()
        self.session.refresh(channel)
        return channel

    def get_channel_dna(self, channel_id: str) -> tuple[dict[str, Any] | None, str | None]:
        row = self.session.execute(
            select(
                cast(Channel.channel_dna_json, Text).label("channel_dna_json"),
                Channel.updated_at,
            ).where(Channel.channel_id == channel_id)
        ).one_or_none()
        if row is None:
            raise AppError("CHANNEL_NOT_FOUND", f"Channel '{channel_id}' was not found.", 404, retryable=False)
        if row.channel_dna_json is None:
            return None, None
        try:
            channel_dna = json.loads(row.channel_dna_json)
        except json.JSONDecodeError as exc:
            raise AppError(
                "CHANNEL_DNA_STORED_INVALID",
                f"Stored Channel DNA for channel '{channel_id}' is invalid.",
                500,
                retryable=False,
                details={"channel_id": channel_id, "parse_error": exc.msg},
            ) from exc
        if not isinstance(channel_dna, dict):
            raise AppError(
                "CHANNEL_DNA_STORED_INVALID",
                f"Stored Channel DNA for channel '{channel_id}' is invalid.",
                500,
                retryable=False,
                details={"channel_id": channel_id, "stored_type": type(channel_dna).__name__},
            )
        try:
            validated, _ = self.validate_channel_dna(channel_dna)
        except AppError as exc:
            raise AppError(
                "CHANNEL_DNA_STORED_INVALID",
                f"Stored Channel DNA for channel '{channel_id}' is invalid.",
                500,
                retryable=False,
                details={"channel_id": channel_id, "validation_errors": exc.details.get("errors", [])},
            ) from exc
        return validated, row.updated_at

    def get_voice_profile(self, channel_id: str) -> tuple[dict[str, Any] | None, str | None]:
        channel = self.get_required(channel_id)
        profile = deepcopy(channel.voice_profile_json) if isinstance(channel.voice_profile_json, dict) else None
        return profile, channel.updated_at if profile is not None else None

    def save_voice_profile(self, channel_id: str, profile: dict[str, Any] | None) -> Channel:
        channel = self.get_required(channel_id)
        if profile is not None:
            from app.api.schemas import ChannelVoiceProfileModel

            profile = ChannelVoiceProfileModel.model_validate(profile).model_dump(mode="json")
        channel.voice_profile_json = profile
        channel.updated_at = utc_now_iso()
        self.session.commit()
        self.session.refresh(channel)
        return channel


class ProjectService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.channels = ChannelRepository(session)
        self.projects = ProjectRepository(session)

    def list_projects(self, *, page: int, page_size: int, channel_id: str | None = None) -> tuple[list[Project], int]:
        offset = (page - 1) * page_size
        return self.projects.list_all(channel_id=channel_id, offset=offset, limit=page_size), self.projects.count_all(channel_id=channel_id)

    def get_required(self, project_id: str) -> Project:
        project = self.projects.get(project_id)
        if project is None:
            raise AppError("PROJECT_NOT_FOUND", f"Project '{project_id}' was not found.", 404, retryable=False)
        return project

    def create_project(self, *, channel_id: str, name: str, description: str | None = None) -> Project:
        channel = self.channels.get(channel_id)
        if channel is None:
            raise AppError("CHANNEL_NOT_FOUND", f"Channel '{channel_id}' was not found.", 404, retryable=False)
        if not isinstance(channel.channel_dna_json, dict) or not isinstance(channel.channel_context_json, dict):
            raise AppError("CHANNEL_DNA_REQUIRED", "Import and validate Channel DNA V3 before creating a project.", 409, retryable=False)
        now = utc_now_iso()
        project = Project(
            project_id=generate_id("project"),
            channel_id=channel.channel_id,
            name=name,
            description=description,
            channel_context_json=channel.channel_context_json,
            channel_dna_json=channel.channel_dna_json,
            selected_topic_ideas_artifact_id=None,
            selected_topic_idea_id=None,
            topic_ideas_artifact_id=None,
            script_artifact_id=None,
            style_bible_artifact_id=None,
            scene_plan_artifact_id=None,
            visual_plan_artifact_id=None,
            prompt_bundle_artifact_id=None,
            style_bible_visual_reference_set_artifact_id=None,
            style_bible_visual_reference_approval_set_artifact_id=None,
            scene_asset_manifest_artifact_id=None,
            narration_audio_artifact_id=None,
            narration_word_timing_map_artifact_id=None,
            beat_timing_map_artifact_id=None,
            assembly_plan_artifact_id=None,
            subtitle_srt_artifact_id=None,
            subtitle_vtt_artifact_id=None,
            created_at=now,
            updated_at=now,
        )
        self.projects.create(project)
        self.session.commit()
        self.session.refresh(project)
        return project


class AuthoringService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.validator = PlatformContractValidator()
        self.settings = get_settings()
        self.artifacts = ArtifactService(session, ArtifactRepository(session), self.settings)
        self.loader = ProjectArtifactLoader(self.artifacts, self.validator)
        self.invalidation = ProjectPipelineInvalidationService(session)
        self.authoring_reservations = ExternalAuthoringReservationService(session)

    def get_project_required(self, project_id: str) -> Project:
        project = self.projects.get(project_id)
        if project is None:
            raise AppError("PROJECT_NOT_FOUND", f"Project '{project_id}' was not found.", 404, retryable=False)
        return project

    def get_topic_ideas(self, project_id: str) -> dict[str, Any]:
        project = self.get_project_required(project_id)
        artifact_id = project.topic_ideas_artifact_id
        if not artifact_id:
            return {"generated": False, "artifact_id": None, "artifact": None}
        artifact = self.loader.load_topic_ideas(project, artifact_id, require_current=True)
        return {"generated": True, "artifact_id": artifact_id, "artifact": artifact}

    def get_script(self, project_id: str) -> dict[str, Any]:
        project = self.get_project_required(project_id)
        artifact_id = project.script_artifact_id
        if not artifact_id:
            return {"generated": False, "artifact_id": None, "artifact": None}
        artifact = self.loader.load_script(project, artifact_id, require_current=True)
        return {"generated": True, "artifact_id": artifact_id, "artifact": artifact}

    @staticmethod
    def _build_topic_ideas_runtime_input(project: Project, user_input: dict[str, Any]) -> dict[str, Any]:
        channel_dna = project.channel_dna_json
        narration_identity = channel_dna.get("narration_identity") if isinstance(channel_dna, dict) else None
        if not isinstance(narration_identity, dict):
            raise AppError(
                "PROJECT_CHANNEL_DNA_NARRATION_IDENTITY_REQUIRED",
                "The Project Channel DNA snapshot must define narration_identity before Topic Ideas authoring.",
                409,
                retryable=False,
            )
        pacing_profiles = channel_dna.get("pacing_profiles") if isinstance(channel_dna, dict) else None
        if not isinstance(pacing_profiles, dict):
            raise AppError(
                "PROJECT_CHANNEL_DNA_PACING_PROFILES_REQUIRED",
                "The Project Channel DNA snapshot must define pacing_profiles before Topic Ideas authoring.",
                409,
                retryable=False,
            )
        return {
            "channel_context": deepcopy(project.channel_context_json),
            "narration_identity": deepcopy(narration_identity),
            "pacing_profiles": deepcopy(pacing_profiles),
            "user_input": deepcopy(user_input),
        }

    def _selected_topic_titles_for_channel(self, project: Project) -> list[str]:
        projects = list(
            self.session.scalars(
                select(Project)
                .where(Project.channel_id == project.channel_id)
                .order_by(Project.created_at.asc(), Project.project_id.asc())
            )
        )
        titles: list[str] = []
        seen: set[str] = set()
        for item in projects:
            if not item.selected_topic_ideas_artifact_id or not item.selected_topic_idea_id:
                continue
            if item.selected_topic_ideas_artifact_id != item.topic_ideas_artifact_id:
                raise AppError(
                    "SELECTED_TOPIC_STALE",
                    f"Project '{item.project_id}' has a selected topic from an outdated Topic Ideas artifact.",
                    409,
                    retryable=False,
                )
            artifact = self.loader.load_topic_ideas(
                item, item.selected_topic_ideas_artifact_id, require_current=True
            )
            idea = self._resolve_topic_idea_exactly_once(artifact, item.selected_topic_idea_id)
            title = str(idea.get("title") or "").strip()
            normalized = " ".join(title.casefold().split())
            if title and normalized not in seen:
                seen.add(normalized)
                titles.append(title)
        return titles

    def _topic_prompt_package(
        self,
        project: Project,
        visible_user_input: dict[str, Any],
    ) -> dict[str, Any]:
        clean_user_input = {
            "seed_topic": visible_user_input.get("seed_topic", ""),
            "topic_count": visible_user_input.get("topic_count", 10),
            "focus_keywords": deepcopy(visible_user_input.get("focus_keywords", [])),
            "focus": visible_user_input.get("focus", "balanced"),
            "avoid_topics": self._selected_topic_titles_for_channel(project),
        }
        self.validator.validate(
            "generate_topic_ideas_http_request.schema.json",
            {"user_input": clean_user_input},
            error_code="TOPIC_IDEAS_REQUEST_INVALID",
        )
        llm_input = self._build_topic_ideas_runtime_input(project, clean_user_input)
        self.validator.validate(
            "generate_topic_ideas_llm_input.schema.json",
            llm_input,
            error_code="TOPIC_IDEAS_LLM_INPUT_INVALID",
        )
        system_prompt, user_prompt = self._compile_prompt(
            "generate_topic_ideas", llm_input, include_output_contract=False
        )
        artifact_id = self.artifacts.preallocate_artifact_id()
        output_schema = deepcopy(self.validator.load_schema("topic_ideas_llm_output.schema.json"))
        output_schema["properties"]["artifact_id"]["const"] = artifact_id
        package = render_external_prompt(
            stage="topic_ideas",
            prompt_version="topic_ideas_external_v6",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_schema_name="topic_ideas_llm_output.schema.json",
            output_schema=output_schema,
            artifact_ids={"topic_ideas": artifact_id},
            output_filename="topic_ideas.json",
        )
        self.authoring_reservations.replace_pending(
            project_id=project.project_id,
            stage=TOPIC_STAGE,
            primary_artifact_id=artifact_id,
            paired_artifact_id=None,
            source={
                "channel_id": project.channel_id,
                "channel_context": deepcopy(project.channel_context_json),
                "user_input": deepcopy(clean_user_input),
                "previous_topic_ideas_artifact_id": project.topic_ideas_artifact_id,
                "prompt_package": deepcopy(package),
            },
        )
        return package

    def build_topic_ideas_prompt(self, project_id: str, user_input: dict[str, Any]) -> dict[str, Any]:
        return self._topic_prompt_package(self.get_project_required(project_id), user_input)

    def get_pending_topic_ideas_prompt(self, project_id: str) -> dict[str, Any]:
        project = self.get_project_required(project_id)
        reservation = self.authoring_reservations.pending_for(project_id=project_id, stage=TOPIC_STAGE)
        if reservation is None:
            return {"generated": False, "prompt_package": None, "request_input": None}
        source = self.authoring_reservations.source(reservation)
        if source.get("previous_topic_ideas_artifact_id") != project.topic_ideas_artifact_id:
            return {"generated": False, "prompt_package": None, "request_input": None}
        package = source.get("prompt_package")
        if not isinstance(package, dict):
            return {"generated": False, "prompt_package": None, "request_input": None}
        request_input = source.get("user_input")
        return {
            "generated": True,
            "prompt_package": deepcopy(package),
            "request_input": deepcopy(request_input) if isinstance(request_input, dict) else None,
        }

    def _validate_topic_import(
        self, project: Project, request_payload: dict[str, Any]
    ) -> tuple[dict[str, Any], Any, dict[str, Any]]:
        raw = parse_external_json(str(request_payload.get("result_json") or ""))
        artifact_id, paired_id = extract_reserved_artifact_ids(raw, stage=TOPIC_STAGE)
        reservation = self.authoring_reservations.require(
            project_id=project.project_id,
            stage=TOPIC_STAGE,
            primary_artifact_id=artifact_id,
            paired_artifact_id=paired_id,
        )
        source = self.authoring_reservations.source(reservation)
        require_source_values(
            expected=source,
            current={"previous_topic_ideas_artifact_id": project.topic_ideas_artifact_id},
            fields=["previous_topic_ideas_artifact_id"],
            message="Topic Ideas changed after this prompt was built.",
        )
        raw = self.validator.validate(
            "topic_ideas_llm_output.schema.json",
            raw,
            error_code="TOPIC_IDEAS_RAW_OUTPUT_INVALID",
        )
        artifact = deepcopy(raw)
        artifact.pop("artifact_id", None)
        for i, idea in enumerate(artifact["ideas"], start=1):
            idea["idea_id"] = f"idea_{i:03d}"
        llm_input = {
            "channel_context": deepcopy(source["channel_context"]),
            "user_input": deepcopy(source["user_input"]),
        }
        artifact = self._ensure_topic_ideas_lineage(artifact, llm_input)
        artifact = self.validator.validate(
            "topic_ideas.schema.json", artifact, error_code="TOPIC_IDEAS_OUTPUT_INVALID"
        )
        return artifact, reservation, llm_input

    def validate_topic_ideas_import(self, project_id: str, request_payload: dict[str, Any]) -> dict[str, Any]:
        project = self.get_project_required(project_id)
        artifact, _reservation, _input = self._validate_topic_import(project, request_payload)
        return validation_success(
            summary={
                "idea_count": len(artifact["ideas"]),
                "titles": [item["title"] for item in artifact["ideas"]],
            }
        )

    def import_topic_ideas(self, project_id: str, request_payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
        project = self.get_project_required(project_id)
        artifact, reservation, _input = self._validate_topic_import(project, request_payload)
        guard = TopicIdeasCommitGuard(previous_topic_ideas_artifact_id=project.topic_ideas_artifact_id)
        staged: StagedArtifact | None = None
        try:
            staged = self.artifacts.stage_artifact_revision(
                logical_id=f"project/{project.project_id}/topic_ideas",
                artifact_type=ArtifactType.JSON,
                artifact_role=ArtifactRole.TOPIC_IDEAS,
                filename="topic_ideas.json",
                content=json.dumps(artifact, ensure_ascii=False, indent=2).encode("utf-8"),
                artifact_id=reservation.primary_artifact_id,
                provider_source="external_chatgpt",
            )
            success = self.projects.compare_and_set_topic_ideas(
                project_id=project.project_id,
                new_topic_ideas_artifact_id=staged.artifact_id,
                guard=guard,
                updated_at=utc_now_iso(),
            )
            if not success:
                raise AppError(
                    "TOPIC_IDEAS_PUBLICATION_CONFLICT",
                    "The current Topic Ideas pointer changed while the import was being published.",
                    409,
                    retryable=False,
                )
            previous_current = self.artifacts.repository.get_current_by_logical_id(staged.logical_id)
            if previous_current is not None and previous_current.artifact_id != staged.artifact_id:
                previous_current.is_current = False
                previous_current.superseded_by_artifact_id = staged.artifact_id
            staged.artifact.is_current = True
            staged.artifact.superseded_by_artifact_id = None
            self.authoring_reservations.mark_consumed(reservation)
            self.session.flush()
            self.session.commit()
            return artifact, staged.artifact_id
        except Exception:
            rollback_safely(self.session, logger=logger)
            cleanup_staged_artifact_safely(staged, logger=logger)
            raise

    def _validate_project_selected_topic_state(self, project: Project) -> None:
        if bool(project.selected_topic_ideas_artifact_id) != bool(project.selected_topic_idea_id):
            raise AppError("PROJECT_SELECTED_TOPIC_STATE_INVALID", "Project selected topic state is partially null.", 409, retryable=False)

    def _resolve_topic_idea_exactly_once(self, artifact: dict[str, Any], idea_id: str) -> dict[str, Any]:
        ideas = artifact.get("ideas", [])
        matched = [idea for idea in ideas if idea.get("idea_id") == idea_id]
        if not matched:
            raise AppError("TOPIC_IDEA_NOT_FOUND", f"Topic idea '{idea_id}' not found in artifact.", 409, retryable=False)
        if len(matched) > 1:
            raise AppError("TOPIC_IDEA_ID_NOT_UNIQUE", f"Topic idea '{idea_id}' is not unique in artifact.", 409, retryable=False)
        return matched[0]

    def select_topic(self, project_id: str, request_payload: dict[str, Any]) -> Project:
        project = self.get_project_required(project_id)
        self._validate_project_selected_topic_state(project)
        self.validator.validate("select_topic_http_request.schema.json", request_payload, error_code="SELECT_TOPIC_REQUEST_INVALID")

        topic_ideas_artifact_id = request_payload["topic_ideas_artifact_id"]
        idea_id = request_payload["idea_id"]

        if not project.topic_ideas_artifact_id:
            raise AppError("TOPIC_IDEAS_NOT_GENERATED", "Project does not have generated topic ideas.", 409, retryable=False)
        if topic_ideas_artifact_id != project.topic_ideas_artifact_id:
            raise AppError("TOPIC_IDEAS_ARTIFACT_NOT_CURRENT", "The requested artifact is not the current topic ideas artifact.", 409, retryable=False)

        artifact = self.loader.load_topic_ideas(project, topic_ideas_artifact_id, require_current=True)
        self._resolve_topic_idea_exactly_once(artifact, idea_id)

        if project.selected_topic_ideas_artifact_id == topic_ideas_artifact_id and project.selected_topic_idea_id == idea_id:
            return project

        project.selected_topic_ideas_artifact_id = topic_ideas_artifact_id
        project.selected_topic_idea_id = idea_id
        self.invalidation.invalidate_from_topic_selection(project)
        project.updated_at = utc_now_iso()
        self.session.commit()
        return project


    def _normalize_script_output(
        self,
        raw_output: dict[str, Any],
        *,
        runtime_input: dict[str, Any],
        topic_idea: dict[str, Any],
        source_topic_ideas_artifact_id: str,
    ) -> dict[str, Any]:
        normalized = deepcopy(raw_output)
        authoring_constraints = runtime_input["authoring_constraints"]
        sections = normalized.get("script_sections")
        if not isinstance(sections, list):
            raise AppError(
                "SCRIPT_SECTION_PLAN_INVALID",
                "Generated Script must contain an ordered script_sections array.",
                422,
                retryable=False,
            )
        if authoring_constraints["section_count_mode"] == SECTION_COUNT_MODE_LLM_DECIDES:
            minimum = authoring_constraints["minimum_section_count"]
            maximum = authoring_constraints["maximum_section_count"]
            if not minimum <= len(sections) <= maximum:
                raise AppError(
                    "SCRIPT_SECTION_PLAN_INVALID",
                    "Generated Script section count is outside the allowed LLM-decides range.",
                    422,
                    retryable=False,
                    details={
                        "minimum_section_count": minimum,
                        "maximum_section_count": maximum,
                        "actual_section_count": len(sections),
                    },
                )
            roles = [
                str(section.get("section_role") or "").strip()
                for section in sections
                if isinstance(section, dict)
            ]
            if len(roles) != len(sections) or any(not role for role in roles):
                raise AppError(
                    "SCRIPT_SECTION_PLAN_INVALID",
                    "Every generated Script section must contain a non-empty section_role.",
                    422,
                    retryable=False,
                )
        else:
            expected_plan = authoring_constraints["section_plan"]
            if len(sections) != len(expected_plan):
                raise AppError(
                    "SCRIPT_SECTION_PLAN_INVALID",
                    "Generated Script must contain exactly one section for every backend-owned section-plan item.",
                    422,
                    retryable=False,
                    details={
                        "expected_section_count": len(expected_plan),
                        "actual_section_count": len(sections),
                    },
                )
            for index, (section, plan_item) in enumerate(zip(sections, expected_plan), start=1):
                if not isinstance(section, dict) or section.get("section_role") != plan_item["section_role"]:
                    raise AppError(
                        "SCRIPT_SECTION_PLAN_INVALID",
                        "Generated Script section roles must match the backend-owned section plan in exact order.",
                        422,
                        retryable=False,
                        details={"section_index": index, "expected_role": plan_item["section_role"]},
                    )
            roles = [str(item["section_role"]) for item in expected_plan]
        narration_wpm_range = runtime_input["user_input"]["narration_wpm"]
        minimum_wpm = int(narration_wpm_range["min"])
        maximum_wpm = int(narration_wpm_range["max"])
        for index, section in enumerate(sections, start=1):
            if not isinstance(section, dict):
                raise AppError(
                    "SCRIPT_SECTION_PLAN_INVALID",
                    "Every generated Script section must be an object.",
                    422,
                    retryable=False,
                    details={"section_index": index},
                )
            planned_wpm = section.get("planned_narration_wpm")
            if (
                isinstance(planned_wpm, bool)
                or not isinstance(planned_wpm, int)
                or not minimum_wpm <= planned_wpm <= maximum_wpm
            ):
                raise AppError(
                    "SCRIPT_SECTION_WPM_OUT_OF_RANGE",
                    "Every generated Script section must choose planned_narration_wpm inside the supplied narration WPM range.",
                    422,
                    retryable=False,
                    details={
                        "section_index": index,
                        "planned_narration_wpm": planned_wpm,
                        "minimum_wpm": minimum_wpm,
                        "maximum_wpm": maximum_wpm,
                    },
                )
            section["section_id"] = f"sec_{index:03d}"
        _resolve_spoken_overrides_and_delivery_cues(sections)
        normalized["video_pacing_profile"] = deepcopy(runtime_input["video_pacing_profile"])
        normalized = self._ensure_script_lineage(normalized, runtime_input, topic_idea, source_topic_ideas_artifact_id)
        normalized = self._normalize_script_full_script(normalized, require_existing_match=False)
        normalized = self._finalize_script_metrics(normalized)
        normalized["computed_constraints"] = build_computed_constraints(
            runtime_input["user_input"]["target_duration_minutes"],
            normalized["planned_average_wpm"],
            section_count_mode=authoring_constraints["section_count_mode"],
            section_roles=roles,
            planned_narration_wpm_range=runtime_input["user_input"]["narration_wpm"],
        )
        return self.validator.validate("script.schema.json", normalized, error_code="SCRIPT_OUTPUT_INVALID")


    @staticmethod
    def _build_script_runtime_input(project: Project, topic_idea: dict[str, Any], user_input: dict[str, Any]) -> dict[str, Any]:
        normalized_user_input = deepcopy(user_input)
        normalized_user_input["narration_wpm"] = _require_narration_wpm_range(
            normalized_user_input.get("narration_wpm"),
            error_code="SCRIPT_REQUEST_INVALID",
            field_path="$.user_input.narration_wpm",
        )
        allowlist = [
            "title",
            "core_question",
            "viewer_hook",
            "topic_angle",
            "tone",
            "content_tags",
            "risk_notes",
            "pov_style",
            "avoid",
            "recommended_pacing_mode",
            "pacing_mode_reason",
        ]
        stripped_topic_idea = {k: topic_idea[k] for k in allowlist if k in topic_idea}
        channel_dna = project.channel_dna_json
        narration_identity = channel_dna.get("narration_identity") if isinstance(channel_dna, dict) else None
        if not isinstance(narration_identity, dict):
            raise AppError(
                "PROJECT_CHANNEL_DNA_NARRATION_IDENTITY_REQUIRED",
                "The Project Channel DNA does not contain a valid narration identity.",
                409,
                retryable=False,
            )
        pacing_profiles = channel_dna.get("pacing_profiles") if isinstance(channel_dna, dict) else None
        try:
            pacing_profile = resolve_video_pacing_profile(pacing_profiles, user_input["pacing_mode"])
        except VideoPacingError as exc:
            raise AppError(
                "PROJECT_CHANNEL_DNA_PACING_PROFILES_INVALID",
                "The Project Channel DNA snapshot does not contain valid pacing_profiles.",
                409,
                retryable=False,
            ) from exc
        authoring_constraints = build_authoring_constraints(
            user_input["target_duration_minutes"],
            section_count_mode=user_input["section_count_mode"],
        )
        # user_input.narration_wpm is the only model-facing WPM envelope.
        # Keep the broad backend safety range internal so the model sees no second authority.
        authoring_constraints.pop("planned_narration_wpm_range", None)
        return {
            "channel_context": project.channel_context_json,
            "narration_identity": deepcopy(narration_identity),
            "topic_idea": stripped_topic_idea,
            "user_input": deepcopy(user_input),
            "video_pacing_profile": pacing_profile,
            "authoring_constraints": authoring_constraints,
        }



    def _compile_prompt(
        self,
        prompt_name: str,
        payload: dict[str, Any],
        *,
        include_output_contract: bool = True,
    ) -> tuple[str, str]:
        prompt_dir = self.settings.root_dir / "contracts" / "prompts" / "platform" / prompt_name
        system_prompt = (prompt_dir / "system.md").read_text(encoding="utf-8")
        user_template = (prompt_dir / "user.md").read_text(encoding="utf-8")
        input_json = json.dumps(payload, ensure_ascii=False, indent=2)
        rendered_user_prompt = user_template.replace("{{input_json}}", input_json)
        if not include_output_contract:
            return system_prompt, rendered_user_prompt
        output_contract = (prompt_dir / "output_contract.md").read_text(encoding="utf-8")
        return system_prompt, rendered_user_prompt + "\n\nOutput contract:\n" + output_contract

    @staticmethod
    def _ensure_topic_ideas_lineage(artifact: dict[str, Any], request_payload: dict[str, Any]) -> dict[str, Any]:
        artifact["artifact_type"] = "topic_ideas"
        artifact["schema_version"] = "1.0"
        artifact["channel_context"] = {"name": request_payload["channel_context"]["name"], "language": request_payload["channel_context"]["language"]}
        artifact["source_user_input"] = request_payload["user_input"]
        return artifact

    @staticmethod
    def _ensure_script_lineage(artifact: dict[str, Any], runtime_input: dict[str, Any], topic_idea: dict[str, Any], source_topic_ideas_artifact_id: str) -> dict[str, Any]:
        artifact["artifact_type"] = "script"
        artifact["schema_version"] = "1.0"
        artifact["channel_context"] = {"name": runtime_input["channel_context"]["name"], "language": runtime_input["channel_context"]["language"]}
        artifact["source_topic_ideas_artifact_id"] = source_topic_ideas_artifact_id

        source_idea_fields = [
            "idea_id",
            "title",
            "core_question",
            "viewer_hook",
            "topic_angle",
            "tone",
            "content_tags",
            "risk_notes",
            "pov_style",
            "avoid",
            "recommended_pacing_mode",
            "pacing_mode_reason",
        ]
        source_idea = {
            key: topic_idea[key] for key in source_idea_fields if key in topic_idea
        }
        artifact["source_topic_idea"] = source_idea

        artifact["source_user_input"] = runtime_input["user_input"]
        return artifact

    @classmethod
    def _finalize_script_metrics(cls, artifact: dict[str, Any]) -> dict[str, Any]:
        try:
            sections = artifact.get("script_sections")
            if not isinstance(sections, list):
                raise ScriptTimingError("script_sections must be an array")
            canonical_full_script = build_full_script_from_sections(sections)
            section_word_counts = build_script_section_word_counts(sections)
            actual_word_count = count_narration_words(canonical_full_script)
            duration = artifact.get("source_user_input", {}).get("target_duration_minutes")
            diagnostics = build_runtime_diagnostics(
                target_duration_minutes=duration,
                script_sections=sections,
            )
        except ScriptTimingError as exc:
            raise AppError(
                "SCRIPT_OUTPUT_INVALID",
                "Script narration could not be normalized into canonical backend-owned integrity and runtime diagnostics.",
                422,
                retryable=False,
                details={"cause": "script_runtime_diagnostics_invalid"},
            ) from exc
        artifact["full_script"] = canonical_full_script
        artifact["planned_average_wpm"] = diagnostics.planned_average_wpm
        artifact["section_word_counts"] = section_word_counts
        artifact["word_count"] = actual_word_count
        artifact["runtime_diagnostics"] = diagnostics.to_dict()
        return artifact

    @staticmethod
    def _normalize_script_full_script(artifact: dict[str, Any], *, require_existing_match: bool = True) -> dict[str, Any]:
        try:
            canonical_full_script = build_full_script_from_sections(artifact.get("script_sections", []))
        except ScriptTimingError as exc:
            raise AppError(
                "SCRIPT_FULL_SCRIPT_STRUCTURE_MISMATCH",
                "Generated Script sections must contain ordered, non-empty narration.",
                422,
                retryable=False,
                details={"mismatch_category": "invalid_section_narration"},
            ) from exc
        stored_full_script = str(artifact.get("full_script", "")).replace("\r\n", "\n").replace("\r", "\n")
        if require_existing_match and stored_full_script != canonical_full_script:
            raise AppError(
                "SCRIPT_FULL_SCRIPT_STRUCTURE_MISMATCH",
                "Generated Script full_script must exactly match ordered section narration.",
                422,
                retryable=False,
                details={
                    "stored_length": len(stored_full_script),
                    "canonical_length": len(canonical_full_script),
                    "mismatch_category": _classify_script_structure_mismatch(stored_full_script, canonical_full_script),
                },
            )
        artifact["full_script"] = canonical_full_script
        return artifact


    @staticmethod
    def _build_full_script_from_sections(script_sections: list[dict[str, Any]]) -> str:
        return build_full_script_from_sections(script_sections)

    @staticmethod
    def _count_words(text: str) -> int:
        return count_narration_words(text)
