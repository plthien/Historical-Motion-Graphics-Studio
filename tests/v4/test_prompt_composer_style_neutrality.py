from __future__ import annotations

import app.modules.v4.prompt_composer as prompt_composer


def test_location_reference_authority_uses_style_neutral_vocabulary() -> None:
    prompt = prompt_composer.compile_reference_authority(
        "location",
        "loc_001",
        {"label": "Detailed Industrial Hall"},
    )

    assert "stable location-identity reference" in prompt
    assert "render them using the current global visual style" in prompt
    assert "do not inherit incompatible reference polish" in prompt
    assert "drawing language" not in prompt
    assert "material simplicity" not in prompt


def test_unreferenced_character_style_preserves_approved_visual_language_without_simplification(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        prompt_composer,
        "compile_visual_identity_module",
        lambda _snapshot: "Approved style: high-detail naturalistic rendering.",
    )
    beat = {
        "selected_reference_bindings": [],
        "representation_family": "character_environment_lived",
        "selected_character_refs": ["char_001"],
        "selected_recurring_visual_subject_refs": [],
    }

    style = prompt_composer._style_block({}, beat, {})

    assert "high-detail naturalistic rendering" in style
    assert "same approved character visual language" in style
    assert "simplified character language" not in style
