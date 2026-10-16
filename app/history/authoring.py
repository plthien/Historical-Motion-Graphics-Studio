"""Durable P2 manual External ChatGPT authoring. No provider/network/model code."""
from __future__ import annotations
import hashlib,json,os,sqlite3,uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
from .db import HistoryDB
from .errors import HistoryError
from .validation import canonical,digest,validate_dna,validate_payload,fail,check_schema
from .service import safe_asset_relative_path,utcnow

STAGES=("topic","script_analysis")
def ident():return uuid.uuid4().hex
def now():return datetime.now(timezone.utc)
def hbytes(b):return hashlib.sha256(b).hexdigest()
def problem(code,status=409,message=None):raise HistoryError(code,status,message)
def project(cn,project_id):
    row=cn.execute("SELECT * FROM projects WHERE project_id=?",(project_id,)).fetchone()
    if row is None:problem("HISTORY_PROJECT_NOT_FOUND",404)
    return dict(row)
def channel(cn,channel_id):
    row=cn.execute("SELECT * FROM channels WHERE channel_id=?",(channel_id,)).fetchone()
    if row is None:problem("HISTORY_CHANNEL_NOT_FOUND",404)
    return dict(row)
def dna(cn,channel_id):
    row=cn.execute("SELECT * FROM p2_channel_dna WHERE channel_id=?",(channel_id,)).fetchone()
    if not row:return None
    return {"channel_id":channel_id,"revision":row["revision"],"content_hash":row["content_hash"],"document":json.loads(row["json_text"])}
def head(cn,project_id,stage):
    row=cn.execute("""SELECT h.revision,h.artifact_id,a.content_hash,l.source_hash,l.parent_artifact_id,l.channel_dna_hash
      FROM stage_heads h JOIN artifacts a ON a.artifact_id=h.artifact_id
      LEFT JOIN p2_approvals l ON l.artifact_id=h.artifact_id
      WHERE h.project_id=? AND h.stage_key=?""",(project_id,stage)).fetchone()
    return dict(row) if row else None

