"""Regression tests for P0 frozen-SHA inventory; no old YTB runtime is loaded."""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
TOOL=ROOT/"tools/history_p0_source_audit.py"
SHA="0119572d651cb2b1902aea21b6267cca9809a940"
def run(*args):
    return subprocess.run([sys.executable,str(TOOL),*args],cwd=ROOT,text=True,capture_output=True)
def test_ledger_includes_launcher_root_dependencies_and_complete_donor_coverage():
    j=json.loads((ROOT/"docs/history/source-ownership-ledger.json").read_text(encoding="utf-8"))
    names={entry["path"] for entry in j["entries"]}
    assert j["donorSha"]==SHA
    for p in ("start.bat","requirements.txt","package.json","package-lock.json","alembic.ini",
              ".gitignore","tsconfig.json","vite.config.ts","src/App.tsx","app/main.py"):
        assert p in names,p
    donor=set(subprocess.check_output(["git","ls-tree","-r","--name-only",SHA],cwd=ROOT,text=True).splitlines())
    assert names<=donor
    assert not [p for p in donor if p.startswith(("app/","src/","tests/","contracts/","alembic/","scripts/","tools/",".github/","kokoro/")) and p not in names]
    assert "runtime/dynamic" in j["completeness"]["dependencyGraph"]
def test_twice_identical_and_checked_in_output(tmp_path):
    first=tmp_path/"first.json"
    second=tmp_path/"second.json"
    for p in (first,second):
        r=run("--output",str(p))
        assert r.returncode==0,r.stderr
    assert first.read_bytes()==second.read_bytes()
    assert first.read_bytes()==(ROOT/"docs/history/source-ownership-ledger.json").read_bytes()
    check=run("--verify")
    assert check.returncode==0,check.stderr
def test_working_tree_source_changes_do_not_change_frozen_sha_report(tmp_path):
    target=ROOT/"src/App.tsx"
    original=target.read_bytes()
    candidate=tmp_path/"frozen.json"
    try:
        target.write_bytes(original+b"\\n// UNCOMMITTED WORKING TREE MARKER\\n")
        r=run("--output",str(candidate))
        assert r.returncode==0,r.stderr
        assert candidate.read_bytes()==(ROOT/"docs/history/source-ownership-ledger.json").read_bytes()
    finally:
        target.write_bytes(original)
def test_missing_donor_commit_errors_explicitly(tmp_path):
    r=run("--donor-sha","0"*40,"--output",str(tmp_path/"invalid.json"))
    assert r.returncode!=0
    assert "DONOR_SHA_UNAVAILABLE" in r.stderr or "DONOR_SHA_INVALID" in r.stderr
def test_relevant_path_cannot_disappear_silently(tmp_path):
    report=tmp_path/"corrupt.json"
    original=json.loads((ROOT/"docs/history/source-ownership-ledger.json").read_text(encoding="utf-8"))
    original["entries"]=[r for r in original["entries"] if r["path"]!="start.bat"]
    report.write_text(json.dumps(original),encoding="utf-8")
    r=run("--verify","--output",str(report))
    assert r.returncode!=0
    assert "P0_LEDGER_OUTDATED" in r.stderr

def test_all_donor_docs_are_classified_and_history_governance_preserved():
    j=json.loads((ROOT/"docs/history/source-ownership-ledger.json").read_text(encoding="utf-8"))
    donor=set(subprocess.check_output(["git","ls-tree","-r","--name-only",SHA],cwd=ROOT,text=True).splitlines())
    expected={p for p in donor if p.startswith(("docs/",".agents/")) or p=="AGENTS.md"}
    indexed={e["path"] for e in j["entries"]}
    assert expected<=indexed,sorted(expected-indexed)
    entries={e["path"]:e for e in j["entries"]}
    for name in ("AGENTS.md","docs/history/CODING_RULES.md",
                 "docs/history/HISTORY_STUDIO_STRICT_CLEAN_CUT_PLAN_V3.md",
                 "docs/history/phases/P0_REMOTION_FEASIBILITY.md",
                 ".agents/skills/remotion-motion-graphics/SKILL.md"):
        assert entries[name]["decision"]=="PRESERVE",name
        assert entries[name]["documentationAuthority"]=="ACTIVE_NEW_HISTORY",name
    for name in ("docs/99_SOURCE_OF_TRUTH.md","docs/67_SCENE_BREAKDOWN_VISUAL_MERGE_COMPLIANCE.md",
                 "docs/69_UNIFIED_IMAGE_HIDDEN_PROMPT_BUNDLE_COMPLIANCE.md"):
        assert entries[name]["decision"] in ("DELETE","ARCHIVE","REWRITE"),name
        assert entries[name]["documentationAuthority"]=="RETIRED_YTB",name
    assert "docs/history/P0_EXECUTION_AUDIT.md" in j["documentationInventory"]["candidateOnlyP0Outputs"]
    assert "docs/history/P0_EXECUTION_AUDIT.md" not in donor

def test_missing_legacy_document_cannot_pass_completeness(tmp_path):
    data=json.loads((ROOT/"docs/history/source-ownership-ledger.json").read_text(encoding="utf-8"))
    data["entries"]=[e for e in data["entries"] if e["path"]!="docs/99_SOURCE_OF_TRUTH.md"]
    out=tmp_path/"missing-doc.json"
    out.write_text(json.dumps(data),encoding="utf-8")
    result=run("--verify","--output",str(out))
    assert result.returncode!=0
    assert "P0_LEDGER_OUTDATED" in result.stderr
