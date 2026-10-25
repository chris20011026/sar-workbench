// Deliberate consent, stale-response guards and reopening a portable transfer.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map(),timers=[],calls=[];
class Element{
 constructor(tag='div',text=''){this.tagName=tag;this.textContent=text||'';this.value='';this.children=[];this.listeners={};this.attributes={};this.hidden=false;this.disabled=false;this.checked=false;this.open=false;}
 set id(v){this._id=v;elements.set(v,this);}get id(){return this._id;}
 append(...nodes){this.children.push(...nodes);if(this.tagName==='select'&&!this.value&&nodes.length)this.value=nodes[0].value;}
 replaceChildren(...nodes){this.children=[];this.append(...nodes);}
 setAttribute(k,v){this.attributes[k]=v;}removeAttribute(k){delete this.attributes[k];}
 addEventListener(n,f){this.listeners[n]=f;}
 before(){}
 querySelectorAll(selector){return selector==='.portable-route li'?routeSteps:[];}
 focus(options){this.focusOptions=options;this.focused=true;}
 showModal(){this.open=true;}close(){this.open=false;this.listeners.close?.();}
 get options(){return this.children;}
}
const routeSteps=Array.from({length:4},()=>new Element('li')),step=()=>routeSteps.findIndex(e=>e.attributes['aria-current']==='step');
const $=id=>{if(!elements.has(id))elements.set(id,new Element(id==='portableEnvironment'?'select':'div'));return elements.get(id);},node=(tag,text,cls)=>{const e=new Element(tag,text);e.className=cls;return e;};
let latest=null,job={},handler=null,refreshes=0,registry=0;
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const window={refreshWorkspaceRegistry:async()=>{registry++;}};
const context=vm.createContext({window,node,$,document:{body:node('body'),hidden:false},defaults:{home:'/home',env:'/env',environments:[{name:'insar_arm',isce:true,mintpy:true,path:'/env'},{name:'isce3',isce:false,mintpy:false,path:'/isce3'}]},current:null,reviewLoaded:null,
 setInterval:f=>timers.push(f),action:(b,f)=>b.onclick=f,refresh:async()=>{refreshes++;},choosePath:async(kind,callback)=>callback('/chosen'),
 api:async(path,body)=>{calls.push({path,body});if(handler){const value=handler(path,body);if(value!==undefined)return value;}if(path==='/api/portable-latest')return{job:latest};if(path.startsWith('/api/portable?'))return job;if(path==='/api/portable-export-preview'||path==='/api/portable-import-preview'){job={job_id:'preview',mode:path.includes('export')?'export':'import',status:'planning'};return job;}if(path==='/api/portable-start'){job={...job,status:'running'};return job;}if(path==='/api/portable-stop'){job={...job,status:'stopped'};return job;}throw Error(path);}});
