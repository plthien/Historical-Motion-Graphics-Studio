"""P2 frozen-gate API, persisted workflow, authoring isolation and invalidation."""
from __future__ import annotations
import json,sqlite3,hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.history.db import HistoryDB
from app.history.errors import HistoryError
from app.history.authoring import HistoryAuthoring

def create(db):return TestClient(create_app(db))
def send(c,path,body):r=c.post(path,json=body);assert r.status_code in (200,201),r.text;return r.json()
def channel(c,language="English"):
    return send(c,"/api/history/channels",{"name":"Ancient History","language":language,"niche":"Ancient humans","history_dna":{}})
DNA={"schema_version":"HistoryChannelDNA.v1","language":"English","audience":"Curious adults",
     "historical_niche":"Ancient human survival",
     "narration_style":"Conversational dry humor with natural speech",
     "editorial_style":"Entertaining and historically careful narrative",
     "map_treatment":"Schematic maps plainly marked; never fabricate exact borders",
     "uncertainty_policy":"Label hypotheses, contested evidence and reconstructed scenes explicitly"}
TOPIC={"title":"The Ancient Hunt That Created a New Problem","hook":"They caught dinner and invented a second emergency.",
       "historical_period":"Late Pleistocene","region":"Western Eurasia",
       "story_angle":"An imagined but plausible cause-and-effect chain about food storage and conflict",
       "novelty_questions":["Which downstream problem follows a successful hunt?"],
       "factual_risks":["Exact event is fictional, not attested by archaeology"]}
def setup(c,language="English"):
    ch=channel(c,language)
    assert c.get(f"/api/history/channels/{ch['channel_id']}/dna").json() is None
    saved=c.put(f"/api/history/channels/{ch['channel_id']}/dna",json={"document":dict(DNA,language=language),"expected_revision":0})
    assert saved.status_code==200,saved.text
    p=send(c,"/api/history/projects",{"channel_id":ch["channel_id"],"name":"Hunt","history_context":{"style":"lightly comedic"}})
    return ch,p
def topic_json(prompt):
    return json.dumps({"schema_version":"TopicIdeas.v1","project_id":prompt["project_id"],
      "reservation_id":prompt["reservation_id"],"source_fingerprint":prompt["source_fingerprint"],
      "ideas":[dict(idea_id=id,**TOPIC) for id in prompt["reserved_ids"]["ideas"]]},ensure_ascii=False)
def script_json(prompt,wrong=False):
    ids=prompt["reserved_ids"]["sections"]
    sections=[{"section_id":sid,"exact_text":f"Section {i}: The hunt succeeds, but people must make a harder choice.",
               "historical_period":"Late Pleistocene","region":"Western Eurasia",
               "delivery_note":"Speak naturally"} for i,sid in enumerate(ids,1)]
    text="\n\n".join(s["exact_text"] for s in sections)
    quote="The hunt succeeds"
    anchor=sections[0]["exact_text"].index(quote)
    claim={"claim_id":"claim_001","section_id":ids[0],"quote":quote,"start":anchor,
           "end":anchor+len(quote),"historical_period":"Late Pleistocene","region":"Western Eurasia",
           "model_verification_state":"hypothetical","evidence_review_status":"unreviewed",
           "source_leads":["Potential archaeological research lead, NOT verified"],
           "uncertainty_note":"Illustrative story event, not an attested incident"}
    return json.dumps({"schema_version":"ScriptHistoricalAnalysis.v1","project_id":prompt["project_id"],
      "reservation_id":prompt["reservation_id"],"source_fingerprint":prompt["source_fingerprint"],
      "parent_topic_artifact_id":prompt["reserved_ids"]["parent_artifact_id"],
      "parent_topic_hash":prompt["reserved_ids"]["parent_hash"],
      "full_script":text+(" altered" if wrong else ""),"sections":sections,
      "claims":[claim],"performance_notes":["Dry comedy, conversational"]},ensure_ascii=False)
