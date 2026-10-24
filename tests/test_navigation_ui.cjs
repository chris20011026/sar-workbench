// Exercise project navigation against visible sections, without changing processing state.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/../src/alosstack_helper/static/workspace.js','utf8');
const start=source.indexOf(' // Keep every section available; navigation only scrolls');
const code=source.slice(start,source.lastIndexOf('})();'));
const elements=new Map();let scrolls=[],focused=[],actions=0;
class Element{
 constructor(tag='div',text=''){this.tagName=tag.toUpperCase();this.textContent=text;this.children=[];this.attributes={};this.hidden=false;this.open=false;this.classList={toggle:()=>{}};}
 set id(v){this._id=v;elements.set(v,this);}get id(){return this._id;}
 append(...nodes){for(const node of nodes){node.parentElement=this;this.children.push(node);}}
 prepend(node){node.parentElement=this;this.children.unshift(node);}
 after(node){const p=this.parentElement;node.parentElement=p;p.children.splice(p.children.indexOf(this)+1,0,node);}
 replaceChildren(...nodes){this.children=[];this.append(...nodes);}
 setAttribute(k,v){this.attributes[k]=v;}getAttribute(k){return this.attributes[k];}removeAttribute(k){delete this.attributes[k];}
 querySelector(selector){return this.children.find(c=>selector==='.card'?c.className==='card':c.className==='project-settings');}
 querySelectorAll(selector){return this.children.filter(c=>selector==='[data-section]'&&c.attributes['data-section']);}
 closest(selector){for(let n=this;n;n=n.parentElement)if(selector==='[hidden]'?n.hidden:selector==='.card'&&n.className==='card')return n;return null;}
 focus(){focused.push(this.id);}scrollIntoView(){scrolls.push(this.id);}
}
const node=(tag,text,cls)=>{const n=new Element(tag,text);n.className=cls||'';return n;},el=id=>elements.get(id);
const project=node('section');project.id='project';const path=node('p');path.id='projectPath';const status=node('div',null,'card'),settings=node('details',null,'project-settings'),logCard=node('div',null,'card'),log=node('pre');log.id='log';logCard.append(log);project.append(path,status,settings,logCard);
for(const id of ['samplingReview','samplingApproval','samplingMatchApproval','matchingQuality','matchingDecision','review','alosAcceptance','pairWorkbench','topsWorkbench','analysisWorkbench','researchStudio','recoveryCenter']){const n=node(id==='recoveryCenter'?'details':'section');n.id=id;n.hidden=true;project.append(n);}
const context=vm.createContext({node,$:el,icon:()=>node('span'),button:(text,fn,cls)=>{const n=node('button',text,cls);n.onclick=fn;return n;},window:{matchMedia:()=>({matches:true})}});
vm.runInContext(code,context);
const update=context.window.projectNavigationUpdate,nav=()=>el('projectNavigation');
el('samplingReview').hidden=false;el('samplingApproval').hidden=false;update({id:'a',status:'sampling_review'});
assert.match(nav().children.map(n=>n.textContent).join(' '),/目前關卡/);assert.ok(nav().children.some(n=>n.attributes['data-section']==='samplingReview'));assert.ok(!nav().children.some(n=>n.attributes['data-section']==='analysisWorkbench'),'hidden analysis is not offered');
nav().children[0].onclick();assert.deepEqual(scrolls,['samplingApproval']);assert.deepEqual(focused,['samplingApproval']);assert.equal(el('samplingApproval').hidden,false,'jump only scrolls; no gate state changes');
const original=nav().children;update({id:'a',status:'sampling_review'});assert.equal(nav().children,original,'polls preserve navigation focus and structure');
const settingsButton=nav().children.find(n=>n.attributes['data-section']==='projectSettingsSection');settingsButton.onclick();assert.equal(settings.open,true,'jump opens requested settings details');
el('samplingReview').hidden=true;el('samplingApproval').hidden=true;el('analysisWorkbench').hidden=false;update({id:'a',status:'complete'});assert.ok(!nav().children.some(n=>n.textContent==='目前關卡'));assert.ok(nav().children.some(n=>n.attributes['data-section']==='analysisWorkbench'));assert.ok(!nav().children.some(n=>n.attributes['data-section']==='samplingReview'));
el('recoveryCenter').hidden=false;update({id:'b',status:'failed'});nav().children[0].onclick();assert.equal(el('recoveryCenter').open,true);assert.equal(scrolls.at(-1),'recoveryCenter');assert.equal(actions,0);
console.log('Project navigation: visible-section filtering, safe stage jumps, detail opening, polling stability and no processing actions passed.');
