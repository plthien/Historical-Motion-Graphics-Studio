"""P2 manual External ChatGPT Topic/Script API on the ONE History app."""
from typing import Any
from fastapi import APIRouter,Depends
from pydantic import Field
from .domain import StrictModel
from .authoring import HistoryAuthoring
from .errors import HistoryError

router=APIRouter()
def service():raise RuntimeError("HistoryAuthoring dependency must be overridden by app.main")

class DNAWrite(StrictModel):
    document:dict[str,Any]
    expected_revision:int=Field(ge=0)
class PromptTopic(StrictModel):
    count:int=Field(default=5,ge=1,le=8)
    avoid:list[str]=Field(default_factory=list,max_length=20)
    emphasis:str=Field(default="",max_length=350)
class PromptScript(StrictModel):
    section_count:int=Field(default=3,ge=1,le=8)
    notes:str=Field(default="",max_length=500)
class RawImport(StrictModel):
    reservation_id:str=Field(min_length=32,max_length=32)
    raw_json:str=Field(max_length=1100000)
class TopicApproval(StrictModel):
    candidate_id:str=Field(min_length=32,max_length=32)
    idea_id:str=Field(min_length=13,max_length=13)
    expected_revision:int=Field(ge=0)
class ScriptApproval(StrictModel):
    candidate_id:str=Field(min_length=32,max_length=32)
    expected_revision:int=Field(ge=0)
class ManualTopic(StrictModel):
    topic:dict[str,Any]
    expected_revision:int=Field(ge=0)
class RepairRequest(RawImport):pass

class DnaState(StrictModel):
    channel_id:str
    revision:int
    content_hash:str
    document:dict[str,Any]
class PromptState(StrictModel):
    reservation_id:str
    project_id:str
    stage:str
    source_fingerprint:str
    prompt:str
    reserved_ids:dict[str,Any]
    created_at:str
    expires_at:str
    state:str
    is_current:bool
class ValidationResponse(StrictModel):
    valid:bool
    errors:list[dict[str,Any]]
    summary:dict[str,Any]
    value:dict[str,Any]|None
class CandidateMeta(StrictModel):
    candidate_id:str
    reservation_id:str|None=None
    stage:str
    content_hash:str
    status:str
    created_at:str|None=None
    summary:dict[str,Any]|None=None
class CandidateReview(StrictModel):
    candidate_id:str
    status:str
    content_hash:str
    reservation_id:str
    is_current:bool
    content:dict[str,Any]
class Approved(StrictModel):
    artifact_id:str
    revision:int
    content_hash:str
    source_fingerprint:str
    content:dict[str,Any]
    stale:bool|None=None
    status:str|None=None
class RepairResult(StrictModel):
    prompt:str
    errors:list[dict[str,Any]]
class Revision(StrictModel):
    artifact_id:str
    version:int
    content_hash:str
    source_fingerprint:str
    parent_artifact_id:str|None
    created_at:str
    content:dict[str,Any]

@router.get("/api/history/channels/{channel_id}/dna",response_model=DnaState|None)
def dna_get(channel_id:str,s:HistoryAuthoring=Depends(service)):
    return s.dna_get(channel_id)
@router.put("/api/history/channels/{channel_id}/dna",response_model=DnaState)
def dna_save(channel_id:str,body:DNAWrite,s:HistoryAuthoring=Depends(service)):
    return s.dna_save(channel_id,body.document,body.expected_revision)

@router.post("/api/history/projects/{project_id}/topic/prompt",response_model=PromptState)
def topic_prompt(project_id:str,body:PromptTopic,s:HistoryAuthoring=Depends(service)):
    return s.build_prompt(project_id,"topic",body.model_dump())
@router.get("/api/history/projects/{project_id}/topic/prompt",response_model=PromptState)
def topic_prompt_current(project_id:str,s:HistoryAuthoring=Depends(service)):
    return s.get_prompt(project_id,"topic")
@router.post("/api/history/projects/{project_id}/topic/import/validate",response_model=ValidationResponse)
def topic_validate(project_id:str,body:RawImport,s:HistoryAuthoring=Depends(service)):
    return s.validate_import(project_id,"topic",body.reservation_id,body.raw_json)
@router.post("/api/history/projects/{project_id}/topic/import",status_code=201,response_model=CandidateMeta)
def topic_import(project_id:str,body:RawImport,s:HistoryAuthoring=Depends(service)):
    return s.import_candidate(project_id,"topic",body.reservation_id,body.raw_json)
