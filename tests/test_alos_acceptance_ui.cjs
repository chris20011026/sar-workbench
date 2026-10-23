const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map(),requests=[];
class Element{
 constructor(tag='div',text=''){this.tagName=tag;this.children=[];this.textContent=text||'';this.value='';this.checked=false;this.disabled=false;this.hidden=false;this.style={};this.attributes={};}
 set id(v){this._id=v;elements.set(v,this);}get id(){return this._id;}
 append(...xs){this.children.push(...xs);if(this.tagName==='select'&&!this.value)this.value=xs[0]?.value||'';}replaceChildren(...xs){this.children=[];if(this.tagName==='select')this.value='';this.append(...xs);}after(e){this.append(e);}setAttribute(k,v){this.attributes[k]=v;}querySelector(){return this;}
 getContext(){return {createImageData:(w,h)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData(){}};}
 set innerHTML(html){for(const m of html.matchAll(/<([a-z0-9]+)[^>]*id="([^"]+)"[^>]*>/g)){const e=new Element(m[1]);e.id=m[2];e.disabled=/\bdisabled\b/.test(m[0]);this.append(e);}}
}
const el=id=>elements.get(id),node=(tag,text,cls)=>{const e=new Element(tag,text);e.className=cls;return e;};const project=node('div');project.id='project';
let state,hook;
const context=vm.createContext({node,$:el,window:{workbenchState:{busy:false}},action:(e,f)=>e.onclick=f,api:async(path,data)=>{requests.push({path,data});return {};},refresh:async()=>hook(state)});
vm.runInContext(fs.readFileSync(__dirname+'/../src/alosstack_helper/static/alos-acceptance.js','utf8'),context);hook=context.window.alosAcceptanceUpdate;
const image={width:2,height:2,values:[0,1,2,3],min:0,max:3},product={preview:image,width:2,height:2,samples:4,finite_fraction:1};
const report={report_id:'stable-report',kind:'ion',can_continue:true,summary:{expected_pairs:1,readable_pairs:1},errors:[],warnings:[],rows:[{pair:'240101-240201',products:{before:product,after:product},correction:image,errors:[]}]};
state={id:'one',config:{workflow:'alosStack'},status:'ion_correction_review',ion_correction_review:report};
function consent(){el('alosAcceptanceReviewed').checked=true;el('alosAcceptanceNotes').value='已確認全部成果';el('alosAcceptanceNotes').oninput();}
(async()=>{
 hook(state);assert.equal(el('alosAcceptanceContinue').disabled,true);assert.equal(requests.length,0,'rendering never starts processing');
 consent();assert.equal(el('alosAcceptanceContinue').disabled,false);hook(state);assert.equal(el('alosAcceptanceReviewed').checked,true,'polling preserves draft');
 await el('alosAcceptanceRefresh').onclick();assert.equal(requests.at(-1).path,'/api/alos-review');assert.equal(el('alosAcceptanceReviewed').checked,false,'explicit refresh resets consent even with identical report hash');assert.equal(el('alosAcceptanceNotes').value,'');assert.equal(el('alosAcceptanceContinue').disabled,true);
 consent();await el('alosAcceptanceContinue').onclick();assert.equal(requests.at(-1).data.action,'ion_correction_continue');assert.equal(requests.at(-1).data.report_id,'stable-report');
 state={...state,status:'running',ion_correction_review_approval:{time:'now',notes:'已確認'}};hook(state);assert.match(el('alosAcceptanceSummary').textContent,/目前正在進行後續處理/);assert.doesNotMatch(el('alosAcceptanceSummary').textContent,/停在解纏之前/);assert.equal(el('alosAcceptanceDecision').hidden,true);assert.equal(el('alosAcceptanceContinue').disabled,true);
 state={...state,status:'failed'};hook(state);assert.match(el('alosAcceptanceSummary').textContent,/後續處理已停止/);assert.doesNotMatch(el('alosAcceptanceTitle').textContent,/先比較再繼續/);
 state={...state,status:'output_review',output_acceptance:{...report,kind:'outputs',report_id:'output-report'}};hook(state);assert.equal(el('alosAcceptanceReviewed').checked,false,'new gate requires separate consent');consent();await el('alosAcceptanceContinue').onclick();assert.equal(requests.at(-1).data.action,'output_accept');
 state={...state,status:'complete',output_acceptance_approval:{time:'now',notes:'已驗收'}};hook(state);assert.match(el('alosAcceptanceSummary').textContent,/成果已人工驗收，可以進行/);assert.equal(el('alosAcceptanceDecision').hidden,true);assert.equal(el('alosAcceptanceContinue').disabled,true);
 state={...state,status:'output_review',output_acceptance_approval:undefined};
 state={...state,output_acceptance:{...state.output_acceptance,report_id:'incomplete',can_continue:false}};hook(state);consent();assert.equal(el('alosAcceptanceContinue').disabled,true,'incomplete products cannot be accepted');
 context.window.workbenchState.busy=true;hook(state);assert.equal(el('alosAcceptanceRefresh').disabled,true);assert.equal(el('alosAcceptanceContinue').disabled,true);
 console.log('ALOS acceptance UI: independent gates, explicit refresh resets, exact report and incomplete-product guards passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
