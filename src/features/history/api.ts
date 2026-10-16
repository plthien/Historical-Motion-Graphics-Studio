export type Channel={channel_id:string;name:string;language:string;niche:string;history_dna:Record<string,unknown>;revision:number;created_at:string};
export type Project={project_id:string;channel_id:string;name:string;history_context:Record<string,unknown>;created_at:string};
export const STAGES=[
 {key:"topic",label:"Topic Ideas"},
 {key:"script_analysis",label:"Script & Historical Analysis"},
 {key:"voice",label:"Voice & Alignment"},
 {key:"assets",label:"Historical Assets"},
 {key:"visual_director",label:"Visual Director"},
 {key:"motion_timeline",label:"Motion Timeline"},
 {key:"final_video",label:"Render & Delivery"}
] as const;
export type StageKey=typeof STAGES[number]["key"];
export type Stage={key:StageKey;label:string;status:"blocked"|"ready"|"stale"|"approved";blocked_reason:string|null;revision:number};
export type ProjectStatus={project_id:string;channel_id:string;stages:Stage[]};
export async function request<T>(url:string,body?:object,method?:string):Promise<T>{
 let response:Response;
 try{response=await fetch(url,{method:method||(body?"POST":"GET"),headers:{"Accept":"application/json",...(body?{"Content-Type":"application/json"}:{})},...(body?{body:JSON.stringify(body)}:{})});}
 catch{throw Error("Cannot reach the local History API on port 8000");}
 if(!response.ok){
   const data=await response.json().catch(()=>null);
   throw Error(String(response.status)+": "+(data?.error?.code||data?.detail?.[0]?.msg||response.statusText));
 }
 return await response.json() as T;
}
export const api={
 channels:()=>request<Channel[]>("/api/history/channels"),
 channelCreate:(body:{name:string;language:string;niche:string;history_dna:Record<string,unknown>})=>request<Channel>("/api/history/channels",body),
 projects:(channel:string)=>request<Project[]>("/api/history/projects?channel_id="+encodeURIComponent(channel)),
 projectCreate:(body:{name:string;channel_id:string;history_context:Record<string,unknown>})=>request<Project>("/api/history/projects",body),
 status:(id:string)=>request<ProjectStatus>("/api/history/projects/"+encodeURIComponent(id)+"/status")
};

export type DnaDocument={schema_version:"HistoryChannelDNA.v1";language:string;audience:string;historical_niche:string;narration_style:string;editorial_style:string;map_treatment:string;uncertainty_policy:string};
export type DnaState={channel_id:string;revision:number;document:DnaDocument;content_hash:string}|null;
export type PromptState={reservation_id:string;project_id:string;stage:"topic"|"script_analysis";source_fingerprint:string;prompt:string;reserved_ids:Record<string,unknown>;created_at:string;expires_at:string;state:string;is_current:boolean};
export type CandidateMeta={candidate_id:string;reservation_id:string;stage:string;content_hash:string;status:string;created_at:string};
export type CandidateReview={candidate_id:string;status:string;content_hash:string;reservation_id:string;is_current:boolean;content:Record<string,unknown>};
export type Approved={artifact_id:string;revision:number;content_hash:string;source_fingerprint:string;content:Record<string,unknown>;stale:boolean;status:string}|null;
export type Validation={valid:boolean;errors:Array<{path:string;code:string;message:string;expected?:string}>;summary:{stage:string;items:number};value:Record<string,unknown>|null};
const path=(project:string,stage:"topic"|"script_analysis")=>"/api/history/projects/"+encodeURIComponent(project)+"/"+(stage==="topic"?"topic":"script-analysis");
export const authoring={
 dnaGet:(channel:string)=>request<DnaState>("/api/history/channels/"+encodeURIComponent(channel)+"/dna"),
 dnaSave:(channel:string,document:DnaDocument,expected_revision:number)=>request<NonNullable<DnaState>>("/api/history/channels/"+encodeURIComponent(channel)+"/dna",{document,expected_revision},"PUT"),
 prompt:(project:string,stage:"topic"|"script_analysis",body:Record<string,unknown>)=>request<PromptState>(path(project,stage)+"/prompt",body),
 promptGet:(project:string,stage:"topic"|"script_analysis")=>request<PromptState>(path(project,stage)+"/prompt"),
 validate:(project:string,stage:"topic"|"script_analysis",reservation_id:string,raw_json:string)=>request<Validation>(path(project,stage)+"/import/validate",{reservation_id,raw_json}),
 importCandidate:(project:string,stage:"topic"|"script_analysis",reservation_id:string,raw_json:string)=>request<CandidateMeta>(path(project,stage)+"/import",{reservation_id,raw_json}),
 candidates:(project:string,stage:"topic"|"script_analysis")=>request<CandidateMeta[]>(path(project,stage)+"/candidates"),
 review:(project:string,stage:"topic"|"script_analysis",id:string)=>request<CandidateReview>(path(project,stage)+(stage==="topic"?"/candidates/"+encodeURIComponent(id):"/review?candidate_id="+encodeURIComponent(id))),
 approveTopic:(project:string,candidate_id:string,idea_id:string,expected_revision:number)=>request<NonNullable<Approved>>(path(project,"topic")+"/approve",{candidate_id,idea_id,expected_revision}),
 approveScript:(project:string,candidate_id:string,expected_revision:number)=>request<NonNullable<Approved>>(path(project,"script_analysis")+"/approve",{candidate_id,expected_revision}),
 manualTopic:(project:string,topic:Record<string,unknown>,expected_revision:number)=>request<NonNullable<Approved>>(path(project,"topic")+"/manual",{topic,expected_revision}),
 current:(project:string,stage:"topic"|"script_analysis")=>request<Approved>(path(project,stage)+"/current"),
 repair:(project:string,stage:"topic"|"script_analysis",reservation_id:string,raw_json:string)=>request<{prompt:string;errors:Validation["errors"]}>(path(project,stage)+"/repair-prompt",{reservation_id,raw_json})
};

export type HistoricalRevision={artifact_id:string;version:number;content_hash:string;source_fingerprint:string;parent_artifact_id:string|null;created_at:string;content:Record<string,unknown>};
export const revisionHistory=(project:string,stage:"topic"|"script_analysis")=>request<HistoricalRevision[]>(
 "/api/history/projects/"+encodeURIComponent(project)+"/"+(stage==="topic"?"topic":"script-analysis")+"/history");
