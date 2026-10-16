// Production UI checks with a fake DOM/API: no real files, dialogs or conversion.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map(),requests=[],maps=[];
class Element{
 constructor(tag='div',text='',cls=''){this.tagName=tag;this.children=[];this._text=text||'';this.value='';this.className=cls||'';this.hidden=false;this.disabled=false;this.checked=false;this.style={};this.events={};this.classList={remove(){},add(){},toggle(){}};}
 set id(v){this._id=v;elements.set(v,this);}get id(){return this._id;}
 get options(){return this.children.filter(e=>e.tagName==='option');}get firstChild(){return this.children[0];}
 set textContent(v){this._text=String(v);this.children=[];}get textContent(){return this._text+' '+this.children.map(e=>e.textContent).join(' ');}
 append(...values){for(const v of values){v.parentElement=this;this.children.push(v);if(this.tagName==='select'&&this.options.length===1)this.value=v.value;}}
 prepend(e){this.children.unshift(e);e.parentElement=this;}before(e){this.parentElement?.append(e);}replaceWith(e){this.parentElement?.append(e);}remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(e=>e!==this);}
 replaceChildren(...values){this.children=[];this._text='';this.append(...values);}
 setAttribute(k,v){this[k]=v;}addEventListener(k,fn){this.events[k]=fn;}closest(){return this.parentElement||null;}showModal(){this.open=true;}close(){this.open=false;}
 querySelectorAll(selector){const children=this.children.flatMap(e=>[e,...e.querySelectorAll(selector)]);if(selector==='input')return children.filter(e=>e.tagName==='input');if(selector==='.ap-group input')return ['apLeftProducts','apRightProducts'].flatMap(id=>elements.get(id)?.querySelectorAll('input')||[]);return [];}
 set innerHTML(html){for(const m of html.matchAll(/<([a-z0-9-]+)[^>]*\bid="([^"]+)"[^>]*>/g)){const e=new Element(m[1]);e.id=m[2];const value=m[0].match(/\bvalue="([^"]*)"/);if(value)e.value=value[1];e.hidden=/\bhidden\b/.test(m[0]);e.disabled=/\bdisabled\b/.test(m[0]);if(m[1]==='select'){const inside=html.slice(m.index+m[0].length,html.indexOf('</select>',m.index));for(const option of inside.matchAll(/<option value="([^"]*)">([^<]*)<\/option>/g)){const o=new Element('option',option[2]);o.value=option[1];e.append(o);}}this.append(e);}}
}
const node=(tag,text,cls)=>new Element(tag,text,cls),main=node('main');
for(const id of ['setup','project','welcome','crumb','pickerStatus','picker','files','chooseDir','browsePath']){const e=node('div');e.id=id;main.append(e);}
const footprint={type:'Polygon',coordinates:[[[121,24],[122,24],[122,25],[121,25],[121,24]]]};
const product=(id,sensor,date,frame,convertible)=>({id,sensor,date,path:'/source/'+id+'/LED',name:'產品 '+id,polarizations:['HH'],metadata:{frame,level:'1.1'},convertible,footprint,output_bytes:1024,footprint_status:'metadata_outline',warnings:[],errors:[]});
function fixture(id='first'){
 return{id,status:'ready',stage:'掃描完成',config:{source:'/source',environment:'/science',root:'',tool:''},report:{products:[product('a','ALOS-2','20200101','0480',false),{...product('b','ALOS-2','20200101','0490',false),footprint:null},product('c','ALOS-4','20250101','9000',true),product('d','ALOS-4','20250201','9000',true)],warnings:[]},progress:{items:[]},log:'掃描紀錄'};
}
const clone=v=>JSON.parse(JSON.stringify(v));let serverJob=fixture(),coverageResolver=null;
const context=vm.createContext({Node:Element,node,$:id=>elements.get(id),current:null,defaults:{home:'/home',environments:[{name:'science',path:'/science',isce:true}],env:'/science'},token:'t',URLSearchParams,
 document:{querySelector:()=>main,activeElement:null,querySelectorAll:()=>[],getElementById:id=>elements.get(id)},
 window:{leaveWorkspace(){},closeLibrary(){},closeMissions(){context.window.closeAlosPreparation?.();},scrollTo(){},refreshWorkspaceRegistry:async()=>{}},
 action:(el,fn)=>el.onclick=fn,clear(){},fail:e=>{throw e;},choosePath:async()=>{},setTimeout:()=>0,clearTimeout(){},geoMaps:new Set(),refreshMapSizes(){},
 labeled:(text,control)=>{const e=node('label',text);e.append(control);return e;},downloadBlob(){},
 L:{featureGroup:()=>({addLayer(){},getBounds:()=>({isValid:()=>true}),addTo(){}}),geoJSON:()=>({bindTooltip(){},addTo(){}})},
 geoBase(){const map={remove(){this.removed=true;}};maps.push(map);const base=node('select');base.append(node('option'));return{map,base};},
 api:async(path,data)=>{requests.push({path,data});if(path==='/api/state')return{defaults:context.defaults};if(path==='/api/alos-preparation')return{jobs:[{id:serverJob.id,name:'準備',status:serverJob.status}]};if(path.startsWith('/api/alos-preparation?'))return clone(serverJob);if(path==='/api/alos-preparation-run'){serverJob.status='running';return clone(serverJob);}if(path==='/api/alos-preparation-coverage')return new Promise(resolve=>coverageResolver=resolve);if(path==='/api/alos-preparation-review')return{...data,processing_unlocked:false};throw Error(path);},
});
let code=fs.readFileSync(__dirname+'/../src/alosstack_helper/static/alos-preparation.js','utf8');
code=code.replace(/\}\)\(\);\s*$/,'globalThis.apTest={render,drawProducts,drawGroups,updateControls,selectionChanged,inspection:()=>inspection,selection:()=>[...convertedSelection]};})();');
vm.runInContext(code,context);
const el=id=>elements.get(id),event={preventDefault(){}};
(async()=>{
 await context.window.openAlosPreparationJob('first');
 assert.equal(el('apProductsSection').hidden,false);
 assert.equal(el('apProducts').querySelectorAll('input').filter(x=>!x.disabled).length,2,'eligible products stay enabled after loading a saved job');
 await el('apSelectReady').onclick();assert.deepEqual(Array.from(context.apTest.selection()),['c','d']);
 assert.equal(el('apRun').disabled,true,'explicit confirmation is still required');
 el('apTool').value='/tool';el('apOutputParent').value='/out';el('apOutputName').value='new';el('apRunConsent').checked=true;el('apRunConsent').onchange();
 await el('apRunForm').onsubmit(event);const run=requests.find(r=>r.path==='/api/alos-preparation-run');assert.deepEqual(Array.from(run.data.product_ids),['c','d']);assert.equal(run.data.root,'/out/new');assert.equal(run.data.reviewed,true);
 serverJob.status='partial';serverJob.progress.items=[{id:'c',status:'complete',output:'/out/c',quality:{status:'passed',format:{status:'passed'},samples:{status:'passed',all_samples_equal:true,total_complex_samples:4096},reader:{status:'passed'},scientific_validation:{limitations:['不代表干涉品質']}}},{id:'d',status:'failed',error:'讀取失敗'}];
 context.apTest.render(clone(serverJob));await el('apSelectUnfinished').onclick();assert.deepEqual(Array.from(context.apTest.selection()),['d'],'completed products cannot be queued again');
 assert.match(el('apProducts').textContent,/全部樣本一致/);assert.match(el('apProducts').textContent,/讀取失敗/);
 el('apLeftDate').value='20200101';el('apLeftDate').onchange();el('apRightDate').value='20250101';el('apRightDate').onchange();
 const pending=el('apInspect').onclick();assert.ok(coverageResolver);coverageResolver({revision:'r1',left:{selected_ids:['a','b']},right:{selected_ids:['c']},intersection:{status:'unknown',area_km2:null,fraction_left:null,fraction_right:null},checks:[{label:'升降軌',status:'unknown',values:{a:'ascending',b:null,c:'ascending'}}],warnings:['影格 b 外框未知'],pairwise:[]});await pending;
 assert.match(el('apCoverageReport').textContent,/完整覆蓋尚無法確認/);assert.match(el('apCoverageReport').textContent,/未知/);assert.equal(el('apReviewOutcome').options.find(o=>o.value==='confirmed').disabled,true);
 el('apReviewOutcome').value='pending';el('apReviewConsent').checked=true;await el('apReviewForm').onsubmit(event);const review=requests.find(r=>r.path==='/api/alos-preparation-review');assert.deepEqual(Array.from(review.data.left_ids),['a','b']);assert.equal(review.data.outcome,'pending');assert.equal(review.data.revision,'r1');
 const stale=el('apInspect').onclick();const resolveOld=coverageResolver;serverJob=fixture('second');await context.window.openAlosPreparationJob('second');resolveOld({revision:'stale',intersection:{status:'overlap'},left:{},right:{}});await stale;
 assert.equal(context.apTest.inspection(),null,'a late coverage response must not change another job');
 assert.equal(requests.some(r=>/action|nisar-run/.test(r.path)),false,'preparation confirmation never starts science processing');
 context.window.closeAlosPreparation();assert.ok(maps.every(map=>map.removed),'navigation disposes old geographic views');
 console.log('ALOS preparation UI: saved-job state, selections, retry exclusions, quality, unknown coverage and stale response guards passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
