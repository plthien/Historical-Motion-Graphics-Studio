import {chromium} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,mkdir,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
const sleep=t=>new Promise(r=>setTimeout(r,t));
const folder=await mkdtemp(path.join(os.tmpdir(),'p2-history-e2e-'));
const dbPath=path.join(folder,'history_app.db');
const python=process.env.PYTHON || 'python';
let backend=null,web=null,browser=null;
const base='http://127.0.0.1:8000';
async function ready(url){
 for(let i=0;i<120;i++){try{const r=await fetch(url);if(r.ok)return;}catch{}await sleep(300);}
 throw Error('P2_SERVICE_NOT_READY '+url);
}
async function stop(p){if(p){p.kill();await sleep(650);}}
async function request(url,method='GET',body=null){
 const r=await fetch(base+url,{method,headers:{'Content-Type':'application/json'},...(body===null?{}:{body:JSON.stringify(body)})});
 const j=await r.json();assert.ok(r.ok,method+' '+url+' failed '+r.status+' '+JSON.stringify(j));return j;
}
function generateTopic(prompt){
 const example={title:"An Ancient Hunt With an Unexpected Consequence",
 hook:"Everyone celebrates dinner. Then the real trouble arrives.",
 historical_period:"Late Pleistocene",region:"Western Eurasia",
 story_angle:"Hypothetical cause-and-effect comedy about a successful hunt",
 novelty_questions:["What consequence creates the next problem?"],
 factual_risks:["This specific story is illustrative, not archaeological evidence"]};
 return {schema_version:'TopicIdeas.v1',project_id:prompt.project_id,reservation_id:prompt.reservation_id,
 source_fingerprint:prompt.source_fingerprint,ideas:prompt.reserved_ids.ideas.map(idea_id=>({...example,idea_id}))};
}
function generateScript(prompt){
 const sections=prompt.reserved_ids.sections.map((section_id,index)=>({
 section_id,exact_text:'Section '+(index+1)+': The group celebrates success, until its next problem walks into camp.',
 historical_period:'Late Pleistocene',region:'Western Eurasia',delivery_note:'Dry conversational delivery'}));
 const quote='The group celebrates success';
 const start=sections[0].exact_text.indexOf(quote);
 return {schema_version:'ScriptHistoricalAnalysis.v1',project_id:prompt.project_id,
 reservation_id:prompt.reservation_id,source_fingerprint:prompt.source_fingerprint,
 parent_topic_artifact_id:prompt.reserved_ids.parent_artifact_id,
 parent_topic_hash:prompt.reserved_ids.parent_hash,
 full_script:sections.map(s=>s.exact_text).join('\n\n'),sections,
 claims:[{claim_id:'claim_001',section_id:sections[0].section_id,quote,start,end:start+quote.length,
 historical_period:'Late Pleistocene',region:'Western Eurasia',
 model_verification_state:'hypothetical',evidence_review_status:'unreviewed',
 source_leads:['Hypothetical archaeological research lead, not approved'],
 uncertainty_note:'Fictional scenario illustrating plausible tradeoffs'}],
 performance_notes:['Speak naturally without invented WPM timestamps']};
}
async function upload(page,stage,fixture){
 await page.locator('[data-testid="authoring-'+stage+'"] input[type="file"]').setInputFiles({
  name:stage+'.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(fixture),'utf8')});
 await page.locator('[data-testid="authoring-'+stage+'"]').getByRole('button',{name:'Validate JSON'}).click();
 await page.locator('[data-testid="authoring-'+stage+'"]').getByText(/VALID · [0-9]+ items/).waitFor();
 await page.locator('[data-testid="authoring-'+stage+'"]').getByRole('button',{name:'Import Candidate'}).click();
 await page.locator('[data-testid="authoring-'+stage+'"] .candidateList button').first().waitFor();
 await page.locator('[data-testid="authoring-'+stage+'"] .candidateList button').first().click();
 await page.locator('[data-testid="authoring-'+stage+'"] .reviewDetails').waitFor();
}
try{
 backend=spawn(python,['-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000'],
   {cwd:process.cwd(),env:{...process.env,HISTORY_DB_PATH:dbPath},stdio:'ignore'});
 await ready(base+'/health');
 const stamp=Math.random().toString(36).slice(2);
 const channel=await request('/api/history/channels','POST',{name:'Ancient '+stamp,language:'English',niche:'Ancient humans',history_dna:{}});
 const project=await request('/api/history/projects','POST',{name:'Hypothetical hunt '+stamp,channel_id:channel.channel_id,history_context:{tone:'Dry comedy'}});
 web=spawn(process.execPath,['node_modules/vite/bin/vite.js','--host','127.0.0.1','--port','5173','--strictPort'],
   {cwd:process.cwd(),stdio:'ignore'});
 await ready('http://127.0.0.1:5173/');
 browser=await chromium.launch({headless:true});
 await mkdir('out/p2-ui',{recursive:true});
 const page=await browser.newPage({viewport:{width:1440,height:900}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:5173/',{waitUntil:'networkidle'});
 await page.locator('#channel-select').selectOption(channel.channel_id);
 await page.locator('#project-select option[value="'+project.project_id+'"]').waitFor({state:'attached'});
 await page.locator('#project-select').selectOption(project.project_id);
 await page.locator('.dnaEditor summary').click();
 await page.locator('.dnaEditor').getByRole('button',{name:'Save ChannelDNA'}).click();
 await page.locator('.dnaEditor summary').getByText(/revision 1/).waitFor();
 await page.locator('[data-testid="authoring-topic"]').getByRole('button',{name:'Build Prompt'}).click();
 await page.locator('[data-testid="authoring-topic"] textarea[aria-label="External ChatGPT prompt"]').waitFor();
 let topicPrompt=await request('/api/history/projects/'+project.project_id+'/topic/prompt');
 assert.equal(topicPrompt.is_current,true);
 await upload(page,'topic',generateTopic(topicPrompt));
 await page.locator('[data-testid="authoring-topic"]').getByRole('button',{name:/Approve new SelectedTopic revision/}).click();
 await page.locator('[data-testid="authoring-topic"] .success').getByText(/Approved and published/).waitFor();
 const topic=await request('/api/history/projects/'+project.project_id+'/topic/current');
 assert.equal(topic.revision,1);
 await page.locator('.sidebar .navItem').nth(1).click();
 await page.locator('[data-testid="authoring-script_analysis"]').getByRole('button',{name:'Build Prompt'}).click();
 await page.locator('[data-testid="authoring-script_analysis"] textarea[aria-label="External ChatGPT prompt"]').waitFor();
 const scriptPrompt=await request('/api/history/projects/'+project.project_id+'/script-analysis/prompt');
 assert.equal(scriptPrompt.reserved_ids.parent_artifact_id,topic.artifact_id);
 await upload(page,'script_analysis',generateScript(scriptPrompt));
 await page.locator('[data-testid="authoring-script_analysis"]').getByRole('button',{name:/Approve new Script/}).click();
 await page.locator('[data-testid="authoring-script_analysis"] .success').getByText(/Approved and published/).waitFor();
 const script=await request('/api/history/projects/'+project.project_id+'/script-analysis/current');
 assert.ok(script.content.full_script&&script.stale===false);
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
 assert.equal(overflow,false,'P2 UI 1440px horizontal overflow');
 assert.equal(await page.locator('.stage').count(),7);
 assert.deepEqual(errors,[]);
 await page.screenshot({path:'out/p2-ui/topic-script-1440x900.png'});
 await page.screenshot({path:'out/p2-ui/topic-script-1440x900-full.png',fullPage:true});
 await stop(backend);backend=null;
 backend=spawn(python,['-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000'],
  {cwd:process.cwd(),env:{...process.env,HISTORY_DB_PATH:dbPath},stdio:'ignore'});
 await ready(base+'/health');
 const reloaded=await request('/api/history/projects/'+project.project_id+'/script-analysis/current');
 assert.equal(reloaded.content_hash,script.content_hash);
 assert.equal(reloaded.artifact_id,script.artifact_id);
 await page.reload({waitUntil:'networkidle'});
 await page.locator('#project-select').waitFor();
 await page.locator('[data-testid="authoring-script_analysis"]').waitFor();
 await page.locator('.sidebar .navItem').first().click();
 const inputs=page.locator('[data-testid="manual-topic"] textarea');
 const changes=['Another Ancient Hunt With New Trouble','Successful hunting attracts hungry rivals.',
 'Late Pleistocene','Western Eurasia','Second imagined problem after food arrives'];
 for(let i=0;i<5;i++)await inputs.nth(i).fill(changes[i]);
 await page.locator('[data-testid="manual-topic"]').getByRole('button',{name:'Approve Manual Topic'}).click();
 await page.locator('[data-testid="authoring-topic"] .success').getByText(/Manual selected Topic approved/).waitFor();
 const stale=await request('/api/history/projects/'+project.project_id+'/script-analysis/current');
 assert.equal(stale.stale,true);assert.equal(stale.content_hash,script.content_hash);
 for(const [w,h] of [[1920,1080]]){
   const p=await browser.newPage({viewport:{width:w,height:h}});
   const errs=[];p.on('pageerror',e=>errs.push(e.message));
   await p.goto('http://127.0.0.1:5173/',{waitUntil:'networkidle'});
   await p.locator('#channel-select').selectOption(channel.channel_id);
   await p.locator('#project-select option[value="'+project.project_id+'"]').waitFor({state:'attached'});
   await p.locator('#project-select').selectOption(project.project_id);
   await p.locator('.sidebar .navItem').nth(1).click();
   await p.locator('[data-testid="authoring-script_analysis"]').waitFor();
   assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   assert.deepEqual(errs,[]);
   await p.screenshot({path:'out/p2-ui/topic-script-1920x1080.png'});
   await p.screenshot({path:'out/p2-ui/topic-script-1920x1080-full.png',fullPage:true});
   await p.close();
 }
 console.log('P2_UI_E2E_PASS 1440x900 1920x1080 real Topic/Script file uploads, approval, restart, stale');
}finally{if(browser)await browser.close();await stop(web);await stop(backend);await rm(folder,{recursive:true,force:true});}
