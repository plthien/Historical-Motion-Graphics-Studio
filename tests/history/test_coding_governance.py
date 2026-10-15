from pathlib import Path

from tools.check_history_governance import ROOT, audit


def test_history_only_governance_present() -> None:
    assert audit(ROOT) == []


def test_governance_fails_if_obsolete_coding_rules_return(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "32_CODING_RULES.md").write_text("old image-per-beat rules\n")
    errors = audit(tmp_path)
    assert any("32_CODING_RULES.md" in error for error in errors)


def test_governance_fails_if_manifest_generator_returns(tmp_path: Path) -> None:
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/package_manifest.py").write_text("old generator\n")
    errors = audit(tmp_path)
    assert any("tools/package_manifest.py" in error for error in errors)
