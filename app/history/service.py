"""History-only persistence, immutable artifacts and transactionally guarded stage-head CAS."""
from __future__ import annotations
import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from .db import HistoryDB, strict_json, STAGE_KEYS
from .errors import HistoryError

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def stable_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",",":"), allow_nan=False)

def safe_asset_relative_path(value: str) -> str:
    if not isinstance(value,str) or len(value)>240 or not re.fullmatch(r"[a-zA-Z0-9_\-./]+",value) or "\\" in value or value.startswith("/") or any(s in ("", ".", "..") or s.startswith(".") for s in value.split("/")):
        raise HistoryError("HISTORY_ARTIFACT_PATH_INVALID", 422)
    return value

def uid() -> str:
    return uuid.uuid4().hex

class HistoryService:
    def __init__(self, db: HistoryDB):
        self.db = db

    def channels(self):
        with self.db.connect() as cn:
            return [self._channel(row) for row in cn.execute("SELECT * FROM channels ORDER BY created_at, channel_id")]

    @staticmethod
    def _channel(row):
        value=dict(row);value["history_dna"]=json.loads(value.pop("history_dna_json"))
        return value

    def create_channel(self, data):
        name=data.name.strip()
        if not name:
            raise HistoryError("HISTORY_CHANNEL_NAME_REQUIRED",422)
        result={"channel_id":uid(),"name":name,"language":data.language.strip(),"niche":data.niche.strip(),"history_dna":data.history_dna,"revision":1,"created_at":utcnow()}
        if not result["language"] or not result["niche"]:
            raise HistoryError("HISTORY_CHANNEL_INPUT_INVALID",422)
        try:
            with self.db.connect() as cn:
                cn.execute("BEGIN IMMEDIATE")
                cn.execute("INSERT INTO channels VALUES (?,?,?,?,?,?,?)",
                           (result["channel_id"],result["name"],result["language"],result["niche"],stable_json(data.history_dna),1,result["created_at"]))
                cn.commit()
        except sqlite3.IntegrityError as e:
            raise HistoryError("HISTORY_CHANNEL_DUPLICATE",409) from e
        return result

    @staticmethod
    def _project(row):
        value=dict(row);value["history_context"]=json.loads(value.pop("history_context_json"))
        return value

    def projects(self, channel_id=None):
        with self.db.connect() as cn:
            if channel_id:
                rows=cn.execute("SELECT * FROM projects WHERE channel_id=? ORDER BY created_at,project_id",(channel_id,))
            else:
                rows=cn.execute("SELECT * FROM projects ORDER BY created_at,project_id")
            return [self._project(row) for row in rows]

    def get_project(self, project_id):
        with self.db.connect() as cn:
            row=cn.execute("SELECT * FROM projects WHERE project_id=?",(project_id,)).fetchone()
            if not row:
                raise HistoryError("HISTORY_PROJECT_NOT_FOUND",404)
            return self._project(row)

    def create_project(self, data):
        name=data.name.strip()
        if not name:raise HistoryError("HISTORY_PROJECT_NAME_REQUIRED",422)
        response={"project_id":uid(),"channel_id":data.channel_id,"name":name,"history_context":data.history_context,"created_at":utcnow()}
        try:
            with self.db.connect() as cn:
                cn.execute("BEGIN IMMEDIATE")
                if not cn.execute("SELECT 1 FROM channels WHERE channel_id=?",(data.channel_id,)).fetchone():
                    raise HistoryError("HISTORY_CHANNEL_NOT_FOUND",404)
                cn.execute("INSERT INTO projects VALUES (?,?,?,?,?)",
                           (response["project_id"],data.channel_id,name,stable_json(data.history_context),response["created_at"]))
                cn.commit()
        except sqlite3.IntegrityError as e:
            raise HistoryError("HISTORY_PROJECT_DUPLICATE",409) from e
        return response

    def get_status(self,project_id):
        from .authoring import HistoryAuthoring
        project=self.get_project(project_id)
        labels=["Topic Ideas","Script & Historical Analysis","Voice & Alignment",
                "Historical Assets","Visual Director","Motion Timeline","Render & Delivery"]
        blocked=["","TOPIC_NOT_APPROVED","NOT_IMPLEMENTED_P3","NOT_IMPLEMENTED_P4",
                 "NOT_IMPLEMENTED_P5","NOT_IMPLEMENTED_P7","NOT_IMPLEMENTED_P8"]
        auth=HistoryAuthoring(self.db)
        stages=[]
        for idx,key in enumerate(STAGE_KEYS):
            artifact=auth.current(project_id,key) if idx<2 else None
            if artifact and artifact["stale"]:state="stale";reason="SOURCE_STALE"
            elif artifact:state="approved";reason=None
            elif idx==0:state="ready";reason=None
            elif idx==1:
                topic=auth.current(project_id,"topic")
                state="ready" if topic and not topic["stale"] else "blocked"
                reason=None if state=="ready" else "TOPIC_NOT_APPROVED"
            else:state="blocked";reason=blocked[idx]
            stages.append({"key":key,"label":labels[idx],"status":state,
                          "blocked_reason":reason,"revision":artifact["revision"] if artifact else 0})
        return {"project_id":project_id,"channel_id":project["channel_id"],"stages":stages}

    def validate_only(self, content: str):
        """No filesystem or database access: only strict syntax. P2 domain validation is future work."""
        return strict_json(content)

    def publish_artifact(self,project_id: str,kind: str,content: str,version: int=1):
        if kind not in STAGE_KEYS or type(version) is not int or version<1:
            raise HistoryError("HISTORY_ARTIFACT_INVALID",422)
        parsed=self.validate_only(content)
        if not isinstance(parsed,dict):
            raise HistoryError("HISTORY_ARTIFACT_INVALID",422)
        data=content.encode("utf-8")
        digest=hashlib.sha256(data).hexdigest()
        name=safe_asset_relative_path("artifacts/"+digest+".json")
        storage_root=self.db.path.parent
        target=storage_root/name
        with self.db.connect() as cn:
            if not cn.execute("SELECT 1 FROM projects WHERE project_id=?",(project_id,)).fetchone():
                raise HistoryError("HISTORY_PROJECT_NOT_FOUND",404)
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.parent.is_symlink() or target.parent.resolve()!=storage_root.resolve()/"artifacts":
            raise HistoryError("HISTORY_ARTIFACT_PATH_INVALID",422)
        try:
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            try:
                with os.fdopen(fd,"wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        except FileExistsError:
            if not target.is_file() or target.is_symlink() or target.read_bytes()!=data:
                raise HistoryError("HISTORY_ARTIFACT_HASH_COLLISION",409)
        record={"artifact_id":uid(),"project_id":project_id,"kind":kind,"content_hash":digest,"storage_uri":name,"status":"approved","version":version,"created_at":utcnow()}
        try:
            with self.db.connect() as cn:
                cn.execute("BEGIN IMMEDIATE")
                cn.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",tuple(record.values()))
                cn.commit()
        except sqlite3.IntegrityError as e:
            raise HistoryError("HISTORY_ARTIFACT_VERSION_CONFLICT",409) from e
        return record

    def load_artifact(self,project_id:str,artifact_id:str)->bytes:
        with self.db.connect() as cn:
            row=cn.execute("SELECT * FROM artifacts WHERE artifact_id=? AND project_id=?",(artifact_id,project_id)).fetchone()
        if row is None:raise HistoryError("HISTORY_ARTIFACT_OWNERSHIP_INVALID",404)
        uri=safe_asset_relative_path(row["storage_uri"])
        target=self.db.path.parent/uri
        if target.is_symlink() or not target.resolve().is_relative_to(self.db.path.parent.resolve()):
            raise HistoryError("HISTORY_ARTIFACT_PATH_INVALID",422)
        data=target.read_bytes()
        if hashlib.sha256(data).hexdigest()!=row["content_hash"]:
            raise HistoryError("HISTORY_ARTIFACT_INTEGRITY_FAILED",409)
        return data

    def compare_and_set_head(self,project_id:str,stage_key:str,artifact_id:str,expected_revision:int)->int:
        if stage_key not in STAGE_KEYS or type(expected_revision) is not int or expected_revision<0:
            raise HistoryError("HISTORY_HEAD_INVALID",422)
        with self.db.connect() as cn:
            try:
                cn.execute("BEGIN IMMEDIATE")
                artifact=cn.execute("SELECT project_id,kind,status FROM artifacts WHERE artifact_id=?",(artifact_id,)).fetchone()
                if artifact is None or artifact["project_id"]!=project_id or artifact["kind"]!=stage_key or artifact["status"]!="approved":
                    raise HistoryError("HISTORY_ARTIFACT_OWNERSHIP_INVALID",409)
                old=cn.execute("SELECT revision FROM stage_heads WHERE project_id=? AND stage_key=?",(project_id,stage_key)).fetchone()
                actual=old["revision"] if old else 0
                if actual!=expected_revision:
                    raise HistoryError("HISTORY_HEAD_STALE",409)
                revision=actual+1
                if old:
                    cn.execute("UPDATE stage_heads SET artifact_id=?,revision=?,updated_at=? WHERE project_id=? AND stage_key=?",
                               (artifact_id,revision,utcnow(),project_id,stage_key))
                else:
                    cn.execute("INSERT INTO stage_heads VALUES (?,?,?,?,?)",(project_id,stage_key,artifact_id,revision,utcnow()))
                cn.commit()
                return revision
            except BaseException:
                cn.rollback()
                raise

    def reserve_authoring(self,project_id:str,stage_key:str,parent_fingerprint:str,minutes:int=30):
        if stage_key not in STAGE_KEYS or not re.fullmatch(r"[0-9a-f]{64}",parent_fingerprint) or minutes<1 or minutes>120:
            raise HistoryError("HISTORY_RESERVATION_INVALID",422)
        with self.db.connect() as cn:
            cn.execute("BEGIN IMMEDIATE")
            if not cn.execute("SELECT 1 FROM projects WHERE project_id=?",(project_id,)).fetchone():
                raise HistoryError("HISTORY_PROJECT_NOT_FOUND",404)
            reservation=uid()
            cn.execute("INSERT INTO authoring_reservations VALUES (?,?,?,?,?,?,?)",
                       (reservation,project_id,stage_key,parent_fingerprint,"pending",utcnow(),
                        (datetime.now(timezone.utc)+timedelta(minutes=minutes)).isoformat(timespec="seconds")))
            cn.commit()
            return reservation
