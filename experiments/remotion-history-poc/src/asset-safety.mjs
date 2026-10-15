// Node-only validation. Must not be imported from browser/player source.
import {realpath, stat} from 'node:fs/promises';
import path from 'node:path';
import {safePath} from './plan.mjs';

export async function validateResolvedAssets(plan, publicRoot){
 const issues=[];
 const refs=[{path:'$.audioSrc',value:plan?.audioSrc,extensions:['wav','mp3']}];
 if(Array.isArray(plan?.setups))for(const [si,s] of plan.setups.entries()){
  if(!Array.isArray(s?.events))continue;
  for(const [ei,e] of s.events.entries()){
   if(e?.assetPath!==undefined)refs.push({path:`$.setups[${si}].events[${ei}].assetPath`,value:e.assetPath,extensions:['png','jpg','jpeg','webp']});
  }
 }
 let root;
 try{root=await realpath(publicRoot);if(!(await stat(root)).isDirectory())throw Error('Not a directory');}
 catch{issues.push({path:'$.publicRoot',code:'invalid_root',message:'Public asset root must exist and be a directory'});return issues;}
 for(const ref of refs){
  if(ref.value===null||ref.value===undefined)continue;
  const extension=typeof ref.value==='string'?ref.value.split('.').at(-1)?.toLowerCase():'';
  if(!safePath(ref.value)||!ref.extensions.includes(extension)){
   issues.push({path:ref.path,code:'asset_path',message:'Unsafe media path or unsupported extension'});continue;
  }
  try{
   const resolved=await realpath(path.resolve(root,ref.value));
   if(!resolved.startsWith(root+path.sep)){
    issues.push({path:ref.path,code:'asset_escape',message:'Asset resolves outside permitted public directory'});continue;
   }
   if(!(await stat(resolved)).isFile()){
    issues.push({path:ref.path,code:'asset_type',message:'Asset must resolve to a regular file'});continue;
   }
  }catch{
    issues.push({path:ref.path,code:'asset_missing',message:'Referenced file is missing or unreadable'});
  }
 }
 return issues;
}
