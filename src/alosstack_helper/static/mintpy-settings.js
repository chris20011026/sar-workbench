/* MintPy settings have one owner per key. This module only submits explicit,
   validated advanced choices; empty choices retain the workbench baseline. */
(() => {
 'use strict';
 const panels={basic:['mintBasicTab','mintBasicPanel'],advanced:['mintAdvancedTab','mintAdvancedPanel'],all:['mintAllTab','mintAllPanel']};
 const state={schema:null,project:null,environment:'',overrides:{},tab:'basic',generation:0,loading:false,error:'',editors:new Map()};
 const statusLabels={basic:'既有欄位',editable:'進階可調整',managed:'工作台管理',unsupported:'尚未開放'};
 const translations={yes:'開啟',no:'關閉',none:'不使用',auto:'依 MintPy 預設',linear:'線性',quadratic:'二次',height_correlation:'高程相關法',gacos:'GACOS 延遲資料',pyaps:'PyAPS / ERA5 氣象模型',bridging:'連通區橋接',phase_closure:'相位閉合','bridging+phase_closure':'橋接後再檢查相位閉合',residue:'擬合殘差',residual:'擬合殘差',covariance:'位移共變異數',bootstrap:'Bootstrap 重抽樣',coh:'相干性',var:'相位變異數',fim:'Fisher 資訊',nearest:'最近鄰',median:'中位數',mean:'平均值',min:'最小值',lzf:'LZF 壓縮',gzip:'GZIP 壓縮'};
 const text=v=>typeof v==='boolean'?(v?'開啟（yes）':'關閉（no）'):v===null||v===undefined||v===''?'未指定':Array.isArray(v)?v.join(', '):String(v);
 const field=id=>$(id)?.value||'';
 const normalize=v=>typeof v==='boolean'?(v?'yes':'no'):String(v).trim();
 const isEditable=o=>o.editable===true&&o.status==='editable';
 const optionFor=key=>state.schema?.options.find(o=>o.key===key);
 const labelValue=v=>translations[String(v)]?`${translations[String(v)]}（${v}）`:text(v);
 function choices(o){return o.choices|| (Array.isArray(o.allowed_values)?o.allowed_values:[]);}
 function supportsOff(o){return o.allow_no&&!choices(o).includes('no');}
 function resolvedDefault(o){return o.default??o.auto_default??'由 MintPy 決定';}
 function baseValue(o){return o.baseline===null||o.baseline===undefined?'依工作台／資料判定':text(o.baseline);}
 function upstreamIon(){return Boolean(state.project?.ion?.apply||state.project?.resolved?.apply||state.project?.config?.ion?.apply);}
 function reason(o){if(o.key.startsWith('mintpy.ionosphericDelay')&&upstreamIon())return '上游 ALOS-2 處理已套用電離層校正；本次 MintPy 保留校正後的輸入，不重複校正。'+(o.disabled_reason?' '+o.disabled_reason:'');return o.disabled_reason||'';}
 function shortControlValue(o){
  const key=o.key;
  if(key==='mintpy.subset.yx'){const v=['mintY0','mintY1','mintX0','mintX1'].map(field);return v.every(Boolean)?`${v[0]}:${v[1]}, ${v[2]}:${v[3]}`:'未指定像素裁切';}
  if(key==='mintpy.subset.lalo'){const v=['regionSouth','regionNorth','regionWest','regionEast'].map(field);return v.every(Boolean)?`${v[0]}:${v[1]}, ${v[2]}:${v[3]}`:'未指定經緯度裁切';}
  if(key==='mintpy.reference.yx'||key==='mintpy.reference.lalo')return '載入後在參考點檢查地圖選擇';
  if(key==='mintpy.network.excludeDate')return [...$('mintDates').querySelectorAll('input:not(:checked)')].map(n=>n.value).join(', ')||'未排除日期';
  if(key==='mintpy.network.excludeDate12'||key==='mintpy.network.excludeIfgIndex')return [...$('mintPairs').querySelectorAll('input:checked')].map(n=>n.value).join(', ')||'未手動排除配對';
  const el=$(o.basic_control);if(!el)return '由既有設定與資料決定';if(el.type==='checkbox')return el.checked?'yes':'no';return el.value||'未指定';
 }
 function valueDescription(o){if(o.status==='basic')return shortControlValue(o);if(Object.hasOwn(state.overrides,o.key))return state.overrides[o.key]==='auto'?`MintPy auto → ${resolvedDefault(o)}`:text(state.overrides[o.key]);return baseValue(o);}
 function showTab(name,focus=false){
  state.tab=name;for(const [key,[tab,panel]] of Object.entries(panels)){const selected=name===key;$(tab).classList.toggle('selected',selected);$(tab).setAttribute('aria-selected',String(selected));$(tab).tabIndex=selected?0:-1;$(panel).hidden=!selected;}
  if(focus)$(panels[name][0]).focus();if(name==='all')renderCatalog();if(name==='basic')setTimeout(()=>window.refreshMapSizes?.(),80);
 }
 function openControl(id){
  const el=$(id);if(!el)return;const panel=el.closest('[role="tabpanel"]');showTab(panel?.id==='mintAdvancedPanel'?'advanced':'basic');for(let p=el.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;
  el.scrollIntoView({behavior:'smooth',block:'center'});el.focus({preventScroll:true});el.classList.add('mint-highlight');setTimeout(()=>el.classList.remove('mint-highlight'),1800);
 }
 function jumpEditor(o){showTab('advanced');const editor=state.editors.get(o.key);if(!editor)return;editor.card.closest('details').open=true;editor.card.scrollIntoView({behavior:'smooth',block:'center'});editor.mode.focus({preventScroll:true});editor.card.classList.add('mint-highlight');setTimeout(()=>editor.card.classList.remove('mint-highlight'),1800);}
 function help(o){
  const d=node('details',undefined,'mint-help');d.append(node('summary','用途、取捨與來源'));
  if(o.purpose)d.append(node('p',o.purpose));if(o.help&&o.help!==o.purpose)d.append(node('p',o.help));
  if(o.tradeoff)d.append(node('p',typeof o.tradeoff==='string'?o.tradeoff:Object.values(o.tradeoff).join(' '),'mint-tradeoff'));
  if(o.note)d.append(node('p',o.note));if(o.conditions)d.append(node('p',o.conditions,'mint-availability'));if(o.allowed)d.append(node('p','接受的值：'+o.allowed,'hint'));
  d.append(node('p',`工作台基準：${baseValue(o)}；所選版本 auto 預設：${text(resolvedDefault(o))}。實際採用值在本次分析紀錄中保存。`,'hint'));
  const refs=node('div',undefined,'mint-sources');for(const source of o.sources||[]){if(!/^https?:\/\//.test(source.url||''))continue;const a=node('a',source.title||source.url);a.href=source.url;a.target='_blank';a.rel='noopener noreferrer';refs.append(a);}if(refs.childNodes.length)d.append(refs);
  d.append(node('code',o.key,'mint-key'));return d;
 }
 function basicHelp(){
  document.querySelectorAll('[data-mint-basic-help]').forEach(el=>el.remove());
  const grouped=new Map();for(const o of state.schema?.options||[])if(o.status==='basic'&&o.basic_control){const list=grouped.get(o.basic_control)||[];list.push(o);grouped.set(o.basic_control,list);}
  for(const [id,opts] of grouped){const input=$(id);if(!input||['mintLoad','mintPairs','mintDates'].includes(id))continue;const d=node('details',undefined,'mint-help mint-basic-help');d.dataset.mintBasicHelp=id;d.append(node('summary','設定說明'));for(const o of opts){d.append(node('strong',o.label),node('p',o.purpose||o.help||''));if(o.tradeoff)d.append(node('p',typeof o.tradeoff==='string'?o.tradeoff:JSON.stringify(o.tradeoff),'mint-tradeoff'));for(const source of o.sources||[]){if(!/^https?:\/\//.test(source.url||''))continue;const a=node('a',source.title||'參考資料');a.href=source.url;a.target='_blank';a.rel='noopener noreferrer';d.append(a);}d.append(node('code',o.key,'mint-key'));}const container=input.closest('label')||input;let wrap=container.parentElement;if(!wrap.classList.contains('mint-control-wrap')){wrap=node('div',undefined,'mint-control-wrap');container.before(wrap);wrap.append(container);}wrap.append(d);}
 }
 function getIssue(){
  if(state.error)return state.error;
  const blocked=Object.keys(state.overrides).filter(k=>!isEditable(optionFor(k)||{}));return blocked.length?'有 '+blocked.length+' 項先前設定在目前環境不可使用；請檢查並移除後再建立分析。':'';
 }
 function refreshSummary(){
  const count=Object.keys(state.overrides).length,issue=getIssue();$('mintSettingsSummary').textContent=issue?'設定需要確認':count?`${count} 項進階自訂 · 其餘沿用工作台基準`:'沿用工作台基準設定';
  const methods=['mintpy.unwrapError.method','mintpy.solidEarthTides','mintpy.troposphericDelay.method'];const enabled=methods.map(k=>optionFor(k)).filter(Boolean).filter(o=>{const v=state.overrides[o.key]??o.baseline;return !['no','none','false',null,undefined].includes(v);}).map(o=>`${o.label}：${valueDescription(o)}`);
  $('mintSettingsSummaryDetail').textContent=issue|| (enabled.length?enabled.join('；')+'。載入前會檢查所需資料與依賴。':'載入後先確認參考點，再開始反演。所有結果均為相對 LOS 位移。');$('mintSettingsSummaryDetail').classList.toggle('error',Boolean(issue));
  if(state.tab==='all')renderCatalog();
 }
 function syncEditor(entry){
  const {o,mode,input,custom,card}=entry,v=state.overrides[o.key],selected=v===undefined?'baseline':v==='auto'?'auto':v==='no'&&supportsOff(o)?'off':'custom';mode.value=selected;custom.hidden=selected!=='custom';input.required=selected==='custom';input.disabled=selected!=='custom'||!isEditable(o);mode.disabled=!isEditable(o);if(selected==='custom')input.value=v;card.classList.toggle('is-custom',v!==undefined);entry.value.textContent='目前：'+valueDescription(o);
 }
 function updateEntry(entry){
  const {o,mode,input}=entry;
  if(mode.value==='baseline')delete state.overrides[o.key];else if(mode.value==='auto')state.overrides[o.key]='auto';else if(mode.value==='off')state.overrides[o.key]='no';else state.overrides[o.key]=input.value.trim();syncEditor(entry);refreshSummary();
 }
 function buildEditor(o){
  const card=node('section',undefined,'mint-param-editor');card.dataset.key=o.key;const header=node('div',undefined,'mint-param-heading'),title=node('label',o.label),badge=node('span',o.group_label,'mint-mini-badge');header.append(title,badge);card.append(header,node('p',o.purpose||o.help||'','mint-param-purpose'));
  const mode=node('select'),sid='mint-option-'+o.key.replaceAll('.','-');mode.id=sid;title.htmlFor=sid;for(const [v,l] of [['baseline','沿用工作台基準：'+baseValue(o)],...(o.allow_auto===false?[]:[['auto','MintPy auto：'+text(resolvedDefault(o))]]),...(supportsOff(o)?[['off','關閉／不限制（no）']]:[]),['custom','自行設定']]){const opt=node('option',l);opt.value=v;mode.append(opt);}card.append(mode);
  const custom=node('div',undefined,'mint-custom-value');custom.hidden=true;const values=choices(o),input=node(values.length?'select':'input');input.id=sid+'-value';input.setAttribute('aria-label',o.label+'的自訂值');
  if(values.length){for(const v of values.filter(x=>x!=='auto')){const opt=node('option',labelValue(v));opt.value=v;input.append(opt);}}else{input.type=o.input_type==='number'?'number':'text';input.placeholder=o.placeholder||o.allowed||'輸入設定值';if(o.min!==undefined)input.min=o.min;if(o.max!==undefined)input.max=o.max;if(input.type==='number')input.step=o.step??'any';}
  custom.append(input);if(o.input_type==='path'){const pick=node('button','選擇資料'+((o.path_kind==='directory'||o.key.endsWith('Dir'))?'夾':'檔'));pick.type='button';pick.setAttribute('aria-label','選擇'+o.label);action(pick,()=>choosePath((o.path_kind==='directory'||o.key.endsWith('Dir'))?'dir':'file',p=>{input.value=p;updateEntry(entry);},input.value.startsWith('/')?input.value:defaults.home));custom.classList.add('input-row');custom.append(pick);}
  const value=node('p','目前：'+baseValue(o),'mint-current-value');card.append(custom,value,help(o));if(reason(o))card.append(node('p',reason(o),'hint'));const entry={o,mode,input,custom,card,value};mode.onchange=()=>{if(mode.value==='custom'&&!input.value){const baseline=o.baseline;if(baseline!=null&&baseline!=='auto'&&!(baseline==='no'&&supportsOff(o)))input.value=baseline;}updateEntry(entry);};input.addEventListener('input',()=>{state.overrides[o.key]=input.value.trim();entry.value.textContent='目前：'+valueDescription(o);refreshSummary();});input.addEventListener('change',()=>updateEntry(entry));syncEditor(entry);return entry;
 }
 function renderAdvanced(){
  const target=$('mintAdvancedGroups');target.replaceChildren();state.editors.clear();const groups=new Map();for(const o of state.schema.options.filter(isEditable)){const list=groups.get(o.group)||[];list.push(o);groups.set(o.group,list);}
  for(const [group,opts] of groups){const d=node('details',undefined,'mint-setting-group');d.dataset.group=group;const sum=node('summary'),labels=node('span',opts[0].group_label||group),count=node('small',`${opts.length} 項可調整`);sum.append(labels,count);d.append(sum);const grid=node('div',undefined,'mint-editor-grid');for(const o of opts){const entry=buildEditor(o);state.editors.set(o.key,entry);grid.append(entry.card);}d.append(grid);target.append(d);}
  const blocked=Object.keys(state.overrides).filter(k=>!isEditable(optionFor(k)||{}));if(blocked.length){const warning=node('div',undefined,'analysis-warning');warning.append(node('strong','目前無法套用的舊設定'));for(const k of blocked){const row=node('div',undefined,'mint-blocked-setting'),remove=node('button','移除此設定');remove.onclick=()=>{delete state.overrides[k];renderAdvanced();refreshSummary();};row.append(node('span',`${optionFor(k)?.label||k}：${state.overrides[k]}`),remove);warning.append(row);}target.prepend(warning);}
  if(!groups.size&&!blocked.length)target.append(node('p','所選環境目前没有可用的進階參數；請參考上方版本檢查訊息。','empty-state'));
 }
 function renderCatalog(){
  if(!state.schema)return;const query=$('mintParamSearch').value.trim().toLowerCase(),group=$('mintParamGroup').value,status=$('mintParamStatus').value;
  const options=state.schema.options.filter(o=>(!group||o.group===group)&&(!status||(status==='changed'?Object.hasOwn(state.overrides,o.key):o.status===status))&&(!query||[o.key,o.label,o.purpose,o.help,o.group_label,o.tradeoff].join(' ').toLowerCase().includes(query)));
  $('mintCatalogCount').textContent=`顯示 ${options.length} / ${state.schema.options.length} 項。常用欄位只有一處設定；工作台管理與未開放的選項會列出原因。`;
  const target=$('mintParameterCatalog');target.replaceChildren();if(!options.length){target.append(node('p','沒有符合的選項。試試其他關鍵字或清除篩選。','empty-state'));return;}
  const groups=new Map();for(const o of options){const list=groups.get(o.group)||[];list.push(o);groups.set(o.group,list);}
  for(const [group,opts] of groups){const d=node('details',undefined,'mint-catalog-group');d.open=Boolean(query||$('mintParamGroup').value||status);const summary=node('summary');summary.append(node('span',opts[0].group_label||group),node('small',`${opts.length} 項`));d.append(summary);for(const o of opts){const row=node('article',undefined,'mint-catalog-row'),top=node('div',undefined,'mint-catalog-row-top'),heading=node('div');heading.append(node('h4',o.label),node('code',o.key,'mint-key'));const right=node('div',undefined,'mint-catalog-action'),badge=node('span',statusLabels[o.status]||'待確認','mint-mini-badge '+o.status);right.append(badge);if(o.status==='basic'){const go=node('button','前往設定');go.type='button';go.onclick=()=>openControl(o.basic_control||'mintLoad');right.append(go);}else if(isEditable(o)){const go=node('button',Object.hasOwn(state.overrides,o.key)?'修改自訂值':'調整');go.type='button';go.onclick=()=>jumpEditor(o);right.append(go);}top.append(heading,right);row.append(top,node('p',o.purpose||o.help||''),node('p','目前設定：'+valueDescription(o),'mint-current-value'));if(reason(o))row.append(node('p',reason(o),'mint-availability'));row.append(help(o));d.append(row);}target.append(d);}
 }
 async function loadSchema(environment){
  const ticket=++state.generation;state.loading=true;state.error='';$('mintSchemaVersion').textContent='檢查所選環境…';$('mintSchemaNotice').textContent='正在讀取 MintPy 版本與可用參數。';
  try{const schema=await api('/api/mintpy-schema?environment='+encodeURIComponent(environment||''));if(ticket!==state.generation)return;state.schema=schema;const meta=schema.metadata||{},version=meta.environment_version||meta.version||'未偵測';$('mintSchemaVersion').textContent=`MintPy ${version} · ${schema.options.length} 項參數`;
   const warnings=meta.warnings||[];$('mintSchemaNotice').textContent=warnings.length?warnings.join(' '):`以所選環境的 MintPy 版本核對設定；常用欄位、進階方法與完整清單共用同一份分析設定。`;$('mintSchemaNotice').className=warnings.length?'analysis-warning':'hint';
   const chosen=$('mintParamGroup').value;$('mintParamGroup').replaceChildren();const all=node('option','所有類別');all.value='';$('mintParamGroup').append(all);const groups=new Map(schema.options.map(o=>[o.group,o.group_label]));for(const [id,label] of groups){const op=node('option',label);op.value=id;$('mintParamGroup').append(op);}$('mintParamGroup').value=groups.has(chosen)?chosen:'';renderAdvanced();basicHelp();renderCatalog();refreshSummary();
  }catch(e){if(ticket!==state.generation)return;state.error='無法讀取 MintPy 設定：'+e.message;state.schema=null;$('mintSchemaVersion').textContent='設定清單尚未就緒';$('mintSchemaNotice').textContent=state.error;$('mintSchemaNotice').className='analysis-warning';$('mintAdvancedGroups').replaceChildren(node('p','請重新選擇分析環境以重試；設定清單讀取成功後才能送出進階自訂值。','empty-state'));$('mintParameterCatalog').replaceChildren();refreshSummary();}finally{if(ticket===state.generation)state.loading=false;}
 }
 function collect(){
  if(state.loading)throw Error('正在確認 MintPy 版本，請稍候再建立分析。');if(!state.schema&&Object.keys(state.overrides).length)throw Error(state.error||'MintPy 設定清單未就緒。');const out={};
  for(const [key,v] of Object.entries(state.overrides)){const o=optionFor(key);if(!o||!isEditable(o))throw Error(`「${o?.label||key}」在目前環境不可使用，請在進階設定移除。`);const value=normalize(v);if(!value)throw Error(`請填寫「${o.label}」，或改回「沿用工作台基準」。`);const entry=state.editors.get(key);if(!['auto','no'].includes(value)&&entry&&!entry.input.checkValidity()){entry.card.closest('details').open=true;showTab('advanced');entry.input.reportValidity();throw Error(`請檢查「${o.label}」的值。`);}out[key]=value;}return out;
 }
 function reportRows(report){const r=report?.resolved||{};return Array.isArray(r)?r.map(x=>[x.key,x.value??x.resolved]):Object.entries(r);}
 function renderRun(job,target){
  const report=job.parameter_report,section=node('section',undefined,'mint-run-report');section.append(node('h3','本次方法與實際設定'));
  if(!report){section.append(node('p',job.status==='running'?'載入檢查完成後會在此保存 MintPy 版本、設定值與各項校正的狀態。':'此分析版本沒有保存完整參數報告；可檢視原有設定，複製為新版本後補齊紀錄。','hint'));target.append(section);return;}
  section.append(node('p',`MintPy ${report.version||'未記錄版本'} · 本次設定已獨立保存`,'hint'));if(typeof exportButton==='function')section.append(exportButton(job.project_id,job.id,'parameter-report.json','下載本次參數報告'));const corrections=node('div',undefined,'mint-correction-grid');
  const names={running:'正在執行',applied:'已完成',complete:'已完成',completed:'已完成',enabled:'預計執行',planned:'預計執行',pending:'等待執行',skipped:'未套用',disabled:'未啟用',off:'未啟用',failed:'執行失敗',upstream:'已由上游校正',not_run:'未執行',unknown:'待確認'};
  for(const c of report.corrections||[]){const item=node('div',undefined,'mint-correction '+(c.state||'unknown'));item.append(node('strong',c.key?.startsWith('mintpy.ionosphericDelay')?'MintPy 電離層校正':c.label||optionFor(c.key)?.label||c.key),node('span',names[c.state]||c.state||'待確認','mint-mini-badge'),node('p',c.detail||'','hint'));corrections.append(item);}if(corrections.childNodes.length)section.append(corrections);if(job.source_ion?.apply)section.append(node('p','上游 ISCE2 已套用電離層校正；這裡的「未啟用」只表示 MintPy 不再重複扣除電離層延遲。','notice'));
  if(report.time_model){const model=report.time_model,dates=model.dates||[];section.append(node('p',`${model.nonlinear?'含季節性／事件或高階項的模型':'線性時間模型'} · ${dates.length} 個日期參與擬合 · ${model.parameters??'—'} 個參數 · 殘差自由度 ${model.residual_dof??'—'}`,'hint'));if(dates.length){const d=node('details',undefined,'mint-fit-dates');d.append(node('summary','查看實際參與速度擬合的日期'),node('p',dates.map(isoDate).join('、'),'hint'),node('p','成果曲線仍顯示全部觀測日期；時間模型的日期篩選只影響速度與模型係數的擬合。','hint'));section.append(d);}if(model.velocity_meaning)section.append(node('p',model.velocity_meaning,'hint'));}
  for(const warning of report.warnings||[])section.append(node('p',typeof warning==='string'?warning:warning.message||JSON.stringify(warning),'analysis-warning'));
  const details=node('details',undefined,'mint-resolved-details');details.append(node('summary','查看本次實際參數'));const search=node('input');search.type='search';search.placeholder='搜尋本次參數';search.setAttribute('aria-label','搜尋本次實際參數');const table=node('table'),wrap=node('div',undefined,'table-scroll');wrap.append(table);details.append(search,wrap);
  const draw=()=>{const query=search.value.trim().toLowerCase();table.replaceChildren();const head=node('tr');['設定','實際採用值'].forEach(v=>head.append(node('th',v)));table.append(head);for(const [k,v] of reportRows(report).filter(([k,v])=>[k,v,optionFor(k)?.label].join(' ').toLowerCase().includes(query))){const row=node('tr'),label=node('td');label.append(node('span',optionFor(k)?.label||k),node('code',k,'mint-key'));row.append(label,node('td',text(v)));table.append(row);}};search.oninput=draw;draw();if(report.template_sha256)details.append(node('p','本次設定識別碼：'+report.template_sha256,'mint-fingerprint'));section.append(details);target.append(section);
 }
 function renderComparison(job,other,target){
  target.replaceChildren();const overrides=job.options?.mintpy_overrides||{},otherOverrides=other?.options?.mintpy_overrides||{},keys=[...new Set([...Object.keys(overrides),...Object.keys(otherOverrides)])].sort();if(!keys.length)target.append(node('p','沒有額外的進階自訂值。','hint'));
  if(keys.length){const table=node('table'),head=node('tr');['進階參數','本次值',...(other?['比較版本']:[])].forEach(v=>head.append(node('th',v)));table.append(head);for(const k of keys){const row=node('tr'),label=node('td');label.append(node('span',optionFor(k)?.label||k),node('code',k,'mint-key'));row.append(label,node('td',Object.hasOwn(overrides,k)?text(overrides[k]):'沿用該版工作台基準'));if(other){const cell=node('td',Object.hasOwn(otherOverrides,k)?text(otherOverrides[k]):'沿用該版工作台基準');if(overrides[k]!==otherOverrides[k])cell.className='changed';row.append(cell);}table.append(row);}const wrap=node('div',undefined,'table-scroll');wrap.append(table);target.append(wrap);}
  if(other){const a=job.parameter_report?.version,b=other.parameter_report?.version;if(a!==b)target.prepend(node('p',`兩次 MintPy 版本不同或未記錄：${a||'未記錄'} → ${b||'未記錄'}。相同 auto 設定未必得到相同的實際值。`,'analysis-warning'));const left=Object.fromEntries(reportRows(job.parameter_report)),right=Object.fromEntries(reportRows(other.parameter_report));const differences=Object.keys(left).filter(k=>Object.hasOwn(right,k)&&JSON.stringify(left[k])!==JSON.stringify(right[k]));if(differences.length){const detail=node('details');detail.append(node('summary',`${differences.length} 項實際採用值不同`));const t=node('table');for(const k of differences){const r=node('tr');r.append(node('td',optionFor(k)?.label||k),node('td',text(left[k])),node('td',text(right[k])));t.append(r);}detail.append(t);target.append(detail);}}
 }
 window.mintpySettings={
  async reset(project,environment){state.project=project;state.environment=environment;state.overrides={};for(const [id,value] of Object.entries({mintMemory:2,mintTemporal:.7,mintRefCoh:.85,mintDeramp:'no',mintWeight:'var',mintRedundancy:1,mintDemOrder:1}))$(id).value=value;$('mintDem').checked=false;['filterCoh','filterDays','filterBaseline'].forEach(id=>$(id).value='');showTab('basic');await loadSchema(environment);},
  async environmentChanged(environment){state.environment=environment;await loadSchema(environment);},
  collect,
  restore(overrides){state.overrides=Object.fromEntries(Object.entries(overrides||{}).map(([k,v])=>[k,normalize(v)]));if(state.schema){renderAdvanced();renderCatalog();}refreshSummary();},
  renderRun,renderComparison,showTab,
 };
 for(const [name,[tab]] of Object.entries(panels)){$(tab).onclick=()=>showTab(name);$(tab).onkeydown=e=>{const keys=Object.keys(panels),index=keys.indexOf(name);if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();showTab(e.key==='Home'?keys[0]:e.key==='End'?keys.at(-1):keys[(index+(e.key==='ArrowRight'?1:-1)+keys.length)%keys.length],true);}};}
 for(const id of ['mintParamSearch','mintParamGroup','mintParamStatus'])$(id).addEventListener(id==='mintParamSearch'?'input':'change',renderCatalog);
 for(const panel of ['mintBasicPanel','mintAdvancedPanel'])$(panel).addEventListener('change',()=>refreshSummary());
})();
