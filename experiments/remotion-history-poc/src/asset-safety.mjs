// Node-only asset resolution: NEVER bundle this into the Vite Player.
import {realpath,stat} from 'node:fs/promises';
import path from 'node:path';
import {safePath} from './plan.mjs';
export async function validateResolvedAssets(plan, publicRoot) {
 const errors=[];
 const root=await realpath(publicRoot);
 const refs=[{path:'$.audioSrc',value:plan?.audioSrc,ext:/\.(wav|mp3)$/i}];
 if(Array.isArray(plan?.setups))for(const [si,s] of plan.setups.entries()){
   if(!Array.isArray(s?.events))continue;
   for(const [ei,e] of s.events.entries())if(e?.assetPath!==undefined)
     refs.push({path:'$.setups['+si+'].events['+ei+'].assetPath',value:e.assetPath,ext:/\.(png|jpe?g|webp)$/i});
 }
 for(const ref of refs){
  if(ref.value===undefined||ref.value===null)continue;
  if(!safePath(ref.value)||!ref.ext.test(ref.value)){errors.push({path:ref.path,code:'asset_path',message:'Unsafe asset path'});continue;}
  try{
   const actual=await realpath(path.resolve(root,ref.value));
   if(!actual.startsWith(root+path.sep)||!(await stat(actual)).isFile())
      errors.push({path:ref.path,code:'asset_escape',message:'Resolved media escaped public directory or was not a file'});
  }catch{errors.push({path:ref.path,code:'asset_missing',message:'Media is missing'});}
 }
 return errors;
}
