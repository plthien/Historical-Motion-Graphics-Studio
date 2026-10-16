import {useEffect,useState,type ChangeEvent} from "react";
import {authoring,type PromptState,type CandidateMeta,type CandidateReview,type Approved,type Validation} from "../api";

type Stage="topic"|"script_analysis";
function download(name:string,text:string){
 const url=URL.createObjectURL(new Blob([text],{type:"text/plain;charset=utf-8"}));
 const a=document.createElement("a");a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function copy(text:string){await navigator.clipboard.writeText(text);}
function inspect(value:unknown){return JSON.stringify(value,null,2);}
export function AuthoringWorkspace({projectId,stage,onChanged}:{projectId:string;stage:Stage;onChanged:()=>void}){
 const [prompt,setPrompt]=useState<PromptState|null>(null),[candidateList,setCandidateList]=useState<CandidateMeta[]>([]);
 const [approved,setApproved]=useState<Approved>(null),[review,setReview]=useState<CandidateReview|null>(null);
 const [raw,setRaw]=useState(""),[fileName,setFileName]=useState(""),[validation,setValidation]=useState<Validation|null>(null);
 const [repair,setRepair]=useState(""),[error,setError]=useState(""),[notice,setNotice]=useState("");
 const [busy,setBusy]=useState(false),[count,setCount]=useState(3),[avoid,setAvoid]=useState("");
 const [selectedIdea,setSelectedIdea]=useState("");
 const [manual,setManual]=useState({title:"",hook:"",historical_period:"",region:"",story_angle:"",novelty_questions:["What remains uncertain?"],factual_risks:["Verify primary evidence independently"]});
 const refresh=async()=>{const [pr,candidates,current]=await Promise.allSettled([
 authoring.promptGet(projectId,stage),authoring.candidates(projectId,stage),authoring.current(projectId,stage)]);
 setPrompt(pr.status==="fulfilled"?pr.value:null);setCandidateList(candidates.status==="fulfilled"?candidates.value:[]);
 setApproved(current.status==="fulfilled"?current.value:null);
 };
 useEffect(()=>{let live=true;setPrompt(null);setReview(null);setRaw("");setValidation(null);setRepair("");
 Promise.allSettled([authoring.promptGet(projectId,stage),authoring.candidates(projectId,stage),authoring.current(projectId,stage)])
 .then(([p,c,a])=>{if(!live)return;setPrompt(p.status==="fulfilled"?p.value:null);
 setCandidateList(c.status==="fulfilled"?c.value:[]);
 setApproved(a.status==="fulfilled"?a.value:null);});
 return()=>{live=false};},[projectId,stage]);
 async function action(fn:()=>Promise<void>){setBusy(true);setError("");setNotice("");
 try{await fn()}catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}}
 const current=Boolean(prompt?.is_current&&prompt.state==="pending");
 const canImport=Boolean(prompt?.is_current&&prompt.state==="pending"&&raw.length>0);
 async function upload(e:ChangeEvent<HTMLInputElement>){
 const f=e.target.files?.[0];if(!f)return;
 setValidation(null);setError("");setFileName(f.name);setRaw("");
 try{
 if(f.size>1024*1024)throw Error("JSON file exceeds 1 MiB");
 const bytes=await f.arrayBuffer();const text=new TextDecoder("utf-8",{fatal:true}).decode(bytes);
 setRaw(text);
 }catch(ex){setError(String(ex))}
 }
 async function openReview(id:string){await action(async()=>{
 const result=await authoring.review(projectId,stage,id);
 setReview(result);setSelectedIdea("");
 const arr=(result.content.ideas||[]) as Array<{idea_id:string}>;
 if(stage==="topic"&&arr.length)setSelectedIdea(arr[0].idea_id);
 });}
 async function approve(){if(!review)return;await action(async()=>{
 if(stage==="topic"){if(!selectedIdea)throw Error("Select an idea before approval");
 await authoring.approveTopic(projectId,review.candidate_id,selectedIdea,approved?.revision||0);}
 else await authoring.approveScript(projectId,review.candidate_id,approved?.revision||0);
 setReview(null);await refresh();onChanged();setNotice("Approved and published as a new immutable stage revision.");
 });}
 return <div className="authoring" data-testid={"authoring-"+stage}>
  <p className="smallMuted">External ChatGPT is manual. Build a prompt, copy/download it, use ChatGPT yourself, download the UTF-8 JSON response and import the file here. No model API is used by this application.</p>
  <div className="authorGrid">
   <section className="authorCard">
    <h3>1 · Build & export prompt</h3>
    <label>{stage==="topic"?"Number of reserved topic IDs":"Number of exact spoken sections"}
     <input type="number" value={count} min={1} max={8} onChange={e=>setCount(Number(e.target.value))}/></label>
    {stage==="topic"&&<label>Avoid repeated ideas (one per line)<textarea value={avoid} onChange={e=>setAvoid(e.target.value)}/></label>}
    <button disabled={busy} onClick={()=>action(async()=>{const p=await authoring.prompt(projectId,stage,
       stage==="topic"?{count,avoid:avoid.split("\n").filter(Boolean)}:{section_count:count});
       setPrompt(p);setRaw("");setValidation(null);setRepair("");setReview(null);await refresh();setNotice("Frozen prompt reservation created.");})}>Build Prompt</button>
    {prompt&&<div className="sourceInfo">
     <p>Reservation: <code>{prompt.reservation_id}</code></p>
     <p>Source fingerprint: <code>{prompt.source_fingerprint}</code></p>
     <p>Expires: {prompt.expires_at} · {prompt.state} · {prompt.is_current?"Current":"Stale/expired"}</p>
     <textarea aria-label="External ChatGPT prompt" readOnly rows={8} value={prompt.prompt}/>
     <div className="actions">
      <button disabled={busy} onClick={()=>action(async()=>{await copy(prompt.prompt);setNotice("Prompt copied");})}>Copy Prompt</button>
      <button disabled={busy} onClick={()=>download(stage+"-prompt.txt",prompt.prompt)}>Download Prompt</button>
     </div>
    </div>}
   </section>
   <section className="authorCard">
    <h3>2 · Validate & import downloaded JSON</h3>
    <label>Upload UTF-8 JSON File <input type="file" accept=".json,application/json" onChange={upload} disabled={!current||busy}/></label>
    {fileName&&<p className="smallMuted">Selected: {fileName}</p>}
    <textarea aria-label="Uploaded JSON text" readOnly value={raw} rows={6} placeholder="Choose a downloaded JSON file (not a mock response)."/>
    <div className="actions">
     <button disabled={!canImport||busy} onClick={()=>action(async()=>{const v=await authoring.validate(projectId,stage,prompt!.reservation_id,raw);setValidation(v);setNotice(v.valid?"Validated only. No candidate or approval was written.":"Validation found errors.");})}>Validate JSON</button>
     <button disabled={!canImport||!validation?.valid||busy} onClick={()=>action(async()=>{await authoring.importCandidate(projectId,stage,prompt!.reservation_id,raw);await refresh();setNotice("Candidate imported. Not approved.");})}>Import Candidate</button>
    </div>
    {validation&&<div role="status" className="sourceInfo"><b>{validation.valid?"VALID":"INVALID"} · {validation.summary.items} items</b>
     {validation.errors.map((e,i)=><p key={i}>{e.path} — {e.code}: {e.message} {e.expected?" Expected: "+e.expected:""}</p>)}</div>}
    <button disabled={!prompt||!raw||busy} onClick={()=>action(async()=>{const v=await authoring.repair(projectId,stage,prompt!.reservation_id,raw);setRepair(v.prompt);})}>Build Repair Prompt</button>
    {repair&&<div><textarea aria-label="Repair prompt" readOnly value={repair} rows={6}/><div className="actions">
     <button onClick={()=>action(async()=>{await copy(repair);setNotice("Repair prompt copied");})}>Copy Repair Prompt</button>
     <button onClick={()=>download(stage+"-repair.txt",repair)}>Download Repair Prompt</button>
    </div></div>}
   </section>
  </div>
  <section className="authorCard">
   <h3>3 · Review and explicitly approve</h3>
   {candidateList.length===0?<p className="smallMuted">No imported candidates yet. Import is never automatic approval.</p>:<div className="candidateList">
   {candidateList.map(c=><button key={c.candidate_id} disabled={busy} onClick={()=>openReview(c.candidate_id)}>
     Review {c.candidate_id.slice(0,9)}… · {c.status} · {c.content_hash.slice(0,12)}…</button>)}</div>}
   {review&&<div className="reviewDetails">
     <p>Candidate: {review.candidate_id} · {review.status} · {review.is_current?"Source current":"STALE SOURCE — approval refused"}</p>
     {stage==="topic"?<><label>Choose exactly one reserved idea before approval
      <select aria-label="Select idea" value={selectedIdea} onChange={e=>setSelectedIdea(e.target.value)}>
       {((review.content.ideas||[]) as Array<{idea_id:string;title:string}>).map(i=><option key={i.idea_id} value={i.idea_id}>{i.idea_id}: {i.title}</option>)}</select></label></>:
       <div><h4>Review exact sections and historical claims</h4>
        {((review.content.sections||[]) as Array<{section_id:string;exact_text:string}>).map(s=><p key={s.section_id}><b>{s.section_id}</b>: {s.exact_text}</p>)}
        <pre>{inspect(review.content.claims)}</pre><p>Model-suggested source leads have not been independently verified.</p></div>}
     <details><summary>Full imported content</summary><pre>{inspect(review.content)}</pre></details>
     <button disabled={busy||!review.is_current||review.status!=="candidate"} onClick={approve}>Approve new {stage==="topic"?"SelectedTopic":"Script + Historical Analysis"} revision</button>
    </div>}
  </section>
  {stage==="topic"&&<section className="authorCard" data-testid="manual-topic">
    <h3>Alternative · Direct manual Topic</h3><p className="smallMuted">A manual Topic is approved explicitly as a new immutable revision, never as a hidden import.</p>
    {(["title","hook","historical_period","region","story_angle"] as const).map(k=><label key={k}>{k.replace(/_/g," ")}
      <textarea value={manual[k]} onChange={e=>setManual(v=>({...v,[k]:e.target.value}))}/></label>)}
    <button disabled={busy||!manual.title||!manual.hook||!manual.historical_period||!manual.region||!manual.story_angle}
      onClick={()=>action(async()=>{await authoring.manualTopic(projectId,manual,approved?.revision||0);await refresh();onChanged();setNotice("Manual selected Topic approved under a new revision.");})}>Approve Manual Topic</button>
   </section>}
  <section className="authorCard">
   <h3>Current approved artifact & revision history</h3>
   {!approved?<p className="smallMuted">No approved artifact exists yet.</p>:<div>
    <p>Revision {approved.revision} · {approved.stale?"STALE — source changed":"CURRENT"} · {approved.artifact_id}</p>
    <p>SHA256: <code>{approved.content_hash}</code></p>
    <details><summary>View exact approved content</summary><pre>{inspect(approved.content)}</pre></details>
    {approved.stale&&<p role="alert">Current approved version is immutable but stale. Create a new prompt from current source before approval.</p>}
   </div>}
  </section>
  {notice&&<p role="status" className="success">{notice}</p>}
  {error&&<p role="alert" className="error">{error}</p>}
 </div>;
}