def approve_topic(c,p):
    base=f"/api/history/projects/{p['project_id']}/topic"
    prompt=send(c,base+"/prompt",{"count":2,"avoid":["Repeated viral hunting videos"],"emphasis":"Novel cause-and-effect comedy"})
    assert "manual" in prompt["prompt"].lower()
    assert "HistoryChannelDNA.v1" in prompt["prompt"]
    assert c.get(base+"/prompt").json()["reservation_id"]==prompt["reservation_id"]
    raw=topic_json(prompt)
    validated=send(c,base+"/import/validate",{"reservation_id":prompt["reservation_id"],"raw_json":raw})
    assert validated["valid"],validated
    assert c.get(base+"/current").json() is None
    candidate=send(c,base+"/import",{"reservation_id":prompt["reservation_id"],"raw_json":raw})
    rev=c.get(base+"/candidates/"+candidate["candidate_id"]).json()
    assert rev["status"]=="candidate" and len(rev["content"]["ideas"])==2
    approved=send(c,base+"/approve",{"candidate_id":candidate["candidate_id"],"idea_id":prompt["reserved_ids"]["ideas"][0],"expected_revision":0})
    assert approved["revision"]==1
    assert c.get(base+"/current").json()["content_hash"]==approved["content_hash"]
    return prompt,approved

def test_full_topic_script_cycle_restart_source_stale_and_bytes(tmp_path):
    db=tmp_path/"history_data"/"history_app.db"
    with create(db) as c:
        ch,p=setup(c)
        t_prompt,topic=approve_topic(c,p)
        script_base=f"/api/history/projects/{p['project_id']}/script-analysis"
        source=send(c,script_base+"/prompt",{"section_count":3,"notes":"Dry understated humor"})
        assert source["reserved_ids"]["parent_artifact_id"]==topic["artifact_id"]
        raw=script_json(source)
        req={"reservation_id":source["reservation_id"],"raw_json":raw}
        assert send(c,script_base+"/import/validate",req)["valid"]
        candidate=send(c,script_base+"/import",req)
        reviewed=c.get(script_base+"/review",params={"candidate_id":candidate["candidate_id"]})
        assert reviewed.status_code==200 and len(reviewed.json()["content"]["claims"])==1
        approved=send(c,script_base+"/approve",{"candidate_id":candidate["candidate_id"],"expected_revision":0})
        assert approved["content"]["full_script"]==json.loads(raw)["full_script"]
        assert approved["content"]["claims"][0]["evidence_review_status"]=="unreviewed"
        assert c.get(script_base+"/current").json()["stale"] is False
        assert c.post(script_base+"/approve",json={"candidate_id":candidate["candidate_id"],"expected_revision":0}).status_code==409
        status=c.get(f"/api/history/projects/{p['project_id']}/status").json()
        assert [x["status"] for x in status["stages"][:2]]==["approved","approved"]
    with create(db) as c:
        old=c.get(script_base+"/current").json()
        assert old["content_hash"]==approved["content_hash"] and old["content"]["full_script"]==approved["content"]["full_script"]
        assert c.get(f"/api/history/channels/{ch['channel_id']}/dna").json()["revision"]==1
        manual=send(c,f"/api/history/projects/{p['project_id']}/topic/manual",{"topic":dict(TOPIC),"expected_revision":1})
        assert manual["revision"]==2
        stale=c.get(script_base+"/current").json()
        assert stale["stale"] is True and stale["content_hash"]==old["content_hash"]
        assert c.post(script_base+"/approve",json={"candidate_id":candidate["candidate_id"],"expected_revision":0}).status_code==409
        assert (db.parent/"artifacts"/(old["content_hash"]+".json")).is_file()
        assert c.get(f"/api/history/projects/{p['project_id']}/status").json()["stages"][1]["status"]=="stale"
    with sqlite3.connect(db) as cn:
        assert cn.execute("SELECT version_num FROM alembic_version").fetchone()[0]=="002_history_authoring_p2"
        assert cn.execute("SELECT COUNT(*) FROM p2_approvals").fetchone()[0]==3
        assert cn.execute("PRAGMA foreign_key_check").fetchall()==[]

