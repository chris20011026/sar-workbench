// Execute the shipped checkpoint UI. Requests are fakes; no processing is started.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map(),requests=[],fetches=[],revoked=[],createdMaps=[],tileRequests=[];
class Element{
 constructor(tag='div',text=''){this.tagName=tag;this.children=[];this.textContent=text||'';this.value='';this.checked=false;this.disabled=false;this.hidden=false;this.attributes={};this.style={setProperty:(k,v)=>this.attributes[k]=v};}
 set id(v){this._id=v;elements.set(v,this);}get id(){return this._id;}
 append(...xs){this.children.push(...xs);}replaceChildren(...xs){this.children=xs;if(this.tagName==='select')this.value='';}insertBefore(e){this.append(e);}after(e){this.append(e);}setAttribute(k,v){this.attributes[k]=v;}removeAttribute(k){delete this.attributes[k];if(k==='src')this.src='';}
 querySelector(){return this;}querySelectorAll(selector){return this.children.flatMap(x=>[...(x.tagName==='input'&&(!selector.includes('[data-frame]')||x.attributes['data-frame'])?[x]:[]),...x.querySelectorAll(selector)]);}
 set innerHTML(html){for(const m of html.matchAll(/<([a-z0-9]+)[^>]*id="([^"]+)"[^>]*>/g)){const e=new Element(m[1]);e.id=m[2];e.disabled=/\bdisabled\b/.test(m[0]);e.checked=/\bchecked\b/.test(m[0]);e.hidden=/\bhidden\b/.test(m[0]);this.append(e);}}
}
const el=id=>elements.get(id),node=(tag,text,cls)=>{const result=new Element(tag,text);result.className=cls;return result;};
for(const id of ['project','stackParameters','properties']){const e=node('div');e.id=id;}
const geometry={type:'Polygon',coordinates:[[[120,24],[121,24],[121,25],[120,25],[120,24]]]};
const product=(id,date,frame,selected,sensor='ALOS-2')=>({id,date,frame,selected,sensor,frame_provenance:id==='ref'?'synthetic_compatibility_id':id==='sec'?'filename':{unexpected:'raw-machine-details'},footprint:geometry,images:[{id:id+'-source',kind:'source',polarization:'HH',width:1000,length:500}],warnings:[]});
const baselineReview={plan_id:'v1',reference:'250425',products:[product('ref','250425','0480',true,'ALOS-4'),product('sec','240822','0480',true),product('extra','240822','0490',false)],coverage:{complete:true,common:geometry},warnings:[],source_fingerprint:'source1'};
let responseReview=baselineReview,reviewDeferred=null,previewDeferred=null,reviewFailure=null,previewFailure=null,blobCounter=0;
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const bounds={isValid:()=>true};
function layer(){return {addTo(map){map.layers.add(this);return this;},bindPopup(popup){this.popup=popup;return this;},on(){return this;}};}
const L={
 map(){const map={layers:new Set(),fitCount:0,removed:false,remove(){this.removed=true;},removeLayer(l){this.layers.delete(l);},fitBounds(){this.fitCount++;return this;},setView(){return this;},invalidateSize(){},on(){}};createdMaps.push(map);return map;},
 geoJSON:()=>layer(),featureGroup:()=>({getBounds:()=>bounds}),control:()=>({addTo(){}}),
 tileLayer(url){tileRequests.push(url);return layer();}
};L.control.scale=()=>({addTo(){}});
const context=vm.createContext({node,$:el,properties:{},window:{workbenchState:{busy:false}},document:{createTextNode:t=>node('text',t)},L,setTimeout:f=>f(),token:'test-session',URL:{createObjectURL:()=>`blob:test-${++blobCounter}`,revokeObjectURL:url=>revoked.push(url)},
 action:(e,f)=>e.onclick=f,choosePath:async(mode,callback)=>callback('/extra/read-only'),
 api:async(path,data)=>{requests.push({path,data});if(path==='/api/sampling-review'){if(reviewFailure)throw reviewFailure;return reviewDeferred?await reviewDeferred.promise:responseReview;}if(path==='/api/sampling-preview'){if(previewFailure)throw previewFailure;return previewDeferred?await previewDeferred.promise:{image_id:data.image_id,url:'/api/sampling-preview-image?id=one&image='+data.image_id,width:100,height:50,source_width:1000,source_height:500,valid_fraction:.95,scale:'log-amplitude'};}return {};},
 fetch:async(url,options)=>{fetches.push({url,options});return {ok:true,blob:async()=>({type:'image/png'})};},refresh:async()=>{}
});
vm.runInContext(fs.readFileSync(__dirname+'/../src/alosstack_helper/static/sampling.js','utf8'),context);
const hooks=context.window,flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
const textTree=e=>[e.textContent,...e.children.map(textTree)].join(' ');
const processing=()=>requests.filter(r=>r.path==='/api/action');
const report={plan_id:'v1',reference:'250425',rows:[],output_bytes:0,virtual:0,changed:0};
let state={id:'one',status:'sampling_review',config:{workflow:'alosStack',source:'/source'},sampling:report};
(async()=>{
 hooks.samplingScanned([{date:'240822',frames:['0480','0490']},{date:'250425',frames:['0480']}]);
 let inputs=el('samplingFrameChoices').querySelectorAll('input[data-frame]');assert.equal(inputs[1].disabled,true);inputs[0].checked=true;inputs[0].onchange();assert.equal(context.properties.frames,'["0480"]');
 hooks.samplingUpdate(state);await flush();assert.equal(el('samplingContinue').disabled,true);assert.equal(createdMaps.length,1);assert.equal(tileRequests.length,0,'network basemap is opt-in');assert.equal(el('samplingPreviewLeftSelect').value,'ref-source');assert.equal(el('samplingPreviewRightSelect').value,'sec-source');assert.equal(processing().length,0);
 assert.match(textTree(el('samplingProductList')),/影格編號由格式轉換建立/);assert.match(textTree(el('samplingProductList')),/影格編號取自檔名/);assert.match(textTree(el('samplingProductList')),/影格編號來源尚未確認/);assert.doesNotMatch(textTree(el('samplingProductList')),/synthetic_compatibility_id|raw-machine-details/);
 const map=createdMaps[0];assert.equal(map.layers.size,3);el('samplingShowCandidates').checked=false;el('samplingShowCandidates').onchange();assert.equal(map.layers.size,2,'candidate visibility does not alter processing');assert.equal(context.properties.frames,'["0480"]');
 el('samplingShowCommon').checked=true;el('samplingShowCommon').onchange();assert.equal(map.layers.size,3);el('samplingCompareDate').value='240822';el('samplingCompareDate').onchange();assert.equal(map.layers.size,3,'comparison keeps reference and selected date plus fixed common outline');
 el('samplingBasemap').value='osm';el('samplingBasemap').onchange();assert.equal(tileRequests.length,1);
 el('samplingConsent').checked=true;el('samplingConsent').onchange();assert.equal(el('samplingContinue').disabled,true);el('samplingNotes').value='已核對影像外框與軌道';el('samplingNotes').oninput();assert.equal(el('samplingContinue').disabled,false);
 hooks.samplingUpdate(state);await flush();assert.equal(el('samplingConsent').checked,true,'polling preserves draft');assert.equal(createdMaps.length,1,'polling preserves map and zoom');
 context.window.workbenchState.busy=true;hooks.samplingUpdate(state);assert.equal(el('samplingContinue').disabled,true);assert.equal(el('samplingPreviewLeftLoad').disabled,true);context.window.workbenchState.busy=false;hooks.samplingUpdate(state);
 // A preview is read-only, header-authenticated and serialized across both panes.
 previewDeferred=deferred();const leftRequest=el('samplingPreviewLeftLoad').onclick();assert.equal(el('samplingPreviewRightLoad').disabled,true);assert.equal(el('samplingContinue').disabled,true);await el('samplingPreviewRightLoad').onclick();assert.equal(requests.filter(r=>r.path==='/api/sampling-preview').length,1);
 previewDeferred.resolve({image_id:'ref-source',url:'/api/sampling-preview-image?id=one&image=ref-source',width:100,height:50,source_width:1000,source_height:500,valid_fraction:.95,scale:'log-amplitude'});await leftRequest;previewDeferred=null;assert.equal(fetches.length,1);assert.equal(fetches[0].options.headers['X-Token'],'test-session');assert.ok(!fetches[0].url.includes('test-session'));assert.equal(el('samplingPreviewLeftImage').hidden,false);assert.equal(el('samplingPreviewLeftImage').src,'blob:test-1');assert.equal(processing().length,0);
 el('samplingPreviewLeftSelect').value='sec-source';el('samplingPreviewLeftSelect').onchange();assert.deepEqual(revoked,['blob:test-1']);assert.equal(el('samplingPreviewLeftImage').hidden,true);
 previewFailure=new Error('影像不可讀');await el('samplingPreviewRightLoad').onclick();previewFailure=null;assert.match(el('samplingPreviewRightStatus').textContent,/影像不可讀/);assert.equal(el('samplingConsent').checked,true,'preview failure does not manufacture or erase manual frame consent');assert.equal(processing().length,0);
 await el('samplingContinue').onclick();assert.equal(processing().length,1);assert.equal(processing()[0].data.action,'sampling_continue');assert.equal(processing()[0].data.plan_id,'v1');assert.equal(processing()[0].data.frames_verified,true);
 // New stage requires a separate matching consent and prefers equalized preview outputs.
 responseReview={...baselineReview,products:baselineReview.products.map(p=>({...p,images:[...p.images,{...p.images[0],id:p.id+'-equalized',kind:'equalized'}]}))};
 state={...state,status:'sampling_ready',sampling_approval:{time:'now',notes:'影格已核對'}};hooks.samplingUpdate(state);await flush();assert.equal(el('samplingApproval').hidden,true);assert.equal(el('samplingMatchApproval').hidden,false);assert.equal(el('samplingMatch').disabled,true);assert.equal(el('samplingPreviewLeftSelect').value,'ref-equalized');assert.equal(el('samplingPreviewRightSelect').value,'sec-equalized');assert.equal(processing().length,1,'finishing sampling never starts matching');
 el('samplingMatchConsent').checked=true;el('samplingMatchConsent').onchange();assert.equal(el('samplingMatch').disabled,false);hooks.samplingUpdate(state);assert.equal(el('samplingMatchConsent').checked,true);await el('samplingMatch').onclick();assert.equal(processing().length,2);assert.equal(processing()[1].data.action,'sampling_match');assert.equal(processing()[1].data.sampling_reviewed,true);
 // Project and plan switches must drop pending image responses and old approval drafts.
 previewDeferred=deferred();const stalePreview=el('samplingPreviewLeftLoad').onclick();state={...state,id:'two',status:'sampling_review',sampling:{...report,plan_id:'v2'}};responseReview={...baselineReview,plan_id:'v2'};hooks.samplingUpdate(state);assert.equal(el('samplingConsent').checked,false);assert.equal(el('samplingNotes').value,'');assert.equal(el('samplingMatchConsent').checked,false);
 previewDeferred.resolve({image_id:'ref-equalized',url:'/api/sampling-preview-image?id=one&image=ref-equalized'});await stalePreview;previewDeferred=null;assert.equal(fetches.length,1,'stale preview response is discarded before image fetch');assert.equal(el('samplingPreviewLeftImage').hidden,true);hooks.samplingUpdate(state);await flush();
 // A failed read does not retry on every poll, and incomplete coverage is never advertised as complete.
 reviewFailure=new Error('磁碟離線');state={...state,sampling:{...report,plan_id:'v3'}};hooks.samplingUpdate(state);await flush();const failedCount=requests.filter(r=>r.path==='/api/sampling-review').length;hooks.samplingUpdate(state);await flush();assert.equal(requests.filter(r=>r.path==='/api/sampling-review').length,failedCount);assert.match(el('samplingReviewStatus').textContent,/磁碟離線/);
 reviewFailure=null;responseReview={...baselineReview,plan_id:'v3',coverage:{complete:false,common:geometry}};await el('samplingLoadReview').onclick();assert.equal(el('samplingShowCommon').disabled,true);assert.match(el('samplingCoverageNote').textContent,/尚無完整共同覆蓋結論/);
 await el('samplingPickExtra').onclick();assert.equal(el('samplingExtraSource').value,'/extra/read-only');await el('samplingLoadReview').onclick();assert.equal(requests.at(-1).data.extra_source,'/extra/read-only');el('samplingExtraSource').value='';el('samplingExtraSource').oninput();await el('samplingLoadReview').onclick();assert.equal(requests.at(-1).data.extra_source,'','clearing the field explicitly removes a previously remembered candidate source');assert.equal(processing().length,2);
 // API-supplied external image URLs cannot receive the session token.
 previewDeferred=deferred();const malicious=el('samplingPreviewLeftLoad').onclick();previewDeferred.resolve({image_id:'ref-source',url:'https://unexpected.invalid/image.png'});await malicious;previewDeferred=null;assert.equal(fetches.length,1);assert.match(el('samplingPreviewLeftStatus').textContent,/網址格式不符/);
 // Pending metadata from another project cannot replace the current map.
 reviewDeferred=deferred();const oldReview=el('samplingLoadReview').onclick();hooks.samplingUpdate({...state,config:{workflow:'topsApp'}});assert.equal(el('samplingReview').hidden,true);const mapCount=createdMaps.length;reviewDeferred.resolve(responseReview);await oldReview;reviewDeferred=null;assert.equal(createdMaps.length,mapCount);
 console.log('Sampling UI: independent gates, map visibility, opt-in basemap, protected previews, serialization, stale responses and offline recovery passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
