const $=id=>document.getElementById(id), token=document.querySelector('meta[name="api-token"]').content;
let properties={}, current=null, projects=[], defaults={}, pickCallback, pickMode, reviewLoaded=null, objectURLs=[];
const ionKeys=['do ionospheric phase estimation','apply ionospheric phase correction'];
const labels={'polarization':'極化方式（polarization）','do ionospheric phase estimation':'估計電離層','apply ionospheric phase correction':'套用電離層校正','number of range looks 1':'Range looks · 第 1 階段','number of azimuth looks 1':'Azimuth looks · 第 1 階段','number of range looks 2':'Range looks · 第 2 階段','number of azimuth looks 2':'Azimuth looks · 第 2 階段','number of range looks ion':'Range looks · 電離層','number of azimuth looks ion':'Azimuth looks · 電離層','number of subsequent dates':'一般配對：後續日期數','number of subsequent dates for estimating ionosphere':'電離層配對：後續日期數'};
const controlled=['data directory','dem for coregistration','dem for geocoding','water body','reference date of the stack','dates processing directory','dates resampled directory','pairs processing directory','baseline directory','dates directory for ionosphere','pairs processing directory for estimating ionosphere'];
const initial=()=>Object.fromEntries(Object.keys(labels).map(k=>[k,'']));
async function api(path,data){const r=await fetch(path,{method:data?'POST':'GET',headers:{'X-Token':token,...(data?{'Content-Type':'application/json'}:{})},body:data?JSON.stringify(data):undefined});const v=await r.json();if(!r.ok)throw Error(v.error||r.statusText);return v;}
function fail(e){$('toast').textContent=e.message||e;$('toast').hidden=false;}
function clear(){ $('toast').hidden=true; }
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function action(el,fn){el.addEventListener('click',async e=>{e.preventDefault();clear();try{await fn(e);}catch(err){fail(err);}});}
function renderProperties(){
 const container=$('properties');container.replaceChildren();
 for(const [key,val] of Object.entries(properties)){
  if(controlled.includes(key)||key==='frames')continue;
  const row=node('div',undefined,'property'),label=node('label',labels[key]||key),selector=node('select');
  selector.setAttribute('aria-label',(labels[key]||key)+' · 設定方式');
  if(key==='polarization'){
   for(const [value,text] of [['','使用程式預設值'],['HH','HH'],['HV','HV'],['VH','VH'],['VV','VV']]){const o=node('option',text);o.value=value;selector.append(o);}
   if(val&&!['HH','HV','VH','VV'].includes(val)){const o=node('option',val+'（匯入值，請確認）');o.value=val;selector.append(o);}
   selector.value=val;selector.onchange=()=>properties[key]=selector.value;label.append(selector);
  }else if(ionKeys.includes(key)){
   for(const [value,text] of [['','使用程式預設值'],['True','啟用（True）'],['False','關閉（False）']]){const o=node('option',text);o.value=value;selector.append(o);}
   selector.value=val?String(val).toLowerCase()==='true'?'True':'False':'';
   selector.onchange=()=>properties[key]=selector.value;
   label.append(selector);
  }else{
   for(const [value,text] of [['default','使用程式預設值'],['custom','自行指定']]){const o=node('option',text);o.value=value;selector.append(o);}
   selector.value=val?'custom':'default';
   const input=node('input');input.value=val;input.disabled=!val;input.placeholder='未設定時不寫入 XML';input.setAttribute('aria-label',labels[key]||key);
   input.oninput=()=>properties[key]=input.value;
   selector.onchange=()=>{input.disabled=selector.value==='default';properties[key]=input.disabled?'':input.value;if(!input.disabled)input.focus();};
   label.append(selector,input);
  }
  const hint=node('small',key==='polarization'?'對應 XML 的 polarization；請選擇影像實際包含的極化，學姊範例為 HH。':ionKeys.includes(key)?'預設值由目前安裝的 ISCE2 決定。':'使用預設值相當於原 XML 未設定或被註解。','hint');label.append(hint);
  row.append(label);container.append(row);
 }
 window.samplingSyncFrames?.();
}