class HistoryAuthoring:
    def __init__(self,db:HistoryDB):self.db=db
    def dna_get(self,channel_id):
        with self.db.connect() as cn:
            channel(cn,channel_id)
            return dna(cn,channel_id)
    def dna_save(self,channel_id,document,expected_revision):
        errors=validate_dna(document)
        if errors:problem("HISTORY_DNA_INVALID",422,canonical(errors))
        if type(expected_revision) is not int or expected_revision<0:problem("HISTORY_DNA_REVISION_INVALID",422)
        data=canonical(document);hash_value=hbytes(data.encode("utf8"))
        with self.db.connect() as cn:
            cn.execute("BEGIN IMMEDIATE")
            channel(cn,channel_id)
            old=dna(cn,channel_id)
            revision=old["revision"] if old else 0
            if revision!=expected_revision:problem("HISTORY_HEAD_STALE",409)
            if old and old["content_hash"]==hash_value:return old
            new=revision+1
            if old:
                cn.execute("UPDATE p2_channel_dna SET revision=?,json_text=?,content_hash=?,updated_at=? WHERE channel_id=?",
                           (new,data,hash_value,utcnow(),channel_id))
            else:
                cn.execute("INSERT INTO p2_channel_dna VALUES (?,?,?,?,?)",(channel_id,new,data,hash_value,utcnow()))
            cn.execute("INSERT INTO p2_channel_dna_history VALUES (?,?,?,?)",(channel_id,new,data,hash_value))
            cn.commit()
            return {"channel_id":channel_id,"revision":new,"document":document,"content_hash":hash_value}

    def _context(self,cn,project_id,stage,options=None):
        p=project(cn,project_id)
        ch=channel(cn,p["channel_id"])
        cd=dna(cn,p["channel_id"])
        if cd is None:problem("HISTORY_CHANNEL_DNA_REQUIRED",409)
        source={"project_id":project_id,"project_name":p["name"],"project_context":json.loads(p["history_context_json"]),
                "channel_id":ch["channel_id"],"channel_name":ch["name"],"channel_revision":ch["revision"],
                "dna_revision":cd["revision"],"dna_hash":cd["content_hash"],"dna":cd["document"],
                "stage_schema":("TopicIdeas.v1" if stage=="topic" else "ScriptHistoricalAnalysis.v1")}
        if stage=="script_analysis":
            t=self._current(cn,project_id,"topic")
            if not t or t["stale"]:problem("HISTORY_TOPIC_NOT_APPROVED",409)
            source["approved_topic_artifact_id"]=t["artifact_id"]
            source["approved_topic_hash"]=t["content_hash"]
            source["selected_topic"]=t["content"]
        if options is not None:source["constraints"]=options
        return source

    def _context_current(self,cn,prompt):
        old=json.loads(prompt["input_json"])["source"]
        # Options from reservation are immutable; current snapshot uses saved constraints.
        new=self._context(cn,prompt["project_id"],prompt["stage_key"],old.get("constraints"))
        return canonical(new)==canonical(old)

    def _prompt_row(self,cn,project_id,stage,reservation_id=None):
        if reservation_id:
            row=cn.execute("SELECT * FROM p2_prompts WHERE reservation_id=? AND project_id=? AND stage_key=?",
                           (reservation_id,project_id,stage)).fetchone()
        else:
            row=cn.execute("""SELECT * FROM p2_prompts WHERE project_id=? AND stage_key=?
                 ORDER BY created_at DESC,rowid DESC LIMIT 1""",(project_id,stage)).fetchone()
        if row is None:problem("HISTORY_PROMPT_NOT_FOUND",404)
        return dict(row)

    def _assert_prompt(self,cn,p,allow_imported=False):
        if p["status"] not in (("pending","imported") if allow_imported else ("pending",)):
            problem("HISTORY_RESERVATION_CONSUMED",409)
        if datetime.fromisoformat(p["expires_at"])<=now():problem("HISTORY_RESERVATION_EXPIRED",409)
        if not self._context_current(cn,p):problem("HISTORY_SOURCE_STALE",409)

    def _prompt_result(self,cn,p):
        try:self._assert_prompt(cn,p,allow_imported=True);valid=True
        except HistoryError:valid=False
        return {"reservation_id":p["reservation_id"],"project_id":p["project_id"],"stage":p["stage_key"],
                "source_fingerprint":p["source_hash"],"prompt":p["prompt_text"],"reserved_ids":json.loads(p["ids_json"]),
                "created_at":p["created_at"],"expires_at":p["expires_at"],"state":p["status"],"is_current":valid}

    def build_prompt(self,project_id,stage,options):
        if stage not in STAGES:problem("HISTORY_STAGE_INVALID",422)
        if stage=="topic":
            count=options.get("count",5)
            if type(count) is not int or not 1<=count<=8:problem("HISTORY_COUNT_INVALID",422)
            avoidance=options.get("avoid",[])
            if not isinstance(avoidance,list) or len(avoidance)>20 or any(not isinstance(x,str) or len(x)>200 for x in avoidance):
                problem("HISTORY_AVOID_INVALID",422)
            options={"count":count,"avoid":avoidance,"emphasis":str(options.get("emphasis",""))[:350]}
        else:
            count=options.get("section_count",3)
            if type(count) is not int or not 1<=count<=8:problem("HISTORY_COUNT_INVALID",422)
            options={"section_count":count,"notes":str(options.get("notes",""))[:500]}
        with self.db.connect() as cn:
            cn.execute("BEGIN IMMEDIATE")
            source=self._context(cn,project_id,stage,options)
            rid=ident()
            if stage=="topic":
                ids={"ideas":["idea_"+ident()[:8] for _ in range(count)]}
            else:
                ids={"sections":[f"sec_{i:03d}" for i in range(1,count+1)],
                     "claims":[f"claim_{i:03d}" for i in range(1,21)],
                     "parent_artifact_id":source["approved_topic_artifact_id"],
                     "parent_hash":source["approved_topic_hash"]}
            fingerprint=digest({"source":source,"reservation_id":rid,"ids":ids})
            filename=("topic_ideas.v1.json" if stage=="topic" else "script_historical_analysis.v1.json")
            schema=json.loads((Path(__file__).resolve().parents[2]/"contracts/history/schemas"/filename).read_text(encoding="utf8"))
            language=source["dna"]["language"]
            task=(
                "Propose historically informed video topic ideas. Fictional reconstructions must be labeled. Avoid duplicate or overdone topics."
                if stage=="topic" else (
                    f"Write one combined exact spoken script in {language} and "
                    "factual/hypothetical historical claims analysis for the approved selected topic. "
                    f"The spoken text in full_script and every section.exact_text must be in {language}. "
                    "Return the result as strict machine-readable JSON."
                )
            )
            instructions={"task":task,"frozen_input_explained":source,"reservation_id":rid,"project_id":project_id,
               "source_fingerprint":fingerprint,"reserved_ids":ids,
               "required_output":{"schema_version":("TopicIdeas.v1" if stage=="topic" else "ScriptHistoricalAnalysis.v1"),
                   "project_id":project_id,"reservation_id":rid,"source_fingerprint":fingerprint},
               "required_contract":schema,"important":["You are an external manually operated writing assistant, not a connected API.",
               "Return one UTF-8 JSON file only, downloadable by the user. No Markdown fences, comments or extra fields.",
               "Every ID must come from reserved_ids. Never invent IDs or sources; source leads are unverified research suggestions.",
               "Use each reserved idea ID exactly once in TopicIdeas output." if stage=="topic" else
                 "Use each section ID in order. full_script must equal section.exact_text joined by two LF characters (\\n\\n); claim spans use zero-based Python/Unicode codepoint offsets within exact_text.",
               "Claims labeled 'verified' by a model still have evidence_review_status='unreviewed' until separate human/source evidence review.",
               "Do not make up source verification. Mark hypothetical reconstructions plainly."]}
            prompt="HISTORICAL MOTION GRAPHICS STUDIO — MANUAL EXTERNAL CHATGPT AUTHORING\n"+json.dumps(instructions,indent=2,ensure_ascii=False)
            cn.execute("UPDATE p2_prompts SET status='superseded' WHERE project_id=? AND stage_key=? AND status IN ('pending','imported')",(project_id,stage))
            created=utcnow();expiry=(now()+timedelta(minutes=90)).isoformat(timespec="seconds")
            cn.execute("INSERT INTO p2_prompts VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (rid,project_id,stage,fingerprint,canonical({"source":source}),prompt,canonical(ids),"pending",created,expiry))
            cn.commit()
            return {"reservation_id":rid,"project_id":project_id,"stage":stage,"source_fingerprint":fingerprint,
                "prompt":prompt,"reserved_ids":ids,"created_at":created,"expires_at":expiry,"state":"pending","is_current":True}

    def get_prompt(self,project_id,stage):
        with self.db.connect() as cn:return self._prompt_result(cn,self._prompt_row(cn,project_id,stage))

    def validate_import(self,project_id,stage,reservation_id,raw):
        with self.db.connect() as cn:
            p=self._prompt_row(cn,project_id,stage,reservation_id)
            self._assert_prompt(cn,p)
            try:
                val,errors=validate_payload(stage,raw,p)
            except HistoryError as e:
                if e.code not in ("HISTORY_JSON_INVALID","HISTORY_JSON_DUPLICATE_KEY"):
                    raise
                val=None
                errors=[{"path":"$","code":e.code,"message":e.message,
                         "expected":"Valid UTF-8 JSON object with no duplicate keys",
                         "actual":"Malformed or ambiguous JSON input"}]
            items=(val.get("ideas") if stage=="topic" else val.get("sections")) if isinstance(val,dict) else []
            return {"valid":not errors,"errors":errors,"summary":{"stage":stage,"items":len(items) if isinstance(items,list) else 0},"value":val if not errors else None}

    def import_candidate(self,project_id,stage,reservation_id,raw):
        # Revalidation INSIDE single write transaction, no side effects on invalid import.
        with self.db.connect() as cn:
            cn.execute("BEGIN IMMEDIATE")
            p=self._prompt_row(cn,project_id,stage,reservation_id)
            self._assert_prompt(cn,p)
            val,errors=validate_payload(stage,raw,p)
            if errors:problem("HISTORY_IMPORT_INVALID",422,canonical(errors))
            candidate_id=ident();data=raw.encode("utf8");hash_value=hbytes(data)
            cn.execute("INSERT INTO p2_candidates VALUES (?,?,?,?,?,?,?,?,?)",
                       (candidate_id,reservation_id,project_id,stage,p["source_hash"],data,hash_value,"candidate",utcnow()))
            cn.execute("UPDATE p2_prompts SET status='imported' WHERE reservation_id=?",(reservation_id,))
            cn.commit()
            return {"candidate_id":candidate_id,"content_hash":hash_value,"status":"candidate","stage":stage,"summary":{"items":len(val.get("ideas",val.get("sections",[])))}}

    def candidates(self,project_id,stage):
        with self.db.connect() as cn:
            project(cn,project_id)
            return [{"candidate_id":r["candidate_id"],"reservation_id":r["reservation_id"],"stage":stage,
                "content_hash":r["content_hash"],"status":r["status"],"created_at":r["created_at"]}
                for r in cn.execute("SELECT * FROM p2_candidates WHERE project_id=? AND stage_key=? ORDER BY created_at DESC,rowid DESC",(project_id,stage))]

    def review(self,project_id,stage,candidate_id):
        with self.db.connect() as cn:
            row=cn.execute("SELECT * FROM p2_candidates WHERE candidate_id=? AND project_id=? AND stage_key=?",
                (candidate_id,project_id,stage)).fetchone()
            if row is None:problem("HISTORY_CANDIDATE_NOT_FOUND",404)
            p=self._prompt_row(cn,project_id,stage,row["reservation_id"])
            # Review old versions is allowed; current status separately indicated.
            try:self._assert_prompt(cn,p,True);current=True
            except HistoryError:current=False
            return {"candidate_id":candidate_id,"status":row["status"],"content_hash":row["content_hash"],
                    "reservation_id":row["reservation_id"],"is_current":current,"content":json.loads(row["content_bytes"])}

    def _artifact_publish(self,cn,project_id,stage,doc,expected_revision,source_hash,parent_id,dna_hash):
        existing=head(cn,project_id,stage)
        revision=existing["revision"] if existing else 0
        if revision!=expected_revision:problem("HISTORY_HEAD_STALE",409)
        data=canonical(doc).encode("utf8");sha=hbytes(data)
        uri=safe_asset_relative_path("artifacts/"+sha+".json")
        storage=self.db.path.parent.resolve();directory=storage/"artifacts";directory.mkdir(parents=True,exist_ok=True)
        if directory.is_symlink() or directory.resolve()!=storage/"artifacts":problem("HISTORY_ARTIFACT_PATH_INVALID",422)
        target=directory/(sha+".json")
        try:
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            try:
                with os.fdopen(fd,"wb") as f:f.write(data);f.flush();os.fsync(f.fileno())
            except BaseException:
                target.unlink(missing_ok=True);raise
        except FileExistsError:
            if target.is_symlink() or not target.is_file() or target.read_bytes()!=data:problem("HISTORY_ARTIFACT_HASH_COLLISION",409)
        aid=ident()
        version=cn.execute("SELECT COALESCE(MAX(version),0)+1 FROM artifacts WHERE project_id=? AND kind=?",
                  (project_id,stage)).fetchone()[0]
        cn.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
                   (aid,project_id,stage,sha,uri,"approved",version,utcnow()))
        if existing:
            cn.execute("UPDATE stage_heads SET artifact_id=?,revision=?,updated_at=? WHERE project_id=? AND stage_key=?",
                       (aid,revision+1,utcnow(),project_id,stage))
        else:
            cn.execute("INSERT INTO stage_heads VALUES (?,?,?,?,?)",(project_id,stage,aid,revision+1,utcnow()))
        cn.execute("INSERT INTO p2_approvals VALUES (?,?,?,?,?,?,?)",
                   (aid,project_id,stage,source_hash,parent_id,dna_hash,utcnow()))
        return {"artifact_id":aid,"revision":revision+1,"content_hash":sha,"content":doc,"source_fingerprint":source_hash}

    def approve(self,project_id,stage,candidate_id,expected_revision,idea_id=None):
        with self.db.connect() as cn:
            cn.execute("BEGIN IMMEDIATE")
            row=cn.execute("SELECT * FROM p2_candidates WHERE candidate_id=? AND project_id=? AND stage_key=?",
                           (candidate_id,project_id,stage)).fetchone()
            if row is None:problem("HISTORY_CANDIDATE_NOT_FOUND",404)
            if row["status"]!="candidate":problem("HISTORY_CANDIDATE_CONSUMED",409)
            p=self._prompt_row(cn,project_id,stage,row["reservation_id"])
            self._assert_prompt(cn,p,True)
            payload=json.loads(row["content_bytes"])
            if hbytes(row["content_bytes"])!=row["content_hash"]:problem("HISTORY_ARTIFACT_INTEGRITY_FAILED",409)
            cd=dna(cn,project(cn,project_id)["channel_id"])
            ids=json.loads(p["ids_json"])
            parent_id=None
            if stage=="topic":
                ideas={x["idea_id"]:x for x in payload["ideas"]}
                if idea_id not in ideas:problem("HISTORY_IDEA_NOT_FOUND",422)
                chosen=dict(ideas[idea_id]);chosen.pop("idea_id")
                doc={"schema_version":"SelectedTopic.v1","project_id":project_id,"source_fingerprint":p["source_hash"],
                     "origin":"imported","source_candidate_id":candidate_id,"selected_idea_id":idea_id,"topic":chosen}
            else:
                approved_topic=self._current(cn,project_id,"topic")
                if not approved_topic or approved_topic["stale"] or approved_topic["artifact_id"]!=ids["parent_artifact_id"]:
                    problem("HISTORY_SOURCE_STALE",409)
                parent_id=approved_topic["artifact_id"];doc=payload
            artifact=self._artifact_publish(cn,project_id,stage,doc,expected_revision,p["source_hash"],parent_id,cd["content_hash"])
            cn.execute("UPDATE p2_candidates SET status='approved' WHERE candidate_id=?",(candidate_id,))
            cn.execute("UPDATE p2_prompts SET status='consumed' WHERE reservation_id=?",(p["reservation_id"],))
            cn.commit()
            return artifact

    def manual_topic(self,project_id,topic,expected_revision):
        errors=check_schema(topic,"selected_topic.v1.json")
        # The direct editor sends a SelectedTopic document; server overwrites all
        # derived IDs/source fields after strict topic-specific checks.
        from .validation import SCHEMA_DIR
        schema=json.loads((SCHEMA_DIR/"selected_topic.v1.json").read_text(encoding="utf8"))["properties"]["topic"]
        from jsonschema import Draft202012Validator
        es=list(Draft202012Validator(schema).iter_errors(topic))
        if es:problem("HISTORY_TOPIC_INVALID",422,canonical([{"path":"$.topic","message":e.message} for e in es]))
        with self.db.connect() as cn:
            cn.execute("BEGIN IMMEDIATE")
            source=self._context(cn,project_id,"topic",{"origin":"manual"})
            source_hash=digest(source);ch=dna(cn,project(cn,project_id)["channel_id"])
            doc={"schema_version":"SelectedTopic.v1","project_id":project_id,"source_fingerprint":source_hash,
                 "origin":"manual","source_candidate_id":None,"selected_idea_id":None,"topic":topic}
            result=self._artifact_publish(cn,project_id,"topic",doc,expected_revision,source_hash,None,ch["content_hash"])
            cn.commit();return result

    def _current(self,cn,project_id,stage):
        record=head(cn,project_id,stage)
        if record is None:return None
        row=cn.execute("SELECT storage_uri FROM artifacts WHERE artifact_id=?",(record["artifact_id"],)).fetchone()
        if row is None:problem("HISTORY_ARTIFACT_INTEGRITY_FAILED",409)
        # Read immutable bytes with P1 integrity checks and ownership.
        from .service import HistoryService
        content=json.loads(HistoryService(self.db).load_artifact(project_id,record["artifact_id"]))
        ch=dna(cn,project(cn,project_id)["channel_id"])
        stale=bool(record["source_hash"] is not None and (not ch or record["channel_dna_hash"]!=ch["content_hash"]))
        if stage=="script_analysis" and record["source_hash"] is not None:
            t=head(cn,project_id,"topic")
            stale=stale or not t or t["artifact_id"]!=record["parent_artifact_id"]
            if t and record["parent_artifact_id"]==t["artifact_id"]:
                stale=stale or t["channel_dna_hash"]!=ch["content_hash"]
        return {"artifact_id":record["artifact_id"],"revision":record["revision"],
                "content_hash":record["content_hash"],"source_fingerprint":record["source_hash"],
                "content":content,"stale":bool(stale),"status":"stale" if stale else "approved"}

    def current(self,project_id,stage):
        with self.db.connect() as cn:
            project(cn,project_id)
            return self._current(cn,project_id,stage)

    def history(self,project_id,stage):
        if stage not in STAGES:problem("HISTORY_STAGE_INVALID",422)
        from .service import HistoryService
        with self.db.connect() as cn:
            project(cn,project_id)
            rows=cn.execute("""SELECT a.artifact_id,a.version,a.content_hash,a.created_at,
               l.source_hash,l.parent_artifact_id
               FROM artifacts a JOIN p2_approvals l ON l.artifact_id=a.artifact_id
               WHERE a.project_id=? AND a.kind=? AND a.status='approved'
               ORDER BY a.version DESC""",(project_id,stage)).fetchall()
        storage=HistoryService(self.db)
        return [{"artifact_id":r["artifact_id"],"version":r["version"],
                 "content_hash":r["content_hash"],"source_fingerprint":r["source_hash"],
                 "parent_artifact_id":r["parent_artifact_id"],
                 "created_at":r["created_at"],
                 "content":json.loads(storage.load_artifact(project_id,r["artifact_id"]))}
                for r in rows]

    def repair_prompt(self,project_id,stage,reservation_id,raw):
        try:
            validation=self.validate_import(project_id,stage,reservation_id,raw)
            errors=validation["errors"]
        except HistoryError as e:
            errors=[{"path":"$","code":e.code,"message":e.message}]
        with self.db.connect() as cn:
            p=self._prompt_row(cn,project_id,stage,reservation_id)
        prompt="MANUAL EXTERNAL CHATGPT TARGETED JSON REPAIR\n"+json.dumps({
            "frozen_prompt":p["prompt_text"],"invalid_paths":errors,
            "strict_rule":"Only repair invalid fields. Preserve valid sections, IDs, exact quoted text and previously approved artifacts. Return downloadable UTF-8 JSON only. Never invent verified evidence.",
            "source_fingerprint":p["source_hash"],"reservation_id":reservation_id},ensure_ascii=False,indent=2)
        return {"prompt":prompt,"errors":errors}
