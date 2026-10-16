// P0 only: preflight and CLI MUST refer to the identical immutable fixture.
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {motionPlan,CANONICAL_PLAN_BYTES,validatePlan} from '../src/plan.mjs';
import {validateResolvedAssets} from '../src/asset-safety.mjs';
const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
export const DEFAULT_PUBLIC_ROOT=path.join(ROOT,'public');
export const RENDER_ARGS=Object.freeze(['--no-install','remotion','render','src/index.jsx','HistoryPOC','out/history-poc.mp4','--codec','h264','--log','error']);
const sha=x=>createHash('sha256').update(x).digest('hex');
export const CANONICAL_PLAN_SHA256=sha(CANONICAL_PLAN_BYTES);
function samePlan(plan){return JSON.stringify(plan)===CANONICAL_PLAN_BYTES;}
function requireCanonical(plan){
 if(!samePlan(plan))throw Error('P0_PLAN_IDENTITY_MISMATCH: alternate plans are not supported by the fixed P0 Remotion Root');
}
export async function preflight(plan=motionPlan, publicRoot=DEFAULT_PUBLIC_ROOT){
 const errors=validatePlan(plan);
 if(errors.length)throw Error('MOTION_PLAN_INVALID '+JSON.stringify(errors));
 const mediaErrors=await validateResolvedAssets(plan,publicRoot);
 if(mediaErrors.length)throw Error('MOTION_MEDIA_INVALID '+JSON.stringify(mediaErrors));
 return {schemaVersion:plan.schemaVersion,assetsValidated:plan.setups.reduce((n,s)=>n+s.events.filter(e=>e.assetPath).length,0)+(plan.audioSrc?1:0)};
}
export async function launchRemotion({plan,planSha256,publicRoot}){
 // This is a static fixed-plan POC. The CLI Root reads the identical imported
 // frozen source file; divergent injected props and public roots fail closed.
 requireCanonical(plan);
 if(planSha256!==CANONICAL_PLAN_SHA256)throw Error('P0_PLAN_IDENTITY_MISMATCH: fingerprint changed');
 if(path.resolve(publicRoot)!==DEFAULT_PUBLIC_ROOT)throw Error('P0_PUBLIC_ROOT_MISMATCH: real CLI renders the repository public assets only');
 const bin=process.platform==='win32'?'npx.cmd':'npx';
 const child=spawn(bin,[...RENDER_ARGS],{cwd:ROOT,stdio:'inherit',shell:process.platform==='win32',windowsHide:true});
 return new Promise((resolve,reject)=>{
  child.once('error',reject);
  child.once('exit',(code,signal)=>code===0?resolve(0):reject(Error(`Remotion exited code=${code} signal=${signal}`)));
 });
}
export async function renderWithPreflight({plan=motionPlan,publicRoot=DEFAULT_PUBLIC_ROOT,execute=launchRemotion,preflightOnly=false}={}){
 // Verify before & after awaited media probing. A snapshot prevents a caller
 // mutating the object while probes are running or before the renderer starts.
 requireCanonical(plan);
 const approved=JSON.parse(CANONICAL_PLAN_BYTES);
 const checked=await preflight(approved,publicRoot);
 requireCanonical(plan);
 if(!samePlan(approved))throw Error('P0_PLAN_IDENTITY_MISMATCH: frozen render snapshot changed');
 const proof={...checked,planSha256:CANONICAL_PLAN_SHA256};
 if(preflightOnly){console.log('P0_RENDER_PREFLIGHT_PASS '+JSON.stringify(proof));return proof;}
 await execute({plan:approved,planSha256:CANONICAL_PLAN_SHA256,publicRoot});
 return proof;
}
const entry=process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url);
if(entry){
 const args=process.argv.slice(2);
 if(args.some(s=>s!=='--preflight-only'))throw Error('Unknown render argument');
 renderWithPreflight({preflightOnly:args.includes('--preflight-only')}).catch(e=>{console.error(e.message);process.exitCode=1;});
}
