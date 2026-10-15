import React from 'react';
import {AbsoluteFill, Audio, Img, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {validatePlan} from './plan.mjs';
import {ZONES} from './layout.mjs';

const PALETTE = {ink:'#22201c', red:'#c94336', yellow:'#f1cb42', paper:'#e9e3d3'};
const FONT = 'Arial, Helvetica, sans-serif';
const MAP_PATH='M170 420 C400 340 600 410 950 310';
const MAP_VIEWBOX='0 0 1200 700';
const clamp={extrapolateLeft:'clamp',extrapolateRight:'clamp'};
const pos=(r)=>({position:'absolute',left:r.x,top:r.y,width:r.w,height:r.h,boxSizing:'border-box'});

function PaperCanvas({children}){
 return <AbsoluteFill style={{
   background:'radial-gradient(circle at 23% 14%, #f5f0e3 0%, transparent 42%), repeating-linear-gradient(0deg, rgba(66,54,40,.018) 0px, rgba(66,54,40,.018) 1px, transparent 1px, transparent 5px), '+PALETTE.paper,
   color:PALETTE.ink, fontFamily:FONT, fontWeight:400, fontStyle:'normal',
   fontSize:32, lineHeight:1.15, overflow:'hidden',
   boxSizing:'border-box'
 }}>
   <div style={{position:'absolute',inset:24,border:'2px solid rgba(55,50,40,.05)',pointerEvents:'none'}}/>
   {children}
 </AbsoluteFill>;
}

function EditorialChrome(){
 return <>
   <div style={{position:'absolute',top:58,left:90,color:'#6b6053',fontWeight:800,letterSpacing:5,fontSize:27}}>HISTORY / MOTION LAB</div>
   <div style={{position:'absolute',bottom:40,right:93,color:'#8d8271',letterSpacing:3,fontSize:24}}>ILLUSTRATIVE TECHNICAL DEMO · 1914</div>
 </>;
}

/** Both geometry and route share the same world transform. Camera drift is setup-local. */
function MapViewport({setup}){
 const frame=useCurrentFrame();
 const route=setup.events.find(e=>e.kind==='route');
 const sinceRoute=route ? frame-route.atFrame : -1;
 const showRoute=route && sinceRoute>=0 && sinceRoute<route.durationFrames;
 const progress=showRoute?interpolate(sinceRoute,[0,85],[0,1],clamp):0;
 const routeHold=showRoute?interpolate(sinceRoute,[0,9,Math.max(10,route.durationFrames-12),route.durationFrames],[0,1,1,0],clamp):0;
 const cameraX=interpolate(frame,[0,630],[0,-28],clamp);
 const cameraScale=interpolate(frame,[0,630],[1,1.016],clamp);
 return <div data-map-viewport="true" style={{...pos(ZONES.map.map), overflow:'hidden', border:'3px solid #928778', background:'#dad8c4',boxShadow:'10px 13px 0 #a49a8766'}}>
  <svg viewBox={MAP_VIEWBOX} width="100%" height="100%" preserveAspectRatio="xMidYMid meet" style={{display:'block',overflow:'visible'}}>
   <g data-map-camera="true" transform={`translate(${cameraX} 0) translate(600 350) scale(${cameraScale}) translate(-600 -350)`}>
    <path d="M50 180 L340 70 L520 195 L720 108 L970 260 L1050 495 L690 600 L350 520 L140 650 Z" fill="#c8cab3" stroke="#4d5147" strokeWidth="9"/>
    <path d="M340 70 L350 520 M520 195 L690 600 M720 108 L690 600" fill="none" stroke="#8b907e" strokeWidth="5" strokeDasharray="12 9"/>
    <path data-road-path="true" d={MAP_PATH} stroke="#a38d68" strokeWidth="22" fill="none" strokeLinecap="round" />
    <circle cx="615" cy="375" r="18" fill={PALETTE.red} />
    <text x="535" y="270" fontSize="37" fontWeight="bold" fill="#535f54">SARAJEVO</text>
    {showRoute&&<g data-route="true" opacity={routeHold}>
      <path data-animated-route="true" d={MAP_PATH} stroke={PALETTE.red} strokeWidth="17" fill="none" strokeLinecap="round" strokeDasharray="1500" strokeDashoffset={1500*(1-progress)}/>
      <circle cx="950" cy="310" r="22" fill={PALETTE.red} opacity={progress>.97?1:0}/>
    </g>}
   </g>
  </svg>
  <div style={{position:'absolute',left:22,bottom:18,background:'#f5efdfa9',padding:'10px 16px',fontWeight:800,fontSize:22,color:'#4d493e'}}>SCHEMATIC MAP · NOT TO SCALE</div>
 </div>;
}

function ArchivePhoto({setup}){
 const frame=useCurrentFrame();
 const photo=setup.events.find(e=>e.kind==='placeholder'||e.kind==='photo');
 if(!photo) return null;
 const relative=frame-photo.atFrame;
 if(relative<0||relative>=photo.durationFrames)return null;
 const entering=interpolate(relative,[0,14],[0,1],clamp);
 const drift=interpolate(frame,[0,600],[-2,0],clamp);
 const root={...pos(ZONES.archive.photo),opacity:entering,transform:`translateY(${(1-entering)*22}px) rotate(${drift}deg)`,border:'16px solid #f9f6ed',background:'#c9c3b6',boxShadow:'18px 22px 26px #4e46384c'};
 return <div style={root}>
  <div style={{position:'absolute',top:-27,left:80,width:180,height:49,background:'#e0d2b0a8',transform:'rotate(-5deg)'}} />
  {photo.kind==='photo'&&photo.assetPath
    ? <Img src={staticFile(photo.assetPath)} style={{width:'100%',height:'100%',objectFit:'contain'}}/>
    : <div style={{position:'absolute',inset:20,border:'5px dashed #82796b',display:'grid',placeItems:'center',padding:45,textAlign:'center',fontWeight:900,fontSize:40,lineHeight:1.2,color:'#5a5246',background:'#d8d0c1'}}>ARCHIVAL IMAGE<br/>PLACEHOLDER</div>}
 </div>;
}

function MotionItem({setup,event}){
 const frame=useCurrentFrame();
 const {fps}=useVideoConfig();
 const motion=spring({fps,frame,config:{damping:18,stiffness:135}});
 const opacity=interpolate(frame,[0,9,Math.max(10,event.durationFrames-12),event.durationFrames],[0,1,1,0],clamp);
 const zone=event.kind==='timestamp'?'timestamp':event.kind;
 const r=ZONES[setup.kind][zone]||ZONES[setup.kind].headline;
 const style={...pos(r),zIndex:event.layer,color:PALETTE.ink,
   opacity, transform:`translateY(${(1-motion)*18}px) scale(${0.97+0.03*motion})`,
   transformOrigin:'center center'};
 if(event.kind==='headline')return <div style={{...style,fontSize:setup.kind==='map'?61:setup.kind==='archive'?76:101,fontWeight:900,letterSpacing:-1.3,lineHeight:1.07,whiteSpace:'pre-line',overflowWrap:'break-word'}}>{event.text}</div>;
 if(event.kind==='timestamp')return <div style={{...style,background:PALETTE.yellow,fontSize:92,fontWeight:900,display:'flex',alignItems:'center',padding:'8px 26px'}}>{event.text}</div>;
 if(event.kind==='label')return <div style={{...style,background:PALETTE.ink,color:'#fffdf5',fontSize:setup.kind==='archive'?31:34,fontWeight:850,letterSpacing:1.5,padding:'18px 20px',display:'flex',alignItems:'center',justifyContent:'center',textAlign:'center'}}>{event.text}</div>;
 if(event.kind==='stamp')return <div style={{...style,color:PALETTE.red,border:'8px solid '+PALETTE.red,fontSize:setup.kind==='title'?57:48,fontWeight:900,display:'grid',placeItems:'center',transform:style.transform+' rotate(-6deg)'}}>{event.text}</div>;
 if(event.kind==='annotation')return <div style={{...style,display:'flex',alignItems:'center',justifyContent:'flex-end',textAlign:'right',color:'#5b5146',borderTop:'3px solid '+PALETTE.red,fontSize:23,fontWeight:800,letterSpacing:1.2}}>{event.text}</div>;
 return null;
}

function VisualSetup({setup}){
 const frame=useCurrentFrame();
 return <PaperCanvas>
   <EditorialChrome/>
   {setup.kind==='title'&&<div style={{position:'absolute',top:190,left:135,width:1310,height:25,transform:'rotate(-1deg)',background:PALETTE.yellow}}/>}
   {setup.kind==='map'&&<MapViewport setup={setup}/>}
   {setup.kind==='archive'&&<ArchivePhoto setup={setup}/>}
   {setup.events.filter(e=>!['route','photo','placeholder'].includes(e.kind)).map(event=>
    <Sequence key={event.id} from={event.atFrame} durationInFrames={event.durationFrames} layout="none">
      <MotionItem setup={setup} event={event}/>
    </Sequence>
   )}
 </PaperCanvas>;
}

export function DocumentaryComposition({plan}){
 const errors=validatePlan(plan);
 if(errors.length)throw new Error('Invalid Motion Plan: '+errors.map(e=>e.path+':'+e.code).join('; '));
 return <AbsoluteFill style={{fontFamily:FONT,color:PALETTE.ink,backgroundColor:PALETTE.paper,overflow:'hidden'}}>
  {plan.setups.map(setup=><Sequence key={setup.id} from={setup.startFrame} durationInFrames={setup.endFrame-setup.startFrame}>
    <VisualSetup setup={setup}/>
  </Sequence>)}
  {plan.audioSrc?<Audio src={staticFile(plan.audioSrc)}/>:null}
 </AbsoluteFill>;
}
