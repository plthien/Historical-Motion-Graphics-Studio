import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,symlink,rm} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {motionPlan} from '../src/plan.mjs';
import {preflight,renderWithPreflight,RENDER_ARGS} from '../scripts/render.mjs';

const temp=()=>mkdtemp(path.join(os.tmpdir(),'p0-preflight-'));
const imagePlan=path=>{const p=structuredClone(motionPlan);const e=p.setups[2].events.find(x=>x.id==='photo');e.kind='photo';e.assetPath=path;return p;};

test('no-media POC passes mandatory preflight without launching renderer',async()=>{
 const root=await temp();let count=0;
 try{
  await renderWithPreflight({plan:structuredClone(motionPlan),publicRoot:root,execute:async()=>{count++;},preflightOnly:true});
  assert.equal(count,0);
  await renderWithPreflight({plan:structuredClone(motionPlan),publicRoot:root,execute:async()=>{count++;}});
  assert.equal(count,1);
 }finally{await rm(root,{recursive:true,force:true});}
});
test('real local media passes; missing image / directory / forbidden extension fail before renderer',async()=>{
 const root=await temp();let calls=0;
 const execute=async()=>{calls++;};
 try{
  await mkdir(path.join(root,'media'));await writeFile(path.join(root,'media','valid.png'),'placeholder');
  await renderWithPreflight({plan:imagePlan('media/valid.png'),publicRoot:root,execute});
  assert.equal(calls,1);
  for(const name of ['media/missing.png','media','media/valid.svg','../external.png','/etc/passwd','https://site.com/a.png']){
   await assert.rejects(()=>renderWithPreflight({plan:imagePlan(name),publicRoot:root,execute}),/MOTION_PLAN_INVALID|MOTION_MEDIA_INVALID/,name);
  }
  assert.equal(calls,1,'invalid files cannot start Chromium');
 }finally{await rm(root,{recursive:true,force:true});}
});
test('symlink out of public root rejected and even valid schema cannot authorize it',async()=>{
 if(process.platform==='win32')return; // symlink elevation policies differ on Windows
 const root=await temp(), external=await temp();let calls=0;
 try{
  await writeFile(path.join(external,'file.png'),'fake');
  await symlink(external,path.join(root,'leak'),'dir');
  await assert.rejects(()=>renderWithPreflight({plan:imagePlan('leak/file.png'),publicRoot:root,execute:async()=>{calls++;}}),/asset_escape/);
  assert.equal(calls,0);
 }finally{await rm(root,{recursive:true,force:true});await rm(external,{recursive:true,force:true});}
});
test('invalid public root fails explicitly; actual render entry uses preflight',async()=>{
 const root=path.join(os.tmpdir(),'p0-nonexistent-root-'+process.pid);
 await assert.rejects(()=>preflight(motionPlan,root),/invalid_root/);
 const args=RENDER_ARGS.join(' ');
 assert.match(args,/remotion render/);
 assert.equal(RENDER_ARGS.includes('HistoryPOC'),true);
});
