// One navigation and history surface; each scientific adapter keeps its own state.
(()=>{
 window.workspaceReady=true;
 const main=document.querySelector('main');
 const dashboard=node('section');dashboard.id='workspaceOverview';dashboard.hidden=true;
 dashboard.innerHTML=`<div class="ws-heading"><div><div class="eyebrow">RESEARCH WORKSPACE</div><h1>研究專案總覽</h1><p class="intro">在同一處查看資料準備、干涉處理與時間序列。選擇工作，接續目前的階段。</p></div><button id="overviewCreate" class="primary">＋ 建立專案</button></div>
 <div id="overviewMetrics" class="ws-metrics"></div><div class="ws-toolbar"><label>搜尋工作<input id="overviewSearch" type="search" placeholder="專案、流程或資料夾"></label><label>狀態<select id="overviewStatus"><option value="">全部狀態</option><option value="running">進行中</option><option value="attention">需要處理</option><option value="ready">等待開始</option><option value="complete">已完成</option><option value="unavailable">位置無法讀取</option></select></label><label>類型<select id="overviewKind"><option value="">全部工作</option><option value="project">ISCE2 專案</option><option value="nisar">NISAR 專案</option><option value="analysis">MintPy／品質報告</option><option value="conversion">格式轉換</option><option value="preparation">資料準備／教學</option></select></label><button id="overviewReload">更新紀錄</button></div><p id="overviewMessage" role="status" class="hint"></p><div id="overviewRecords" class="ws-records"></div>`;
 const chooser=node('section');chooser.id='workspaceCreate';chooser.hidden=true;
 chooser.innerHTML=`<div class="eyebrow">NEW RESEARCH PROJECT</div><h1>從資料選擇合適的流程。</h1><p class="intro">先辨識資料，再選擇研究目標。熟悉資料的使用者，也可以直接選擇下方流程。</p>
 <div class="card ws-source"><div class="card-heading"><span class="number">01</span><div><h2>辨識我的資料 <small>選填</small></h2><p>支援 ALOS、Sentinel-1 IW SLC、NISAR。只讀取描述資料。</p></div></div>
 <label>影像檔案或資料夾<span class="input-row"><input id="workspaceSource" placeholder="選擇含影像的資料夾，或一份 ZIP／HDF5"><button id="workspacePickDir">資料夾</button><button id="workspacePickFile">檔案</button></span></label>
 <label>辨識使用的環境<select id="workspaceSourceEnv"></select></label><div class="inline"><button id="workspaceIdentify" class="primary">辨識並篩選流程</button><button id="workspaceManual">直接選擇全部流程</button></div><p id="workspaceSourceStatus" role="status" class="hint"></p><div id="workspaceSourceReport"></div></div>
 <div class="ws-heading"><div><div class="eyebrow">02 / CHOOSE A WORKFLOW</div><h2>研究目標與處理方式</h2></div><label class="ws-goal">顯示<select id="workspaceGoal"><option value="all">全部流程</option><option value="pair">兩期比較</option><option value="stack">多日期分析</option><option value="prepare">資料準備</option></select></label></div>
 <p class="hint">「已實作」表示介面與執行流程已提供；仍需通過專案健檢與科學品質檢查。規劃中的流程不能啟動。</p><div id="workspaceFlows" class="ws-flows"></div><div class="notice">MintPy 由已完成的 ALOS-2 多日期專案接續，請從專案總覽開啟原專案，再建立分析版本。</div>`;
 main.append(dashboard,chooser);
 const registryNotice=node('div',undefined,'ws-registry-notice');registryNotice.id='registryNotice';registryNotice.hidden=true;registryNotice.setAttribute('role','status');
 $('overviewRecords').before(registryNotice);
 const removedPanel=node('details',undefined,'card ws-removed');removedPanel.id='removedProjects';removedPanel.hidden=true;
 removedPanel.innerHTML='<summary id="removedProjectsSummary">已移除的專案</summary><p class="hint">這裡只保留專案的清單紀錄。復原會讓專案重新顯示，不會救回已刪除的檔案。</p><div id="removedProjectsList"></div>';
 dashboard.append(removedPanel);
 const removeDialog=node('dialog',undefined,'ws-remove-dialog');removeDialog.id='removeProjectDialog';removeDialog.setAttribute('aria-labelledby','removeProjectTitle');removeDialog.setAttribute('aria-describedby','removeProjectDescription');
 removeDialog.innerHTML='<h2 id="removeProjectTitle">從工作台移除專案？</h2><p id="removeProjectName"></p><p id="removeProjectPath" class="ws-path"></p><p id="removeProjectDescription">專案及其 MintPy／品質報告會從總覽隱藏。磁碟上的資料、成果和科學環境會保留；之後可從「已移除的專案」復原清單紀錄。</p><p id="removeProjectError" class="error" role="alert"></p><div class="ws-dialog-actions"><button id="cancelProjectRemoval" autofocus>取消</button><button id="confirmProjectRemoval" class="ws-remove-button">移除紀錄</button></div>';
 document.body.append(removeDialog);
 const nav=['openOverview','newProject','openLibrary','openWelcome'];
 let snapshot=null,flows=[],probe=null,probeVersion=0,loading=null,environmentLoading=false,pendingRemoval=null,removing=false;
 const maturity={implemented:'已實作',experimental:'試驗中',planned:'規劃中',preparation:'僅資料準備'};
 const environmentNames={isce:'ISCE2',mintpy:'MintPy',isce3:'ISCE3／NISAR'};
 function navState(id){for(const key of nav){$(key).classList.toggle('active',key===id);if(key===id)$(key).setAttribute('aria-current','page');else $(key).removeAttribute('aria-current');}}
 window.leaveWorkspace=id=>{dashboard.hidden=true;chooser.hidden=true;navState(id);};
 function enter(panel,id,title){current=null;reviewLoaded=null;window.closeLibrary?.();window.closeMissions?.();for(const key of ['setup','project','welcome','workspaceOverview','workspaceCreate'])$(key).hidden=true;panel.hidden=false;navState(id);$('crumb').textContent=title;clear();window.scrollTo({top:0,behavior:'instant'});}
 function button(label,fn,cls){const b=node('button',label,cls);action(b,fn);return b;}
 function showEmpty(box,text,actionLabel,fn){const card=node('div',undefined,'card ws-empty');card.append(node('h2',text));if(actionLabel)card.append(button(actionLabel,fn,'primary'));box.append(card);}
 async function route(a){
  if(a.type==='project'){current=a.id;reviewLoaded=null;clear();await refresh();}
  else if(a.type==='analysis')await window.openAnalysisRecord(a.id,a.run);
  else if(a.type==='nisar'){await window.openNisarJob(a.id);navState('openOverview');$('crumb').textContent='專案總覽／NISAR 工作';}
  else if(a.type==='conversion'){await window.openAlosPreparation('',a.id);navState('openOverview');$('crumb').textContent='專案總覽／格式轉換工作';}
  else if(a.type==='preparation')await window.openPreparation(a.id);
 }
 function drawRecords(){
  if(!snapshot)return;const query=$('overviewSearch').value.trim().toLowerCase(),status=$('overviewStatus').value,kind=$('overviewKind').value;
  const selected=snapshot.records.filter(r=>(!status||r.group===status)&&(!kind||r.kind===kind)&&(!query||[r.name,r.path,r.workflow,r.stage].join(' ').toLowerCase().includes(query)));
  const box=$('overviewRecords');box.replaceChildren();
  $('overviewMessage').textContent=`顯示 ${selected.length} / ${snapshot.records.length} 筆工作。`+(snapshot.warnings.length?' '+snapshot.warnings.join('；'):'');
  if(!selected.length){showEmpty(box,snapshot.records.length?'沒有符合條件的工作':'還沒有研究專案',snapshot.records.length?'清除篩選':'建立第一個專案',snapshot.records.length?()=>{$('overviewSearch').value='';$('overviewStatus').value='';$('overviewKind').value='';drawRecords();}:window.openWorkspaceCreate);return;}
  for(const r of selected){
   const card=node('article',undefined,'card ws-record');card.append(node('span',r.status_label,'ws-status '+r.group),node('p',r.workflow||'專案紀錄','eyebrow'),node('h2',r.name));
   card.append(node('p',r.stage||'等待查看紀錄','ws-stage'));
   if(r.path)card.append(node('p',r.path,'ws-path'));
   if(r.environment){const details=node('details');details.append(node('summary','運算環境'),node('p',r.environment,'ws-path'));card.append(details);}
   if(r.error)card.append(node('p',r.error,'ws-error'));
   const footer=node('div',undefined,'ws-record-footer');
   footer.append(node('span',r.updated?new Date(r.updated*1000).toLocaleString('zh-TW'):'未記錄時間','hint'));
   const actions=node('div',undefined,'ws-record-actions');
   if(r.action)actions.append(button(['running','attention'].includes(r.group)?'查看目前階段':'開啟工作',()=>route(r.action)));
   if(r.registration){const remove=button('從工作台移除',()=>askRemove(r),'ws-remove-button');remove.disabled=r.registration.busy;remove.setAttribute('aria-label','從工作台移除「'+r.name+'」');if(remove.disabled)remove.title='請先等待專案與分析運算完成，或停止工作。';actions.append(remove);}
   footer.append(actions);
   if(!r.action)card.append(node('p','若資料已不需要，可從工作台移除這筆紀錄；暫時離線則可保留。','hint'));
   card.append(footer);box.append(card);
  }
 }
 function drawMetrics(){const box=$('overviewMetrics');box.replaceChildren();for(const [group,label,value] of [['','全部工作',snapshot.records.length],['running','進行中',snapshot.counts.running],['attention','需要處理',snapshot.counts.attention],['complete','已完成',snapshot.counts.complete]]){const b=button('',()=>{$('overviewStatus').value=group;drawRecords();},'ws-metric');b.append(node('strong',String(value)),node('span',label));box.append(b);}}
 function loadOverview(){if(loading)return loading;loading=(async()=>{try{const next=await api('/api/workspace');const changed=JSON.stringify(next)!==JSON.stringify(snapshot);snapshot=next;flows=snapshot.workflows;if(changed){drawMetrics();drawRecords();drawRemoved();}else if($('overviewMessage').dataset.failed){drawRecords();}delete $('overviewMessage').dataset.failed;}catch(e){$('overviewMessage').dataset.failed='true';$('overviewMessage').textContent='暫時無法更新；保留上次顯示的紀錄。'+e.message;}finally{loading=null;}})();return loading;}
 async function reloadRegistry(){if(loading)await loading;await loadOverview();}
 function registrationNotice(text,record){registryNotice.replaceChildren(node('span',text));if(record)registryNotice.append(button('復原清單紀錄',()=>restoreProject(record)));registryNotice.hidden=false;}
 function askRemove(record){pendingRemoval=record;$('removeProjectName').textContent=record.name;$('removeProjectPath').textContent=record.path;$('removeProjectError').textContent='';removeDialog.showModal();$('cancelProjectRemoval').focus();}
 $('cancelProjectRemoval').onclick=()=>removeDialog.close();removeDialog.addEventListener('cancel',e=>{if(removing)e.preventDefault();});
 action($('confirmProjectRemoval'),async()=>{
  if(removing||!pendingRemoval)return;removing=true;$('cancelProjectRemoval').disabled=$('confirmProjectRemoval').disabled=true;$('removeProjectError').textContent='';
  try{const record=await api('/api/project-remove',{id:pendingRemoval.registration.id});removeDialog.close();await reloadRegistry();registrationNotice('已從工作台移除「'+record.name+'」。',record);$('overviewReload').focus();}
  catch(e){$('removeProjectError').textContent=e.message;}
  finally{removing=false;$('cancelProjectRemoval').disabled=$('confirmProjectRemoval').disabled=false;}
 });
 async function restoreProject(record){
  const buttons=[...registryNotice.querySelectorAll('button'),...removedPanel.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
  try{await api('/api/project-restore',{token:record.token});await reloadRegistry();registrationNotice('已復原「'+record.name+'」的清單紀錄。若原資料夾已刪除，仍會顯示位置無法讀取。');$('overviewReload').focus();}
  finally{buttons.forEach(b=>b.disabled=false);}
 }
 function drawRemoved(){const rows=snapshot.removed_projects||[];removedPanel.hidden=!rows.length;$('removedProjectsSummary').textContent=`已移除的專案（${rows.length}）`;
  const box=$('removedProjectsList');box.replaceChildren();for(const r of rows){const row=node('div',undefined,'ws-removed-row'),info=node('div');info.append(node('strong',r.name),node('p',r.root,'ws-path'),node('small',new Date(r.removed_at*1000).toLocaleString('zh-TW'),'hint'));row.append(info,button('復原清單紀錄',()=>restoreProject(r)));box.append(row);}
 }
 window.openWorkspaceOverview=async()=>{enter(dashboard,'openOverview','專案總覽');await loadOverview();};
 action($('openOverview'),window.openWorkspaceOverview);action($('overviewReload'),loadOverview);
 $('overviewSearch').oninput=drawRecords;$('overviewStatus').onchange=$('overviewKind').onchange=drawRecords;
 setInterval(()=>{if(!dashboard.hidden&&!document.hidden)loadOverview();},5000);

 function flowGoal(f){return f.id==='alos4'?'prepare':f.id.endsWith('Stack')?'stack':'pair';}
 function drawFlows(){
  const box=$('workspaceFlows');box.replaceChildren();const goal=$('workspaceGoal').value;
  for(const f of flows.filter(f=>goal==='all'||flowGoal(f)===goal)){
   const compatible=!probe||probe.eligible.includes(f.id),enabled=f.available&&compatible;
   const card=node('article',undefined,'ws-flow'+(enabled?'':' ws-unavailable'));
   const top=node('div',undefined,'ws-flow-top');top.append(node('span',f.engine,'eyebrow'),node('span',maturity[f.maturity],'ws-status '+f.maturity));
   card.append(top,node('h3',f.name),node('p',f.description));
   if(probe&&!compatible&&f.available)card.append(node('p',probe.pending?'等待辨識結果…':'目前辨識的資料不適用此入口。','hint'));
   const b=button(f.maturity==='preparation'?'前往資料準備':f.available?'設定這個流程':'尚未開放',()=>begin(f),'primary');b.disabled=!enabled;card.append(b);box.append(card);
  }
 }
 window.renderWorkflowHeader=f=>{const h=$('setup').querySelector('h1');h.textContent=f.name;$('setup').querySelector('.intro').textContent=f.description;$('crumb').textContent='建立專案／'+f.name;};
 async function begin(f){
  if(!f.available||probe&&!probe.eligible.includes(f.id))return;
  const p=probe;
  if(f.id==='nisar'){
   const images=(p?.products||[]).filter(x=>x.type==='RSLC').sort((a,b)=>a.date.localeCompare(b.date));
   await window.openNisar(images.length===2?{reference:images[0].path,secondary:images[1].path}:{});
   $('missionNisarTab').click();
  }else if(f.id==='alos4')await window.openAlosPreparation(p?.source||'');
  else{
   newView();document.querySelector(`[data-workflow="${f.id}"]`).click();window.renderWorkflowHeader(f);
   if(p&&f.id==='topsApp'){
    const images=p.products.filter(x=>x.type==='IW SLC').sort((a,b)=>a.date.localeCompare(b.date));
    if(images.length===2)for(const [k,index] of [['reference_safe',0],['secondary_safe',1]]){$('tops-'+k).value=images[index].path;$('tops-'+k).dispatchEvent(new Event('input'));}
   }else if(p){
    if(p.products.some(x=>x.type==='CEOS ZIP')){window.openAlosImport(p.source);return;}
    $('source').value=p.source;
   }
  }
  window.scrollTo({top:0,behavior:'instant'});
 }
 window.openWorkspaceCreate=async()=>{
  enter(chooser,'newProject','建立專案');
  const state=await api('/api/state');window.workbenchCatalog?.(state);defaults=state.defaults;
  const old=$('workspaceSourceEnv').value;$('workspaceSourceEnv').replaceChildren();
  for(const e of defaults.environments.filter(e=>e.isce||e.isce3)){const o=node('option',e.name+' · '+(e.isce?'ISCE2':'ISCE3'));o.value=e.path;$('workspaceSourceEnv').append(o);}
  if([...$('workspaceSourceEnv').options].some(o=>o.value===old))$('workspaceSourceEnv').value=old;
  if(!flows.length){const data=await api('/api/workspace');flows=data.workflows;}
  drawFlows();
 };
 $('newProject').onclick=()=>window.openWorkspaceCreate().catch(fail);action($('overviewCreate'),window.openWorkspaceCreate);
 const back=button('← 更換處理流程',window.openWorkspaceCreate,'ws-back');$('setup').prepend(back);
 const missionBack=button('← 所有處理流程',window.openWorkspaceCreate,'ws-back');$('missionsPanel').prepend(missionBack);
 function sourceChanged(){probeVersion++;probe=null;$('workspaceSourceReport').replaceChildren();$('workspaceSourceStatus').textContent='來源已變更，請重新辨識。';drawFlows();}
 $('workspaceSource').oninput=$('workspaceSourceEnv').onchange=sourceChanged;
 action($('workspacePickDir'),()=>choosePath('dir',p=>{$('workspaceSource').value=p;sourceChanged();},$('workspaceSource').value||defaults.home));
 action($('workspacePickFile'),()=>choosePath('file',p=>{$('workspaceSource').value=p;sourceChanged();},defaults.home));
 action($('workspaceManual'),()=>{probeVersion++;probe=null;$('workspaceSourceReport').replaceChildren();$('workspaceSourceStatus').textContent='直接選擇流程；資料會在後續表單與專案健檢中確認。';drawFlows();});
 $('workspaceGoal').onchange=drawFlows;
 action($('workspaceIdentify'),async()=>{
  const version=++probeVersion,path=$('workspaceSource').value.trim();if(!path)throw Error('請先選擇資料。');probe={eligible:[],pending:true};drawFlows();$('workspaceIdentify').disabled=true;$('workspaceSourceStatus').textContent='正在辨識產品與日期，未執行影像處理…';$('workspaceSourceReport').replaceChildren();
  try{
   const data=await api('/api/workspace-identify',{path,environment:$('workspaceSourceEnv').value});if(version!==probeVersion)return;
   probe=data;$('workspaceSourceStatus').textContent=(data.families.join('／')||'未確認類型')+` · ${data.products.length} 份產品／日期紀錄`;
   const box=$('workspaceSourceReport');for(const warning of data.warnings)box.append(node('p',warning,'notice'));box.append(node('p',data.note,'hint'));
   if(data.products.length){const details=node('details');details.append(node('summary','查看辨識清單'));for(const p of data.products)details.append(node('p',`${p.sensor} · ${p.type} · ${p.date||'日期未確認'}\n${p.path}`,'ws-path'));box.append(details);}
   if(data.families.length===1&&data.families[0]==='ALOS-2')box.append(button('整理 ALOS-2 日期資料夾',()=>window.openAlosImport(data.source)));
   if(data.products.some(p=>p.type==='GUNW'))box.append(button('瀏覽已有成果',()=>window.viewNisarProduct(data.products.find(p=>p.type==='GUNW').path)));
   drawFlows();
  }catch(e){if(version===probeVersion){probe={eligible:[]};$('workspaceSourceStatus').textContent='辨識未完成：'+e.message;drawFlows();}}
  finally{$('workspaceIdentify').disabled=false;}
 });

 const envPanel=$('environmentPanel');envPanel.innerHTML=`<div class="card"><div class="card-heading"><div><h2>電腦上的科學環境</h2><p>環境偵測與基本驗證分開顯示，每個流程使用自己選定的環境。</p></div><button id="environmentRefresh">重新偵測</button></div><p id="environmentMessage" role="status" class="hint"></p><div id="environmentRows" class="ws-environments"></div></div>`;
 window.loadEnvironmentPanel=async()=>{
  if(environmentLoading)return;environmentLoading=true;
  try{const data=await api('/api/environment-status');drawEnvironments(data);}
  catch(e){$('environmentMessage').textContent='環境資訊暫時無法讀取：'+e.message;}
  finally{environmentLoading=false;}
 };
 function drawEnvironments(data){
  const rows=data.environments;window.detectedEnvironments=rows;
  $('environmentMessage').textContent=rows.length?`找到 ${rows.length} 個科學環境。可直接選擇用於專案；基本驗證只檢查匯入與入口，不安裝或更新套件。`:'尚未找到可辨識的科學環境。可使用下方安裝工具，或從專案設定指定環境路徑。';
  const box=$('environmentRows');box.replaceChildren();
  for(const e of rows){
   const card=node('article',undefined,'ws-environment'),top=node('div',undefined,'ws-flow-top');
   top.append(node('h3',e.name),node('span',{detected:'已偵測・尚未驗證',verified:'基本驗證通過',failed:'驗證需要處理',changed:'驗證已過期・請重驗'}[e.validation],'ws-status '+(e.validation==='verified'?'complete':e.validation==='failed'?'attention':'ready')));
   card.append(top,node('p',e.capabilities.map(k=>environmentNames[k]).join(' · ')),node('p',e.path,'ws-path'));
   if(e.report){const details=node('details');details.append(node('summary','驗證項目與時間'),node('p',new Date(e.report.checked_at*1000).toLocaleString('zh-TW'),'hint'));for(const c of e.report.checks)details.append(node('p',(c.ok?'✓ ':c.required===false?'○ 選用項目未提供：':'✕ ')+c.name+'：'+c.detail,c.ok||c.required===false?'hint':'error'));details.append(node('p',e.report.note,'hint'));card.append(details);if(e.report.checks.some(c=>!c.ok&&c.required===false))card.append(node('p','部分選用功能未提供，請展開驗證項目查看。','notice'));}
   const check=button('驗證這個環境',async()=>{
    document.querySelectorAll('.ws-environment button').forEach(b=>b.disabled=true);$('environmentMessage').textContent='正在驗證 '+e.name+'，不會執行衛星影像運算…';
    try{await api('/api/environment-check',{path:e.path});await window.loadEnvironmentPanel();}
    catch(error){$('environmentMessage').textContent=error.message;}
    finally{document.querySelectorAll('.ws-environment button').forEach(b=>b.disabled=false);}
   });card.append(check);box.append(card);
  }
 }
 action($('environmentRefresh'),window.loadEnvironmentPanel);
 window.openWorkspaceOverview().catch(fail);
})();
