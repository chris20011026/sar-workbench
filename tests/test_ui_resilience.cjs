// Execute the production refresh functions with failed/local API responses.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/../src/alosstack_helper/static/app.js','utf8');
const code=source.slice(source.indexOf('async function refresh(){'),source.indexOf('async function renderReview(){'));
const elements=new Map();let updates=0;
const element=()=>({hidden:false,textContent:'',scrollHeight:0,scrollTop:0,clientHeight:0,classList:{add(){},remove(){}},append(){},replaceChildren(){}});
const project={id:'one',config:{name:'Example',root:'/example'},status:'complete',history:[]};
const context=vm.createContext({TypeError,current:'one',projects:[],defaults:{},statusNames:{complete:'complete'},reviewLoaded:null,
 $:id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);},node:element,summary(){},
 window:{workbenchUpdate:async()=>{updates++;}},
 api:async path=>{if(path==='/api/state')return{projects:[project],defaults:{}};throw new TypeError('Failed to fetch');}});
vm.runInContext(code,context);
(async()=>{
 await vm.runInContext('refresh()',context);
 assert.equal(updates,1,'a failed log request must not prevent the analysis workbench from loading');
 assert.equal(elements.get('logStatus').hidden,false);
 assert.match(elements.get('logStatus').textContent,/其他設定與成果仍可使用/);
 assert.equal(elements.get('log').textContent,'日誌尚未載入。');
 context.api=async()=>({text:'Completed processing'});
 await vm.runInContext('refreshLog()',context);
 assert.equal(elements.get('logStatus').hidden,true);
 assert.equal(elements.get('log').textContent,'Completed processing');
 let first,second;
 context.api=path=>new Promise(resolve=>{if(path.endsWith('one'))first=resolve;else second=resolve;});
 const old=vm.runInContext('refreshLog()',context);context.current='two';const newer=vm.runInContext('refreshLog()',context);
 second({text:'Project two'});await newer;first({text:'Project one'});await old;
 assert.equal(elements.get('log').textContent,'Project two','late responses cannot replace the current project log');
 console.log('UI resilience: failed log isolation, recovery, and stale response checks passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