def test_strict_validation_no_writes_and_repair_prompt(tmp_path):
    db=tmp_path/"app.db"
    with create(db) as c:
        ch,p=setup(c)
        base=f"/api/history/projects/{p['project_id']}/topic"
        prompt=send(c,base+"/prompt",{"count":1})
        assert c.get(base+"/prompt").json()["source_fingerprint"]==prompt["source_fingerprint"]
        raw=topic_json(prompt)
        cases=[
          raw.replace('"title":','"unknown":42,"title":',1),
          '{"schema_version":"TopicIdeas.v1","schema_version":"TopicIdeas.v1"}',
          raw.replace('"idea_id":','"idea_id":"bad", "idea_id":',1),
          raw.replace('"ideas": [','"ideas": null, "foo": [',1),
          raw.replace(prompt["reserved_ids"]["ideas"][0],"idea_wrong"),
          '{"broken": NaN}',
          "x"*(1024*1024+1),
        ]
        for invalid in cases:
            before=db.stat().st_mtime_ns
            payload={"reservation_id":prompt["reservation_id"],"raw_json":invalid}
            response=c.post(base+"/import/validate",json=payload)
            assert response.status_code in (200,422)
            if response.status_code==200:assert not response.json()["valid"]
            assert c.get(base+"/candidates").json()==[]
            assert c.get(base+"/current").json() is None
        repair=send(c,base+"/repair-prompt",{"reservation_id":prompt["reservation_id"],"raw_json":'{"missing":true}'})
        assert "invalid_paths" in repair["prompt"]
        assert "frozen_prompt" in repair["prompt"]
        assert c.get(base+"/prompt").json()["state"]=="pending"

def test_stale_dna_and_reservation_rebuild_replay(tmp_path):
    db=tmp_path/"a.db"
    with create(db) as c:
        ch,p=setup(c);base=f"/api/history/projects/{p['project_id']}/topic"
        one=send(c,base+"/prompt",{"count":1})
        two=send(c,base+"/prompt",{"count":1})
        assert c.post(base+"/import",json={"reservation_id":one["reservation_id"],"raw_json":topic_json(one)}).status_code==409
        body=dict(DNA);body["editorial_style"]="Different historical production style"
        assert c.put(f"/api/history/channels/{ch['channel_id']}/dna",json={"document":body,"expected_revision":1}).status_code==200
        assert c.post(base+"/import",json={"reservation_id":two["reservation_id"],"raw_json":topic_json(two)}).status_code==409
        three=send(c,base+"/prompt",{"count":1})
        imported=send(c,base+"/import",{"reservation_id":three["reservation_id"],"raw_json":topic_json(three)})
        assert c.post(base+"/import",json={"reservation_id":three["reservation_id"],"raw_json":topic_json(three)}).status_code==409
        assert c.post(base+"/approve",json={"candidate_id":imported["candidate_id"],"idea_id":three["reserved_ids"]["ideas"][0],"expected_revision":5}).status_code==409

def test_p1_populated_history_database_migrates_without_losing_records(tmp_path):
    db=tmp_path/"existing_history.db"
    from alembic import command
    from alembic.config import Config
    from app.history.db import ROOT
    config=Config(str(ROOT/"alembic.ini"))
    config.set_main_option("script_location",str(ROOT/"alembic"))
    config.set_main_option("sqlalchemy.url","sqlite:///"+db.as_posix())
    command.upgrade(config,"001_history_initial")
    with sqlite3.connect(db) as cn:
        cn.execute("PRAGMA foreign_keys=ON")
        cn.execute("INSERT INTO channels VALUES (?,?,?,?,?,?,?)",("a"*32,"P1 Channel","English","History","{}",1,"2026-01-01T00:00:00+00:00"))
        cn.execute("INSERT INTO projects VALUES (?,?,?,?,?)",("b"*32,"a"*32,"P1 Project","{}","2026-01-01T00:00:00+00:00"))
    with create(db) as c:
        assert c.get("/api/history/projects/"+"b"*32).status_code==200
        assert c.get("/health").status_code==200
    with sqlite3.connect(db) as cn:
        assert cn.execute("SELECT version_num FROM alembic_version").fetchone()[0]=="002_history_authoring_p2"
        assert cn.execute("SELECT name FROM channels").fetchone()[0]=="P1 Channel"

