"""Pure P2 strict schema+lineage and exact script validation; NO IO or model calls."""
from __future__ import annotations
import json
import hashlib
from pathlib import Path
from jsonschema import Draft202012Validator
from .db import strict_json, ROOT
from .errors import HistoryError

SCHEMA_DIR=ROOT/"contracts"/"history"/"schemas"
STAGE_SCHEMA={"topic":"topic_ideas.v1.json","script_analysis":"script_historical_analysis.v1.json"}
def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)
def digest(value):
    return hashlib.sha256((canonical(value) if not isinstance(value,bytes) else value).encode("utf8") if isinstance(value,str) else (canonical(value).encode("utf8") if not isinstance(value,bytes) else value)).hexdigest()
def check_schema(value,name):
    schema=json.loads((SCHEMA_DIR/name).read_text(encoding="utf8"))
    issues=[]
    for e in sorted(Draft202012Validator(schema).iter_errors(value),key=lambda e:(list(map(str,e.absolute_path)),e.message)):
        p="$"+"".join("["+str(x)+"]" if isinstance(x,int) else "."+str(x) for x in e.absolute_path)
        issues.append({"path":p,"code":"SCHEMA_"+e.validator.upper(),"message":e.message[:300],"expected":str(e.validator_value)[:120]})
    return issues
def fail(code,message,path="$"):
    raise HistoryError(code,422,json.dumps({"path":path,"message":message},ensure_ascii=False))
def parse_json(raw):
    if not isinstance(raw,str):fail("HISTORY_JSON_INVALID","Provide a UTF-8 JSON string")
    return strict_json(raw)
def validate_dna(value):
    return check_schema(value,"history_channel_dna.v1.json")
def validate_payload(stage,raw,reservation):
    value=parse_json(raw)
    errors=check_schema(value,STAGE_SCHEMA[stage])
    if errors:return value,errors
    for field,expected in (("project_id",reservation["project_id"]),("reservation_id",reservation["reservation_id"]),("source_fingerprint",reservation["source_hash"])):
        if value[field]!=expected:errors.append({"path":"$."+field,"code":"SOURCE_MISMATCH","message":"Must match frozen prompt identity"})
    ids=json.loads(reservation["ids_json"])
    if stage=="topic":
        found=[i["idea_id"] for i in value["ideas"]]
        if len(found)!=len(set(found)):errors.append({"path":"$.ideas","code":"DUPLICATE_ID","message":"Duplicate idea IDs"})
        if len(found)!=len(ids["ideas"]) or set(found)!=set(ids["ideas"]):
            errors.append({"path":"$.ideas","code":"RESERVED_IDS","message":"All and only server-reserved idea IDs required"})
    else:
        if value["parent_topic_artifact_id"]!=ids["parent_artifact_id"] or value["parent_topic_hash"]!=ids["parent_hash"]:
            errors.append({"path":"$.parent_topic_artifact_id","code":"PARENT_MISMATCH","message":"Exact approved Topic ID/hash required"})
        sections=value["sections"]
        section_ids=[s["section_id"] for s in sections]
        if section_ids!=ids["sections"]:
            errors.append({"path":"$.sections","code":"SECTION_SEQUENCE","message":"Sections must follow reserved IDs and order"})
        if value["full_script"]!="\n\n".join(s["exact_text"] for s in sections):
            errors.append({"path":"$.full_script","code":"EXACT_TEXT","message":"Exactly join ordered section exact_text with two LF characters; no normalization"})
        seen=set()
        for n,c in enumerate(value["claims"]):
            cp="$.claims["+str(n)+"]"
            if c["claim_id"] in seen:errors.append({"path":cp+".claim_id","code":"DUPLICATE_ID","message":"Claim identifier repeated"})
            seen.add(c["claim_id"])
            if c["claim_id"] not in ids["claims"]:errors.append({"path":cp+".claim_id","code":"RESERVED_IDS","message":"Claim ID not reserved"})
            if c["section_id"] not in section_ids:
                errors.append({"path":cp+".section_id","code":"SECTION_REF","message":"Unknown section"})
            else:
                idx=section_ids.index(c["section_id"])
                spoken=sections[idx]["exact_text"]
                a,b=c["start"],c["end"]
                if b<=a or b>len(spoken) or spoken[a:b]!=c["quote"]:
                    errors.append({"path":cp+".quote","code":"QUOTE_SPAN","message":"Claim span must quote exact section code-point offsets"})
    return value,errors
