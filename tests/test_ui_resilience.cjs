// Exercise shipped state refresh, image review and action handlers. No real processing.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/../src/alosstack_helper/static/app.js','utf8');
const code=source.slice(source.indexOf('function renderStackTimeline('),source.indexOf('function summary('))+source.slice(source.indexOf('let refreshGeneration='),source.indexOf("$('closeImage').onclick"));
const elements=new Map(),revoked=[],requests=[],fetches=[];let updates=0,urlCount=0;
class Element{
 constructor(tag='div',text=''){this.tagName=tag;this.textContent=text||'';this.children=[];this.hidden=false;this.disabled=false;this.checked=false;this.value='';this.attributes={};this.scrollHeight=0;this.scrollTop=0;this.clientHeight=0;this.classList={add(){},remove(){}};}
 append(...xs){for(const x of xs)this.children.push(...(x.tagName==='fragment'?x.children:[x]));}
 replaceChildren(...xs){this.children=[];this.textContent='';this.append(...xs);}
 setAttribute(k,v){this.attributes[k]=v;}
 querySelectorAll(selector){return this.children.flatMap(x=>[...(x.tagName==='input'&&(!selector.includes(':checked')||x.checked)?[x]:[]),...x.querySelectorAll(selector)]);}
 showModal(){this.open=true;}
}
const el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const node=(tag,text,cls)=>{const e=new Element(tag,text);e.className=cls||'';return e;},tree=e=>[e.textContent,...e.children.map(tree)].join(' ');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};},flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
const project={id:'one',config:{name:'Example',root:'/example',workflow:'alosStack'},ion:{estimate:true,apply:true},status:'complete',history:[]},two={...project,id:'two',config:{...project.config,name:'Second',root:'/second'}};
const context=vm.createContext({TypeError,current:'one',projects:[],defaults:{},statusNames:{complete:'complete',review:'review'},reviewLoaded:null,objectURLs:[],token:'test-token',
 $:el,node,summary(){},fail(e){el('toast').textContent=e.message;},clear(){},action:(e,f)=>e.onclick=f,
 document:{createDocumentFragment:()=>node('fragment'),createTextNode:t=>node('text',t)},URL:{createObjectURL:()=>`blob:${++urlCount}`,revokeObjectURL:u=>revoked.push(u)},
 window:{workbenchUpdate:async()=>{updates++;}},
 fetch:async(path,options)=>{fetches.push({path,options});return{ok:true,blob:async()=>({})};},
 api:async path=>{if(path==='/api/state')return{projects:[project],defaults:{}};throw new TypeError('Failed to fetch');}});
vm.runInContext(code,context);
(async()=>{
 await vm.runInContext('refresh()',context);
 assert.equal(updates,1,'failed log fetch must not hide the analysis workbench');assert.equal(el('logStatus').hidden,false);assert.match(el('logStatus').textContent,/其他設定與成果仍可使用/);assert.equal(el('log').textContent,'日誌尚未載入。');
 assert.match(tree(el('timeline')),/校正後檢查/);assert.match(tree(el('timeline')),/cmd4 成果驗收/,'exercise actual timeline helper, not a stub');
 context.api=async()=>({text:'Completed processing'});await vm.runInContext('refreshLog()',context);assert.equal(el('logStatus').hidden,true);assert.equal(el('log').textContent,'Completed processing');
 let first,second;context.api=path=>new Promise(resolve=>{if(path.endsWith('one'))first=resolve;else second=resolve;});const old=vm.runInContext('refreshLog()',context);context.current='two';const newer=vm.runInContext('refreshLog()',context);second({text:'Project two'});await newer;first({text:'Project one'});await old;assert.equal(el('log').textContent,'Project two','late logs cannot replace the current project');
 // Overlapping state polls never restore the previous project/status.
 const a=deferred(),b=deferred();let calls=0;context.current='one';context.api=path=>path==='/api/state'?(calls++?b.promise:a.promise):Promise.resolve({text:'log'});
 const oldState=vm.runInContext('refresh()',context);context.current='two';const freshState=vm.runInContext('refresh()',context);b.resolve({projects:[project,two],defaults:{}});await freshState;a.resolve({projects:[{...project,status:'failed'}],defaults:{}});await oldState;assert.equal(el('projectName').textContent,'Second');assert.equal(context.projects.length,2);
 // A review is committed only after every image for that project has loaded.
 const reviewOne={...project,status:'review'},reviewTwo={...two,status:'review'};context.projects=[reviewOne,reviewTwo];context.current='one';vm.runInContext('syncIonReview(projects[0])',context);
 context.api=async(path,body)=>{requests.push({path,body});if(path.startsWith('/api/review')){const p=path.endsWith('one')?'240101-240201':'250101-250201';return{pairs:[p],images:[p+'.png'],options:{nrlks1:2}};}return{};};
 const imageOne=deferred();context.fetch=async(path,options)=>{fetches.push({path,options});return path.includes('id=one')?imageOne.promise:{ok:true,blob:async()=>({})};};
 const loadingOne=vm.runInContext("renderReview('one')",context);await flush();assert.equal(el('continue').disabled,true);assert.equal(context.reviewLoaded,null);
 context.current='two';vm.runInContext('syncIonReview(projects[1])',context);await vm.runInContext("renderReview('two')",context);assert.equal(context.reviewLoaded,'two');assert.match(tree(el('pairs')),/250101-250201/);assert.equal(el('continue').disabled,false);
 imageOne.resolve({ok:true,blob:async()=>({})});await loadingOne;assert.equal(urlCount,1,'discard old review before generating its image URL');assert.equal(context.reviewLoaded,'two');assert.doesNotMatch(tree(el('pairs')),/240101-240201/);assert.equal(fetches.at(-1).options.headers['X-Token'],'test-token');
 // Requests capture the reviewed project; switching while submitting cannot change the target.
 el('reviewed').checked=true;el('pairs').querySelectorAll('input')[0].checked=true;const send=deferred();context.api=async(path,body)=>{requests.push({path,body});return path==='/api/action'?send.promise:{};};const submitting=el('continue').onclick();assert.equal(el('continue').disabled,true);assert.equal(requests.at(-1).body.id,'two');assert.deepEqual(Array.from(requests.at(-1).body.excluded),['250101-250201']);context.current='one';send.resolve({});await submitting;assert.equal(el('continue').disabled,true,'other project has no loaded review');
 // A failed/missing image cannot leave an old consent enabled; no partial cards are mounted.
 vm.runInContext('syncIonReview(projects[0])',context);context.api=async()=>({pairs:['240101-240201'],images:[],options:{}});await assert.rejects(vm.runInContext("renderReview('one')",context),/沒有對應檢查圖/);assert.equal(context.reviewLoaded,null);assert.equal(el('continue').disabled,true);assert.match(tree(el('pairs')),/未完整載入/);
 // Repeated polls share an in-flight load, preventing slow previews from restarting forever.
 const waiting=deferred();let reviewCalls=0;context.api=async()=>{reviewCalls++;return waiting.promise;};const r1=vm.runInContext("renderReview('one')",context),r2=vm.runInContext("renderReview('one')",context);assert.equal(reviewCalls,1);waiting.resolve({pairs:['240101-240201'],images:['240101-240201.png'],options:{}});context.fetch=async()=>({ok:true,blob:async()=>({})});await Promise.all([r1,r2]);assert.equal(context.reviewLoaded,'one');assert.equal(el('continue').disabled,false);
 console.log('UI resilience: actual timeline, failed logs, stale state/review images, project-bound actions and complete-review gates passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
