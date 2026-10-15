import React from 'react';
import {AbsoluteFill, Audio, Img, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {validatePlan} from './plan.mjs';

const ink = '#22201c', yellow = '#f1cb42', paper = '#e9e3d3', red = '#c94336';

function Paper({children}) {
  return <AbsoluteFill style={{background: 'radial-gradient(circle at 23% 14%, #f5f0e3 0%, transparent 42%), repeating-linear-gradient(0deg, rgba(66,54,40,.018) 0px, rgba(66,54,40,.018) 1px, transparent 1px, transparent 5px), '+paper, color: ink, fontFamily: 'Arial, Helvetica, sans-serif', overflow: 'hidden'}}>
    <AbsoluteFill style={{border: '25px solid rgba(55,50,40,.025)', pointerEvents: 'none'}}/>
    {children}
  </AbsoluteFill>;
}

function MapBackdrop() {
  return <svg viewBox="0 0 1200 700" style={{position: 'absolute', left: 400, top: 120, width: 1350, height: 800}}>
    <path d="M50 180 L340 70 L520 195 L720 108 L970 260 L1050 495 L690 600 L350 520 L140 650 Z" fill="#c8cab3" stroke="#4d5147" strokeWidth="9"/>
    <path d="M340 70 L350 520 M520 195 L690 600 M720 108 L690 600" fill="none" stroke="#8b907e" strokeWidth="5" strokeDasharray="12 9"/>
    <path d="M170 420 C400 340 600 410 950 310" stroke="#a38d68" strokeWidth="22" fill="none" strokeLinecap="round"/>
    <text x="570" y="310" fontSize="45" fontWeight="bold" fill="#535f54">SARAJEVO</text>
    <circle cx="615" cy="375" r="18" fill={red}/>
    <text x="640" y="440" fontSize="23" fill="#5f5b53">SƠ ĐỒ MINH HỌA — KHÔNG THEO TỶ LỆ</text>
  </svg>;
}

function ArchiveBackdrop() {
  return <div style={{position: 'absolute', left: 130, top: 130, width: 870, height: 720, transform: 'rotate(-4deg)', border: '15px solid #fbf8ed', background: '#cec7b8', boxShadow: '25px 28px 30px #4e46384c'}}>
    <div style={{position: 'absolute', left: 120, top: -30, width: 240, height: 65, background: '#d6c7a099', transform: 'rotate(-7deg)'}}/>
    <div style={{height: '100%', display: 'grid', placeItems: 'center', border: '3px solid #6e665a'}}>
      <div style={{width: 460, height: 280, border: '8px dashed #80786b', display: 'grid', placeItems: 'center', textAlign: 'center', fontWeight: 700, fontSize: 40, color: '#766d5d'}}>ARCHIVAL IMAGE<br/>PLACEHOLDER</div>
    </div>
  </div>;
}

function SceneBase({kind}) {
  return <Paper>
    <div style={{position: 'absolute', top: 60, left: 95, fontSize: 28, letterSpacing: 6, fontWeight: 700, color: '#797065'}}>HISTORY / MOTION LAB</div>
    <div style={{position: 'absolute', bottom: 54, right: 95, fontSize: 28, letterSpacing: 3, color: '#8d8271'}}>RESEARCH DEMO · 1914</div>
    {kind === 'map' ? <MapBackdrop/> : null}
    {kind === 'archive' ? <ArchiveBackdrop/> : null}
    {kind === 'title' ? <div style={{position: 'absolute', top: 175, left: 125, height: 27, width: 1320, background: yellow, transform: 'rotate(-1.5deg)'}}/> : null}
  </Paper>;
}

function Route() {
  const frame = useCurrentFrame();
  const progress = interpolate(frame, [0, 85], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return <svg viewBox="0 0 1200 700" style={{position: 'absolute', left: 400, top: 120, width: 1350, height: 800}}>
    <path d="M170 420 C400 340 600 410 950 310" stroke={red} strokeWidth="19" strokeDasharray="1500" strokeDashoffset={1500 * (1 - progress)} fill="none" strokeLinecap="round"/>
    <circle cx="950" cy="310" r="24" fill={red} opacity={progress > .97 ? 1 : 0}/>
  </svg>;
}

function MotionItem({event}) {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const pop = spring({fps, frame, config: {damping: 17, stiffness: 160}});
  const opacity = interpolate(frame, [0, 8, Math.max(9, event.durationFrames - 12), event.durationFrames], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const common = {position: 'absolute', zIndex: event.layer, opacity, transform: 'translateY(' + ((1 - pop) * 35) + 'px) scale(' + (0.88 + .12 * pop) + ')'};
  if (event.kind === 'route') return <div style={{position: 'absolute', inset: 0, zIndex: event.layer, opacity}}><Route/></div>;
  if (event.kind === 'placeholder') return <div style={{...common, left: 350, top: 400, padding: 25, border:'7px dashed #817763', fontWeight:900, fontSize:40, color:'#605746'}}>NO VERIFIED ARCHIVE PHOTO</div>;
  if (event.kind === 'photo') return event.assetPath ? <Img src={staticFile(event.assetPath)} style={{...common, left: 165, top: 160, width: 780, height: 650, objectFit: 'contain'}}/> : null;
  if (event.kind === 'stamp') return <div style={{...common, top: 745, right: 150, padding: '16px 38px', color: red, border: '12px solid '+red, fontSize: 75, fontWeight: 900, transform: common.transform + ' rotate(-10deg)'}}>{event.text}</div>;
  if (event.kind === 'timestamp') return <div style={{...common, left: 132, top: 590, background: yellow, fontSize: 95, fontWeight: 900, padding: '15px 38px'}}>{event.text}</div>;
  if (event.kind === 'label') return <div style={{...common, left: event.id === 'map-place' ? 1200 : 133, top: event.id === 'map-place' ? 765 : 870, background: ink, color: '#fffdf5', fontSize: 42, fontWeight: 800, letterSpacing: 3, padding: '15px 25px'}}>{event.text}</div>;
  return <div style={{...common, left: event.id === 'archive-heading' ? 970 : 120, top: event.id === 'map-title' ? 255 : event.id === 'archive-heading' ? 285 : 295, width: event.id === 'archive-heading' ? 850 : 1450, fontSize: event.id === 'archive-heading' ? 75 : 105, fontWeight: 900, letterSpacing: -2, lineHeight: 1.04, whiteSpace: 'pre-line', textShadow: '2px 2px 0 #faf5e9'}}>{event.text}</div>;
}

function VisualSetup({setup}) {
  return <AbsoluteFill>
    <SceneBase kind={setup.kind}/>
    {setup.events.map((event) => <Sequence key={event.id} from={event.atFrame} durationInFrames={event.durationFrames} layout="none">
      <MotionItem event={event}/>
    </Sequence>)}
  </AbsoluteFill>;
}

export function DocumentaryComposition({plan}) {
  const errors = validatePlan(plan);
  if (errors.length) throw new Error('Invalid motion plan: ' + errors.join('; '));
  return <AbsoluteFill>
    {plan.setups.map((setup) => <Sequence key={setup.id} from={setup.startFrame} durationInFrames={setup.endFrame - setup.startFrame}>
      <VisualSetup setup={setup}/>
    </Sequence>)}
    {plan.audioSrc ? <Audio src={staticFile(plan.audioSrc)}/> : null}
  </AbsoluteFill>;
}
