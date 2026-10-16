"""Transferred P0-TRANSFER-001: frozen donor new History docs must survive P1."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def test_new_document_authority_not_classified_as_deleted_ytb():
    j=json.loads((ROOT/"docs/history/source-ownership-ledger.json").read_text("utf8"))
    rows={x["path"]:x for x in j["entries"]}
    for name in ("00_START_HERE.md","docs/00_INDEX.md"):
        assert rows[name]["decision"] in ("PRESERVE","REWRITE")
        assert rows[name]["documentationAuthority"]=="ACTIVE_NEW_HISTORY"
    assert rows["README.md"]["decision"]=="REWRITE"
    assert rows["README.md"]["documentationAuthority"]=="ACTIVE_NEW_HISTORY"
    assert rows["docs/99_SOURCE_OF_TRUTH.md"]["documentationAuthority"]=="RETIRED_YTB"
    for name in ("README.md","00_START_HERE.md","docs/00_INDEX.md"):
        active=(ROOT/name).read_text("utf8")
        assert "Historical Motion Graphics Studio" in active
        assert "CODING_RULES.md" in active
        assert "Scene Assets V2" not in active
