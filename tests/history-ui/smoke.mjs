import {chromium} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,mkdir,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));
const folder=await mkdtemp(path.join(os.tmpdir(),'history-p1-ui-'));
const dbPath=path.join(folder,'history_app.db');
let api=null,web=null,browser=null;
const python=process.env.PYTHON || (process.platform==='win32'?'.venv/Scripts/python.exe':'python');
async function waitUrl(url){
 for(let i=0;i<90;i++){try{const r=await fetch(url);if(r.ok)return;}catch{}await sleep(300);}
 throw Error('UI smoke server unavailable: '+url);
}
async function kill(p){if(p){p.kill();await sleep(350);}}
try{
 api=spawn(python,['-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000'],{cwd:process.cwd(),env:{...process.env,HISTORY_DB_PATH:dbPath},stdio:'ignore'});
 await waitUrl('http://127.0.0.1:8000/health');
 const suffix=Math.random().toString(36).slice(2);
 const created=await fetch('http://127.0.0.1:8000/api/history/channels',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'History '+suffix,language:'English',niche:'European history',history_dna:{}})});
 assert.equal(created.status,201);
 const channel=await created.json();
 const createdProject=await fetch('http://127.0.0.1:8000/api/history/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Sarajevo '+suffix,channel_id:channel.channel_id,history_context:{}})});
 assert.equal(createdProject.status,201);const project=await createdProject.json();
 web=spawn(process.execPath,['node_modules/vite/bin/vite.js','--host','127.0.0.1','--port','5173','--strictPort'],{cwd:process.cwd(),stdio:'ignore'});
 await waitUrl('http://127.0.0.1:5173/');
 browser=await chromium.launch({headless:true});
 await mkdir('out/p1-ui',{recursive:true});
 for(const [w,h] of [[1440,900],[1920,1080]]){
  const page=await browser.newPage({viewport:{width:w,height:h}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:5173/',{waitUntil:'networkidle'});
  await page.getByText('Production Cockpit').first().waitFor();
  await page.locator('#channel-select').selectOption(channel.channel_id);
  await page.locator('#project-select').selectOption(project.project_id);
  await page.getByText('NOT_IMPLEMENTED_P2').first().waitFor();
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
  assert.equal(overflow,false,'Horizontal overflow at '+w);
  assert.equal(await page.locator('.stage').count(),7);
  assert.equal(await page.locator('.inspector button:disabled').count(),1);
  assert.deepEqual(errors,[]);
  await page.screenshot({path:'out/p1-ui/history-'+w+'x'+h+'.png',fullPage:true});
  await page.close();
 }
 await kill(api);api=null;
 api=spawn(python,['-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000'],{cwd:process.cwd(),env:{...process.env,HISTORY_DB_PATH:dbPath},stdio:'ignore'});
 await waitUrl('http://127.0.0.1:8000/health');
 const reopened=await fetch('http://127.0.0.1:8000/api/history/projects/'+project.project_id);
 assert.equal(reopened.status,200);
 assert.equal((await reopened.json()).project_id,project.project_id);
 console.log('HISTORY_UI_SMOKE_PASS 1440x900 1920x1080 seven stages, API persistence/restart');
}finally{
 if(browser)await browser.close();
 await kill(web);await kill(api);
 await rm(folder,{recursive:true,force:true});
}