async function browse(path){const data=await api('/api/browse?path='+encodeURIComponent(path));$('browsePath').value=data.path;$('up').onclick=()=>browse(data.parent).catch(fail);$('files').replaceChildren();for(const item of data.entries){if(pickMode!=='file'&&!item.dir)continue;const b=node('button',(item.dir?'▸  ':'   ')+item.name,'file');b.onclick=async()=>{if(item.dir){try{await browse(item.path);}catch(e){fail(e);}}else{$('picker').close();try{await pickCallback(item.path);}catch(e){fail(e);}}};$('files').append(b);} }
let nativePending=false,nativeFeedback=null;
async function choosePath(mode,callback,start){
 if(nativePending){const message='正在等候另一個 macOS 選擇視窗。請先完成或取消該視窗，再選擇其他位置。';if(nativeFeedback)nativeFeedback.textContent=message;fail(Error(message));return;}
 let fallbackMessage='';
 const useNative=localStorage.getItem('alos-picker')!=='web';
 if(useNative){
  nativePending=true;
  const buttons=[...document.querySelectorAll('[data-pick],#importTemplate')].map(b=>[b,b.disabled]);buttons.forEach(([b])=>b.disabled=true);
  nativeFeedback?.remove();nativeFeedback=node('p','正在準備 macOS 選擇視窗（首次使用可能稍久）；請完成選擇或按取消。','hint');nativeFeedback.setAttribute('role','status');nativeFeedback.setAttribute('aria-live','polite');
  const origin=document.activeElement?.closest('label,.input-row,form,dialog');if(origin)origin.append(nativeFeedback);else document.querySelector('main').prepend(nativeFeedback);
  $('pickerStatus').textContent=nativeFeedback.textContent;
  let selectedResult=null;
  try{
   selectedResult=await api('/api/pick',{mode,start:start||defaults.home});
   nativeFeedback.textContent=selectedResult.supported?(selectedResult.cancelled?'已取消選擇，原欄位保持不變。':'已選擇位置。'):'目前系統使用網頁內建選擇器。';
  }catch(e){fallbackMessage=e.message||String(e);nativeFeedback.textContent=fallbackMessage;}
  finally{nativePending=false;buttons.forEach(([b,disabled])=>b.disabled=disabled);$('pickerStatus').textContent='';}
  if(selectedResult?.supported){if(!selectedResult.cancelled)await callback(selectedResult.path);return;}
 }
 pickMode=mode;pickCallback=callback;$('chooseDir').hidden=mode==='file';$('files').textContent='正在讀取資料夾…';
 let notice=$('pickerFallbackNotice');if(!notice){notice=node('p',undefined,'notice');notice.id='pickerFallbackNotice';notice.setAttribute('role','status');$('files').before(notice);}notice.hidden=!fallbackMessage;notice.textContent=fallbackMessage;
 if(!$('picker').open)$('picker').showModal();
 try{await browse(start||defaults.home);}catch(e){if(start&&start!==defaults.home){notice.hidden=false;notice.textContent=[fallbackMessage,'先前位置無法讀取，已回到個人資料夾。'].filter(Boolean).join(' ');await browse(defaults.home);}else throw e;}
}
$('pickerPreference').value=localStorage.getItem('alos-picker')||'native';
$('pickerPreference').onchange=()=>localStorage.setItem('alos-picker',$('pickerPreference').value);
document.querySelectorAll('[data-pick]').forEach(b=>action(b,()=>choosePath(b.dataset.mode||'file',p=>{$(b.dataset.pick).value=b.dataset.mode==='newdir'?p+(window.selectedWorkflow==='alos2App'?'/alos2-pair':'/alos-project'):p;},$(b.dataset.pick).value||defaults.home)));
action($('chooseDir'),async()=>{$('picker').close();await pickCallback($('browsePath').value);});
action($('goPath'),()=>browse($('browsePath').value));$('closePicker').onclick=()=>$('picker').close();
async function importTemplate(path){const d=await api('/api/template',{path});properties={...initial(),...d.properties};renderProperties();$('templateNote').textContent='已載入：'+path+'。已保留明確設定；未設定或註解的參數使用程式預設值。資料路徑以表單為準。';}
action($('importTemplate'),()=>choosePath('file',importTemplate));
action($('addProperty'),()=>{const k=$('propName').value.trim();if(!k||controlled.includes(k))throw Error('請輸入其他參數名稱；資料路徑請在上方設定。');properties[k]=$('propValue').value;renderProperties();$('propName').value='';$('propValue').value='';});
async function scanDates(){const d=await api('/api/scan',{path:$('source').value});$('reference').replaceChildren(node('option','請選擇參考日期'));$('reference').firstChild.value='';$('dates').replaceChildren();for(const x of d.dates){const o=node('option',x.date);o.value=x.date;$('reference').append(o);$('dates').append(node('span',x.date+' · '+x.leaders+' LED / '+x.images+' IMG · frames '+(x.frames||[]).join(',')+' · '+(x.polarizations||[]).join('/')+(x.leaders&&x.images?'':' · 缺檔')+(x.warnings?.length?' · '+x.warnings.join('；'):''),'chip'));}$('scanSummary').textContent=`找到 ${d.dates.length} 個日期`;window.pairScanned?.(d.dates);window.samplingScanned?.(d.dates);if(!d.dates.length)throw Error('找不到 YYMMDD 資料夾，可使用「工具與環境」中的匯入精靈。');return d.dates;}
action($('scan'),scanDates);
function projectConfig(){const c={};for(const k of ['name','root','source','env','dem','dem_geo','wbd','reference'])c[k]=$(k).value.trim();c.workflow=window.selectedWorkflow||'alosStack';c.copy=$('copy').checked;c.properties=Object.fromEntries(Object.entries(properties).filter(([k,v])=>v.trim()&&!controlled.includes(k)));if(c.workflow==='alos2App')Object.assign(c,window.pairFormOptions());if(c.workflow==='alosStack')Object.assign(c,window.samplingFormOptions?.());const bounds=['checkSouth','checkNorth','checkWest','checkEast'].map(id=>$(id).value.trim());if(bounds.some(Boolean)){if(!bounds.every(Boolean))throw Error('完整影像範圍請填滿四個界線，或全部留白。');c.image_bounds=bounds.map(Number);}return c;}
const projectHealth=node('details',undefined,'card'),projectHealthNote=node('p',undefined,'hint'),projectHealthButton=node('button','重新檢查資料與環境'),projectHealthResults=node('div');projectHealth.append(node('summary','資料健檢與失敗處理'),projectHealthNote,projectHealthButton,projectHealthResults);$('project').insertBefore(projectHealth,$('project').querySelector('.project-settings'));
let healthProjectId=null;
window.preflightProject=s=>{projectHealth.hidden=!!s.import_only;if(s.import_only)return;if(healthProjectId!==s.id){healthProjectId=s.id;projectHealthResults.replaceChildren();}projectHealthButton.disabled=s.status==='running';const h=s.history?.at(-1);projectHealthNote.textContent=(h?'最後完成：'+h.stage+'。':'尚無已完成階段。')+(s.status==='failed'?(s.can_retry_prepare?'準備階段可重試：先修正健檢問題，再按「重新檢查並準備指令」。':'已保留半成品與日誌。重新健檢只檢查輸入，不會重跑；科學運算需先診斷失敗步驟。雙日期若提供檢查點續跑，仍需先核對檢查點。'):'這個檢查只讀取資料與描述檔，不會重新運算或修改成果。');};
action(projectHealthButton,async()=>{const id=current;projectHealthButton.disabled=true;try{const r=await api('/api/project-check',{id});if(current!==id)return;projectHealthResults.replaceChildren();for(const row of r.checks){const d=node('div',undefined,'health-row '+row.status);d.append(node('strong',({pass:'✓ 已確認',warning:'△ 請確認',error:'✕ 需修正'}[row.status])+' · '+row.title),node('p',row.detail,'hint'));projectHealthResults.append(d);}}finally{projectHealthButton.disabled=false;}});
const healthFields=node('details',undefined,'card');healthFields.append(node('summary','運算前健檢：完整影像範圍（可選）'),node('p','填寫資料提供者的整批 SAR 影像外框，核對 DEM／WBD 覆蓋。這不是研究區裁切；不知道可留白，摘要會提醒尚未確認。','hint'));
const healthGrid=node('div',undefined,'coordinate-fields');for(const [id,label] of [['checkSouth','南界（°N）'],['checkNorth','北界（°N）'],['checkWest','西界（°E）'],['checkEast','東界（°E）']]){const l=node('label',label),i=node('input');i.id=id;i.type='number';i.step='any';l.append(i);healthGrid.append(l);}healthFields.append(healthGrid);$('createForm').insertBefore(healthFields,$('createForm').querySelector('.footer-action'));
const healthDialog=node('dialog');healthDialog.id='preflightDialog';healthDialog.setAttribute('aria-labelledby','preflightTitle');const healthTitle=node('h2','開始前，確認這次處理內容');healthTitle.id='preflightTitle';healthDialog.append(healthTitle);const healthResults=node('div');healthResults.setAttribute('aria-live','polite');healthDialog.append(healthResults);const healthLabel=node('label',undefined,'check'),healthConsent=node('input');healthConsent.type='checkbox';healthLabel.append(healthConsent,document.createTextNode('我已閱讀需要人工確認的事項，了解檢查通過不代表科學品質已驗證。'));healthDialog.append(healthLabel);const healthActions=node('div',undefined,'inline'),healthConfirm=node('button','確認並建立專案','primary'),healthCancel=node('button','返回修改');healthActions.append(healthConfirm,healthCancel);healthDialog.append(healthActions);document.body.append(healthDialog);healthCancel.onclick=()=>healthDialog.close();
let healthSnapshot=null,healthReport=null;
healthConsent.onchange=()=>{healthConfirm.disabled=!healthReport?.can_proceed||(healthReport.needs_review&&!healthConsent.checked);};
action(healthConfirm,async()=>{if(JSON.stringify(projectConfig())!==healthSnapshot){healthDialog.close();throw Error('設定已改變，請重新送出健檢。');}healthConfirm.disabled=true;try{const s=await api('/api/create',{...JSON.parse(healthSnapshot),preflight_reviewed:healthConsent.checked,...(healthReport.confirmation_id?{confirmation_id:healthReport.confirmation_id}:{})});healthDialog.close();current=s.id;reviewLoaded=null;await refresh();}finally{healthConsent.onchange();}});
$('createForm').onsubmit=async e=>{e.preventDefault();clear();const submit=$('createForm').querySelector('[type="submit"]');submit.disabled=true;try{const c=projectConfig();healthSnapshot=JSON.stringify(c);healthReport=null;healthConsent.checked=false;healthConfirm.disabled=true;healthResults.replaceChildren(node('p','正在核對影像、配對規則與執行環境，請稍候…','notice'));healthDialog.showModal();healthReport=await api(c.workflow==='alosStack'?'/api/confirmation':'/api/preflight',c);if(JSON.stringify(projectConfig())!==healthSnapshot)throw Error('設定已改變，請返回重新預覽。');window.renderConfirmation(healthResults,healthReport,()=>healthDialog.close());healthConsent.checked=false;healthLabel.hidden=!healthReport.needs_review;healthConsent.onchange();}catch(e){healthConfirm.disabled=true;healthResults.append(node('p',e.message||String(e),'error'));fail(e);}finally{submit.disabled=false;}};
function newView(){window.leaveWorkspace?.('newProject');window.closeLibrary?.();window.closeMissions?.();$('openWelcome')?.classList.remove('active');$('newProject').classList.add('active');if($('welcome'))$('welcome').hidden=true;current=null;reviewLoaded=null;$('setup').hidden=false;$('project').hidden=true;$('crumb').textContent='建立專案';clear();}
$('newProject').onclick=newView;
const statusNames={imported:'外部成果已匯入',new:'等待準備',ready:'準備完成',running:'執行中',review:'需要判讀',failed:'已停止',complete:'運算完成'};
statusNames.tops_ready='等待確認共同 burst';statusNames.processed='等待整理成果';
statusNames.sampling_review='等待確認影格與取樣';
statusNames.sampling_ready='等待檢查取樣成果';
statusNames.matching_review='等待檢查匹配品質';
statusNames.pair_ready='等待開始干涉';
function summary(s){$('summary').replaceChildren();const c=s.config;for(const [k,v] of Object.entries({'資料來源':c.source,...(c.workflow==='topsApp'?{'比較影像':c.secondary_safe,'子波束':c.swaths.join('、'),'極化':c.polarization,'選區':c.roi?.join('、')||'完整共同範圍'}:{}),'資料使用方式':c.copy?'複製與驗證':'直接讀取','參考日期':c.reference,...(c.workflow==='alos2App'?{'比較日期':c.secondary,'frame':c.frames.join('、'),'地理編碼範圍':c.bbox?.join('、')||'使用參考影像範圍'}:{}),'環境':c.env,'對位 DEM':c.dem,'地理編碼 DEM':c.dem_geo,'水體遮罩':c.wbd,...c.properties,...(s.ion?{'本次電離層估計':s.ion.estimate==null?'未記錄':s.ion.estimate?'啟用':'關閉','本次套用校正':s.ion.apply==null?'未記錄':s.ion.estimate&&s.ion.apply?'啟用':'不執行',...(c.workflow==='alos2App'?{}:{'本次一般配對數':s.ion.subsequent,'本次電離層配對數':s.ion.subsequent_ion})}:{}),...Object.fromEntries(Object.entries(s.looks||{}).filter(([k])=>k.includes('lks')).map(([k,v])=>['本次生成 '+k,v]))})){$('summary').append(node('dt',labels[k]||k),node('dd',v));}}
async function refresh(){const data=await api('/api/state');projects=data.projects;defaults=data.defaults;window.workbenchState=data;window.workbenchCatalog?.(data);window.onboardingUpdate?.(data);$('projects').replaceChildren();for(const s of projects){const b=node('button',s.display_name||s.config.name||'未命名專案','nav'+(current===s.id?' active':''));b.onclick=async()=>{current=s.id;reviewLoaded=null;clear();try{await refresh();}catch(e){fail(e);}};$('projects').append(b);}for(const p of data.unavailable_projects||[]){const b=node('button','專案路徑無法讀取','nav');b.disabled=true;b.title=p.root+' — '+p.reason;$('projects').append(b);}if(!current)return;window.leaveWorkspace?.('openOverview');window.closeLibrary?.();window.closeMissions?.();if($('welcome'))$('welcome').hidden=true;$('openWelcome')?.classList.remove('active');$('newProject').classList.remove('active');const s=projects.find(x=>x.id===current);if(!s){await window.projectBecameUnavailable?.();return;}$('setup').hidden=true;$('project').hidden=false;$('crumb').textContent=s.display_name||s.config.name;$('projectName').textContent=s.display_name||s.config.name;$('projectPath').textContent=s.config.root;$('status').textContent=statusNames[s.status];$('stage').textContent=s.stage;$('error').textContent=s.error||'';summary(s);window.samplingUpdate?.(s,data);$('timeline').hidden=!!s.import_only;window.topsValidationUpdate?.(s,data);window.researchUpdate?.(s,data);window.projectStorageUpdate?.(s,data);$('history').replaceChildren();for(const h of s.history)$('history').append(node('p',h.time+' · '+h.stage,'hint'));if($('topsWorkbench'))$('topsWorkbench').hidden=s.config.workflow!=='topsApp';if(s.config.workflow==='topsApp'){await window.topsUpdate(s,data);await refreshLog();return;}if(s.config.workflow==='alos2App'){await window.pairUpdate(s,data);await refreshLog();return;}if($('pairWorkbench'))$('pairWorkbench').hidden=true;$('timeline').replaceChildren();$('timeline').hidden=!!s.import_only;(s.config.sampling_equalization?['準備專案','影格確認','取樣成果確認','匹配品質確認','電離層判讀','後半段']:['準備專案','跑到檢查點','人工判讀','後半段']).forEach((x,i)=>$('timeline').append(node('div',`${i+1}  ${x}`,(s.status==='review'&&i===(s.config.sampling_equalization?4:2)||s.status==='sampling_review'&&i===1||s.status==='sampling_ready'&&i===2||s.status==='matching_review'&&i===3)?'selected':'')));$('actions').replaceChildren();const add=(text,a)=>{const b=node('button',text,'primary');b.disabled=Boolean(s.storage?.relocated);action(b,async()=>{b.disabled=true;try{if(a==='run'){await window.confirmProcessing(current);return;}await api('/api/action',{id:current,action:a});await refresh();}finally{b.disabled=false;}});$('actions').append(b);};if(s.status==='new')add('確認設定，準備資料與指令','prepare');if(s.status==='ready')add(s.config.sampling_equalization?'讀取資料，前往影格與取樣檢查':s.ion?.estimate===false?'開始 cmd1、cmd2、cmd4（跳過電離層）':'開始 cmd1～3，跑到檢查點','run');if(s.status==='running'){const b=node('button','停止工作');action(b,()=>api('/api/stop',{}));$('actions').append(b);}if(s.status==='failed'){if(s.can_retry_prepare){add('重新檢查並準備指令','prepare');$('actions').append(node('p','尚未進入科學運算，可重試準備步驟。','hint'));}else $('actions').append(node('p','已保留日誌與現有輸出。請開啟「恢復中心」診斷；具備有效副本才可確認復原。','hint'));}$('review').hidden=s.status!=='review';$('continue').disabled=Boolean(s.storage?.relocated);$('continue').textContent=s.ion?.apply===false?'確認判讀，保留估計並跑 cmd4（不套用校正） →':'確認並執行後半段 →';if(s.status==='review'&&reviewLoaded!==current){await renderReview();reviewLoaded=current;}await Promise.all([refreshLog(),window.workbenchUpdate?.(s,data)]);}
let logProject=null,logGeneration=0;
async function refreshLog(){
 const project=projects.find(s=>s.id===current);if(project){window.preflightProject?.(project);window.confirmationProject?.(project);window.recoveryUpdate?.(project);}
 if(!current)return;const key=current,generation=++logGeneration,el=$('log'),status=$('logStatus');
 if(logProject!==key){logProject=key;el.textContent='正在載入執行日誌…';status.hidden=true;}
 try{
  const d=await api('/api/project-output?id='+encodeURIComponent(key));
  if(current!==key||generation!==logGeneration)return;
  const bottom=el.scrollHeight-el.scrollTop-el.clientHeight<50;el.textContent=d.text;status.hidden=true;if(bottom)el.scrollTop=el.scrollHeight;
 }catch(e){
  if(current!==key||generation!==logGeneration)return;
  if(el.textContent==='正在載入執行日誌…')el.textContent='日誌尚未載入。';
  status.textContent='執行日誌暫時無法讀取；其他設定與成果仍可使用。請點「更新」重試。'+(e instanceof TypeError?' 瀏覽器可能攔截了請求，或本機服務連線中斷。':' '+e.message);status.hidden=false;
 }
}
async function renderReview(){const d=await api('/api/review?id='+current);$('pairs').replaceChildren();$('looks').replaceChildren();$('reviewed').checked=false;objectURLs.forEach(URL.revokeObjectURL);objectURLs=[];for(const [k,v]of Object.entries(d.options))if(k.includes('lks'))$('looks').append(node('span',k+': '+v,'chip'));for(const pair of d.pairs){const card=node('div',undefined,'pair'),label=node('label',undefined,'check'),check=node('input');check.type='checkbox';check.value=pair;label.append(check,document.createTextNode('排除 '+pair));card.append(label);const images=d.images.filter(x=>x.includes(pair));if(!images.length)card.append(node('p','沒有找到對應圖片，請查看完整檢查輸出。','error'));for(const file of images){const r=await fetch('/api/image?id='+current+'&file='+encodeURIComponent(file),{headers:{'X-Token':token}});if(!r.ok)throw Error('圖片載入失敗');const url=URL.createObjectURL(await r.blob());objectURLs.push(url);const img=node('img');img.src=url;img.alt=pair+' 電離層檢查';img.onclick=()=>{$('largeImage').src=url;$('imageDialog').showModal();};card.append(img);}$('pairs').append(card);}}
action($('continue'),async()=>{if(!$('reviewed').checked)throw Error('請先檢查圖片並勾選確認。');const excluded=[...$('pairs').querySelectorAll('input:checked')].map(x=>x.value);await api('/api/action',{id:current,action:'continue',excluded});await refresh();});
$('closeImage').onclick=()=>$('imageDialog').close();action($('refreshLog'),refreshLog);
(async()=>{try{await refresh();$('env').value=defaults.env;properties=initial();renderProperties();/* Deliberately do not import example geographic paths or dates. */}catch(e){fail(e);}})();
setInterval(()=>{if(current&&!document.hidden)refresh().catch(fail);},3000);

