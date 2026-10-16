// Execute the shipped UI with a minimal DOM: no real processing is started.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map(),requests=[];
class Element{
 constructor(tag='div',text=''){this.tagName=tag;this.children=[];this.textContent=text||'';this.value='';this.checked=false;this.disabled=false;this.hidden=false;this.attributes={};}
 set id(v){this._id=v;elements.set(v,this);}get id(){return this._id;}
 append(...xs){this.children.push(...xs);}replaceChildren(...xs){this.children=xs;}insertBefore(e){this.append(e);}after(e){this.append(e);}setAttribute(k,v){this.attributes[k]=v;}
 querySelector(){return this;}querySelectorAll(selector){return this.children.flatMap(x=>[...(x.tagName==='input'&&(!selector.includes('[data-frame]')||x.attributes['data-frame'])?[x]:[]),...x.querySelectorAll(selector)]);}
 set innerHTML(html){for(const m of html.matchAll(/<([a-z0-9]+)[^>]*id="([^"]+)"[^>]*>/g)){const e=new Element(m[1]);e.id=m[2];e.disabled=/\bdisabled\b/.test(m[0]);this.append(e);}}
}
const el=id=>elements.get(id),node=(tag,text)=>new Element(tag,text);
for(const id of ['project','stackParameters','properties']){const e=node('div');e.id=id;}
const context=vm.createContext({node,$:el,properties:{},window:{workbenchState:{busy:false}},document:{createTextNode:t=>node('text',t)},action:(e,f)=>e.onclick=f,api:async(path,data)=>requests.push({path,data}),refresh:async()=>{}});
vm.runInContext(fs.readFileSync(__dirname+'/../src/alosstack_helper/static/sampling.js','utf8'),context);
const hooks=context.window;
hooks.samplingScanned([{date:'240822',frames:['0480','0490']},{date:'250425',frames:['0480']}]);
let inputs=el('samplingFrameChoices').querySelectorAll('input[data-frame]');assert.equal(inputs[1].disabled,true);inputs[0].checked=true;inputs[0].onchange();assert.equal(context.properties.frames,'["0480"]');
const report={plan_id:'v1',reference:'250425',rows:[],output_bytes:0,virtual:0,changed:0};
let state={id:'one',status:'sampling_review',config:{workflow:'alosStack'},sampling:report};
hooks.samplingUpdate(state);assert.equal(el('samplingContinue').disabled,true);
el('samplingConsent').checked=true;el('samplingConsent').onchange();assert.equal(el('samplingContinue').disabled,true);
el('samplingNotes').value='已核對影像外框與軌道';el('samplingNotes').oninput();assert.equal(el('samplingContinue').disabled,false);
hooks.samplingUpdate(state);assert.equal(el('samplingConsent').checked,true,'polling preserves the current draft');
context.window.workbenchState.busy=true;hooks.samplingUpdate(state);assert.equal(el('samplingContinue').disabled,true);context.window.workbenchState.busy=false;
(async()=>{
 hooks.samplingUpdate(state);await el('samplingContinue').onclick();assert.equal(requests.length,1);assert.equal(requests[0].data.action,'sampling_continue');assert.equal(requests[0].data.plan_id,'v1');assert.equal(requests[0].data.frames_verified,true);
 state={...state,sampling:{...report,plan_id:'v2'}};hooks.samplingUpdate(state);assert.equal(el('samplingConsent').checked,false);assert.equal(el('samplingNotes').value,'');assert.equal(el('samplingContinue').disabled,true);
 state={...state,status:'running'};hooks.samplingUpdate(state);assert.equal(el('samplingApproval').hidden,true);
 hooks.samplingUpdate({...state,config:{workflow:'topsApp'}});assert.equal(el('samplingReview').hidden,true);
 console.log('Sampling UI: common frames, explicit consent, polling, busy state and stale plan checks passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