vm.runInContext(fs.readFileSync(__dirname+'/../src/alosstack_helper/static/portable.js','utf8'),context);
const submit=()=>$('portableForm').onsubmit({preventDefault(){}}),tick=async()=>{timers[0]();await new Promise(r=>setImmediate(r));};
const preview={source:'/old/proc',destination:'/destination/package',files:12,bytes:1024,required:2048,free:4096,dates:['220101','220201','220301'],pairs:['220101-220201','220201-220301'],reference:'220201',warnings:['範例品質未知']};
(async()=>{
 $('portableDialog').scrollTop=700;await window.openPortableExport('source',{name:'原專案',root:'/old'});assert.equal($('portableDialog').scrollTop,0);assert.equal($('portableTitle').focusOptions.preventScroll,true);assert.equal(step(),0,'fresh export starts at step 01');assert.equal($('portableTitle').textContent,'建立可攜分析包');assert.equal($('portableImportFields').hidden,true);assert.equal($('portableEnvironment').options.length,1);
 $('portableParent').value='/destination';$('portableFolder').value='package';await submit();assert.equal(calls.at(-1).path,'/api/portable-export-preview');assert.equal($('portableStop').hidden,false);
 job={job_id:'preview',source_id:'source',mode:'export',status:'ready',preview};await tick();assert.equal($('portableStart').disabled,true);assert.equal($('portableConsent').checked,false);
 const before=calls.length;await $('portableStart').onclick();assert.equal(calls.length,before,'unchecked consent never sends a start');
 $('portableConsent').checked=true;$('portableConsent').onchange();assert.equal($('portableStart').disabled,false);
 const pending=deferred();handler=path=>path==='/api/portable-start'?pending.promise:undefined;const first=$('portableStart').onclick();const twice=$('portableStart').onclick();assert.equal($('portableStart').disabled,true);assert.equal(calls.filter(c=>c.path==='/api/portable-start').length,1,'double-click sends one request');pending.resolve({...job,status:'running'});await first;await twice;handler=null;assert.equal($('portableConsent').checked,false,'phase change clears consent');
 job={...job,status:'complete',destination:'/destination/package'};await tick();assert.equal($('portableImportResult').hidden,false);assert.equal($('portableOpenProject').hidden,true);assert.ok(registry>0);assert.equal(step(),3,'completed export shows step 04');
 $('portableDialog').scrollTop=900;await $('portableImportResult').onclick();assert.equal($('portableDialog').scrollTop,0,'new import form opens at its title');assert.equal(step(),0,'fresh import clears completed step 04');assert.equal($('portableSource').value,'/destination/package');assert.equal($('portableTitle').textContent,'匯入可攜分析包');assert.equal($('portableForm').hidden,false);assert.equal($('portableConsent').checked,false);
 $('portableName').value='新專案';$('portableParent').value='/new';$('portableFolder').value='analysis';await submit();assert.equal(calls.at(-1).path,'/api/portable-import-preview');assert.equal(calls.at(-1).body.environment,'/env');
 job={job_id:'import-one',mode:'import',status:'ready',preview};await tick();assert.equal($('portableStart').disabled,true);
 $('portableConsent').checked=true;$('portableConsent').onchange();await $('portableStart').onclick();assert.equal(calls.at(-1).body.reviewed,true);
 job={...job,status:'complete',project_id:'new-project'};await tick();assert.equal($('portableOpenProject').hidden,false);await $('portableOpenProject').onclick();assert.equal(context.current,'new-project');assert.equal(refreshes,1);assert.equal($('portableDialog').open,false);
 latest={job_id:'in-progress',mode:'export',status:'running',preview};job=latest;await window.openPortableImport();assert.equal($('portableTitle').textContent,'建立可攜分析包','an active export can be reopened from import entry');assert.equal($('portableForm').hidden,true);assert.equal($('portableStop').hidden,false);assert.equal(step(),2,'restored active job keeps processing step');
 await $('portableStop').onclick();assert.equal($('portableReset').hidden,false);assert.match($('portableNext').textContent,/未完成/);
 $('portableDialog').scrollTop=500;await $('portableReset').onclick();assert.equal($('portableDialog').scrollTop,0,'reset returns to the visual introduction');assert.equal(step(),0,'reset sets exactly step 01');assert.equal(routeSteps.filter(e=>e.attributes['aria-current']).length,1);
 // Old latest/poll responses cannot replace a later opening or a closed dialog.
 latest=null;const stale=deferred();handler=path=>path==='/api/portable-latest'?stale.promise:undefined;const opening=window.openPortableImport();$('portableClose').onclick();handler=null;await window.openPortableExport('different',{root:'/different'});stale.resolve({job:{job_id:'stale',mode:'import',status:'running'}});await opening;assert.equal($('portableTitle').textContent,'建立可攜分析包');assert.equal($('portableForm').hidden,false);
 $('portableParent').value='/new';$('portableFolder').value='../overwrite';await submit();assert.match($('portableError').textContent,/斜線/);assert.equal($('portablePreview').disabled,false);
 const record={source_name:'來源 <img src=x onerror=alert(1)>',source_project_id:'old-id',processing_environment:'/previous/insar_arm',history:[{action:'finish'}],analysis_records:[{options:{deramp:'linear'}}],ion:{estimate:true,apply:false},ion_excluded:['220101-220201'],source_config:{properties:{polarization:'HH'}},environment_packages:[{name:'isce2',version:'2.6'}]};
 handler=path=>path.startsWith('/api/portable-provenance')?record:undefined;
 const tree=e=>[e.textContent,...e.children.map(tree)].join(' ');
 await window.portableProjectUpdate({id:'portable-one',portable_import:{}});
 assert.equal($('portableProvenance').hidden,false);assert.match(tree($('portableProvenanceContent')),/來源 <img/);assert.match(tree($('portableProvenanceContent')),/套用：關閉/);assert.match(tree($('portableProvenanceContent')),/1 個既有分析/);assert.match($('portableProvenanceStatus').textContent,/不會改動/);
 const count=calls.length;await window.portableProjectUpdate({id:'portable-one',portable_import:{}});assert.equal(calls.length,count,'ordinary page polling does not reread source records');
 const oldRecord=deferred();handler=path=>path.includes('id=old-record')?oldRecord.promise:path.startsWith('/api/portable-provenance')?{...record,source_name:'new-record'}:undefined;
 const oldRead=window.portableProjectUpdate({id:'old-record',portable_import:{}});await window.portableProjectUpdate({id:'new-record',portable_import:{}});oldRecord.resolve({...record,source_name:'stale-record'});await oldRead;assert.match(tree($('portableProvenanceContent')),/new-record/);assert.doesNotMatch(tree($('portableProvenanceContent')),/stale-record/);
 await window.portableProjectUpdate({id:'ordinary'});assert.equal($('portableProvenance').hidden,true);assert.equal($('portableProvenanceContent').children.length,0);
 const wb=fs.readFileSync(__dirname+'/../src/alosstack_helper/static/workbench.js','utf8'),note=wb.match(/function environmentNote\(s\)\{[^\n]+\}/)[0];vm.runInContext(note,context);$('analysisEnv').value='/new-env';context.environmentNote({portable_import:{},import_only:true,config:{env:'/old-env'}});assert.match($('analysisEnvNote').textContent,/分析包來源紀錄/);assert.doesNotMatch($('analysisEnvNote').textContent,/未記錄/);
 console.log('Portable UI: explicit consent, one start per click burst, new project routing, active job reopening, incomplete copy guidance and stale response guards passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
