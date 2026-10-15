from tools.audit_voice_first_legacy import _classification


def test_new_history_agent_and_skills_are_documentation_not_runtime() -> None:
    assert _classification("AGENTS.md", "kokoro") == "DOCUMENTATION_OR_SPEC"
    assert _classification(".agents/skills/voice-alignment/SKILL.md", "kokoro") == "DOCUMENTATION_OR_SPEC"


def test_active_voice_lineage_remains_forbidden() -> None:
    assert (
        _classification("app/modules/v4/voice_service.py", "source_scene_plan_revision_id")
        == "ACTIVE_VOICE_LINEAGE_FORBIDDEN"
    )