@router.get("/api/history/projects/{project_id}/topic/candidates",response_model=list[CandidateMeta])
def topic_candidates(project_id:str,s:HistoryAuthoring=Depends(service)):
    return s.candidates(project_id,"topic")
@router.get("/api/history/projects/{project_id}/topic/candidates/{candidate_id}",response_model=CandidateReview)
def topic_candidate(project_id:str,candidate_id:str,s:HistoryAuthoring=Depends(service)):
    return s.review(project_id,"topic",candidate_id)
@router.post("/api/history/projects/{project_id}/topic/approve",response_model=Approved)
def topic_approve(project_id:str,body:TopicApproval,s:HistoryAuthoring=Depends(service)):
    return s.approve(project_id,"topic",body.candidate_id,body.expected_revision,body.idea_id)
@router.post("/api/history/projects/{project_id}/topic/manual",response_model=Approved)
def topic_manual(project_id:str,body:ManualTopic,s:HistoryAuthoring=Depends(service)):
    return s.manual_topic(project_id,body.topic,body.expected_revision)
@router.get("/api/history/projects/{project_id}/topic/current",response_model=Approved|None)
def topic_current(project_id:str,s:HistoryAuthoring=Depends(service)):
    return s.current(project_id,"topic")
@router.post("/api/history/projects/{project_id}/topic/repair-prompt",response_model=RepairResult)
def topic_repair(project_id:str,body:RepairRequest,s:HistoryAuthoring=Depends(service)):
    return s.repair_prompt(project_id,"topic",body.reservation_id,body.raw_json)

@router.post("/api/history/projects/{project_id}/script-analysis/prompt",response_model=PromptState)
def script_prompt(project_id:str,body:PromptScript,s:HistoryAuthoring=Depends(service)):
    return s.build_prompt(project_id,"script_analysis",body.model_dump())
@router.get("/api/history/projects/{project_id}/script-analysis/prompt",response_model=PromptState)
def script_prompt_current(project_id:str,s:HistoryAuthoring=Depends(service)):
    return s.get_prompt(project_id,"script_analysis")
@router.post("/api/history/projects/{project_id}/script-analysis/import/validate",response_model=ValidationResponse)
def script_validate(project_id:str,body:RawImport,s:HistoryAuthoring=Depends(service)):
    return s.validate_import(project_id,"script_analysis",body.reservation_id,body.raw_json)
@router.post("/api/history/projects/{project_id}/script-analysis/import",status_code=201,response_model=CandidateMeta)
def script_import(project_id:str,body:RawImport,s:HistoryAuthoring=Depends(service)):
    return s.import_candidate(project_id,"script_analysis",body.reservation_id,body.raw_json)
@router.get("/api/history/projects/{project_id}/script-analysis/candidates",response_model=list[CandidateMeta])
def script_candidates(project_id:str,s:HistoryAuthoring=Depends(service)):
    return s.candidates(project_id,"script_analysis")
@router.get("/api/history/projects/{project_id}/script-analysis/review",response_model=CandidateReview)
def script_review(project_id:str,candidate_id:str,s:HistoryAuthoring=Depends(service)):
    return s.review(project_id,"script_analysis",candidate_id)
@router.post("/api/history/projects/{project_id}/script-analysis/approve",response_model=Approved)
def script_approve(project_id:str,body:ScriptApproval,s:HistoryAuthoring=Depends(service)):
    return s.approve(project_id,"script_analysis",body.candidate_id,body.expected_revision)
@router.get("/api/history/projects/{project_id}/script-analysis/current",response_model=Approved|None)
def script_current(project_id:str,s:HistoryAuthoring=Depends(service)):
    return s.current(project_id,"script_analysis")
@router.post("/api/history/projects/{project_id}/script-analysis/repair-prompt",response_model=RepairResult)
def script_repair(project_id:str,body:RepairRequest,s:HistoryAuthoring=Depends(service)):
    return s.repair_prompt(project_id,"script_analysis",body.reservation_id,body.raw_json)

@router.get("/api/history/projects/{project_id}/topic/history",response_model=list[Revision])
def topic_history(project_id:str,s:HistoryAuthoring=Depends(service)):
    return s.history(project_id,"topic")
@router.get("/api/history/projects/{project_id}/script-analysis/history",response_model=list[Revision])
def script_history(project_id:str,s:HistoryAuthoring=Depends(service)):
    return s.history(project_id,"script_analysis")
