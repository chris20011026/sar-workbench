// A copy is never a registry switch until the user confirms the verified result.
(()=>{
 const dialog=node('dialog',undefined,'project-manager migration-dialog');dialog.id='migrationDialog';dialog.setAttribute('aria-labelledby','migrationTitle');
 dialog.innerHTML=`<div class="card-heading"><div><p class="eyebrow">PROJECT TRANSFER</p><h2 id="migrationTitle">安全搬移已完成專案</h2></div><button id="migrationClose">關閉</button></div>
 <p>選擇新位置，複製並驗證成果，再確認切換工作台。原專案會保留，這次操作不會釋出原磁碟空間。</p>
 <ol class="migration-steps" aria-label="搬移步驟"><li>1 選擇位置</li><li>2 預覽與複製</li><li>3 驗證內容</li><li>4 確認切換</li></ol>
 <p id="migrationError" class="error" role="alert"></p>
 <section id="migrationSetup"><label>存放位置<span class="input-row"><input id="migrationParent" placeholder="選擇外接硬碟或另一個資料夾"><button id="migrationPick">選擇資料夾</button></span></label><label>新資料夾名稱<input id="migrationName" maxlength="160"></label><p class="hint">會在所選位置建立新的資料夾；已存在的資料夾不會被覆寫。</p><button id="migrationPlan" class="primary">預覽搬移內容</button></section>
 <section id="migrationRecord" hidden><div class="migration-locations"><div><small>原專案 · 保留</small><p id="migrationSource" class="ws-path"></p></div><div><small>目的位置 · 新副本</small><p id="migrationDestination" class="ws-path"></p></div></div>
 <h3 id="migrationStatus" role="status"></h3><div class="migration-stats" id="migrationStats"></div><progress id="migrationProgress" max="1" aria-label="搬移進度"></progress><p id="migrationCurrent" class="hint ws-path"></p>
 <details id="migrationDependencies"><summary id="migrationDependencyTitle">外部資料與連結</summary><p class="hint">只複製專案資料夾內的檔案。以下外部資料仍留在原位置，連結不代表資料也被複製；清單包含已設定的來源與檔案連結，不會解析所有科學程式內部路徑。</p><div id="migrationDependencyRows"></div></details>
 <p class="notice">搬移後可查看既有成果。ISCE2 指令、檢查點與運算設定保留原樣，暫不支援在新位置接續科學運算；請保留原專案與外部資料。</p>
 <p id="migrationSpaceWarning" class="notice" hidden></p><p id="migrationResumeHint" class="hint" hidden>接續時會重新核對已完成檔案；中斷時正在複製的單一檔案會重新複製。完整內容驗證需要讀取兩邊資料，可能花較長時間。</p>
 <label id="migrationConsentRow" class="check"><input id="migrationConsent" type="checkbox"><span id="migrationConsentText">我已確認目的位置與外部資料仍需保留</span></label>
 <div class="migration-actions"><button id="migrationCopy" class="primary" hidden>開始複製並驗證</button><button id="migrationSwitch" class="primary" hidden>再次驗證並切換位置</button><button id="migrationPause" hidden>暫停搬移</button><button id="migrationReplan" hidden>重新預覽</button><button id="migrationChange" hidden>更換目的位置</button><button id="migrationDone" hidden>返回工作空間</button></div>
 <details id="migrationAbandonPanel"><summary>結束這次搬移紀錄</summary><p>原專案與已建立的副本都會保留。未完成的副本不能直接加入工作台；結束後如需重做，請選擇另一個新資料夾。</p><label class="check"><input id="migrationAbandonConsent" type="checkbox">保留所有檔案，結束這次紀錄</label><button id="migrationAbandon" disabled>結束紀錄</button></details></section>
 <p class="hint">關閉視窗仍會繼續工作；關閉工作台會暫停。下次從「管理專案 → 安全搬移」接續。</p>`;
 document.body.append(dialog);
 const active=new Set(['planning','copying','verifying','switch_check','switch_ready']);
 const titles={planning:'正在整理檔案清單',preview:'預覽完成 · 請確認目的位置',copying:'正在複製與核對檔案',verifying:'正在比對兩份專案的完整內容',ready:'驗證通過 · 等待你確認切換',switch_check:'切換前再次驗證內容',switch_ready:'正在切換工作台位置',complete:'已切換到新位置 · 原專案已保留',paused:'搬移已暫停',failed:'搬移停止 · 需要檢查'};
 let key=null,record=null,generation=0,polling=false,lastPhase='',lastTransfer='',source='';
 const size=n=>{if(!Number.isFinite(n))return '—';const units=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<4){n/=1024;i++;}return n.toFixed(i?1:0)+' '+units[i];};
 function render(r){
  record=r;const phase=r?.status||'',changed=lastPhase!==phase||lastTransfer!==r?.transfer;
  if(changed){$('migrationConsent').checked=false;$('migrationAbandonConsent').checked=false;lastPhase=phase;lastTransfer=r?.transfer||'';}
  $('migrationSetup').hidden=!!r;$('migrationRecord').hidden=!r;if(!r)return;
  $('migrationSource').textContent=r.source;$('migrationDestination').textContent=r.destination;$('migrationStatus').textContent=titles[phase]||phase;
  if(changed||r.error)$('migrationError').textContent=r.error||'';
  const step=phase==='planning'?0:phase==='preview'?1:phase==='copying'?1:phase==='verifying'?2:3;
  for(const [i,item] of [...dialog.querySelectorAll('.migration-steps li')].entries()){if(i===step)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');}
  $('migrationSpaceWarning').hidden=phase!=='preview';$('migrationSpaceWarning').textContent=`估計需要 ${size(r.required)}（含 64 MB 餘裕）。`+(r.space_ok===false?'預覽時空間不足；請先釋出空間，開始複製時會再次檢查。':'複製前會再次檢查磁碟空間。');
  const stats=$('migrationStats');stats.replaceChildren();
  for(const [label,value] of [['檔案總量',size(r.bytes)],['預覽時可用空間',size(r.free)],['已核對檔案',(phase==='verifying'||phase==='switch_check'?r.verified:r.copied||0)+' / '+r.files]]){const box=node('div');box.append(node('small',label),node('strong',value));stats.append(box);}
  const progress=$('migrationProgress');progress.hidden=!active.has(phase);if(phase==='planning'||!r.files)progress.removeAttribute('value');else progress.value=Math.min(1,(phase==='verifying'||phase==='switch_check'?r.verified||0:r.copied||0)/r.files);
  $('migrationCurrent').textContent=(r.current||'')+(active.has(phase)&&r.file_size?' · '+size(r.file_bytes)+' / '+size(r.file_size):'');
  $('migrationResumeHint').hidden=!['paused','failed','copying','verifying','switch_check'].includes(phase);
  $('migrationDependencies').hidden=!('dependencies' in r);
  if(changed){
   const rows=$('migrationDependencyRows');rows.replaceChildren();
   $('migrationDependencyTitle').textContent=`外部資料 ${r.dependencies?.length||0} 項 · 外部連結 ${r.external_links||0} 個`;
   for(const d of r.dependencies||[]){const row=node('div',undefined,'storage-path-row');row.append(node('strong',d.label),node('p',d.path,'ws-path'));rows.append(row);}
   for(const d of r.external||[]){const row=node('div',undefined,'storage-path-row');row.append(node('strong',d.path),node('p',d.target,'ws-path'));rows.append(row);}
   if(!rows.childElementCount)rows.append(node('p','沒有發現專案設定或符號連結指向外部資料。'));
   if(r.external_links>50)rows.append(node('p','此處列出前 50 個外部連結；完整清單已保留於搬移驗證紀錄。'));
   rows.append(node('p',`專案內部連結 ${r.internal_links||0} 個，副本會維持內部對應。`,'hint'));
  }
  const resume=['paused','failed'].includes(phase)&&r.mode!=='plan';
  $('migrationCopy').hidden=phase!=='preview'&&!resume;$('migrationCopy').textContent=resume?'接續複製與驗證':'開始複製並驗證';
  $('migrationSwitch').hidden=phase!=='ready';$('migrationPause').hidden=!active.has(phase);$('migrationDone').hidden=phase!=='complete';
  $('migrationConsentRow').hidden=$('migrationCopy').hidden&&$('migrationSwitch').hidden;
  $('migrationConsentText').textContent=phase==='ready'?'我確認切換到已驗證的新位置，並保留原專案與外部資料':'我已確認目的位置與外部資料仍需保留';
  $('migrationReplan').hidden=!(r.mode==='plan'&&['paused','failed'].includes(phase));$('migrationPause').textContent=phase==='planning'?'停止預覽':'暫停搬移';$('migrationChange').hidden=active.has(phase)||!!r.destination_identity;$('migrationAbandonPanel').hidden=active.has(phase)||!r.destination_identity;controls();
 }
 function controls(){
  $('migrationCopy').disabled=!$('migrationConsent').checked;
  $('migrationSwitch').disabled=!$('migrationConsent').checked;
  $('migrationAbandon').disabled=!$('migrationAbandonConsent').checked;
 }
 $('migrationConsent').onchange=controls;$('migrationAbandonConsent').onchange=controls;
 function run(id,fn){action($(id),async()=>{const version=generation;$(id).disabled=true;$('migrationError').textContent='';try{const d=await fn();if(version===generation&&d&&'transfer' in d)render(d.transfer);}catch(e){if(version===generation)$('migrationError').textContent=e.message;}finally{if(version===generation){$(id).disabled=false;controls();}}});}
 async function poll(){if(!key||polling)return;polling=true;const version=generation,id=key;try{const d=await api('/api/migration?id='+encodeURIComponent(id));if(version===generation)render(d.transfer);}finally{polling=false;}}
 window.openProjectMigration=async(id,d)=>{generation++;key=id;record=null;lastPhase='';lastTransfer='';source=d.root;for(const button of dialog.querySelectorAll('button'))button.disabled=false;$('migrationError').textContent='';$('migrationParent').value='';$('migrationName').value=(source.split('/').pop()||'project')+'-copy';render(null);dialog.showModal();try{await poll();}catch(e){$('migrationError').textContent=e.message;}};
 $('migrationClose').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{key=null;generation++;});
 run('migrationPick',()=>{const version=generation;return choosePath('dir',path=>{if(version===generation)$('migrationParent').value=path;},$('migrationParent').value||source||defaults.home);});
 run('migrationPlan',async()=>{const d=await api('/api/migration-plan',{id:key,parent:$('migrationParent').value,name:$('migrationName').value});return d;});
 for(const [id,mode] of [['migrationCopy','copy'],['migrationSwitch','switch']])run(id,async()=>{const d=await api('/api/migration-'+mode,{id:key,transfer:record.transfer,consent:$('migrationConsent').checked});return d;});
 run('migrationPause',async()=>{const d=await api('/api/migration-stop',{id:key});return d;});
 run('migrationReplan',()=>{const split=record.destination.lastIndexOf('/');return api('/api/migration-plan',{id:key,parent:record.destination.slice(0,split)||'/',name:record.destination.slice(split+1)});});
 run('migrationChange',()=>api('/api/migration-abandon',{id:key,transfer:record.transfer}));
 run('migrationAbandon',async()=>{if(!$('migrationAbandonConsent').checked)return;const d=await api('/api/migration-abandon',{id:key,transfer:record.transfer});return d;});
 run('migrationDone',async()=>{dialog.close();await window.openWorkspaceOverview();await window.refreshWorkspaceRegistry?.();});
 window.workspaceMigrationUpdate=r=>{
  let banner=$('workspaceMigrationBanner');if(!banner){banner=node('div',undefined,'ws-registry-notice');banner.id='workspaceMigrationBanner';$('overviewMetrics').before(banner);}
  banner.hidden=!r;if(!r)return;banner.replaceChildren();const text=node('div');text.append(node('strong','專案安全搬移進行中'),node('p',(titles[r.status]||r.status)+' · '+(r.source.split('/').pop()),'hint'));const button=node('button','查看搬移進度');action(button,()=>window.openProjectMigration(r.id,{root:r.source}));banner.append(text,button);
 };
 setInterval(()=>{if(dialog.open&&!document.hidden)poll().catch(e=>{$('migrationError').textContent=e.message;});},1200);
})();