def test_script_quotes_spans_exactness_and_cross_project_candidate(tmp_path):
    db=tmp_path/"h.db"
    with create(db) as c:
        ch,p=setup(c);_p,topic=approve_topic(c,p)
        base=f"/api/history/projects/{p['project_id']}/script-analysis"
        prompt=send(c,base+"/prompt",{"section_count":2})
        original=json.loads(script_json(prompt))
        bads=[]
        v=json.loads(json.dumps(original));v["full_script"]+="x";bads.append(v)
        v=json.loads(json.dumps(original));v["sections"].reverse();bads.append(v)
        v=json.loads(json.dumps(original));v["claims"][0]["start"]=0;bads.append(v)
        v=json.loads(json.dumps(original));v["claims"][0]["section_id"]="sec_999";bads.append(v)
        v=json.loads(json.dumps(original));v["claims"][0]["evidence_review_status"]="verified";bads.append(v)
        v=json.loads(json.dumps(original));v["parent_topic_artifact_id"]="f"*32;bads.append(v)
        for value in bads:
            result=send(c,base+"/import/validate",{"reservation_id":prompt["reservation_id"],"raw_json":json.dumps(value)})
            assert result["valid"] is False
        p2=send(c,"/api/history/projects",{"channel_id":ch["channel_id"],"name":"Other Project","history_context":{}})
        assert c.post(f"/api/history/projects/{p2['project_id']}/script-analysis/import",json={
            "reservation_id":prompt["reservation_id"],"raw_json":script_json(prompt)}).status_code in (404,409)


def test_expired_reservation_wrong_stage_and_validate_only_purity(tmp_path):
    db=tmp_path/"h.db"
    with create(db) as c:
        ch,p=setup(c)
        topic=f"/api/history/projects/{p['project_id']}/topic"
        reservation=send(c,topic+"/prompt",{"count":1})
        data={"reservation_id":reservation["reservation_id"],"raw_json":topic_json(reservation)}
        before_bytes=db.read_bytes()
        before_mtime=db.stat().st_mtime_ns
        assert send(c,topic+"/import/validate",data)["valid"]
        # Read-only validate has no DB/artifact or head writes.
        assert db.read_bytes()==before_bytes and db.stat().st_mtime_ns==before_mtime
        assert not (db.parent/"artifacts").exists()
        with sqlite3.connect(db) as cn:
            cn.execute("UPDATE p2_prompts SET expires_at='2000-01-01T00:00:00+00:00' WHERE reservation_id=?",
                       (reservation["reservation_id"],))
        assert c.post(topic+"/import",json=data).status_code==409
        assert c.post(f"/api/history/projects/{p['project_id']}/script-analysis/import",json=data).status_code in (404,409)
        assert c.get(topic+"/current").json() is None

def test_concurrent_approval_one_winner_and_immutable_history(tmp_path):
    db=tmp_path/"h.db"
    with create(db) as c:
        ch,p=setup(c)
        topic=f"/api/history/projects/{p['project_id']}/topic"
        reservation=send(c,topic+"/prompt",{"count":1})
        candidate=send(c,topic+"/import",{"reservation_id":reservation["reservation_id"],"raw_json":topic_json(reservation)})
        srv=HistoryAuthoring(HistoryDB(db))
        def run():
            try:return srv.approve(p["project_id"],"topic",candidate["candidate_id"],0,reservation["reserved_ids"]["ideas"][0])
            except HistoryError as e:return e.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:run(),range(2)))
        winners=[v for v in results if isinstance(v,dict)]
        assert len(winners)==1,results
        assert results.count("HISTORY_CANDIDATE_CONSUMED")==1
        first=winners[0]
        assert len(c.get(topic+"/history").json())==1
        second=send(c,topic+"/manual",{"topic":TOPIC,"expected_revision":1})
        rows=c.get(topic+"/history").json()
        assert [x["version"] for x in rows]==[2,1]
        assert rows[1]["artifact_id"]==first["artifact_id"]
        assert rows[1]["content_hash"]==first["content_hash"]
        assert rows[1]["content"]==first["content"]
        assert rows[0]["artifact_id"]==second["artifact_id"]
        assert c.get(topic+"/current").json()["revision"]==2

