from __future__ import annotations
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.history.db import HistoryDB, strict_json
from app.history.errors import HistoryError
from app.history.service import HistoryService, safe_asset_relative_path
from app.history.domain import ChannelCreate,ProjectCreate,STAGE_KEYS

def create_api(db:Path):
    return TestClient(create_app(db))

def sample_channel(client,name="History Research"):
    response=client.post("/api/history/channels",json={"name":name,"language":"English","niche":"History","history_dna":{}})
    assert response.status_code==201,response.text
    return response.json()

def sample_project(client,channel_id,name="The Event"):
    response=client.post("/api/history/projects",json={"channel_id":channel_id,"name":name,"history_context":{}})
    assert response.status_code==201,response.text
    return response.json()

def test_fresh_channel_project_status_persist_restart(tmp_path):
    db=tmp_path/"history_only"/"history_app.db"
    with create_api(db) as client:
        assert client.get("/health").json()["product"]=="historical-motion-graphics-studio"
        channel=sample_channel(client)
        project=sample_project(client,channel["channel_id"])
        status=client.get("/api/history/projects/"+project["project_id"]+"/status")
        assert status.status_code==200
        stages=status.json()["stages"]
        assert [s["key"] for s in stages]==list(STAGE_KEYS)
        assert len(stages)==7
        assert all(x["status"]=="blocked" and x["blocked_reason"].startswith("NOT_IMPLEMENTED_P") for x in stages)
        assert client.get("/api/history/projects/"+project["project_id"]).status_code==200
        openapi=client.get("/openapi.json").json()
        paths=set(openapi["paths"])
        assert paths=={"/health","/api/history/channels","/api/history/projects",
                        "/api/history/projects/{project_id}","/api/history/projects/{project_id}/status"}
        for old in ("/api/projects","/api/v4","/api/scene-assets","/api/voice","/api/render"):
            assert client.get(old).status_code==404
        assert client.post("/api/history/projects",json={"channel_id":channel["channel_id"],"name":"The Event","history_context":{}}).status_code==409
        assert client.post("/api/history/channels",json={"name":channel["name"],"language":"en","niche":"a","history_dna":{}}).status_code==409
        assert client.post("/api/history/channels",json={"name":"extra","language":"en","niche":"n","bad":1}).status_code==422
        assert client.post("/api/history/projects",json={"channel_id":"0"*32,"name":"xx","history_context":{}}).status_code==404
        assert client.get("/api/history/projects/missing").status_code==404
    with create_api(db) as restarted:
        assert len(restarted.get("/api/history/channels").json())==1
        assert restarted.get("/api/history/projects?channel_id="+channel["channel_id"]).json()[0]["project_id"]==project["project_id"]
        assert restarted.get("/api/history/projects/"+project["project_id"]+"/status").status_code==200
    with sqlite3.connect(db) as cn:
        tables={r[0] for r in cn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"channels","projects","stage_heads","artifacts","authoring_reservations","history_meta","alembic_version"}<=tables
        assert not any("scene" in n or "beat" in n or "prompt_bundle" in n or "visual_reference" in n for n in tables)
        assert cn.execute("PRAGMA foreign_key_check").fetchall()==[]
        assert cn.execute("SELECT version_num FROM alembic_version").fetchone()[0]=="001_history_initial"

@pytest.mark.parametrize("kind",["foreign","legacy","empty"])
def test_foreign_and_legacy_database_rejected_without_any_mutation(tmp_path,kind):
    path=tmp_path/("foreign-"+kind+".db")
    if kind=="empty":path.write_bytes(b"")
    else:
        with sqlite3.connect(path) as cn:
            cn.execute("CREATE TABLE "+("scene_assets" if kind=="legacy" else "foreign_meta")+" (id INTEGER)")
    before=path.read_bytes();before_mtime=path.stat().st_mtime_ns
    with pytest.raises(HistoryError,match="HISTORY_LEGACY_STORAGE_UNSUPPORTED"):
        with create_api(path):pass
    assert path.read_bytes()==before
    assert hashlib.sha256(path.read_bytes()).digest()==hashlib.sha256(before).digest()
    assert path.stat().st_mtime_ns==before_mtime
    assert not path.with_name(path.name+"-wal").exists()
    assert not path.with_name(path.name+"-shm").exists()