// Shipped help stays available offline, including when a later API request fails.
(()=>{
 const dialog=node('dialog',undefined,'help-dialog');dialog.id='workbenchHelp';
 dialog.setAttribute('aria-labelledby','helpTitle');
 dialog.innerHTML=`<div class="card-heading"><div><p class="eyebrow">KEEP YOUR RESEARCH RUNNING</p><h2 id="helpTitle">使用與故障排查</h2></div><button id="closeHelp" type="button">關閉</button></div>
 <p class="notice">工作台與運算工具在你的電腦執行。ChatGPT／Codex 訂閱到期，不會使已安裝的工作台停止運作。</p>
 <details open><summary>每天怎麼開啟？</summary><p>開啟 SAR Workbench.app，按「準備並開啟工作台」，再從專案總覽選擇專案。使用原始碼版本時，則使用下載資料夾內的啟動檔。瀏覽器網址只在本機服務啟動時有效。</p><p>運算期間保持 App、電源及資料磁碟連線，避免闔蓋休眠。關閉瀏覽器頁面不等於停止運算；請在工作台使用「停止工作」。</p></details>
 <details><summary>網頁開不了／出現 Failed to fetch</summary><ol><li>確認啟動器仍在執行，再使用啟動器提供的網址。</li><li>重新整理網頁；若日誌暫時載入失敗，先查看其他設定與成果是否仍可開啟。</li><li>若已有運算在跑，先確認執行狀態，不要重複啟動同一個專案。</li></ol></details>
 <details><summary>外接硬碟拔掉後，專案不見了</summary><p>接回同一顆磁碟、等待掛載，再按專案總覽的「更新紀錄」。位置變動時，使用專案管理中的「重新連結專案資料夾」選回原專案；不要另外建立同名專案。資料仍在其他磁碟時，來源磁碟也須連線。找到搬移後的專案不代表所有處理路徑已修復，請依位置變更提示核對後再繼續。</p><p>「從工作台移除」只移除清單紀錄，不刪除磁碟上的研究資料。請勿為了清理清單直接刪除成果資料夾。</p></details>
 <details><summary>選檔視窗沒出現</summary><p>先確認其他 macOS 選擇視窗是否仍在等待。可以將「檔案選擇方式」切換成「網頁內建選擇器」，直接選取資料。取消選擇不會開始處理。</p></details>
 <details><summary>運算失敗或中斷，如何繼續？</summary><ol><li>記下目前階段和日誌最後的完整錯誤，確認磁碟空間及來源連線。</li><li>ALOS 多日期可開啟「恢復中心」查看診斷；其他流程依各自頁面的中斷說明處理。具有有效副本時，依提示核對後恢復；沒有副本時不要直接重跑半成品。</li><li>已統一取樣的專案，不要自行重新跑原本 cmd1 的 read_data，避免覆寫已確認的成果。</li><li>環境問題先到「工具與環境」檢查所選環境，再決定是否修復；不要一開始就重裝或刪除環境。</li></ol></details>
 <details><summary>更新、備份與解除安裝</summary><p>先完成或停止工作並結束 App。備份整個專案（包含隱藏的 .helper、proc、analyses）及外部來源，再更新啟動器或程式碼。只有專案捷徑／連結的備份不包含原始影像。</p><p>專用環境在新版本確認可用前先保留；既有專案不會因為下載新版而自動改用另一套環境。解除安裝請使用啟動器的「解除安裝運算工具」，核對路徑；不要直接刪除整個 sar-workbench 資料夾。</p></details>
 <details><summary>畫面顯示完成，代表結果可信嗎？</summary><p>「運算完成」只代表程式結束。ALOS 跨衛星流程仍需要影格對應、取樣成果、匹配品質及電離層判讀；MintPy 也需要確認參考點、配對網路與結果品質。未知資訊會保留為待確認。單軌 LOS 位移不能直接當作垂直沉降。</p></details>
 <p class="hint">這份說明隨程式保存，離線可讀。完整操作與已知限制：</p><div class="inline"><a href="https://github.com/chris20011026/sar-workbench/blob/main/docs/OPERATIONS.md" target="_blank" rel="noopener noreferrer">日常維護指南 ↗</a><a href="https://github.com/chris20011026/sar-workbench/releases" target="_blank" rel="noopener noreferrer">下載與版本紀錄 ↗</a><a href="https://github.com/chris20011026/sar-workbench/issues" target="_blank" rel="noopener noreferrer">回報問題 ↗</a></div><p class="hint">回報前請遮蔽帳號、憑證與私人路徑；附上版本、出錯階段及錯誤訊息即可。</p>`;
 document.body.append(dialog);
 $('openHelp').onclick=()=>{dialog.showModal();$('closeHelp').focus();};
 $('closeHelp').onclick=()=>{dialog.close();$('openHelp').focus();};
})();
