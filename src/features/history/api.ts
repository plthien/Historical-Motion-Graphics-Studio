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
export type Stage={key:StageKey;label:string;status:"blocked"|"approved";blocked_reason:string|null;revision:number};
export type ProjectStatus={project_id:string;channel_id:string;stages:Stage[]};
async function request<T>(url:string,body?:object):Promise<T>{
 let response:Response;
 try{response=await fetch(url,{method:body?"POST":"GET",headers:{"Accept":"application/json",...(body?{"Content-Type":"application/json"}:{})},...(body?{body:JSON.stringify(body)}:{})});}
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
