// Exercise production comparison rendering, map picking and async view changes.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const elements = new Map(), renderedMaps = [], plotCalls = [], revoked = new Set();
let urlNumber = 0, requests = [], pendingImage = null, delayedPoint = null;
class Element {
 constructor(tag='div',text='',cls='') {this.tagName=tag;this.children=[];this.parentElement=null;this._text=text||'';this._classes=new Set(cls.split(' ').filter(Boolean));this.isConnected=true;this.value='';this.style={};this.options=[];this.classList={add:x=>this._classes.add(x),remove:x=>this._classes.delete(x),contains:x=>this._classes.has(x),toggle:(x,b)=>b===false?this._classes.delete(x):this._classes.add(x)};}
 set id(value){this._id=value;elements.set(value,this);}get id(){return this._id;}
 set className(v){this._classes=new Set(v.split(' '));}get className(){return [...this._classes].join(' ');}
 set textContent(v){this._text=String(v);this.children=[];}get textContent(){return this._text+this.children.map(x=>x.textContent||'').join(' ');}
 set innerHTML(v){for(const m of v.matchAll(/id="([^"]+)"/g)){const e=new Element();e.id=m[1];this.append(e);}}
 append(...values){for(const value of values){const e=typeof value==='string'?new Element('text',value):value;e.parentElement=this;e.isConnected=this.isConnected;this.children.push(e);if(e.tagName==='option')this.options.push(e);}}
 replaceChildren(...values){const detach=e=>{e.isConnected=false;for(const c of e.children)detach(c);};this.children.forEach(detach);this.children=[];this.options=[];this._text='';this.append(...values);}
 setAttribute(k,v){this[k]=v;}after(e){e.isConnected=true;}get firstChild(){return this.children[0];}
 getBoundingClientRect(){return{top:0,left:0,width:900,height:800};}focus(){}
}
const node=(tag,text,cls)=>new Element(tag,text,cls);
new Element().id='analysisWorkbench';
const position=(lat,lng)=>({lat,lng,equals(other,tolerance=1e-9){return Math.abs(lat-other.lat)<tolerance&&Math.abs(lng-other.lng)<tolerance;}});
const bounds = input => {
 const value={south:Infinity,west:Infinity,north:-Infinity,east:-Infinity,extend(other){if(Array.isArray(other)){this.extend({south:other[0][0],west:other[0][1],north:other[1][0],east:other[1][1]});}else{this.south=Math.min(this.south,other.south);this.west=Math.min(this.west,other.west);this.north=Math.max(this.north,other.north);this.east=Math.max(this.east,other.east);}return this;}};
 return input?.length?value.extend(input):value;
};
function geoBase(el,initial) {
 const b=bounds(initial), map={options:{crs:{project:p=>({x:p.lng,y:p.lat})}},handlers:{},center:position((b.south+b.north)/2,(b.west+b.east)/2),zoom:4,
  on(events,fn){events.split(' ').forEach(e=>this.handlers[e]=fn);return this;},
  fitBounds(b){this.center=position((b.south+b.north)/2,(b.west+b.east)/2);return this;},
  setView(c,z){this.center=c;this.zoom=z;this.handlers.moveend?.();return this;},getCenter(){return this.center;},getZoom(){return this.zoom;},
  getContainer(){return el;},remove(){this.removed=true;this.handlers={};},removeLayer(){},invalidateSize(){}};
 const base=new Element('select');base.options=[new Element('option')];renderedMaps.push(map);return{map,base};
}
const L={latLngBounds:bounds,latLng:v=>position(v[0],v[1]),imageOverlay:()=>({addTo(){return this;}}),circleMarker:()=>({bindTooltip(){return this;},addTo(){return this;}})};
const image={file:'left.png',coordinate_system:'EPSG:3857',bounds:[[180,100],[200,130]],transform:[100,10,0,200,0,-10],width:3,height:2,native_shape:[8,9],native_index_file:'left-index.bin',min:-10,max:10,unit:'mm/year',preview_stride:1};
const stat={count:63,mean:2,std:1,rms:2.2,min:-1,max:4};
function fixture(id='first') {
 const run=(runId,name)=>({id:runId,name,unit:'mm/year',native_shape:[8,9],reference:{y:2,x:2,latitude:25,longitude:121,date:'20200101'},model:{dates:['20200101','20210101','20220101'],model:{polynomial:1},nonlinear:false},statistics:{valid_pixels:63,total_pixels:72,velocity:stat,temporal_coherence:stat,velocity_std:stat},selected_timeseries:'corrected.h5',direction:'正值朝向衛星',map:{...image,file:runId+'.png',native_index_file:runId+'-index.bin'}});
 return{id,project_id:'test',status:'complete',left:run('aaaaaaaaaaaa','分析 A'),right:run('bbbbbbbbbbbb','分析 B'),compatibility:{allow_difference:true,same_grid:true,reasons:[],checks:[{code:'grid',passed:true,reason:''},{code:'reference_date',passed:true,reason:''}]},colour_scale:{shared:true,min:-10,max:10,unit:'mm/year'},coverage:{shared:62,left_only:1,right_only:0,neither:9},difference:{available:true,statistics:stat,map:{...image,file:'difference.png',native_index_file:'difference-index.bin'}},options_diff:[{key:'temporal_coherence',left:.7,right:.8,left_present:true,right_present:true}]};
}
function pointReply(y=4,x=4,differentReference=false) {
 const curve=id=>({available:true,analysis_id:id,y,x,latitude:25,longitude:121,dates:['20200101','20210101','20220101'],displacement_mm:[0,1,2],unit:'mm',velocity_mm_year:1,reference:{y:2,x:2,date:'20200101'}});
 const left=curve('aaaaaaaaaaaa'),right=curve('bbbbbbbbbbbb');if(differentReference)right.reference.date='20210101';
 return{left,right,same_coordinate:true,y,x,side:'left',latitude:25,longitude:121,reasons:differentReference?['參考日期不同']:[],difference:null};
}
const context=vm.createContext({Node:Element,window:{},document:{getElementById:id=>elements.get(id)||null},
 $:id=>elements.get(id),node,action:(b,fn)=>b.onclick=fn,labeled:(label,control)=>{const e=node('label',label);e.append(control);return e;},
 geoBase,geoMaps:new Set(),L,token:'test',URLSearchParams,DataView,URL:{createObjectURL:()=> 'blob:test-'+(++urlNumber),revokeObjectURL:u=>revoked.add(u)},
 isoDate:s=>String(s).replace(/(\d{4})(\d{2})(\d{2})/,'$1-$2-$3'),refreshMapSizes(){},downloadBlob(){},refresh:async()=>{},fail:e=>{throw e;},setTimeout:()=>0,clearTimeout(){},
 Plotly:{purge:el=>el.classList.remove('js-plotly-plot'),newPlot:async(el,traces,layout)=>{el.classList.add('js-plotly-plot');plotCalls.push({el,traces,layout});}},
 fetch:async path=>{
  const query=new URLSearchParams(path.split('?')[1]);
  if(query.get('file').endsWith('.bin'))return{ok:true,arrayBuffer:async()=>Int32Array.from([0,10,20,30,40,50]).buffer};
  if(pendingImage&&query.get('comparison')===pendingImage.id)return{ok:true,blob:()=>new Promise(resolve=>pendingImage.resolve=resolve)};
  return{ok:true,blob:async()=>({})};
 },api:async(path,data)=>{requests.push({path,data});if(path==='/api/comparison-point'){if(delayedPoint)return new Promise(resolve=>delayedPoint.resolve=resolve);return pointReply(data.y,data.x);}throw Error('Unexpected API '+path);}
});
let source=fs.readFileSync(__dirname+'/../src/alosstack_helper/static/research.js','utf8');
source=source.replace(/\}\)\(\);\s*$/, 'globalThis.testResearch={render:c=>renderComparison(c,generation),selectPoint,cleanup,selected:()=>selectedPoint,setProject:()=>{key="test";generation++;}};})();');
vm.runInContext(source,context);const ui=context.testResearch;ui.setProject();
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const c=fixture();await ui.render(c);
 assert.match(elements.get('researchComparison').textContent,/原生經緯度格網/);
 assert.match(elements.get('researchComparison').textContent,/有效像素/);
 assert.match(elements.get('researchComparison').textContent,/時間相干性門檻/);
 assert.equal(renderedMaps.length,3,'both maps and the B-A map are rendered');
 renderedMaps[0].setView(position(190,115),7);
 assert.equal(renderedMaps[2].getZoom(),7,'difference map participates in synchronized zoom');
 renderedMaps[0].handlers.click({latlng:position(185,115)});await flush();await flush();
 const pointRequest=requests.find(r=>r.path==='/api/comparison-point');
 assert.equal(pointRequest.data.y,4);assert.equal(pointRequest.data.x,4,'GeoTransform/index mapping picks native row/column');
 assert.equal(plotCalls.at(-1).traces.length,2);
 assert.equal(elements.get('researchPointCsv').disabled,false);
 context.api=async()=>pointReply(4,4,true);await ui.selectPoint(4,4,'left',c);
 assert.match(plotCalls.at(-1).traces[0].name,/各自基準/);
 assert.match(elements.get('researchPointReferences').textContent,/不能直接當作位移差/);
 assert.match(plotCalls.at(-1).layout.yaxis.title,/各自基準/);
 pendingImage={id:'old'};const old=ui.render(fixture('old'));await flush();assert.ok(pendingImage.resolve);
 await ui.render(fixture('new'));const before=revoked.size;pendingImage.resolve({});await old;
 assert.ok(revoked.size>before,'a late stale blob is revoked');
 assert.equal(renderedMaps.filter(m=>!m.removed).length,3,'old image reply cannot add stale maps');
 delayedPoint={};context.api=async()=>new Promise(resolve=>delayedPoint.resolve=resolve);
 const stale=ui.selectPoint(4,4,'left',fixture('new'));await flush();await ui.render(fixture('newer'));
 delayedPoint.resolve(pointReply());await stale;
 assert.equal(ui.selected(),null,'late point response cannot replace a newly selected comparison');
 assert.equal(elements.get('researchPointCsv').disabled,true);
 ui.cleanup();assert.equal(renderedMaps.filter(m=>!m.removed).length,0);
 console.log('Comparison UI: readable statistics, geographic picking, synced difference map, reference labels and stale image/point guards passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
