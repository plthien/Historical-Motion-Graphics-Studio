// R0 frozen demonstrator: all timing is in absolute integer frames at 30 FPS.
// Later phases replace this fixture with an approved, source-checked director contract.
export const motionPlan = {
  schemaVersion: 'history_motion_poc.v1',
  fps: 30,
  width: 1920,
  height: 1080,
  durationFrames: 1800,
  audioSrc: null,
  setups: [
    {
      id: 'opening',
      kind: 'title',
      startFrame: 0,
      endFrame: 570,
      events: [
        {id: 'intro', kind: 'headline', atFrame: 12, durationFrames: 450, text: 'MỘT BUỔI SÁNG\nTHAY ĐỔI LỊCH SỬ', layer: 2},
        {id: 'date', kind: 'timestamp', atFrame: 94, durationFrames: 375, text: '28.06.1914', layer: 3},
        {id: 'location', kind: 'label', atFrame: 180, durationFrames: 305, text: 'SARAJEVO · BOSNIA', layer: 4},
        {id: 'stamp-a', kind: 'stamp', atFrame: 382, durationFrames: 155, text: 'BƯỚC NGOẶT', layer: 5}
      ]
    },
    {
      id: 'map',
      kind: 'map',
      startFrame: 570,
      endFrame: 1200,
      events: [
        {id: 'map-title', kind: 'headline', atFrame: 24, durationFrames: 570, text: 'MỘT TUYẾN ĐƯỜNG\nQUA SARAJEVO', layer: 2},
        {id: 'map-place', kind: 'label', atFrame: 125, durationFrames: 450, text: 'SARAJEVO', layer: 3},
        {id: 'route', kind: 'route', atFrame: 250, durationFrames: 300, text: 'LỘ TRÌNH', layer: 4},
        {id: 'map-warning', kind: 'stamp', atFrame: 435, durationFrames: 160, text: 'RẼ SAI', layer: 5}
      ]
    },
    {
      id: 'archive',
      kind: 'archive',
      startFrame: 1200,
      endFrame: 1800,
      events: [
        {id: 'photo', kind: 'photo', atFrame: 35, durationFrames: 520, text: 'TƯ LIỆU LỊCH SỬ', layer: 2},
        {id: 'archive-heading', kind: 'headline', atFrame: 128, durationFrames: 430, text: 'MỘT SỰ KIỆN,\nNHIỀU HỆ QUẢ', layer: 3},
        {id: 'archive-note', kind: 'label', atFrame: 310, durationFrames: 250, text: 'ẢNH CẦN XÁC MINH NGUỒN', layer: 4},
        {id: 'archive-stamp', kind: 'stamp', atFrame: 430, durationFrames: 135, text: 'HỆ QUẢ', layer: 5}
      ]
    }
  ]
};

