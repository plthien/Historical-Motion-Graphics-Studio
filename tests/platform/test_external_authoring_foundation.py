from __future__ import annotations

import pytest

from app.api.errors import AppError
from app.core.external_authoring import extract_reserved_artifact_ids, parse_external_json, render_external_prompt


def test_external_json_parser_rejects_duplicate_keys() -> None:
    with pytest.raises(AppError) as exc_info:
        parse_external_json('{"script": {}, "script": {}}')
    assert exc_info.value.error_code == "EXTERNAL_RESULT_DUPLICATE_KEY"


def test_external_json_parser_rejects_non_object_root() -> None:
    with pytest.raises(AppError) as exc_info:
        parse_external_json('[1, 2, 3]')
    assert exc_info.value.error_code == "EXTERNAL_RESULT_ROOT_INVALID"


def test_prompt_contains_reserved_artifact_ids_and_json_file_delivery() -> None:
    artifact_id = "artifact_aaaaaaaaaaaa"
    package = render_external_prompt(
        stage="topic_ideas",
        prompt_version="v2",
        system_prompt="System",
        user_prompt="User",
        output_schema_name="output.schema.json",
        output_schema={"type": "object"},
        artifact_ids={"topic_ideas": artifact_id},
        output_filename="topic_ideas.json",
    )
    assert package["artifact_ids"] == {"topic_ideas": artifact_id}
    assert artifact_id in package["prompt_text"]
    assert "Do not modify, regenerate, omit, or replace" in package["prompt_text"]
    assert "Create and attach/export a UTF-8 JSON file named `topic_ideas.json`" in package["prompt_text"]


def test_reserved_artifact_identity_is_required_in_imported_json() -> None:
    with pytest.raises(AppError) as exc_info:
        extract_reserved_artifact_ids({"ideas": []}, stage="topic_ideas")
    assert exc_info.value.error_code == "AUTHORING_ARTIFACT_ID_MISSING"
