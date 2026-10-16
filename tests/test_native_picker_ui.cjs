// Test choosePath in isolation: no real macOS UI is started.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
class Element{
 constructor(){this.disabled=false;this.hidden=false;this.children=[];this.textContent='';this.open=false;}
 set id(v){this._id=v;elements.set(v,this);}get id(){return this._id;}
 append(e){this.children.push(e);}prepend(e){this.append(e);}before(e){this.append(e);}remove(){}setAttribute(){}closest(){return holder;}showModal(){this.open=true;}close(){this.open=false;}
}
const holder=new Element(),active=new Element(),disabled=new Element();disabled.disabled=true;
for(const id of ['pickerStatus','picker','chooseDir','files']){const e=new Element();e.id=id;}
let response={supported:true,cancelled:true},selected=0,requests=0,browseCalls=[],failure=null,pending;
const context=vm.createContext({node:(tag,text)=>{const e=new Element();e.textContent=text;return e;},$:id=>elements.get(id),defaults:{home:'/home'},localStorage:{getItem:()=>null},document:{activeElement:active,querySelector:()=>holder,querySelectorAll:()=>[active,disabled]},
 api:async()=>{requests++;if(pending)return new Promise(resolve=>pending.resolve=resolve);if(response instanceof Error)throw response;return response;},
 browse:async path=>{browseCalls.push(path);if(path==='/missing')throw Error('gone');},fail:e=>{failure=e.message;},Error});
const source=fs.readFileSync(__dirname+'/../src/alosstack_helper/static/app.js','utf8');
vm.runInContext(source.slice(source.indexOf('let nativePending='),source.indexOf("$('pickerPreference').value=")),context);
(async()=>{
 await context.choosePath('dir',()=>selected++,'/home');assert.equal(selected,0);assert.equal(elements.get('picker').open,false);assert.equal(disabled.disabled,true);
 pending={};const wait=context.choosePath('dir',()=>selected++,'/home');await context.choosePath('file',()=>selected++,'/home');assert.match(failure,/等候另一個/);assert.equal(requests,2);pending.resolve({supported:true,cancelled:true});await wait;pending=null;
 response=Error('Native GUI service unavailable');await context.choosePath('dir',()=>selected++,'/missing');assert.equal(elements.get('picker').open,true);assert.deepEqual(browseCalls,['/missing','/home']);assert.match(elements.get('pickerFallbackNotice').textContent,/Native GUI service unavailable/);
 elements.get('picker').close();browseCalls=[];response={supported:true,cancelled:false,path:'/chosen'};await assert.rejects(()=>context.choosePath('dir',()=>{throw Error('invalid selected dataset');},'/home'),/invalid selected dataset/);assert.equal(elements.get('picker').open,false,'a dataset validation failure is not mistaken for a native dialog failure');assert.equal(browseCalls.length,0);
 console.log('Native picker UI: cancellation, visible pending, preserved disabled state, fallback and callback errors passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
