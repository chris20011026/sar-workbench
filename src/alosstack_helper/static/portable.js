/* Portable downstream inputs are copied and verified, never executed on import. */
(()=>{
 const dialog=node('dialog',undefined,'portable-dialog');dialog.id='portableDialog';dialog.setAttribute('aria-labelledby','portableTitle');
 dialog.innerHTML=`<div class="portable-heading"><span class="portable-symbol" aria-hidden="true">⇄</span><div><p class="eyebrow">PORTABLE ANALYSIS</p><h2 id="portableTitle" tabindex="-1">可攜分析包</h2></div><button id="portableClose" aria-label="關閉可攜分析包視窗">關閉</button></div>
 <p id="portableIntro" class="portable-intro"></p>
 <ol class="portable-route" aria-label="可攜分析流程"><li><b>01</b><span>選擇位置</span></li><li><b>02</b><span>檢查內容</span></li><li><b>03</b><span>複製與驗證</span></li><li><b>04</b><span>建立新分析</span></li></ol>
 <div class="portable-scope"><div><span aria-hidden="true">▥</span><strong>干涉與幾何成果</strong><small>帶走新分析所需的完整影像</small></div><div><span aria-hidden="true">✓</span><strong>完整檔案核對</strong><small>逐檔 SHA-256，原檔保留</small></div><div><span aria-hidden="true">↗</span><strong>新專案、新分析</strong><small>重新選環境與確認品質</small></div></div>
 <p id="portableError" class="error" role="alert" hidden></p><button id="portableLast" class="portable-last" hidden>查看上一次打包／匯入</button>
 <form id="portableForm"><fieldset id="portableFields"><legend class="sr-only">選擇分析包與新位置</legend>
 <div id="portableExportSource"><label>來源專案</label><p id="portableProjectName"></p><p id="portableProjectPath" class="ws-path hint"></p></div>
 <div id="portableImportFields"><label>分析包資料夾<span class="input-row"><input id="portableSource" placeholder="選擇包含 sar-analysis-package.json 的完整資料夾"><button id="portablePickSource" type="button">選擇資料夾</button></span></label><label>新專案名稱<input id="portableName" maxlength="100" placeholder="例如：台北移地分析"></label><label>分析環境<select id="portableEnvironment"></select></label><p class="hint">使用這台電腦已安裝的 ISCE2／MintPy 環境。</p></div>
 <label>存放位置<span class="input-row"><input id="portableParent" placeholder="選擇本機或外接硬碟"><button id="portablePickParent" type="button">選擇資料夾</button></span></label>
 <label id="portableFolderLabel">新資料夾名稱<input id="portableFolder" maxlength="120"></label><p id="portableDestination" class="hint ws-path"></p>
 </fieldset><button id="portablePreview" class="primary" type="submit">檢查並預覽</button></form>
 <section id="portableRecord" hidden aria-label="可攜分析工作"><div class="portable-status"><span id="portableBadge" class="badge"></span><h3 id="portableStage" role="status" aria-live="polite"></h3></div><div id="portableStats" class="portable-stats"></div><progress id="portableProgress" max="1" aria-label="複製與驗證進度" hidden></progress><p id="portableCurrent" class="hint ws-path"></p><div id="portableLocations"></div><p id="portableSpace" class="hint"></p>
 <details id="portableDetails"><summary>檢視日期、配對與處理紀錄說明</summary><div id="portableContents"></div></details>
 <label id="portableConsentRow" class="check"><input id="portableConsent" type="checkbox"><span id="portableConsentText"></span></label>
 <div class="portable-actions"><button id="portableStart" class="primary" disabled>確認並開始</button><button id="portableStop" hidden>停止這次工作</button><button id="portableReset" hidden>重新設定</button><button id="portableImportResult" hidden>在這台電腦匯入此包</button><button id="portableOpenProject" class="primary" hidden>開啟新專案</button></div><p id="portableNext" class="portable-next" hidden></p></section>
 <details class="portable-limits"><summary>分析包與整個專案搬移有何不同？</summary><p>分析包包含標準 alosStack 的解纏、遮罩解纏、相干性、連通區、地形與地理定位影像，以及必要的描述資料。匯入後可重新進行品質檢查與 MintPy，不依賴舊電腦上的影像路徑。</p><p>原始 SLC、前處理中間檔、舊 MintPy 結果與可執行檢查點不在此包內；既有設定與處理歷史僅供追溯。若要保留整個成果資料夾，請使用「安全搬移」。</p><p>目前支援已完成或已匯入的標準 alosStack 多日期成果。匯入與檔案驗證不代表科學品質通過；新分析仍保留人工判讀關卡。</p></details>
 <p class="hint portable-footer">關閉視窗仍會繼續複製。請保持來源與目的磁碟連線；停止或關閉工作台後，未完成的副本不能匯入。</p>`;
 document.body.append(dialog);
 const live=new Set(['planning','running','copying','verifying','importing']);
 const labels={planning:'檢查中',ready:'等待確認',running:'處理中',copying:'複製中',verifying:'驗證中',importing:'匯入中',complete:'已完成',failed:'需要檢查',stopped:'已停止',interrupted:'已中斷'};
 let mode='import',key=null,details=null,job=null,lastJob=null,generation=0,requestPending=false,polling=false,seen='',completion='';
 const size=n=>{if(!Number.isFinite(n))return '—';const units=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<4){n/=1024;i++;}return n.toLocaleString('zh-TW',{maximumFractionDigits:i?1:0})+' '+units[i];};
 const list=v=>Array.isArray(v)?v:[];
 function error(value){$('portableError').textContent=value||'';$('portableError').hidden=!value;}
 function destination(){const parent=$('portableParent').value.trim().replace(/\/$/,'');return parent?parent+'/'+$('portableFolder').value.trim():'';}
 function destinationLabel(){$('portableDestination').textContent=destination()?'將建立：'+destination():'';}
 function environments(){const select=$('portableEnvironment'),before=select.value;select.replaceChildren();const envs=(window.detectedEnvironments||defaults.environments||[]).filter(e=>e.isce&&e.mintpy);for(const env of envs){const option=node('option',env.name+' · ISCE2／MintPy');option.value=env.path;select.append(option);}if(envs.some(e=>e.path===before))select.value=before;else if(envs.some(e=>e.path===defaults.env))select.value=defaults.env;if(!envs.length){const o=node('option','請先在「工具與環境」安裝或檢查環境');o.value='';select.append(o);}}
 function setMode(next){mode=next;$('portableTitle').textContent=mode==='export'?'建立可攜分析包':'匯入可攜分析包';$('portableIntro').textContent=mode==='export'?'把完成的成果整理成獨立資料包，帶到另一台電腦繼續分析。':'選擇完整分析包，在新位置建立獨立專案。原包與原專案會保留。';$('portableExportSource').hidden=mode!=='export';$('portableImportFields').hidden=mode!=='import';$('portablePreview').textContent=mode==='export'?'預覽要帶走的內容':'檢查分析包';$('portableStart').textContent=mode==='export'?'確認打包並驗證':'確認匯入為新專案';$('portableConsentText').textContent=mode==='export'?'確認複製以上分析輸入；原專案保留，這不是包含所有中間檔的完整備份。':'確認建立新的獨立專案；接下來會重新檢查品質，並由我確認分析設定。';}
 function controls(){const active=live.has(job?.status);$('portableFields').disabled=requestPending||active;$('portablePreview').disabled=requestPending||active;$('portableStart').disabled=requestPending||job?.status!=='ready'||!$('portableConsent').checked||(job?.preview?.free!=null&&job?.preview?.required!=null&&job.preview.free<job.preview.required);$('portableReset').disabled=requestPending;$('portableStop').disabled=requestPending;}
 function setStep(step){for(const [i,item] of [...dialog.querySelectorAll('.portable-route li')].entries()){if(i===step)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');}}
 function showFromTop(){if(!dialog.open)dialog.showModal();$('portableTitle').focus({preventScroll:true});dialog.scrollTop=0;}
 function reset(next,source=''){
  generation++;job=null;seen='';completion='';requestPending=false;setMode(next);setStep(0);error('');$('portableForm').hidden=false;$('portableRecord').hidden=true;$('portableConsent').checked=false;$('portableLast').hidden=!lastJob;$('portableSource').value=source;$('portableName').value='';$('portableParent').value='';$('portableFolder').value=mode==='export'?(details?.root?.split('/').pop()||'alos-stack')+'-analysis-package':'portable-alos-analysis';$('portableProjectName').textContent=details?.name||'已完成的 alosStack 成果';$('portableProjectPath').textContent=details?.root||'';environments();destinationLabel();controls();dialog.scrollTop=0;if(dialog.open)showFromTop();
 }
 function render(value){
  if(!value)return;job=value;lastJob=value;if(value.mode&&value.mode!==mode)setMode(value.mode);
  const changed=seen!==value.job_id+':'+value.status;seen=value.job_id+':'+value.status;
  if(changed)$('portableConsent').checked=false;
  $('portableForm').hidden=true;$('portableRecord').hidden=false;$('portableLast').hidden=true;error(value.error);
  const p=value.preview||{},active=live.has(value.status),complete=value.status==='complete';
  if(mode==='export'){key=value.source_id||value.id||key;if(p.source)details={...(details||{}),root:p.source,name:value.name||details?.name};}
  $('portableBadge').textContent=labels[value.status]||value.status;$('portableBadge').className='badge '+(complete?'done':['failed','stopped','interrupted'].includes(value.status)?'warn':'');$('portableStage').textContent=value.stage||labels[value.status]||'';
  const step=value.status==='planning'?1:value.status==='ready'?1:complete?3:2;setStep(step);
  const stats=$('portableStats');stats.replaceChildren();
  for(const [title,text] of [['資料量',size(p.bytes)],['日期／配對',Array.isArray(p.dates)?p.dates.length+'／'+(Array.isArray(p.pairs)?p.pairs.length:p.pairs??'—'):'—'],['檔案',p.files??'—']]){const box=node('div');box.append(node('small',title),node('strong',String(text)));stats.append(box);}
  const progress=value.progress||value,bar=$('portableProgress');bar.hidden=!active;
  if(p.bytes>0&&Number.isFinite(progress.bytes_done))bar.value=Math.max(0,Math.min(.99,progress.bytes_done/p.bytes));else if(p.files>0&&Number.isFinite(progress.files_done))bar.value=Math.max(0,Math.min(.99,progress.files_done/p.files));else bar.removeAttribute('value');
  $('portableCurrent').textContent=progress.current||value.current||'';
  const loc=$('portableLocations');loc.replaceChildren();for(const [title,path] of [['來源',p.source||value.source],['新位置',p.destination||value.destination],...(['failed','stopped','interrupted'].includes(value.status)&&progress.staging_path?[['未完成副本（已保留）',progress.staging_path]]:[])])if(path){const row=node('div',undefined,'portable-location');row.append(node('small',title),node('p',path,'ws-path'));loc.append(row);}
  $('portableSpace').textContent=p.required!=null?'預計需要 '+size(p.required)+' · 可用 '+size(p.free)+(p.free<p.required?'。可用空間不足，請改用其他位置或釋出空間後重新預覽。':'。會建立完整獨立副本。'):'';
  $('portableDetails').hidden=!value.preview;
  if(changed){const contents=$('portableContents');contents.replaceChildren();if(p.reference)contents.append(node('p','幾何參考日期：'+p.reference));if(list(p.dates).length)contents.append(node('p','日期：'+p.dates.join('、')));if(list(p.pairs).length)contents.append(node('p','配對：'+p.pairs.map(x=>typeof x==='string'?x:x.pair).join('、')));for(const warning of p.warnings||[])contents.append(node('p',warning,'hint'));}
  $('portableConsentRow').hidden=value.status!=='ready';$('portableStart').hidden=value.status!=='ready';$('portableStop').hidden=!active;$('portableReset').hidden=active;$('portableImportResult').hidden=!complete||mode!=='export';$('portableOpenProject').hidden=!complete||mode!=='import'||!value.project_id;
  const incomplete=['failed','stopped','interrupted'].includes(value.status);$('portableNext').hidden=!complete&&!incomplete;$('portableNext').textContent=incomplete?'這次工作未完成。原始資料保留；若已產生未完成副本，請保留供檢查並使用新的資料夾名稱重新預覽。':mode==='export'?'分析包已完成。把整個資料夾帶到另一台電腦，在工作台的「專案總覽 → 匯入可攜分析包」開啟。':'新專案已建立。下一步先做成果品質檢查，再建立新的 MintPy 分析版本。';
  controls();if(complete&&completion!==value.job_id){completion=value.job_id;window.refreshWorkspaceRegistry?.().catch(()=>{});}
 }
 async function poll(){if(!job?.job_id||polling)return;polling=true;const id=job.job_id,v=generation;try{const result=await api('/api/portable?job='+encodeURIComponent(id));if(v===generation&&job?.job_id===id)render(result);}catch(e){if(v===generation)error('暫時無法取得進度，請確認工作台連線。'+e.message);}finally{polling=false;}}
 async function withRequest(fn){if(requestPending)return;requestPending=true;controls();error('');const v=generation;try{const result=await fn();if(v===generation&&result)render(result);}catch(e){if(v===generation)error(e.message);}finally{if(v===generation){requestPending=false;controls();}}}
 async function open(next,id=null,info=null){key=id;details=info;reset(next);showFromTop();const v=generation;try{const latest=await api('/api/portable-latest');if(v!==generation)return;lastJob=latest.job||null;$('portableLast').hidden=!lastJob;if(lastJob&&(live.has(lastJob.status)||(lastJob.status==='ready'&&lastJob.mode===next&&(next==='import'||lastJob.source_id===id||lastJob.project_id===id||lastJob.id===id))))render(lastJob);}catch(e){if(v===generation)error('無法讀取上次工作：'+e.message);}}
 window.openPortableExport=(id,info)=>open('export',id,info);window.openPortableImport=source=>{if(!source)return open('import');reset('import',source);showFromTop();};
 $('portableClose').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{generation++;requestPending=false;});
 for(const id of ['portableSource','portableName','portableEnvironment','portableParent','portableFolder'])$(id).addEventListener('input',()=>{generation++;destinationLabel();});
 $('portableConsent').onchange=controls;
 for(const [button,target] of [['portablePickSource','portableSource'],['portablePickParent','portableParent']])action($(button),async()=>{const v=generation;try{await choosePath('dir',path=>{if(v!==generation)return;$(target).value=path;generation++;destinationLabel();},$(target).value||defaults.home);}catch(e){if(v===generation)error(e.message);}});
 $('portableForm').onsubmit=async e=>{e.preventDefault();await withRequest(async()=>{const parent=$('portableParent').value.trim(),folder=$('portableFolder').value.trim();if(!parent||!folder||/[\\/]/.test(folder)||['.','..'].includes(folder))throw Error('請選擇存放位置，並輸入不含斜線的新資料夾名稱。');if(mode==='export')return api('/api/portable-export-preview',{id:key,parent,name:folder});const source=$('portableSource').value.trim(),name=$('portableName').value.trim(),environment=$('portableEnvironment').value;if(!source||!name||!environment)throw Error('請選擇分析包、填寫新專案名稱，並選擇可用的分析環境。');return api('/api/portable-import-preview',{source,parent,folder,name,environment});});};
 action($('portableStart'),()=>{if(job?.status!=='ready'||!$('portableConsent').checked||$('portableStart').disabled)return;return withRequest(()=>api('/api/portable-start',{job_id:job.job_id,reviewed:true}));});
 action($('portableStop'),()=>job?.job_id?withRequest(()=>api('/api/portable-stop',{job_id:job.job_id})):undefined);
 action($('portableReset'),()=>{if(!live.has(job?.status))reset(mode);});
 action($('portableLast'),()=>{if(lastJob){generation++;render(lastJob);poll();}});
 action($('portableImportResult'),()=>{const path=job?.preview?.destination||job?.destination;if(path)reset('import',path);});
 action($('portableOpenProject'),async()=>{if(!job?.project_id)return;const id=job.project_id;dialog.close();current=id;reviewLoaded=null;await refresh();});
 setInterval(()=>{if(dialog.open&&!document.hidden&&job?.job_id)poll();},1300);

 // The original provenance is evidence only, never an active review or setting.
 const provenance=node('section',undefined,'card portable-provenance');provenance.id='portableProvenance';provenance.hidden=true;
 provenance.innerHTML=`<div class="portable-provenance-heading"><span class="portable-symbol" aria-hidden="true">↗</span><div><p class="eyebrow">SOURCE RECORD</p><h2>分析包來源紀錄</h2></div><span class="badge">唯讀參考</span></div><p class="hint">原專案的設定與判讀紀錄保留於此。新專案會重新檢查品質，不沿用原本的核准結果。</p><p id="portableProvenanceStatus" role="status"></p><div id="portableProvenanceContent"></div><button id="portableProvenanceRetry" hidden>重新讀取來源紀錄</button>`;
 $('analysisWorkbench').before(provenance);
 let provenanceKey=null,provenanceRevision=0;
 const ionValue=v=>v===true?'開啟':v===false?'關閉':'未記錄';
 function recordDetail(title,value){const box=node('details');box.append(node('summary',title));const text=node('pre',JSON.stringify(value??{},null,2),'portable-record-json');box.append(text);return box;}
 function drawProvenance(record){
  const content=$('portableProvenanceContent');content.replaceChildren();const cards=node('div',undefined,'portable-provenance-summary'),history=list(record.history),analyses=list(record.analysis_records||record.analyses),packages=list(record.environment_packages);
  for(const [label,value,note] of [['來源專案',record.source_name||record.source_config?.name||'名稱未記錄',record.source_project_id?'專案代號：'+record.source_project_id:'專案代號未記錄'],['原處理環境',record.processing_environment?String(record.processing_environment).split('/').filter(Boolean).pop():'未記錄',packages.length+' 筆套件版本紀錄'],['保留的處理紀錄',history.length+' 筆階段紀錄',analyses.length+' 個既有分析的設定']]){const box=node('div');box.append(node('small',label),node('strong',String(value)),node('small',note));cards.append(box);}content.append(cards);
  const ion=node('div',undefined,'portable-provenance-ion');ion.append(node('strong','原電離層設定'),node('span','估計：'+ionValue(record.ion?.estimate)),node('span','套用：'+ionValue(record.ion?.apply)),node('span','原排除配對：'+list(record.ion_excluded).length+' 組'));content.append(ion);
  const details=node('details');details.append(node('summary','查看來源設定與歷史（只讀，不套用）'));
  if(record.processing_environment)details.append(node('p','原環境位置：'+record.processing_environment,'ws-path hint'));
  if(list(record.ion_excluded).length)details.append(node('p','原電離層排除：'+record.ion_excluded.join('、'),'ws-path'));
  details.append(recordDetail('來源處理設定',record.source_config),recordDetail('階段與人工判讀紀錄',{history:record.history||[],processing_reviews:record.processing_reviews||{}}),recordDetail('既有 MintPy／品質分析設定',analyses),recordDetail('原環境套件與版本',packages));content.append(details);
 }
 async function readProvenance(id,revision){
  $('portableProvenanceStatus').textContent='正在讀取分析包內保留的來源紀錄…';$('portableProvenanceRetry').hidden=true;
  try{const record=await api('/api/portable-provenance?id='+encodeURIComponent(id));if(provenanceKey!==id||revision!==provenanceRevision)return;drawProvenance(record);$('portableProvenanceStatus').textContent='來源紀錄已載入；這些資料不會改動本次分析選項。';}
  catch(e){if(provenanceKey!==id||revision!==provenanceRevision)return;$('portableProvenanceStatus').textContent='暫時無法讀取來源紀錄：'+e.message;$('portableProvenanceRetry').hidden=false;}
 }
 window.portableProjectUpdate=s=>{
  const id=s?.portable_import?s.id:null;provenance.hidden=!id;if(id===provenanceKey)return;provenanceKey=id;const revision=++provenanceRevision;$('portableProvenanceContent').replaceChildren();$('portableProvenanceStatus').textContent='';if(id)return readProvenance(id,revision);
 };
 action($('portableProvenanceRetry'),()=>{if(provenanceKey)return readProvenance(provenanceKey,++provenanceRevision);});
})();