const kinds = new Set(['title','map','archive']);
const events = new Set(['headline','timestamp','label','stamp','route','photo']);
const object = x => x !== null && typeof x === 'object' && !Array.isArray(x);
const int = x => Number.isSafeInteger(x);
const issue = (a,path,code) => a.push({path,code,message:code});
const keys = (x,allowed,path,a) => Object.keys(x).forEach(k => {if(!allowed.includes(k)) issue(a,path+'.'+k,'unknown_field')});
export const safePath = x => typeof x === 'string' && x.length < 241 && /^[A-Za-z0-9_./-]+$/.test(x) && !x.startsWith('/') && !x.split('/').some(s=>!s||s==='.'||s==='..') && !/^[a-z]+:/i.test(x);
export function validatePlan(plan) {
 const a=[];
 if(!object(plan)) return [{path:'$',code:'root_type',message:'Expected object'}];
 keys(plan,['schemaVersion','fps','width','height','durationFrames','audioSrc','setups'],'$',a);
 if(plan.schemaVersion!=='history_motion_poc.v1') issue(a,'$.schemaVersion','version');
 if(plan.fps!==30||plan.width!==1920||plan.height!==1080||plan.durationFrames!==1800) issue(a,'$','composition');
 if(plan.audioSrc!==null && (plan.audioSrc===undefined||!safePath(plan.audioSrc)||! /\.(wav|mp3)$/i.test(plan.audioSrc))) issue(a,'$.audioSrc','audio');
 if(!Array.isArray(plan.setups)||!plan.setups.length||plan.setups.length>20){issue(a,'$.setups','setups');return a;}
 const sid=new Set(),eid=new Set();let cursor=0;
 for(const [i,s] of plan.setups.entries()){
  const sp='$.setups['+i+']';
  if(!object(s)){issue(a,sp,'setup_type');continue;}
  keys(s,['id','kind','startFrame','endFrame','events'],sp,a);
  if(typeof s.id!=='string'||!s.id||sid.has(s.id)) issue(a,sp+'.id','setup_id');
  else sid.add(s.id);
  if(!kinds.has(s.kind)) issue(a,sp+'.kind','setup_kind');
  if(!int(s.startFrame)||!int(s.endFrame)||s.startFrame!==cursor||s.endFrame<=s.startFrame||s.endFrame>1800){issue(a,sp,'setup_timing');}
  if(int(s.endFrame)) cursor=s.endFrame;
  if(!Array.isArray(s.events)||s.events.length>100){issue(a,sp+'.events','events');continue;}
  for(const [j,e] of s.events.entries()){
   const ep=sp+'.events['+j+']';
   if(!object(e)){issue(a,ep,'event_type');continue;}
   keys(e,['id','kind','atFrame','durationFrames','layer','text','assetPath'],ep,a);
   if(typeof e.id!=='string'||!e.id||eid.has(e.id)) issue(a,ep+'.id','event_id');
   else eid.add(e.id);
   if(!events.has(e.kind)) issue(a,ep+'.kind','event_kind');
   if(!int(e.atFrame)||e.atFrame<0||!int(e.durationFrames)||e.durationFrames<=0||e.atFrame+e.durationFrames>s.endFrame-s.startFrame) issue(a,ep,'event_timing');
   if(!int(e.layer)||e.layer<0||e.layer>99) issue(a,ep+'.layer','layer');
   if(typeof e.text!=='string'||e.text.length>220||e.text.length<1) issue(a,ep+'.text','text');
   if(e.assetPath!==undefined&&(!safePath(e.assetPath)||!/\.(png|jpe?g|webp)$/i.test(e.assetPath)))issue(a,ep+'.assetPath','asset_path');
  }
 }
 if(cursor!==1800) issue(a,'$.setups','coverage');
 return a;
}
export async function validateResolvedAssets(plan,root){
 const fs=await import('node:fs/promises'),path=await import('node:path');
 const errors=[],base=await fs.realpath(root);
 const paths=[plan?.audioSrc,...(plan?.setups||[]).flatMap(s=>(s?.events||[]).map(e=>e?.assetPath))].filter(x=>x!==null&&x!==undefined);
 for(const p of paths){if(!safePath(p)){issue(errors,p,'asset_path');continue;}
 try{const real=await fs.realpath(path.resolve(base,p));if(!real.startsWith(base+path.sep)||!(await fs.stat(real)).isFile())issue(errors,p,'asset_escape');}catch{issue(errors,p,'asset_missing');}}
 return errors;
}
export function setupAtFrame(plan,frame){
 if(!object(plan)||!Array.isArray(plan.setups)||!int(frame)||frame<0||frame>=plan.durationFrames)return null;
 return plan.setups.find(s=>object(s)&&frame>=s.startFrame&&frame<s.endFrame)??null;
}
export function visibleEventsAtFrame(plan,frame){
 const s=setupAtFrame(plan,frame);if(!s||!Array.isArray(s.events))return [];
 const t=frame-s.startFrame;
 return s.events.filter(e=>object(e)&&t>=e.atFrame&&t<e.atFrame+e.durationFrames).map(e=>e.id);
}
