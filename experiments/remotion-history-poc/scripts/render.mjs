// P0 preflight: validation MUST complete successfully before Remotion/Chromium is invoked.
import {spawn} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {motionPlan,validatePlan} from '../src/plan.mjs';
import {validateResolvedAssets} from '../src/asset-safety.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
export const DEFAULT_PUBLIC_ROOT = path.join(ROOT,'public');
export const RENDER_ARGS = Object.freeze(['--no-install','remotion','render','src/index.jsx','HistoryPOC','out/history-poc.mp4','--codec','h264','--log','error']);

export async function preflight(plan=motionPlan, publicRoot=DEFAULT_PUBLIC_ROOT){
 const errors=validatePlan(plan);
 if(errors.length)throw Error('MOTION_PLAN_INVALID '+JSON.stringify(errors));
 const mediaErrors=await validateResolvedAssets(plan,publicRoot);
 if(mediaErrors.length)throw Error('MOTION_MEDIA_INVALID '+JSON.stringify(mediaErrors));
 return {schemaVersion:plan.schemaVersion,assetsValidated:plan.setups.reduce((n,s)=>n+s.events.filter(e=>e.assetPath).length,0)+(plan.audioSrc?1:0)};
}
export async function launchRemotion(){
 const bin=process.platform==='win32'?'npx.cmd':'npx';
 const child=spawn(bin,[...RENDER_ARGS],{cwd:ROOT,stdio:'inherit',shell:process.platform==='win32',windowsHide:true});
 return new Promise((resolve,reject)=>{
  child.once('error',reject);
  child.once('exit',(code,signal)=>code===0?resolve(0):reject(Error(`Remotion exited code=${code} signal=${signal}`)));
 });
}
export async function renderWithPreflight({plan=motionPlan,publicRoot=DEFAULT_PUBLIC_ROOT,execute=launchRemotion,preflightOnly=false}={}){
 const result=await preflight(plan,publicRoot);
 if(preflightOnly){console.log('P0_RENDER_PREFLIGHT_PASS '+JSON.stringify(result));return result;}
 await execute();
 return result;
}
const entry = process.argv[1] && path.resolve(process.argv[1])===fileURLToPath(import.meta.url);
if(entry){
 const args=process.argv.slice(2);
 if(args.some(s=>s!=='--preflight-only'))throw Error('Unknown render argument');
 renderWithPreflight({preflightOnly:args.includes('--preflight-only')}).catch(e=>{console.error(e.message);process.exitCode=1;});
}
