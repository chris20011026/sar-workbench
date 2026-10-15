// Bookmarks and display metadata are local; scientific project files stay intact.
(()=>{
 const dialog=node('dialog',undefined,'project-manager');dialog.id='projectManager';dialog.setAttribute('aria-labelledby','projectManagerTitle');
 dialog.innerHTML=`<div class="card-heading"><h2 id="projectManagerTitle">管理專案</h2><button id="closeProjectManager">關閉</button></div>
 <p id="managerError" class="error" role="alert"></p>
 <label>顯示名稱<input id="managerName" maxlength="100"></label><label>研究備註<textarea id="managerNotes" rows="3" maxlength="2000" placeholder="例如研究區、用途或資料來源"></textarea></label>
 <div class="inline"><button id="saveProjectDetails" class="primary">儲存名稱與備註</button><span id="managerSaved" role="status" class="hint"></span></div>
 <section class="storage-section"><div class="card-heading"><h3>儲存位置與連線</h3><button id="recheckProjectStorage">重新檢查連線</button></div><div id="managerConnection" role="status"></div><div id="managerCandidates"></div></section>
 <details class="storage-section" id="managerRelink"><summary>重新連結專案資料夾</summary><p class="hint">選擇原專案搬移後的位置。會核對專案代號，不搬移或複製影像。</p>
 <label>專案資料夾<span class="input-row"><input id="relinkPath" placeholder="選擇包含 proc 與 .helper 的資料夾"><button id="pickRelinkPath">選擇資料夾</button></span></label><button id="inspectRelink" class="storage-inspect">檢查這個位置</button><div id="relinkPreview"></div><button id="confirmRelink" class="primary" disabled>確認重新連結</button></details>
 <details class="storage-section"><summary>原始資料與地形路徑</summary><p class="hint">檢查來源、DEM、WBD 是否可讀。這裡不掃描影像內容，也不修改原處理設定。</p><div id="managerPaths"></div></details>`;
 const importer=node('dialog',undefined,'project-manager');importer.id='existingProjectDialog';importer.setAttribute('aria-labelledby','existingProjectTitle');
 importer.innerHTML=`<div class="card-heading"><h2 id="existingProjectTitle">加入既有專案</h2><button id="closeExistingProject">關閉</button></div><p>選擇以前由本工作台建立的 ISCE2 專案資料夾。大型影像和成果留在原位置。</p><p class="hint">這個入口加入現有的工作台紀錄；只有手動建立的 proc 或 XML，仍需另建工作台專案。</p><p id="existingError" class="error" role="alert"></p><label>專案資料夾<span class="input-row"><input id="existingPath" placeholder="包含 proc 與 .helper 的資料夾"><button id="pickExistingPath">選擇資料夾</button></span></label><button id="inspectExisting" class="storage-inspect">檢查這個專案</button><div id="existingPreview"></div><button id="confirmExisting" class="primary" disabled>加入清單</button>`;
 document.body.append(dialog,importer);
 const labels={online:'已連線',offline:'儲存裝置未連線',relocated:'已找到新位置',wrong_disk:'磁碟身分不符',unavailable:'位置無法讀取',missing:'找不到資料',unknown:'等待確認磁碟'};
 let key=null,details=null,generation=0,refreshing=false;
 const previews={relink:null,existing:null},versions={relink:0,existing:0};
 function localAction(id,error,fn){action($(id),async()=>{$(error).textContent='';try{await fn();}catch(e){$(error).textContent=e.message;}});}
 function drawConnection(d){
  const box=$('managerConnection');box.replaceChildren(node('span',labels[d.status]||d.status,'ws-status '+(d.status==='online'?'complete':'unavailable')));
  box.append(node('p',d.volume?'儲存裝置：'+d.volume.label:'儲存於本機或指定資料夾'),node('p',d.root,'ws-path'));
  if(d.reason)box.append(node('p',d.reason,'notice'));
  if(d.volume&&!d.volume.identity)box.append(node('p','尚未取得固定磁碟識別；可在原位置接回，或手動選擇專案資料夾核對。','hint'));
  if(d.original_root&&d.original_root!==d.root)box.append(node('p','專案已重新連結。原指令／檢查點仍可能使用下列位置，目前保留成果檢視，暫停啟動新運算：','notice'),node('p',d.original_root,'ws-path'));
  const candidates=$('managerCandidates');candidates.replaceChildren();
  for(const path of d.candidates||[]){const b=node('button','檢查找到的位置：'+path,'storage-candidate');action(b,async()=>{$('managerRelink').open=true;$('relinkPath').value=path;invalidate('relink');await inspect('relink');});candidates.append(b);}
  const paths=$('managerPaths');paths.replaceChildren();
  for(const row of d.paths||[]){const item=node('div',undefined,'storage-path-row');item.append(node('strong',row.label+' · '+(labels[row.status]||row.status)),node('p',row.path,'ws-path'));for(const path of row.candidates||[])item.append(node('p','找到新位置：'+path,'ws-path'));paths.append(item);}
  if(!d.paths?.length)paths.append(node('p','專案連線後可查看完整資料路徑。','hint'));
 }
 async function refreshDetails(){if(!key||refreshing)return;refreshing=true;const version=generation,id=key;try{const d=await api('/api/project-details?id='+encodeURIComponent(id));if(version!==generation||id!==key)return;if(JSON.stringify(details)!==JSON.stringify(d))drawConnection(d);details=d;}finally{refreshing=false;}}
 window.openProjectManager=async id=>{
  generation++;key=id;details=null;$('managerError').textContent='';$('managerSaved').textContent='';$('managerConnection').textContent='正在檢查連線…';$('managerCandidates').replaceChildren();$('managerPaths').replaceChildren();$('managerRelink').open=false;$('relinkPath').value='';invalidate('relink');
  const version=generation,d=await api('/api/project-details?id='+encodeURIComponent(id));if(version!==generation)return;
  details=d;$('managerName').value=d.name;$('managerNotes').value=d.notes||'';drawConnection(d);dialog.showModal();
 };
 window.openExistingProject=()=>{$('existingError').textContent='';$('existingPath').value='';invalidate('existing');importer.showModal();};
 $('closeProjectManager').onclick=()=>dialog.close();$('closeExistingProject').onclick=()=>importer.close();
 dialog.addEventListener('close',()=>{generation++;key=null;});
 localAction('saveProjectDetails','managerError',async()=>{
  const id=key;$('saveProjectDetails').disabled=true;
  try{await api('/api/project-edit',{id,name:$('managerName').value,notes:$('managerNotes').value});$('managerSaved').textContent='已儲存；資料夾名稱與處理設定保持原樣。';await window.refreshWorkspaceRegistry?.();if(current===id)await refresh();}finally{$('saveProjectDetails').disabled=false;}
 });
 localAction('recheckProjectStorage','managerError',async()=>{await api('/api/storage-refresh',{});await refreshDetails();await window.refreshWorkspaceRegistry?.();});
 function invalidate(mode){versions[mode]++;previews[mode]=null;$(mode+'Preview').replaceChildren();$(mode==='relink'?'confirmRelink':'confirmExisting').disabled=true;}
 for(const mode of ['relink','existing']){
  $(mode+'Path').oninput=()=>invalidate(mode);
  localAction(mode==='relink'?'pickRelinkPath':'pickExistingPath',mode==='relink'?'managerError':'existingError',()=>choosePath('dir',async path=>{$(mode+'Path').value=path;invalidate(mode);await inspect(mode);},details?.root||defaults.home));
  localAction(mode==='relink'?'inspectRelink':'inspectExisting',mode==='relink'?'managerError':'existingError',()=>inspect(mode));
 }
 async function inspect(mode){
  invalidate(mode);const version=versions[mode],id=key,value=$(mode+'Path').value.trim();if(!value)throw Error('請先選擇專案資料夾。');const box=$(mode+'Preview');box.textContent='正在核對專案代號與位置…';
  try{const d=await api('/api/project-preview',{path:value,...(mode==='relink'?{id}: {})});if(version!==versions[mode]||mode==='relink'&&id!==key)return;
   previews[mode]=d;box.replaceChildren();const card=node('div',undefined,'notice');card.append(node('strong',d.name),node('p',d.workflow+' · '+d.stage),node('p',d.path,'ws-path'));
   if(d.current_root&&d.current_root!==d.path)card.append(node('p','清單已登記相同專案代號；確認後會切換到所選位置，不合併兩份資料。'));
   card.append(node('p',d.relocated?'原指令或檢查點仍可能引用舊路徑。連結後可檢視既有成果；原運算需恢復原位置或另建處理專案。':'專案主資料夾與原設定位置一致。接續運算前仍會檢查來源資料與環境。'));
   box.append(card);$(mode==='relink'?'confirmRelink':'confirmExisting').disabled=false;
  }catch(e){if(version===versions[mode])box.textContent='';throw e;}
 }
 for(const mode of ['relink','existing'])localAction(mode==='relink'?'confirmRelink':'confirmExisting',mode==='relink'?'managerError':'existingError',async()=>{
  const preview=previews[mode];if(!preview)throw Error('請先檢查所選位置。');const button=$(mode==='relink'?'confirmRelink':'confirmExisting');button.disabled=true;
  try{await api('/api/project-attach',{id:preview.id,path:preview.path});(mode==='relink'?dialog:importer).close();await window.openWorkspaceOverview();await window.refreshWorkspaceRegistry?.();}finally{button.disabled=false;}
 });
 setInterval(()=>{if(dialog.open&&!document.hidden)refreshDetails().catch(e=>{$('managerError').textContent=e.message;});},5000);

 const banner=node('div',undefined,'notice storage-project-banner');banner.id='projectStorageBanner';banner.hidden=true;$('projectPath').after(banner);
 window.projectStorageUpdate=(s,data)=>{
  banner.replaceChildren();banner.hidden=!s.storage?.relocated;
  if(s.storage?.relocated){data.busy=true;banner.append(node('p','專案已重新連結，可檢視既有成果。原指令與檢查點可能仍引用舊位置；啟動運算前需恢復原位置或另建處理專案。'));const b=node('button','檢查儲存位置');action(b,()=>window.openProjectManager(s.id));banner.append(b);}
 };
 window.projectBecameUnavailable=async()=>{await window.openWorkspaceOverview();};
 const manage=node('button','管理專案','storage-project-manage');action(manage,()=>window.openProjectManager(current));$('projectPath').after(manage);
})();
