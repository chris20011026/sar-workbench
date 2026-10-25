/* Preparation guidance is intentionally separate from scientific approvals and jobs. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const staticDir=path.join(__dirname,'../src/alosstack_helper/static');
const elements=new Map(),calls=[];
let document;
class Element{
 constructor(tag='div'){this.tagName=tag;this.children=[];this.attributes={};this.dataset={};this.listeners={};this.textContent='';this.hidden=false;this.disabled=false;this.value='';this.checked=false;this.open=false;this.isConnected=true;this.scrollTop=0;}
 set id(value){this._id=value;elements.set(value,this);}get id(){return this._id;}
 append(...items){this.children.push(...items);for(const item of items)item.parentElement=this;if(this.tagName==='select'&&!this.value&&items.length)this.value=items[0].value;}
 replaceChildren(...items){this.children=[];this.append(...items);}
 setAttribute(name,value){this.attributes[name]=String(value);}removeAttribute(name){delete this.attributes[name];}
 addEventListener(name,fn){this.listeners[name]=fn;}
 focus(options){document.activeElement=this;this.focusOptions=options;}
 showModal(){this.open=true;}
 close(){this.open=false;this.listeners.close?.();}
 click(){if(!this.disabled)return this.onclick?.();}
}
document={body:new Element('body'),createElement:tag=>new Element(tag),getElementById:id=>elements.get(id)||null,activeElement:null};
const create=(id,fn)=>{const e=new Element('button');e.id=id;e.onclick=fn;document.body.append(e);return e;};
const opener=create('testOpener');document.activeElement=opener;
create('openWelcome',()=>calls.push('tools'));
create('openLesson',()=>calls.push('lesson-panel'));
create('missionNisarTab',()=>calls.push('nisar-tab'));
// Any accidental job click fails the test, instead of resembling successful navigation.
for(const id of ['runLesson','installRuntime','organizeImport','terrainStart','applyGuided','samplingConfirm'])create(id,()=>{throw Error('guide must not execute '+id);});
const window={};
for(const [name,label] of Object.entries({openAlosImport:'alos-import',openWorkspaceCreate:'identify',openLegacyImport:'legacy',openPortableImport:'portable',openAlosPreparation:'alos4',openNisar:'nisar'}))window[name]=()=>calls.push(label);
const context=vm.createContext({window,document,console,Set,api:()=>{throw Error('guide must not call processing APIs');},fetch:()=>{throw Error('guide must not fetch');}});
vm.runInContext(fs.readFileSync(path.join(staticDir,'getting-started-data.js'),'utf8'),context,{filename:'getting-started-data.js'});
vm.runInContext(fs.readFileSync(path.join(staticDir,'getting-started.js'),'utf8'),context,{filename:'getting-started.js'});
const get=id=>{const value=elements.get(id);assert.ok(value,`missing #${id}`);return value;};
const descendants=root=>root.children.flatMap(item=>[item,...descendants(item)]);
const text=root=>[root.textContent,...root.children.map(text)].join(' ');
const action=name=>{const matches=descendants(get('gettingStartedContent')).filter(e=>e.dataset.starterAction===name);assert.ok(matches.length,`missing action ${name}`);return matches[0];};
const changeWorkflow=id=>{get('gettingStartedWorkflow').value=id;get('gettingStartedWorkflow').onchange();};
const step=i=>get(`gettingStartedStep${i}`).click();
const checks=()=>descendants(get('gettingStartedContent')).filter(e=>e.tagName==='input'&&e.type==='checkbox');
const markAll=()=>{for(const input of checks()){input.checked=true;input.onchange();}};
const currentSteps=()=>[0,1,2,3,4].filter(i=>get(`gettingStartedStep${i}`).attributes['aria-current']==='step');
const guide=window.SAR_STARTER_GUIDE;

async function main(){
 assert.ok(guide.workflows.length>=9,'all six core modes and additional preparation/import paths are described');
 assert.equal(new Set(guide.workflows.map(item=>item.id)).size,guide.workflows.length,'workflow ids are unique');
 for(const flow of guide.workflows){
  assert.ok(flow.summary&&flow.requirements.length&&flow.folders.length&&flow.checklist.length,`${flow.id} needs data, folders and a personal checklist`);
  assert.equal(new Set(flow.checklist.map(item=>item.id)).size,flow.checklist.length);
  for(const source of flow.sources||[])assert.match(source.url,/^https:\/\//,'only explicit HTTPS links');
  if(flow.status==='planned')assert.equal(flow.next.length,0,'planned flows cannot launch');
 }

 window.openGettingStarted();
 assert.equal(get('gettingStartedDialog').open,true);
 assert.equal(get('gettingStartedDialog').attributes['aria-labelledby'],'gettingStartedTitle');
 assert.equal(get('gettingStartedClose').attributes['aria-label'],'關閉從零開始指引');
 assert.equal(document.activeElement,get('gettingStartedTitle'));
 assert.deepEqual(currentSteps(),[0]);
 assert.equal(get('gettingStartedPrevious').hidden,true);
 assert.equal(get('gettingStartedWorkflow').value,'alosStack');
 assert.equal(calls.length,0,'opening guide does not navigate or run a job');
 get('gettingStartedNext').click();
 assert.deepEqual(currentSteps(),[1]);
 assert.match(text(get('gettingStartedContent')),/必要資料/);
 assert.match(text(get('gettingStartedContent')),/選用／依需求/);
 assert.match(text(get('gettingStartedContent')),/不確定資料種類/);
 assert.ok(action('identify'));
 assert.match(text(get('gettingStartedContent')),/看不懂的名詞/);
 assert.equal(document.activeElement,get('gettingStartedSectionTitle'),'step transitions announce their own heading');
 const sources=descendants(get('gettingStartedContent')).filter(e=>e.tagName==='a');
 assert.ok(sources.length,'official sources are discoverable');
 for(const link of sources){assert.equal(link.target,'_blank');assert.equal(link.rel,'noopener noreferrer');}
 assert.equal(calls.length,0,'displaying sources does not request external data');

 step(2);
 assert.equal(get('gettingStartedFolder0').attributes['aria-pressed'],'true');
 const secondFolder=guide.workflows.find(item=>item.id==='alosStack').folders[1];
 get('gettingStartedFolder1').click();
 assert.equal(get('gettingStartedFolder0').attributes['aria-pressed'],'false');
 assert.equal(get('gettingStartedFolder1').attributes['aria-pressed'],'true');
 assert.match(text(get('gettingStartedFolderInfo')),new RegExp(secondFolder.role));
 assert.match(text(get('gettingStartedContent')),/不是你電腦的實際檔案/);

 step(3);assert.ok(checks().length);
 markAll();
 const total=checks().length;
 assert.equal(get('gettingStartedChecklistCount').textContent,`已自行勾選 ${total} / ${total} 項`);
 assert.equal(calls.length,0,'personal checklist has no navigation/job side effects');
 assert.match(text(get('gettingStartedContent')),/不會替你通過影格對應/);
 step(4);assert.match(text(get('gettingStartedContent')),/尚未執行資料驗證/);
 assert.equal(get('gettingStartedNext').hidden,true);
 step(3);assert.ok(checks().every(e=>e.checked),'navigation preserves this workflow preparation');
 changeWorkflow('topsApp');assert.ok(checks().every(e=>!e.checked),'changing workflow clears the personal checklist');
 changeWorkflow('alosStack');assert.ok(checks().every(e=>!e.checked),'returning to old workflow does not restore previous approvals');
 get('gettingStartedDialog').scrollTop=300;
 step(1);assert.equal(get('gettingStartedDialog').scrollTop,0,'new step starts at top');

 // Walk each pathway; planned workflows must never provide a primary run entry.
 for(const flow of guide.workflows){
  changeWorkflow(flow.id);
  for(let i=1;i<=4;i++){step(i);assert.deepEqual(currentSteps(),[i]);}
  if(flow.status==='planned'){
   assert.match(text(get('gettingStartedContent')),/尚未開放執行/);
   assert.equal(descendants(get('gettingStartedContent')).filter(e=>e.tagName==='button'&&e.className==='primary').length,0);
  }
 }

 window.openGettingStarted({workflow:'alosStack',step:4});
 await action('alos-import').click();assert.equal(calls.pop(),'alos-import');assert.equal(get('gettingStartedDialog').open,false);
 window.openGettingStarted();await action('demo').click();assert.deepEqual(calls.splice(0),['tools','lesson-panel'],'sample action reveals panels only');
 for(const [actionId,expected] of [['tools','tools'],['identify','identify'],['legacy','legacy'],['portable','portable']]){
  window.openGettingStarted({step:4});await action(actionId).click();assert.deepEqual(calls.splice(0),[expected]);
 }
 for(const id of ['alos4','nisar']){
  window.openGettingStarted({workflow:id,step:4});await action(id).click();assert.deepEqual(calls.splice(0),id==='nisar'?['nisar','nisar-tab']:[id]);
 }

 // A failed asynchronous screen open must leave a visible recovery route.
 window.openWorkspaceCreate=async()=>{throw Error('入口暫時不可用');};
 window.openGettingStarted({step:4});await action('identify').click();
 assert.equal(get('gettingStartedDialog').open,true);
 assert.equal(get('gettingStartedError').hidden,false);
 assert.equal(get('gettingStartedError').textContent,'入口暫時不可用');
 assert.equal(document.activeElement,get('gettingStartedError'));
 step(0);assert.equal(get('gettingStartedError').hidden,true);

 let resolvePending;let navigationCount=0;
 window.openWorkspaceCreate=()=>{navigationCount++;return new Promise(resolve=>{resolvePending=resolve;});};
 window.openGettingStarted({step:4});
 const identify=action('identify'),pending=identify.click();await identify.click();assert.equal(navigationCount,1,'repeated clicks do not open duplicate screens');resolvePending();await pending;

 // Returning focus and resetting the route works on repeated opens and native Escape close.
 opener.focus();window.openGettingStarted();get('gettingStartedClose').click();assert.equal(document.activeElement,opener);
 opener.focus();window.openGettingStarted();step(4);get('gettingStartedDialog').scrollTop=500;get('gettingStartedDialog').close();assert.equal(document.activeElement,opener);
 window.openGettingStarted();assert.deepEqual(currentSteps(),[0]);assert.equal(get('gettingStartedDialog').scrollTop,0);
 assert.equal(get('gettingStartedNext').hidden,false);
 assert.equal(calls.length,0);
 console.log('Getting started guide: workflow, folders, checklist isolation, safe navigation, accessibility and recovery checks passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
