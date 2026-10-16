import React, {useRef, useEffect} from 'react';
import {createRoot} from 'react-dom/client';
import {Player} from '@remotion/player';
import {DocumentaryComposition} from './DocumentaryComposition.jsx';
import {motionPlan, validatePlan} from './plan.mjs';
import './style.css';

function App() {
  const player = useRef(null);
  useEffect(() => {window.__p0Player = player; return () => {delete window.__p0Player;};}, []);
  const parity = new URLSearchParams(window.location.search).has('parity');
  if(parity)return <Player
    ref={player}
    component={DocumentaryComposition}
    inputProps={{plan:motionPlan}}
    compositionWidth={1920} compositionHeight={1080} fps={30} durationInFrames={1800}
    controls={false} autoPlay={false}
    style={{width:1920,height:1080,margin:0,padding:0}}
  />;
  const errors = validatePlan(motionPlan);
  if (errors.length) return <main><h1>Invalid plan</h1><pre>{errors.map((e) => `${e.path}: ${e.message}`).join('\n')}</pre></main>;
  return <main>
    <header><div><div className="eyebrow">P0 · Remotion technical proof</div><h1>Historical Motion Lab</h1><p>A layer-based 60-second prototype. All historical maps and images are illustrative placeholders, not primary sources.</p></div><span className="chip">Personal study · local first</span></header>
    <section className="layout">
      <div className="screen">
        <Player
          ref={player}
          component={DocumentaryComposition}
          inputProps={{plan: motionPlan}}
          compositionWidth={motionPlan.width}
          compositionHeight={motionPlan.height}
          fps={motionPlan.fps}
          durationInFrames={motionPlan.durationFrames}
          controls
          autoPlay={false}
          style={{width: '100%', aspectRatio: '16 / 9'}}
        />
        <div className="foot">1920 × 1080 · 30 FPS · 60 seconds · composited from independent motion events</div>
      </div>
      <aside><h2>Visual setups</h2>
        {motionPlan.setups.map((setup, i) => <button className="jump" key={setup.id} type="button" onClick={() => player.current?.seekTo(setup.startFrame)}>
          <strong>0{i+1}. {setup.kind.toUpperCase()}</strong>
          <small>{(setup.startFrame / motionPlan.fps).toFixed(1)}s — {(setup.endFrame / motionPlan.fps).toFixed(1)}s</small>
          <span>{setup.events.length} timed layer events</span>
        </button>)}
        <h2>P0 acceptance limits</h2>
        <p>No LLM calls, factual source verification, TTS/alignment, saved project state or production export API yet. The command-line render is available after installing dependencies.</p>
      </aside>
    </section>
    <section className="timeline"><h2>Motion event plan</h2><div className="track">
      {motionPlan.setups.map((setup) => <div key={setup.id} className="segment" style={{flex: setup.endFrame-setup.startFrame}}>{setup.kind}<span>{setup.events.map(e => e.kind).join(' · ')}</span></div>)}
    </div></section>
  </main>;
}
createRoot(document.getElementById('root')).render(<App/>);