def test_storage_write_failure_never_publishes_missing_head(tmp_path,monkeypatch):
    db=tmp_path/"h.db"
    with create(db) as c:
        ch,p=setup(c)
        topic=f"/api/history/projects/{p['project_id']}/topic"
        reservation=send(c,topic+"/prompt",{"count":1})
        candidate=send(c,topic+"/import",{"reservation_id":reservation["reservation_id"],"raw_json":topic_json(reservation)})
        srv=HistoryAuthoring(HistoryDB(db))
        import os
        real_open=os.open
        def fail_open(*args,**kwargs):
            if "artifacts" in str(args[0]):
                raise PermissionError("injected disk failure")
            return real_open(*args,**kwargs)
        with monkeypatch.context() as m:
            m.setattr("app.history.authoring.os.open",fail_open)
            with pytest.raises(PermissionError,match="injected disk failure"):
                srv.approve(p["project_id"],"topic",candidate["candidate_id"],0,reservation["reserved_ids"]["ideas"][0])
        assert c.get(topic+"/current").json() is None
        reviewed=c.get(topic+"/candidates/"+candidate["candidate_id"]).json()
        assert reviewed["status"]=="candidate"
        approved=srv.approve(p["project_id"],"topic",candidate["candidate_id"],0,reservation["reserved_ids"]["ideas"][0])
        assert approved["revision"]==1

def test_channel_dna_changes_make_approved_topic_stale_without_deleting_history(tmp_path):
    db=tmp_path/"h.db"
    with create(db) as c:
        ch,p=setup(c)
        _,t=approve_topic(c,p)
        changed=dict(DNA);changed["audience"]="Specialist archaeology enthusiasts"
        r=c.put(f"/api/history/channels/{ch['channel_id']}/dna",json={"document":changed,"expected_revision":1})
        assert r.status_code==200 and r.json()["revision"]==2
        head=c.get(f"/api/history/projects/{p['project_id']}/topic/current").json()
        assert head["stale"] is True and head["content_hash"]==t["content_hash"]
        assert c.get(f"/api/history/projects/{p['project_id']}/topic/history").json()[0]["content_hash"]==t["content_hash"]
        status=c.get(f"/api/history/projects/{p['project_id']}/status").json()
        assert status["stages"][0]["status"]=="stale"
        assert c.post(f"/api/history/projects/{p['project_id']}/script-analysis/prompt",json={"section_count":2}).status_code==409

def test_invalid_utf8_json_http_and_claim_period_conflict(tmp_path):
    db=tmp_path/"h.db"
    with create(db) as c:
        ch,p=setup(c)
        prompt,_=approve_topic(c,p)
        # Browser rejects non-UTF8 File inputs before fetch; HTTP server must reject invalid JSON bytes.
        result=c.post(f"/api/history/projects/{p['project_id']}/topic/import",
                     content=b'{"bad":"\\xff"}',headers={"content-type":"application/json"})
        assert result.status_code==422
        sp=send(c,f"/api/history/projects/{p['project_id']}/script-analysis/prompt",{"section_count":2})
        bad=json.loads(script_json(sp))
        bad["claims"][0]["region"]="Unrelated geography"
        out=send(c,f"/api/history/projects/{p['project_id']}/script-analysis/import/validate",
                {"reservation_id":sp["reservation_id"],"raw_json":json.dumps(bad)})
        assert not out["valid"]
        assert any(e["code"]=="CHRONOLOGY_MISMATCH" for e in out["errors"])

