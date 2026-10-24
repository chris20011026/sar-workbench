const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map(),polls=[],calls=[];
class Element{
 constructor(tag='div',text=''){this.tagName=tag;this.textContent=text||'';this.children=[];this.value='';this.disabled=false;this.hidden=false;this.checked=false;this.listeners={};}
 append(...items){this.children.push(...items);if(this.tagName==='select'&&!this.value&&items[0])this.value=items[0].value;}
 replaceChildren(...items){this.children=[];this.value='';this.append(...items);}
 after(){} setAttribute(){} scrollIntoView(){} addEventListener(n,f){this.listeners[n]=f;}
 get options(){return this.children;}
}
const $=id=>{if(!elements.has(id))elements.set(id,new Element(id==='selftestEnvironment'?'select':'div'));return elements.get(id);};
const node=(tag,text,cls)=>{const e=new Element(tag,text);e.className=cls;return e;};
const tree=e=>[e.textContent,...e.children.map(tree)].join(' '),all=e=>[e,...e.children.flatMap(all)];
let status={status:'idle'},refreshes=0,report={};
const window={detectedEnvironments:[{name:'isce3',mintpy:true,isce:false,path:'/not-suitable'},{name:'insar_arm',mintpy:true,isce:true,path:'/env'}],workbenchState:{busy:false}};
const context=vm.createContext({window,node,$,setInterval:f=>polls.push(f),setTimeout(){},
 document:{createTextNode:t=>node('text',t)},action:(b,f)=>b.onclick=f,
 refresh:async()=>{refreshes++;window.workbenchState.busy=status.status==='running';},
 api:async(path,body)=>{calls.push({path,body});if(path==='/api/selftest-start'){status={status:'running'};return status;}if(path==='/api/selftest-stop'){status={status:'cancelled'};return status;}if(path==='/api/selftest')return status;if(path.startsWith('/api/analysis-recovery'))return report;if(path==='/api/analysis-recover')return{id:'new',status:'diagnostic_review'};throw Error(path);}});
vm.runInContext(fs.readFileSync(__dirname+'/../src/alosstack_helper/static/operations.js','utf8'),context);
(async()=>{
 assert.equal($('selftestEnvironment').options.length,1);assert.equal($('selftestEnvironment').value,'/env');
 await $('selftestStart').onclick();assert.equal($('selftestStart').disabled,true);assert.equal(window.workbenchState.busy,true);
 status={status:'passed',run_id:'one',checks:[{id:'known',label:'Known answer',status:'passed',details:'correct'}]};
 await $('openWelcome').listeners.click();assert.equal(window.workbenchState.busy,false);assert.equal($('selftestStart').disabled,false,'terminal self-test must refresh global busy state');assert.equal(refreshes,2);
 assert.match(tree($('selftestResults')),/Known answer/);
 const job={kind:'mintpy',status:'failed',id:'old',project_id:'test'},target=node('div');let current=true,recovered=null;
 report={can_recover:true,checkpoint_id:'verified',status:'diagnostic_review',checks:['完整性已核對'],bytes:1024};
 window.mintpyRecovery.render(job,target,{busy:false,isCurrent:()=>current,onRecovered:async r=>{recovered=r;}});
 let inspect=all(target).find(e=>e.tagName==='button');await inspect.onclick();
 const consent=all(target).find(e=>e.tagName==='input'),button=all(target).find(e=>e.textContent==='建立恢復版本 →');
 assert.equal(button.disabled,true);assert.equal(recovered,null);consent.checked=true;consent.onchange();assert.equal(button.disabled,false);
 await button.onclick();assert.equal(recovered.id,'new');assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1).body)),{id:'test',run:'old',checkpoint_id:'verified',reviewed:true});
 const unavailable=node('div');report={can_recover:false,reason:'來源已改變'};window.mintpyRecovery.render(job,unavailable,{busy:false,isCurrent:()=>true,onRecovered(){throw Error('must not recover');}});await all(unavailable).find(e=>e.tagName==='button').onclick();assert.match(tree(unavailable),/來源已改變/);assert.equal(all(unavailable).filter(e=>e.tagName==='input').length,0);
 const stale=node('div');report={can_recover:true};window.mintpyRecovery.render(job,stale,{busy:false,isCurrent:()=>false,onRecovered(){}});await all(stale).find(e=>e.tagName==='button').onclick();assert.equal(all(stale).filter(e=>e.tagName==='input').length,0);
 console.log('Operations UI: environment filter, terminal busy refresh, explicit bound recovery, changed-source refusal and stale response guards passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