def test_old_storage_named_path_is_rejected_without_writes(tmp_path):
    path=tmp_path/"youtube_ai_studio_v3.db"
    path.write_bytes(b"legacy")
    before=path.read_bytes()
    with pytest.raises(HistoryError,match="HISTORY_LEGACY_STORAGE_UNSUPPORTED"):
        create_app(path)
    assert path.read_bytes()==before

def test_json_parser_validation_has_no_write_side_effects(tmp_path):
    db=tmp_path/"history.db"
    with create_api(db) as client:
        original=db.read_bytes();mtime=db.stat().st_mtime_ns
        svc=HistoryService(HistoryDB(db))
        assert svc.validate_only('{"a":1}')=={"a":1}
        assert db.read_bytes()==original and db.stat().st_mtime_ns==mtime
        for raw in ('{"a":1,"a":2}','{"a":{"b":1,"b":2}}'):
            with pytest.raises(HistoryError,match="HISTORY_JSON_DUPLICATE_KEY"):
                svc.validate_only(raw)
        for raw in ("[", '{"x": NaN}'):
            with pytest.raises(HistoryError,match="HISTORY_JSON_INVALID"):
                svc.validate_only(raw)
        assert db.read_bytes()==original and db.stat().st_mtime_ns==mtime

def test_immutable_artifact_cross_project_and_cas_race(tmp_path):
    db=tmp_path/"h.db"
    with create_api(db) as client:
        c=sample_channel(client)
        a=sample_project(client,c["channel_id"],"One")
        b=sample_project(client,c["channel_id"],"Two")
        svc=HistoryService(HistoryDB(db))
        first=svc.publish_artifact(a["project_id"],"topic",'{"approved":"one"}',version=1)
        second=svc.publish_artifact(a["project_id"],"topic",'{"approved":"two"}',version=2)
        original=svc.load_artifact(a["project_id"],first["artifact_id"])
        assert original==b'{"approved":"one"}'
        with pytest.raises(HistoryError,match="HISTORY_ARTIFACT_OWNERSHIP_INVALID"):
            svc.load_artifact(b["project_id"],first["artifact_id"])
        with pytest.raises(HistoryError,match="HISTORY_ARTIFACT_OWNERSHIP_INVALID"):
            svc.compare_and_set_head(b["project_id"],"topic",first["artifact_id"],0)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(svc.compare_and_set_head,a["project_id"],"topic",entry["artifact_id"],0)
                     for entry in (first,second)]
            successes=0;failures=0
            for future in futures:
                try:assert future.result()==1;successes+=1
                except HistoryError as ex: assert ex.code=="HISTORY_HEAD_STALE";failures+=1
            assert (successes,failures)==(1,1)
        assert svc.get_status(a["project_id"])["stages"][0]["status"]=="approved"
        with pytest.raises(HistoryError,match="HISTORY_HEAD_STALE"):
            svc.compare_and_set_head(a["project_id"],"topic",second["artifact_id"],0)
        with pytest.raises(HistoryError,match="HISTORY_ARTIFACT_VERSION_CONFLICT"):
            svc.publish_artifact(a["project_id"],"topic",'{"new":1}',version=1)
        assert svc.load_artifact(a["project_id"],first["artifact_id"])==original
        assert svc.reserve_authoring(a["project_id"],"topic","0"*64)
        with pytest.raises(HistoryError,match="HISTORY_RESERVATION_INVALID"):
            svc.reserve_authoring(a["project_id"],"topic","not-a-fingerprint")

@pytest.mark.parametrize("unsafe",["../secret","/etc/passwd","C:\\Windows\\file","a/../b","http://evil/file","a//b",".hidden"])
def test_unsafe_artifact_paths_rejected(unsafe):
    with pytest.raises(HistoryError,match="HISTORY_ARTIFACT_PATH_INVALID"):
        safe_asset_relative_path(unsafe)


def test_artifact_storage_symlink_escape_rejected(tmp_path):
    root=tmp_path/"private";db=root/"history_app.db"
    foreign=tmp_path/"outside";foreign.mkdir()
    with create_api(db) as client:
        c=sample_channel(client);p=sample_project(client,c["channel_id"])
        (root/"artifacts").symlink_to(foreign,target_is_directory=True)
        svc=HistoryService(HistoryDB(db))
        with pytest.raises(HistoryError,match="HISTORY_ARTIFACT_PATH_INVALID"):
            svc.publish_artifact(p["project_id"],"topic",'{"no":"escape"}')
        assert not list(foreign.iterdir())