@pytest.mark.parametrize("language", ["English", "Japanese", "Vietnamese"])
def test_script_prompt_uses_frozen_channel_dna_language(tmp_path,language):
    from app.history.validation import digest
    db=tmp_path/"script_language.db"
    with create(db) as c:
        ch,p=setup(c,language=language)
        _,topic=approve_topic(c,p)
        base=f"/api/history/projects/{p['project_id']}/script-analysis"
        reserved=send(c,base+"/prompt",{"section_count":2})
        assert len(reserved["source_fingerprint"])==64
        assert reserved["reserved_ids"]["parent_artifact_id"]==topic["artifact_id"]
        assert reserved["reserved_ids"]["parent_hash"]==topic["content_hash"]
        assert reserved["stage"]=="script_analysis" and reserved["state"]=="pending"
        instructions=json.loads(reserved["prompt"].split("\n",1)[1])
        task=instructions["task"]
        assert f"exact spoken script in {language}" in task
        assert f"full_script and every section.exact_text must be in {language}" in task
        assert "spoken English" not in task if language!="English" else "in English" in task
        assert instructions["frozen_input_explained"]["dna"]["language"]==language
        assert instructions["frozen_input_explained"]["approved_topic_artifact_id"]==topic["artifact_id"]
        assert instructions["required_output"]["schema_version"]=="ScriptHistoricalAnalysis.v1"
        assert instructions["reservation_id"]==reserved["reservation_id"]
        assert instructions["source_fingerprint"]==reserved["source_fingerprint"]
        assert digest({"source":instructions["frozen_input_explained"],
                       "reservation_id":reserved["reservation_id"],
                       "ids":reserved["reserved_ids"]})==reserved["source_fingerprint"]
        assert c.get(base+"/prompt").json()==reserved


def test_script_language_change_rebuild_preserves_old_prompt_and_cas(tmp_path):
    db=tmp_path/"script_language_change.db"
    with create(db) as c:
        ch,p=setup(c)
        _,topic=approve_topic(c,p)
        base=f"/api/history/projects/{p['project_id']}/script-analysis"
        old=send(c,base+"/prompt",{"section_count":2})
        old_text=old["prompt"]
        candidate=send(c,base+"/import",{"reservation_id":old["reservation_id"],
                                         "raw_json":script_json(old)})
        changed=dict(DNA,language="Japanese")
        saved=c.put(f"/api/history/channels/{ch['channel_id']}/dna",
                    json={"document":changed,"expected_revision":1})
        assert saved.status_code==200 and saved.json()["revision"]==2
        old_get=c.get(base+"/prompt")
        assert old_get.status_code==200
        assert old_get.json()["prompt"]==old_text
        assert old_get.json()["reservation_id"]==old["reservation_id"]
        assert old_get.json()["is_current"] is False
        assert c.post(base+"/approve",json={"candidate_id":candidate["candidate_id"],
                                            "expected_revision":0}).status_code==409
        assert c.post(base+"/prompt",json={"section_count":2}).status_code==409
        # A newly approved topic is required because the old topic is stale under revised DNA.
        fresh_topic=send(c,f"/api/history/projects/{p['project_id']}/topic/manual",
                         {"topic":dict(TOPIC),"expected_revision":1})
        assert fresh_topic["artifact_id"]!=topic["artifact_id"]
        rebuilt=send(c,base+"/prompt",{"section_count":2})
        task=json.loads(rebuilt["prompt"].split("\n",1)[1])["task"]
        assert "exact spoken script in Japanese" in task
        assert "spoken English" not in task
        assert rebuilt["source_fingerprint"]!=old["source_fingerprint"]
        assert rebuilt["reservation_id"]!=old["reservation_id"]
        assert rebuilt["reserved_ids"]["parent_artifact_id"]==fresh_topic["artifact_id"]
        assert c.get(base+"/prompt").json()==rebuilt
        assert c.post(base+"/approve",json={"candidate_id":candidate["candidate_id"],
                                            "expected_revision":0}).status_code==409
    with sqlite3.connect(db) as cn:
        stored=cn.execute("SELECT prompt_text FROM p2_prompts WHERE reservation_id=?",
                          (old["reservation_id"],)).fetchone()
        assert stored is not None and stored[0]==old_text
