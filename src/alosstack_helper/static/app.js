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
  if(controlled.includes(key))continue;
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
}

async function browse(path){const data=await api('/api/browse?path='+encodeURIComponent(path));$('browsePath').value=data.path;$('up').onclick=()=>browse(data.parent).catch(fail);$('files').replaceChildren();for(const item of data.entries){if(pickMode!=='file'&&!item.dir)continue;const b=node('button',(item.dir?'▸  ':'   ')+item.name,'file');b.onclick=async()=>{if(item.dir){try{await browse(item.path);}catch(e){fail(e);}}else{$('picker').close();try{await pickCallback(item.path);}catch(e){fail(e);}}};$('files').append(b);} }
let nativePending=false;
async function choosePath(mode,callback,start){
 if(nativePending)return;
 const useNative=localStorage.getItem('alos-picker')!=='web';
 if(useNative){
  nativePending=true;
  document.querySelectorAll('[data-pick],#importTemplate').forEach(b=>b.disabled=true);
  $('pickerStatus').textContent='請在 macOS 選擇視窗中選取，或按取消返回。';
  try{
   const result=await api('/api/pick',{mode,start:start||defaults.home});
   if(result.supported){if(!result.cancelled)await callback(result.path);return;}
  }catch(e){fail(e);}
  finally{nativePending=false;document.querySelectorAll('[data-pick],#importTemplate').forEach(b=>b.disabled=false);$('pickerStatus').textContent='';}
 }
 pickMode=mode;pickCallback=callback;$('chooseDir').hidden=mode==='file';$('files').textContent='正在讀取資料夾…';$('picker').showModal();await browse(start||defaults.home);
}
$('pickerPreference').value=localStorage.getItem('alos-picker')||'native';
$('pickerPreference').onchange=()=>localStorage.setItem('alos-picker',$('pickerPreference').value);
document.querySelectorAll('[data-pick]').forEach(b=>action(b,()=>choosePath(b.dataset.mode||'file',p=>{$(b.dataset.pick).value=b.dataset.mode==='newdir'?p+(window.selectedWorkflow==='alos2App'?'/alos2-pair':'/alos-project'):p;},$(b.dataset.pick).value||defaults.home)));
action($('chooseDir'),async()=>{$('picker').close();await pickCallback($('browsePath').value);});
action($('goPath'),()=>browse($('browsePath').value));$('closePicker').onclick=()=>$('picker').close();
async function importTemplate(path){const d=await api('/api/template',{path});properties={...initial(),...d.properties};renderProperties();$('templateNote').textContent='已載入：'+path+'。已保留明確設定；未設定或註解的參數使用程式預設值。資料路徑以表單為準。';}
action($('importTemplate'),()=>choosePath('file',importTemplate));
action($('addProperty'),()=>{const k=$('propName').value.trim();if(!k||controlled.includes(k))throw Error('請輸入其他參數名稱；資料路徑請在上方設定。');properties[k]=$('propValue').value;renderProperties();$('propName').value='';$('propValue').value='';});
async function scanDates(){const d=await api('/api/scan',{path:$('source').value});$('reference').replaceChildren(node('option','請選擇參考日期'));$('reference').firstChild.value='';$('dates').replaceChildren();for(const x of d.dates){const o=node('option',x.date);o.value=x.date;$('reference').append(o);$('dates').append(node('span',x.date+' · '+x.leaders+' LED / '+x.images+' IMG · frames '+(x.frames||[]).join(',')+' · '+(x.polarizations||[]).join('/')+(x.leaders&&x.images?'':' · 缺檔')+(x.warnings?.length?' · '+x.warnings.join('；'):''),'chip'));}$('scanSummary').textContent=`找到 ${d.dates.length} 個日期`;window.pairScanned?.(d.dates);if(!d.dates.length)throw Error('找不到 YYMMDD 資料夾，可使用「開始使用」中的匯入精靈。');return d.dates;}
action($('scan'),scanDates);
function projectConfig(){const c={};for(const k of ['name','root','source','env','dem','dem_geo','wbd','reference'])c[k]=$(k).value.trim();c.workflow=window.selectedWorkflow||'alosStack';c.copy=$('copy').checked;c.properties=Object.fromEntries(Object.entries(properties).filter(([k,v])=>v.trim()&&!controlled.includes(k)));if(c.workflow==='alos2App')Object.assign(c,window.pairFormOptions());const bounds=['checkSouth','checkNorth','checkWest','checkEast'].map(id=>$(id).value.trim());if(bounds.some(Boolean)){if(!bounds.every(Boolean))throw Error('完整影像範圍請填滿四個界線，或全部留白。');c.image_bounds=bounds.map(Number);}return c;}
const projectHealth=node('details',undefined,'card'),projectHealthNote=node('p',undefined,'hint'),projectHealthButton=node('button','重新檢查資料與環境'),projectHealthResults=node('div');projectHealth.append(node('summary','資料健檢與失敗處理'),projectHealthNote,projectHealthButton,projectHealthResults);$('project').insertBefore(projectHealth,$('project').querySelector('.project-settings'));
let healthProjectId=null;
window.preflightProject=s=>{if(healthProjectId!==s.id){healthProjectId=s.id;projectHealthResults.replaceChildren();}projectHealthButton.disabled=s.status==='running';const h=s.history?.at(-1);projectHealthNote.textContent=(h?'最後完成：'+h.stage+'。':'尚無已完成階段。')+(s.status==='failed'?(s.can_retry_prepare?'準備階段可重試：先修正健檢問題，再按「重新檢查並準備指令」。':'已保留半成品與日誌。重新健檢只檢查輸入，不會重跑；科學運算需先診斷失敗步驟。雙日期若提供檢查點續跑，仍需先核對檢查點。'):'這個檢查只讀取資料與描述檔，不會重新運算或修改成果。');};
action(projectHealthButton,async()=>{const id=current;projectHealthButton.disabled=true;try{const r=await api('/api/project-check',{id});if(current!==id)return;projectHealthResults.replaceChildren();for(const row of r.checks){const d=node('div',undefined,'health-row '+row.status);d.append(node('strong',({pass:'✓ 已確認',warning:'△ 請確認',error:'✕ 需修正'}[row.status])+' · '+row.title),node('p',row.detail,'hint'));projectHealthResults.append(d);}}finally{projectHealthButton.disabled=false;}});
const healthFields=node('details',undefined,'card');healthFields.append(node('summary','運算前健檢：完整影像範圍（可選）'),node('p','填寫資料提供者的整批 SAR 影像外框，核對 DEM／WBD 覆蓋。這不是研究區裁切；不知道可留白，摘要會提醒尚未確認。','hint'));
const healthGrid=node('div',undefined,'coordinate-fields');for(const [id,label] of [['checkSouth','南界（°N）'],['checkNorth','北界（°N）'],['checkWest','西界（°E）'],['checkEast','東界（°E）']]){const l=node('label',label),i=node('input');i.id=id;i.type='number';i.step='any';l.append(i);healthGrid.append(l);}healthFields.append(healthGrid);$('createForm').insertBefore(healthFields,$('createForm').querySelector('.footer-action'));
const healthDialog=node('dialog');healthDialog.id='preflightDialog';healthDialog.setAttribute('aria-labelledby','preflightTitle');const healthTitle=node('h2','開始前，確認這次處理內容');healthTitle.id='preflightTitle';healthDialog.append(healthTitle);const healthResults=node('div');healthResults.setAttribute('aria-live','polite');healthDialog.append(healthResults);const healthLabel=node('label',undefined,'check'),healthConsent=node('input');healthConsent.type='checkbox';healthLabel.append(healthConsent,document.createTextNode('我已閱讀需要人工確認的事項，了解檢查通過不代表科學品質已驗證。'));healthDialog.append(healthLabel);const healthActions=node('div',undefined,'inline'),healthConfirm=node('button','確認並建立專案','primary'),healthCancel=node('button','返回修改');healthActions.append(healthConfirm,healthCancel);healthDialog.append(healthActions);document.body.append(healthDialog);healthCancel.onclick=()=>healthDialog.close();
let healthSnapshot=null,healthReport=null;
healthConsent.onchange=()=>{healthConfirm.disabled=!healthReport?.can_proceed||(healthReport.needs_review&&!healthConsent.checked);};
action(healthConfirm,async()=>{if(JSON.stringify(projectConfig())!==healthSnapshot){healthDialog.close();throw Error('設定已改變，請重新送出健檢。');}healthConfirm.disabled=true;try{const s=await api('/api/create',{...JSON.parse(healthSnapshot),preflight_reviewed:healthConsent.checked,...(healthReport.confirmation_id?{confirmation_id:healthReport.confirmation_id}:{})});healthDialog.close();current=s.id;reviewLoaded=null;await refresh();}finally{healthConsent.onchange();}});
$('createForm').onsubmit=async e=>{e.preventDefault();clear();const submit=$('createForm').querySelector('[type="submit"]');submit.disabled=true;try{const c=projectConfig();healthSnapshot=JSON.stringify(c);healthReport=null;healthConsent.checked=false;healthConfirm.disabled=true;healthResults.replaceChildren(node('p','正在核對影像、配對規則與執行環境，請稍候…','notice'));healthDialog.showModal();healthReport=await api(c.workflow==='alosStack'?'/api/confirmation':'/api/preflight',c);if(JSON.stringify(projectConfig())!==healthSnapshot)throw Error('設定已改變，請返回重新預覽。');window.renderConfirmation(healthResults,healthReport,()=>healthDialog.close());healthConsent.checked=false;healthLabel.hidden=!healthReport.needs_review;healthConsent.onchange();}catch(e){healthConfirm.disabled=true;healthResults.append(node('p',e.message||String(e),'error'));fail(e);}finally{submit.disabled=false;}};
function newView(){$('openWelcome')?.classList.remove('active');$('newProject').classList.add('active');if($('welcome'))$('welcome').hidden=true;current=null;reviewLoaded=null;$('setup').hidden=false;$('project').hidden=true;$('crumb').textContent='建立專案';clear();}
$('newProject').onclick=newView;
const statusNames={new:'等待準備',ready:'準備完成',running:'執行中',review:'需要判讀',failed:'已停止',complete:'運算完成'};
statusNames.pair_ready='等待開始干涉';
function summary(s){$('summary').replaceChildren();const c=s.config;for(const [k,v] of Object.entries({'資料來源':c.source,'資料使用方式':c.copy?'複製與驗證':'直接讀取','參考日期':c.reference,...(c.workflow==='alos2App'?{'比較日期':c.secondary,'frame':c.frames.join('、'),'地理編碼範圍':c.bbox?.join('、')||'使用參考影像範圍'}:{}),'環境':c.env,'對位 DEM':c.dem,'地理編碼 DEM':c.dem_geo,'水體遮罩':c.wbd,...c.properties,...(s.ion?{'本次電離層估計':s.ion.estimate?'啟用':'關閉','本次套用校正':s.ion.estimate&&s.ion.apply?'啟用':'不執行',...(c.workflow==='alos2App'?{}:{'本次一般配對數':s.ion.subsequent,'本次電離層配對數':s.ion.subsequent_ion})}:{}),...Object.fromEntries(Object.entries(s.looks||{}).filter(([k])=>k.includes('lks')).map(([k,v])=>['本次生成 '+k,v]))})){$('summary').append(node('dt',labels[k]||k),node('dd',v));}}
async function refresh(){const data=await api('/api/state');projects=data.projects;defaults=data.defaults;window.workbenchState=data;window.workbenchCatalog?.(data);window.onboardingUpdate?.(data);$('projects').replaceChildren();for(const s of projects){const b=node('button',s.config.name||'未命名專案','nav'+(current===s.id?' active':''));b.onclick=async()=>{current=s.id;reviewLoaded=null;clear();try{await refresh();}catch(e){fail(e);}};$('projects').append(b);}if(!current)return;if($('welcome'))$('welcome').hidden=true;$('openWelcome')?.classList.remove('active');$('newProject').classList.remove('active');const s=projects.find(x=>x.id===current);if(!s)return;$('setup').hidden=true;$('project').hidden=false;$('crumb').textContent=s.config.name;$('projectName').textContent=s.config.name;$('projectPath').textContent=s.config.root;$('status').textContent=statusNames[s.status];$('stage').textContent=s.stage;$('error').textContent=s.error||'';summary(s);$('history').replaceChildren();for(const h of s.history)$('history').append(node('p',h.time+' · '+h.stage,'hint'));if(s.config.workflow==='alos2App'){await window.pairUpdate(s,data);await refreshLog();return;}if($('pairWorkbench'))$('pairWorkbench').hidden=true;$('timeline').replaceChildren();['準備專案','跑到檢查點','人工判讀','後半段'].forEach((x,i)=>$('timeline').append(node('div',`${i+1}  ${x}`,s.status==='review'&&i===2?'selected':'')));$('actions').replaceChildren();const add=(text,a)=>{const b=node('button',text,'primary');action(b,async()=>{b.disabled=true;try{if(a==='run'){await window.confirmProcessing(current);return;}await api('/api/action',{id:current,action:a});await refresh();}finally{b.disabled=false;}});$('actions').append(b);};if(s.status==='new')add('確認設定，準備資料與指令','prepare');if(s.status==='ready')add(s.ion?.estimate===false?'開始 cmd1、cmd2、cmd4（跳過電離層）':'開始 cmd1～3，跑到檢查點','run');if(s.status==='running'){const b=node('button','停止工作');action(b,()=>api('/api/stop',{}));$('actions').append(b);}if(s.status==='failed'){if(s.can_retry_prepare){add('重新檢查並準備指令','prepare');$('actions').append(node('p','尚未進入科學運算，可重試準備步驟。','hint'));}else $('actions').append(node('p','已保留日誌與現有輸出。請開啟「恢復中心」診斷；具備有效副本才可確認復原。','hint'));}$('review').hidden=s.status!=='review';$('continue').textContent=s.ion?.apply===false?'確認判讀，保留估計並跑 cmd4（不套用校正） →':'確認並執行後半段 →';if(s.status==='review'&&reviewLoaded!==current){await renderReview();reviewLoaded=current;}await Promise.all([refreshLog(),window.workbenchUpdate?.(s,data)]);}
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
