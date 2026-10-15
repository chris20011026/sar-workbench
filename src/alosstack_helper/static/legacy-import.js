// Manual ISCE2 results are external, read-only inputs to a new workspace.
(()=>{
 const dialog=node('dialog',undefined,'legacy-import-dialog');dialog.id='legacyImportDialog';dialog.setAttribute('aria-labelledby','legacyImportTitle');
 dialog.innerHTML=`<div class="card-heading"><div><div class="eyebrow">REUSE EXISTING RESULTS</div><h2 id="legacyImportTitle">匯入手動 ISCE2 成果</h2></div><button id="legacyClose">關閉</button></div>
 <p>將以前手動完成的 <strong>ALOS-2 多日期 alosStack</strong> 成果接到品質檢查與 MintPy。大型影像留在原位置，新的分析儲存在你選擇的工作資料夾。</p>
 <p class="notice">目前支援標準 pairs／dates_resampled／baseline 結構；TOPS、stripmap 和雙日期格式尚未開放此入口。匯入不會補做原始運算，也不能判定電離層校正是否正確。</p>
 <form id="legacyImportForm"><fieldset id="legacyFields"><legend>1 · 選擇已完成的成果</legend>
 <label>來源成果資料夾<span class="input-row"><input id="legacySource" required placeholder="選擇 proc 或包含 proc 的資料夾"><button type="button" id="legacyPickSource">選擇資料夾</button></span></label>
 <label>幾何參考日期（通常可留白）<input id="legacyReference" placeholder="YYMMDD；有多個完整幾何日期時才指定" pattern="[0-9]{6}"></label>
 <p class="hint">請先確認原本的 ISCE2 處理已停止。需要解纏、遮罩解纏、相干性、連通區、五項幾何影像，以及 track／frame／baseline 描述資料。</p>
 <h3>2 · 建立獨立工作資料夾</h3>
 <label>研究名稱<input id="legacyName" maxlength="100" required placeholder="例如：台北既有 ALOS-2 成果"></label>
 <label>存放位置<span class="input-row"><input id="legacyParent" required placeholder="選擇本機或外接硬碟上的父資料夾"><button type="button" id="legacyPickParent">選擇資料夾</button></span></label>
 <label>新資料夾名稱<input id="legacyFolder" required value="imported-alos-stack" pattern="[^/\\\\]+" maxlength="100"></label>
 <p id="legacyRootPreview" class="ws-path hint"></p>
 <label>分析環境<select id="legacyEnvironment"></select></label>
 <p class="hint">這裡選擇已安裝的 ISCE2／MintPy 環境；新成果的分析會使用這個環境。</p></fieldset>
 <div class="inline"><button id="legacyInspect" class="primary" type="submit">檢查並預覽</button><button id="legacyStop" type="button" hidden>停止檢查／匯入</button></div></form>
 <p id="legacyError" class="error" role="alert"></p><p id="legacyProgress" role="status" aria-live="polite"></p><section id="legacyPreview" aria-label="匯入預覽"></section>
 <div id="legacyConfirmation" hidden><label class="check"><input id="legacyConsent" type="checkbox">我已確認原處理停止，了解來源硬碟必須保持可讀，且原始品質與校正紀錄仍待人工確認。</label><button id="legacyCreate" class="primary" disabled>確認匯入這份成果</button></div>
 <div id="legacySuccess" class="notice" hidden><p>已建立獨立的工作資料夾。先做品質檢查，再建立 MintPy 分析版本。</p><button id="legacyOpenProject" class="primary">開啟匯入的專案</button></div>`;
 document.body.append(dialog);
 let previewId=null,job=null,timer=null,busy=false,loading=false,version=0;
 const fields=['legacySource','legacyReference','legacyName','legacyParent','legacyFolder','legacyEnvironment'];
 const bytes=n=>n>=1024**3?(n/1024**3).toFixed(2)+' GB':(n/1024**2).toFixed(1)+' MB';
 function destination(){return $('legacyParent').value.trim().replace(/\/$/,'')+'/'+$('legacyFolder').value.trim();}
 function updateDestination(){$('legacyRootPreview').textContent=$('legacyParent').value.trim()?'新工作資料夾：'+destination():'';}
 function invalidate(){if(busy)return;version++;previewId=null;job=null;$('legacyPreview').replaceChildren();$('legacyConfirmation').hidden=true;$('legacySuccess').hidden=true;$('legacyConsent').checked=false;$('legacyCreate').disabled=true;$('legacyProgress').textContent='';updateDestination();}
 for(const key of fields)$(key).addEventListener('input',invalidate);
 function setBusy(value){busy=value;$('legacyFields').disabled=value;$('legacyInspect').disabled=value;$('legacyStop').hidden=!value;}
 function localAction(id,fn){action($(id),async()=>{$('legacyError').textContent='';try{await fn();}catch(e){$('legacyError').textContent=e.message;}});}
 localAction('legacyPickSource',()=>choosePath('dir',path=>{$('legacySource').value=path;invalidate();},$('legacySource').value||defaults.home));
 localAction('legacyPickParent',()=>choosePath('dir',path=>{$('legacyParent').value=path;invalidate();},$('legacyParent').value||defaults.home));
 $('legacyClose').onclick=()=>dialog.close();
 $('legacyConsent').onchange=()=>{$('legacyCreate').disabled=!$('legacyConsent').checked||job?.status!=='ready';};
 function render(next){
  job=next;setBusy(['running','importing'].includes(job.status));$('legacyProgress').textContent=job.stage||'';$('legacyError').textContent=job.error||'';
  $('legacyConfirmation').hidden=job.status!=='ready';$('legacySuccess').hidden=job.status!=='complete';$('legacyCreate').disabled=job.status!=='ready'||!$('legacyConsent').checked;
  const report=job.preview,box=$('legacyPreview');box.replaceChildren();
  if(report){
   const summary=node('div',undefined,'legacy-summary');
   for(const [title,value] of [['流程','ALOS-2 多日期 · 外部成果'],['日期與配對',`${report.dates.length} 個日期／${report.pairs.length} 個配對`],['影像尺寸',`${report.width} × ${report.height} 像素`],['幾何參考日期',report.reference],['極化與 looks',`${report.sensor.polarization} · ${report.looks.replace(/^_/,'')}`],['引用成果大小',bytes(report.source_bytes)+'（不複製影像）']]){const item=node('div');item.append(node('span',title,'hint'),node('strong',value));summary.append(item);}
   box.append(summary,node('p','來源：'+report.source,'ws-path'),node('p','新工作資料夾：'+report.root,'ws-path'));
   const details=node('details');details.append(node('summary','檢視日期與配對清單'),node('p','日期：'+report.dates.join('、')));const list=node('ul');for(const pair of report.pairs)list.append(node('li',pair));details.append(list);box.append(details);
   const warnings=node('ul',undefined,'notice');for(const text of report.warnings)warnings.append(node('li',text));box.append(warnings);
  }
 }
 async function poll(){
  if(!previewId||loading)return;loading=true;const captured=previewId,v=version;
  try{const next=await api('/api/legacy-import?preview='+encodeURIComponent(captured));if(captured!==previewId||v!==version)return;render(next);if(!busy)await window.refreshWorkspaceRegistry?.();}
  catch(e){$('legacyError').textContent='暫時無法取得進度，請確認服務連線。'+e.message;}
  finally{loading=false;clearTimeout(timer);if(previewId&&busy)timer=setTimeout(poll,1200);}
 }
 $('legacyImportForm').onsubmit=async e=>{
  e.preventDefault();if(busy)return;invalidate();$('legacyError').textContent='';setBusy(true);$('legacyProgress').textContent='正在檢查描述資料與配對網路…';
  try{const data=await api('/api/legacy-preview',{source:$('legacySource').value.trim(),root:destination(),name:$('legacyName').value.trim(),reference:$('legacyReference').value.trim(),environment:$('legacyEnvironment').value});previewId=data.preview_id;render(data);await poll();}
  catch(error){setBusy(false);$('legacyError').textContent=error.message;$('legacyProgress').textContent='檢查尚未完成。';}
 };
 localAction('legacyCreate',async()=>{if(!previewId||!$('legacyConsent').checked)throw Error('請先檢查並確認匯入內容。');$('legacyCreate').disabled=true;render(await api('/api/legacy-create',{preview_id:previewId,reviewed:true}));await poll();});
 localAction('legacyStop',async()=>{if(previewId){await api('/api/legacy-stop',{preview_id:previewId});await poll();}});
 localAction('legacyOpenProject',async()=>{if(!job?.project_id)return;dialog.close();current=job.project_id;reviewLoaded=null;await refresh();});
 window.openLegacyImport=source=>{
  if(!busy&&!previewId){$('legacyEnvironment').replaceChildren();for(const env of defaults.environments||[]){if(!env.isce||!env.mintpy)continue;const o=node('option',env.name+' · ISCE2／MintPy');o.value=env.path;$('legacyEnvironment').append(o);}if(!$('legacyEnvironment').options.length&&defaults.env){const o=node('option',defaults.env);o.value=defaults.env;$('legacyEnvironment').append(o);}$('legacyEnvironment').value=defaults.env||$('legacyEnvironment').options[0]?.value||'';if(source)$('legacySource').value=source;updateDestination();}
  if(!dialog.open)dialog.showModal();if(previewId)poll();
 };
})();
