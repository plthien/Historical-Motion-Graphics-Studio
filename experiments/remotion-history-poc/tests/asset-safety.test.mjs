import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import os from 'node:os';
import {mkdtemp,mkdir,writeFile,symlink,rm} from 'node:fs/promises';
import {motionPlan} from '../src/plan.mjs';
import {validateResolvedAssets} from '../src/asset-safety.mjs';

test('accept only existing local files contained in public root',async()=>{
 const dir=await mkdtemp(path.join(os.tmpdir(),'hist-assets-'));
 const external=await mkdtemp(path.join(os.tmpdir(),'hist-external-'));
 try{
  await mkdir(path.join(dir,'img'));await writeFile(path.join(dir,'img','a.png'),'placeholder');
  const p=structuredClone(motionPlan);
  p.setups[2].events[0].assetPath='img/a.png';
  assert.deepEqual(await validateResolvedAssets(p,dir),[]);
  p.setups[2].events[0].assetPath='img/missing.png';
  assert.equal((await validateResolvedAssets(p,dir))[0].code,'asset_missing');
  if(process.platform!=='win32'){
   await writeFile(path.join(external,'outside.png'),'external');
   await symlink(external,path.join(dir,'leak'));
   p.setups[2].events[0].assetPath='leak/outside.png';
   assert.equal((await validateResolvedAssets(p,dir))[0].code,'asset_escape');
  }
 }finally{await rm(dir,{recursive:true,force:true});await rm(external,{recursive:true,force:true});}
});
