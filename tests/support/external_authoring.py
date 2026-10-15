from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from sqlalchemy.orm import Session

from app.modules.platform.services import AuthoringService, ChannelService, ProjectService
from tests.support.fixtures import sample_channel_dna, sample_script_artifact


def create_channel_project(session: Session, *, project_name: str = "External Authoring Project") -> tuple[str, str]:
    channel = ChannelService(session).create_channel(
        {
            "name": "External Authoring Channel",
            "language": "English",
            "niche": "education",
            "audience": "curious viewers",
        }
    )
    ChannelService(session).save_channel_dna(channel.channel_id, sample_channel_dna())
    project = ProjectService(session).create_project(
        channel_id=channel.channel_id,
        name=project_name,
    )
    return channel.channel_id, project.project_id


def create_project_in_channel(session: Session, channel_id: str, *, name: str) -> str:
    return ProjectService(session).create_project(channel_id=channel_id, name=name).project_id


def raw_topic_output(*titles: str) -> dict[str, Any]:
    return {
        "ideas": [
            {
                "title": title,
                "core_question": f"What makes {title.lower()} worth understanding?",
                "viewer_hook": f"The familiar story of {title.lower()} hides a stranger question.",
                "topic_angle": f"Explain {title.lower()} through a concrete human experience.",
                "tone": "curious and grounded",
                "pov_style": "second-person experiential narration with measured third-person context",
                "avoid": ["unsupported certainty", "generic motivational framing"],
                "ctr_score": 8,
                "difficulty": "medium",
                "content_tags": ["education", "human experience"],
                "risk_notes": [],
                "recommended_pacing_mode": "medium",
                "pacing_mode_reason": "Balanced explanatory progression with regular visual-state changes.",
            }
            for title in titles
        ]
    }


def import_topics(
    session: Session,
    project_id: str,
    *,
    titles: list[str],
    seed_topic: str = "human experience",
) -> tuple[dict[str, Any], str]:
    service = AuthoringService(session)
    package = service.build_topic_ideas_prompt(
        project_id,
        {
            "seed_topic": seed_topic,
            "topic_count": len(titles),
            "focus_keywords": [],
            "focus": "balanced",
        },
    )
    return service.import_topic_ideas(
        project_id,
        external_import_payload(package, raw_topic_output(*titles)),
    )


def import_and_select_topic(
    session: Session,
    project_id: str,
    *,
    title: str = "Why Rest Feels Unsafe After Dark",
) -> tuple[dict[str, Any], str]:
    artifact, artifact_id = import_topics(session, project_id, titles=[title], seed_topic=title)
    AuthoringService(session).select_topic(
        project_id,
        {"topic_ideas_artifact_id": artifact_id, "idea_id": "idea_001"},
    )
    return artifact, artifact_id


def combined_script_analysis_output() -> dict[str, Any]:
    canonical = sample_script_artifact()
    raw_script = {
        "title": canonical["title"],
        "hook": canonical["hook"],
        "script_sections": [
            {
                "section_title": section["section_title"],
                "section_role": section["section_role"],
                "planned_narration_wpm": section["planned_narration_wpm"],
                "narration": section["narration"],
                "spoken_overrides": deepcopy(section.get("spoken_overrides", [])),
                "delivery_cues": deepcopy(section.get("delivery_cues", [])),
            }
            for section in canonical["script_sections"]
        ],
    }
    return {
        "script": raw_script,
        "analysis": {
            "section_guidance": [
                {"section_id": f"sec_{index:03d}", "pacing_density": "high" if index == 1 else "medium"}
                for index in range(1, len(raw_script["script_sections"]) + 1)
            ],
            "narrative_markers": [],
            "continuity_catalog": {
                "characters": [],
                "locations": [],
                "recurring_props": [],
                "recurring_visual_subjects": [],
                "visual_motifs": [],
            },
            "visual_context_catalog": [
                {
                    "label": "Complete authored narrative",
                    "mode": "story_world",
                    "timeframe": None,
                    "setting": None,
                    "constraints": [],
                    "presence_ranges": [
                        {
                            "section_id": f"sec_{index:03d}",
                            "start_quote": section["narration"],
                            "end_quote": section["narration"],
                        }
                        for index, section in enumerate(raw_script["script_sections"], start=1)
                    ],
                }
            ],
        },
    }


def external_import_payload(package: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(result)
    analysis = payload.get("analysis")
    script = payload.get("script")
    if isinstance(analysis, dict) and isinstance(script, dict):
        catalog = analysis.get("visual_context_catalog")
        sections = script.get("script_sections")
        if (
            isinstance(catalog, list)
            and len(catalog) == 1
            and catalog[0].get("label") == "Complete authored narrative"
            and isinstance(sections, list)
        ):
            catalog[0]["presence_ranges"] = [
                {
                    "section_id": f"sec_{index:03d}",
                    "start_quote": section["narration"],
                    "end_quote": section["narration"],
                }
                for index, section in enumerate(sections, start=1)
            ]
    artifact_ids = package["artifact_ids"]
    if "topic_ideas" in artifact_ids:
        payload["artifact_id"] = artifact_ids["topic_ideas"]
    elif "script" in artifact_ids and "analysis" in artifact_ids:
        payload["artifact_ids"] = {
            "script": artifact_ids["script"],
            "analysis": artifact_ids["analysis"],
        }
    elif "scene_plan" in artifact_ids and "visual_plan" in artifact_ids:
        payload["artifact_ids"] = {
            "scene_plan": artifact_ids["scene_plan"],
            "visual_plan": artifact_ids["visual_plan"],
        }
    else:
        raise AssertionError(f"Unsupported prompt Artifact IDs: {artifact_ids}")
    return {"result_json": json.dumps(payload, ensure_ascii=False)}
