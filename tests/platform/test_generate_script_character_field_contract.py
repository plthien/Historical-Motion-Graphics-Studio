from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
USER_PROMPT = ROOT / "contracts" / "prompts" / "platform" / "generate_script_and_analysis" / "user.md"


def test_character_analysis_fields_have_non_overlapping_authority() -> None:
    text = USER_PROMPT.read_text(encoding="utf-8")

    assert "compact **WHO/WHEN** identity card" in text
    assert "three to seven short, atomic, directly visible, stable facts" in text
    assert "internal continuity metadata only" in text
    assert "not intended to be copied as a separate block into the Visual Reference generation prompt" in text
    assert "`narrative_role` owns story function" in text
    assert "Do not encode rendering-language constraints" in text
    assert "do not use section IDs merely to express scope already represented by `presence_ranges`" in text
    assert "Do not repeat a channel-wide visual baseline in every character" in text


def test_character_analysis_description_and_traits_reject_style_and_scene_content() -> None:
    text = USER_PROMPT.read_text(encoding="utf-8")

    assert "Do **not** include visual style/rendering terms" in text
    assert "section IDs or section names" in text
    assert "temporary actions, scene summaries, the character arc, or narrative role" in text
    assert "Do **not** include actions, temporary handheld items, story function" in text
    assert "style/rendering instructions, negative constraints" in text
