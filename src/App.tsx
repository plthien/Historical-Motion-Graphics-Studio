import {useCallback,useEffect,useState,type FormEvent} from "react";
import {api,STAGES,type Channel,type Project,type ProjectStatus} from "./features/history/api";
import {ChannelDNAEditor} from "./features/history/authoring/ChannelDNAEditor";
import {TopicWorkspace} from "./features/history/topic/TopicWorkspace";
import {ScriptHistoricalAnalysisWorkspace} from "./features/history/script/ScriptHistoricalAnalysisWorkspace";
const CHANNEL_KEY="history.channel.v1", PROJECT_KEY="history.project.v1";
function saved(k:string){try{return localStorage.getItem(k)||""}catch{return ""}}
function keep(k:string,v:string){try{if(v)localStorage.setItem(k,v);else localStorage.removeItem(k)}catch{}}
function err(e:unknown){return e instanceof Error?e.message:String(e)}
export default function App(){
 const [channels,setChannels]=useState<Channel[]>([]);
 const [projects,setProjects]=useState<Project[]>([]);
 const [channelId,setChannelId]=useState(()=>saved(CHANNEL_KEY));
 const [projectId,setProjectId]=useState(()=>saved(PROJECT_KEY));
 const [status,setStatus]=useState<ProjectStatus|null>(null);
 const [stageKey,setStageKey]=useState<string>("topic");
 const [name,setName]=useState(""),[language,setLanguage]=useState("English"),[niche,setNiche]=useState("History"),[projectName,setProjectName]=useState("");
 const [message,setMessage]=useState(""),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true);
 const reload=useCallback(async()=>{const rows=await api.channels();setChannels(rows);setChannelId(id=>rows.some(c=>c.channel_id===id)?id:(rows[0]?.channel_id||""));},[]);
 useEffect(()=>{let active=true;reload().catch(e=>active&&setMessage(err(e))).finally(()=>active&&setLoading(false));return()=>{active=false}},[reload]);
 useEffect(()=>{keep(CHANNEL_KEY,channelId);if(!channelId){setProjects([]);setProjectId("");setStatus(null);return;}
   let live=true;api.projects(channelId).then(rows=>{if(!live)return;setProjects(rows);setProjectId(id=>rows.some(p=>p.project_id===id)?id:(rows[0]?.project_id||""));}).catch(e=>{if(live){setMessage(err(e));setProjects([]);setProjectId("")}});
   return()=>{live=false};
 },[channelId]);
 useEffect(()=>{keep(PROJECT_KEY,projectId);setStatus(null);if(!projectId)return;
   let live=true;api.status(projectId).then(value=>{if(live)setStatus(value)}).catch(e=>live&&setMessage(err(e)));return()=>{live=false};
 },[projectId]);
 async function addChannel(e:FormEvent){e.preventDefault();setBusy(true);setMessage("");
   try{const result=await api.channelCreate({name,language,niche,history_dna:{}});await reload();setChannelId(result.channel_id);setProjectId("");setName("");}
   catch(ex){setMessage(err(ex))}finally{setBusy(false)}
 }
 async function addProject(e:FormEvent){e.preventDefault();if(!channelId)return;setBusy(true);setMessage("");
   try{const result=await api.projectCreate({name:projectName,channel_id:channelId,history_context:{}});setProjects(await api.projects(channelId));setProjectId(result.project_id);setProjectName("");}
   catch(ex){setMessage(err(ex))}finally{setBusy(false)}
 }
 const channel=channels.find(c=>c.channel_id===channelId), project=projects.find(p=>p.project_id===projectId);
 const current=status?.stages.find(s=>s.key===stageKey);
 async function refreshStatus(){if(projectId){try{setStatus(await api.status(projectId));}catch(e){setMessage(err(e));}}}
 return <div className="studio">
  <aside className="sidebar"><div className="brand"><span className="brandIcon">H</span><div><strong>HISTORY</strong><small>MOTION GRAPHICS STUDIO</small></div></div>
   <p className="sideCaption">WORKSPACE</p><div className="navPill">◈ &nbsp;Production Cockpit</div>
   <p className="sideCaption">SEVEN HISTORY STAGES</p><nav aria-label="History stages">{STAGES.map((s,i)=><button key={s.key} type="button" className={"navItem "+(stageKey===s.key?"selected":"")} onClick={()=>setStageKey(s.key)}><span className="index">{String(i+1).padStart(2,"0")}</span>{s.label}<span className="lock">○</span></button>)}</nav>
   <div className="sideFoot"><b>P2 AUTHORING ACTIVE</b><p>Topic and Script authoring work by manual External ChatGPT prompt and JSON import. Voice and rendering remain future phases.</p></div>
  </aside>
  <main className="main"><header className="topbar"><span>HISTORY STUDIO &nbsp; / &nbsp; WORKSPACE</span><span className="topStatus">LOCAL FIRST • P2</span></header>
   <div className="page"><section className="heading"><div><p className="eyebrow">HISTORICAL MOTION GRAPHICS STUDIO</p><h1>Production Cockpit</h1><p>Create History channels and projects, configure ChannelDNA, then manually author Topic and Script JSON. The five later stages remain blocked.</p></div><span className="phase">P2 MANUAL AUTHORING</span></section>
    {message&&<div role="alert" className="error"><b>Request failed:</b> {message}<button type="button" aria-label="Dismiss error" onClick={()=>setMessage("")}>×</button></div>}
    {loading&&<p role="status">Loading saved History channels…</p>}
    <section className="overview" aria-label="Channel and project">
     <article className="panel"><div className="panelHead"><h2>◉ &nbsp; Channel</h2><small>01 / SOURCE</small></div>
      <label htmlFor="channel-select">Selected Channel</label><select id="channel-select" value={channelId} onChange={e=>{setChannelId(e.target.value);setProjectId("")}}><option value="">Choose a channel</option>{channels.map(c=><option value={c.channel_id} key={c.channel_id}>{c.name}</option>)}</select>
      <p className="detail">{channel?channel.language+" · "+channel.niche+" · Rev "+channel.revision:"No channels yet."}</p>
      <form onSubmit={addChannel}><h3>Create new Channel</h3><div className="dual"><input required maxLength={100} aria-label="Channel name" placeholder="Channel name" value={name} onChange={e=>setName(e.target.value)}/><input required maxLength={32} aria-label="Language" value={language} onChange={e=>setLanguage(e.target.value)}/></div><input required maxLength={120} aria-label="History niche" value={niche} onChange={e=>setNiche(e.target.value)}/><button disabled={busy||loading}>+ Create Channel</button></form>
      {channelId&&<ChannelDNAEditor channelId={channelId} language={channel?.language||language} niche={channel?.niche||niche} onSaved={refreshStatus}/>}
     </article>
     <article className="panel"><div className="panelHead"><h2>▤ &nbsp; Project</h2><small>02 / WORKSPACE</small></div>
      <label htmlFor="project-select">Selected Project</label><select id="project-select" disabled={!channelId} value={projectId} onChange={e=>setProjectId(e.target.value)}><option value="">Choose a project</option>{projects.map(p=><option value={p.project_id} key={p.project_id}>{p.name}</option>)}</select>
      <p className="detail">{project?"Persisted · "+project.created_at.slice(0,10):"Create a project under the selected channel."}</p>
      <form onSubmit={addProject}><h3>Create new Project</h3><input required maxLength={120} aria-label="Project name" placeholder="Historical subject" value={projectName} onChange={e=>setProjectName(e.target.value)}/><button disabled={busy||!channelId}>+ Create Project</button></form>
     </article>
    </section>
    <div className="sectionHead"><div><p className="eyebrow">SEVEN-STAGE HISTORY PIPELINE</p><h2>Project readiness</h2></div><small>{status?"Live backend status":"Select a project"}</small></div>
    <section className="stages" aria-label="Project stages">{STAGES.map((s,i)=>{const state=status?.stages.find(x=>x.key===s.key);return <button key={s.key} type="button" className={"stage "+(stageKey===s.key?"focused":"")} onClick={()=>setStageKey(s.key)}>
      <div className="stageLine"><b>{String(i+1).padStart(2,"0")}</b><span>○ {state?.status==="approved"?"APPROVED":state?.status==="stale"?"STALE":state?.status==="ready"?"READY":"BLOCKED"}</span></div><h3>{s.label}</h3><p>{state?.blocked_reason||"Select a project to load readiness"}</p><small>{s.key==="topic"||s.key==="script_analysis"?"Manual authoring →":"Future phase — unavailable →"}</small>
     </button>})}</section>
    <section className="panel inspector"><div className="panelHead"><div><p className="eyebrow">STAGE INSPECTOR</p><h2>{current?.label||STAGES.find(s=>s.key===stageKey)?.label}</h2></div><small>READ ONLY</small></div>
     {projectId&&stageKey==="topic"?<TopicWorkspace key={projectId+":topic"} projectId={projectId} onChanged={refreshStatus}/>:
      projectId&&stageKey==="script_analysis"?<ScriptHistoricalAnalysisWorkspace key={projectId+":script"} projectId={projectId} onChanged={refreshStatus}/>:
      <><p>{!project?"Select a saved project to load stage status from the actual History API.":"This future stage is unavailable: "+(current?.blocked_reason||"not loaded")+". Implementation belongs to a later phase."}</p>
      <button disabled title="No backend endpoint implemented for this future stage" className="disabled">Unavailable — future phase</button></>}
    </section><footer>History-only P2 • Manual External ChatGPT prompts and validated JSON files • No internal model API • No Voice/Render service</footer>
   </div>
  </main>
 </div>;
}
