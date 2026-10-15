from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.modules.platform.services import build_full_script_from_sections
from app.modules.platform.video_pacing import resolve_video_pacing_profile
from app.modules.v4.script_word_budget import (
    build_computed_constraints,
    build_script_section_word_counts,
    build_runtime_diagnostics,
    count_narration_words,
)

SECTION_1 = (
    "You turn off the light, the room goes soft and quiet. "
    "The door is locked. The blanket is warm. A refrigerator hums somewhere far away."
)
SECTION_2 = (
    "But for most of human life, rest was never just rest. "
    "The house is finally still, but some older part of you seems to stay standing."
)
SECTION_3 = (
    "Sleep is not a landing place but a small surrender. "
    "The body learns, slowly, that the quiet can hold."
)


def sample_pacing_profiles() -> dict[str, Any]:
    return {
        "fast": {
            "high": {"preferred_hold_seconds": {"min": 2.5, "max": 4.0}},
            "medium": {"preferred_hold_seconds": {"min": 4.0, "max": 6.0}},
            "low": {"preferred_hold_seconds": {"min": 6.0, "max": 8.0}},
        },
        "medium": {
            "high": {"preferred_hold_seconds": {"min": 3.5, "max": 5.5}},
            "medium": {"preferred_hold_seconds": {"min": 5.5, "max": 8.0}},
            "low": {"preferred_hold_seconds": {"min": 8.0, "max": 11.0}},
        },
        "slow": {
            "high": {"preferred_hold_seconds": {"min": 5.0, "max": 7.0}},
            "medium": {"preferred_hold_seconds": {"min": 7.0, "max": 10.0}},
            "low": {"preferred_hold_seconds": {"min": 10.0, "max": 14.0}},
        },
    }


def sample_script_artifact() -> dict[str, Any]:
    section_wpm = 155
    filler_count = max(0, 155 - count_narration_words(f"{SECTION_1} {SECTION_2} {SECTION_3}"))
    sections = [
        {"section_id": "sec_001", "section_title": "The Quiet Room", "section_role": "hook", "planned_narration_wpm": section_wpm, "narration": SECTION_1, "spoken_overrides": [], "delivery_cues": []},
        {"section_id": "sec_002", "section_title": "The Ancient Guard", "section_role": "development", "planned_narration_wpm": section_wpm, "narration": SECTION_2, "spoken_overrides": [], "delivery_cues": []},
        {"section_id": "sec_003", "section_title": "The Small Surrender", "section_role": "resolution", "planned_narration_wpm": section_wpm, "narration": SECTION_3 + " " + " ".join("reflection" for _index in range(1, filler_count + 1)), "spoken_overrides": [], "delivery_cues": []},
    ]
    full_script = build_full_script_from_sections(sections)
    actual = count_narration_words(full_script)
    diagnostics = build_runtime_diagnostics(target_duration_minutes=1, script_sections=sections)
    return {
        "artifact_type": "script",
        "schema_version": "1.0",
        "channel_context": {"name": "Zenn", "language": "English"},
        "source_topic_ideas_artifact_id": "artifact_aaaaaaaaaaaa",
        "source_topic_idea": {
            "idea_id": "idea_001", "title": "Why Rest Feels Unsafe After Dark",
            "core_question": "Why can a safe room still make the body feel alert at night?",
            "viewer_hook": "You turn off the light, but your body starts listening harder.",
            "topic_angle": "modern nighttime unease connected to ancient vulnerability",
            "tone": "calm", "pov_style": "mixed", "avoid": ["unsupported certainty"],
            "content_tags": ["psychology", "sleep"], "risk_notes": [],
            "recommended_pacing_mode": "medium",
            "pacing_mode_reason": "Balanced explanatory progression with regular visual-state changes.",
        },
        "source_user_input": {"target_duration_minutes": 1, "pov_style": "mixed", "avoid": [], "section_count_mode": "backend_plan", "pacing_mode": "medium", "narration_wpm": {"min": 145, "max": 165}},
        "video_pacing_profile": resolve_video_pacing_profile(sample_pacing_profiles(), "medium"),
        "planned_average_wpm": diagnostics.planned_average_wpm,
        "computed_constraints": build_computed_constraints(1, diagnostics.planned_average_wpm, planned_narration_wpm_range={"min": 145, "max": 165}),
        "title": "Why Rest Feels Unsafe After Dark",
        "hook": "You turn off the light, but your body starts listening harder.",
        "script_sections": sections,
        "full_script": full_script,
        "section_word_counts": build_script_section_word_counts(sections),
        "word_count": actual,
        "runtime_diagnostics": diagnostics.to_dict(),
    }


def sample_channel_dna() -> dict[str, Any]:
    return {
        "schema_version": "3.0",
        "channel": {"name": "Zenn", "language": "English", "niche": "Psychology", "audience": "Curious viewers"},
        "narration_wpm": {"min": 145, "max": 180},
        "pacing_profiles": sample_pacing_profiles(),
        "narration_identity": {
            "voice": ["clear evidence-led narrator"],
            "delivery": ["match the channel intent without imposing a universal creative style"],
            "explanation_style": ["translate difficult ideas into concrete viewer-understandable consequences"],
            "tone_boundaries": ["do not fabricate facts for stylistic effect"],
            "avoid": ["style drift away from the supplied channel intent"],
        },
        "visual_identity": {
            "medium": ["rough hand-drawn 2D illustration"], "linework": ["uneven black outlines"],
            "rendering": ["flat shapes"], "palette": ["muted colors"],
            "shape_language": ["simple rounded silhouettes"], "texture": ["rough digital texture"],
            "editorial_language": ["switch representation mode when another honest visual form communicates the locked meaning better"],
        },
        "entity_styles": {
            "character": {"appearance": ["simple circular heads"], "avoid": ["realistic anatomy"]},
            "location": {"appearance": ["minimal grounded backgrounds"], "avoid": ["cluttered interiors"]},
            "prop": {"appearance": ["simple recognizable silhouettes"], "avoid": ["real logos"]},
            "recurring_visual_subject": {"appearance": ["consistent reusable visual identity"], "avoid": ["story-specific action"]},
        },
        "global_negative_constraints": ["readable text", "watermark"],
    }


class FakeQueue:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def enqueue(self, function: str, payload: dict[str, Any], **kwargs: Any) -> None:
        self.calls.append({"function": function, "payload": deepcopy(payload), **deepcopy(kwargs)})
        if self.error is not None:
            raise self.error
