import {useEffect,useState,type FormEvent} from "react";
import {authoring,type DnaDocument,type DnaState} from "../api";
const fields: Array<{key:keyof DnaDocument;label:string}>=[
 {key:"language",label:"Output language"},{key:"audience",label:"Audience"},{key:"historical_niche",label:"Historical niche"},
 {key:"narration_style",label:"Narration tone and delivery"},{key:"editorial_style",label:"Editorial framing and style"},
 {key:"map_treatment",label:"Map treatment"},{key:"uncertainty_policy",label:"Uncertainty, disputed facts and fictional reconstruction"}
];
export function ChannelDNAEditor({channelId,language,niche,onSaved}:{channelId:string;language:string;niche:string;onSaved:()=>void}){
 const [data,setData]=useState<DnaDocument>({schema_version:"HistoryChannelDNA.v1",language,audience:"General history audience",historical_niche:niche,
 narration_style:"Conversational, clear and engaging",editorial_style:"Careful historical storytelling with entertainment",
 map_treatment:"Visibly label schematic maps and uncertain period borders",
 uncertainty_policy:"Distinguish evidence, uncertainty, disputes and hypothetical reconstruction"});
 const [current,setCurrent]=useState<DnaState>(null),[error,setError]=useState(""),[busy,setBusy]=useState(false);
 useEffect(()=>{let live=true;setError("");authoring.dnaGet(channelId).then(v=>{if(!live)return;setCurrent(v);setData(v?.document||{
 schema_version:"HistoryChannelDNA.v1",language,audience:"General history audience",historical_niche:niche,
 narration_style:"Conversational, clear and engaging",editorial_style:"Clear chronology, grounded claims and explanatory humor",
 map_treatment:"Label schematic maps and uncertain historical borders",
 uncertainty_policy:"Distinguish sources, uncertainty, disputed facts and fictional reconstruction"});}).catch(e=>live&&setError(String(e)));
 return()=>{live=false};},[channelId,language,niche]);
 async function save(e:FormEvent){e.preventDefault();setBusy(true);setError("");
 try{const saved=await authoring.dnaSave(channelId,data,current?.revision||0);setCurrent(saved);onSaved();}
 catch(ex){setError(String(ex))}finally{setBusy(false)}}
 return <details className="dnaEditor" data-testid="dna-editor"><summary>History ChannelDNA {current?"— revision "+current.revision:"— configure before authoring"}</summary>
 <form onSubmit={save}>{fields.map(({key,label})=><label className="dnaField" key={key}>{label}
 <textarea value={data[key]} required minLength={key==="language"?2:4} maxLength={key==="uncertainty_policy"?700:500}
 onChange={e=>setData(d=>({...d,[key]:e.target.value}))}/></label>)}
 <p className="smallMuted">Every saved revision is immutable history. Editing DNA makes dependent prompts and approvals stale; it never deletes their bytes.</p>
 <button disabled={busy}>{busy?"Saving…":"Save ChannelDNA"}</button>{current&&<small>Source hash: {current.content_hash.slice(0,16)}…</small>}
 {error&&<p role="alert">{error}</p>}</form></details>
}
