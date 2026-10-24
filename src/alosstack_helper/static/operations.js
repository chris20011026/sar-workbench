/* Synthetic health checks and explicit MintPy recovery. No research decisions. */
(() => {
 const labels={idle:'尚未執行',running:'正在驗證',passed:'通過',complete:'已完成',failed:'需檢查',cancelled:'已停止',interrupted:'已中斷',stopped:'已停止',skipped:'未檢查'};
 const successful=s=>['passed','PASS','pass'].includes(s);
 const skipped=s=>['skipped','SKIP'].includes(s);
 const measurementLabels={max_error_mm_year:['最大速度差','mm/年'],tolerance_mm_year:['容許差','mm/年'],max_error_mm:['最大位移差','mm'],max_displacement_error_mm:['校正後最大位移差','mm'],max_height_error_m:['最大高程差','m'],injected_max_height_m:['注入最大高程誤差','m'],source_files:['來源檔案','個'],injected_radians:['注入相位','rad'],affected_pixels:['影響像素','個'],suggested_dates:['建議檢查日期','']};
 function checkDetails(details){if(typeof details==='string')return details;return Object.entries(details||{}).map(([key,value])=>{const [label,unit]=measurementLabels[key]||[key,''];const shown=Array.isArray(value)?value.join('、'):typeof value==='number'?value.toLocaleString('zh-TW',{maximumFractionDigits:6}):String(value);return `${label}：${shown}${unit?' '+unit:''}`;}).join(' · ');}

 const panel=node('section',undefined,'card selftest-panel');panel.id='advancedSelftest';
 panel.innerHTML=`<div class="operation-heading"><span class="operation-icon" aria-hidden="true">◎</span><div><div class="eyebrow">WORKBENCH HEALTH</div><h2>進階功能自測</h2><p class="muted">用有已知答案的小型資料，確認這台電腦能完成分析與匯出。</p></div><span id="selftestBadge" class="badge">尚未執行</span></div>
 <div class="selftest-route" aria-label="自測範圍"><span><b>01</b>時序與判讀</span><span><b>02</b>已知錯誤</span><span><b>03</b>地圖與匯出</span></div>
 <div class="inline"><label class="grow" for="selftestEnvironment">測試使用的環境<select id="selftestEnvironment"></select></label><button id="selftestStart" class="primary">執行進階自測</button><button id="selftestStop" hidden>停止自測</button></div>
 <p id="selftestStage" class="hint" role="status" aria-live="polite">合成資料會另外存放，不會讀寫你的研究專案。</p><progress id="selftestProgress" hidden aria-label="進階自測進行中"></progress>
 <div id="selftestResults"></div><p id="selftestError" class="error" hidden></p>
 <details class="operation-details"><summary>測試內容與限制</summary><p>透過所選環境執行 MintPy，檢查已知位移、診斷、DEM 誤差與日期異常，以及地理圖層與成果匯出。未執行或不適用的項目會另外列出。</p><p>這是軟體功能驗證，不能代替真實衛星資料的對位、解纏或研究品質判讀。測試中的地理網格是人工建立的，與實際觀測無關。</p><p id="selftestLocation" class="hint"></p><button id="selftestDownload" hidden>下載完整驗證紀錄</button><pre id="selftestLog">尚未執行。</pre></details>`;
 $('environmentPanel').after(panel);
 let job={},fetching=false,envSignature='',stamp='';
 function environments(){
  const envs=(window.detectedEnvironments||window.workbenchState?.defaults?.environments||[]).filter(e=>e.mintpy&&e.isce),sig=JSON.stringify(envs);if(sig===envSignature)return;envSignature=sig;
  const select=$('selftestEnvironment'),selected=select.value;select.replaceChildren();
  for(const env of envs){const option=node('option',env.name+' · ISCE2 / MintPy');option.value=env.path;select.append(option);}
  if(!envs.length){const option=node('option',window.workbenchState?.defaults?'尚未找到 ISCE2 / MintPy 環境':'正在取得可用環境…');option.value='';select.append(option);}
  if(envs.some(e=>e.path===selected))select.value=selected;else{const preferred=envs.find(e=>e.name==='insar_arm');if(preferred)select.value=preferred.path;}
 }
 function paint(data){
  job=data||{};environments();const running=job.status==='running';
  $('selftestBadge').textContent=labels[job.status]||job.status||'尚未執行';$('selftestBadge').className='badge '+(job.status==='failed'?'warn':successful(job.status)?'done':'');
  $('selftestStart').disabled=running||!$('selftestEnvironment').value||Boolean(window.workbenchState?.busy);
  $('selftestStart').textContent=job.status&&job.status!=='idle'?'重新執行進階自測':'執行進階自測';
  $('selftestEnvironment').disabled=running;$('selftestStop').hidden=!running;$('selftestProgress').hidden=!running;
  $('selftestStage').textContent=job.stage||'合成資料會另外存放，不會讀寫你的研究專案。';
  $('selftestError').hidden=!job.error;$('selftestError').textContent=job.error||'';
  $('selftestLocation').textContent=job.report_path?'驗證紀錄：'+job.report_path:'';
  $('selftestLog').textContent=job.log_tail||job.log||'尚未執行。';$('selftestDownload').hidden=!job.report_path;
  const checks=job.checks||[],signature=JSON.stringify([job.run_id,job.status,checks]);if(signature===stamp)return;stamp=signature;
  const target=$('selftestResults');target.replaceChildren();if(!checks.length)return;
  const counts={pass:checks.filter(c=>successful(c.status)).length,fail:checks.filter(c=>['failed','FAIL','fail'].includes(c.status)).length,skip:checks.filter(c=>skipped(c.status)).length};
  const summary=node('div',undefined,'selftest-counts');for(const [value,label,kind] of [[counts.pass,'通過','pass'],[counts.fail,'未通過','fail'],[counts.skip,'未檢查','skip']]){const item=node('div',undefined,kind);item.append(node('strong',String(value)),node('span',label));summary.append(item);}target.append(summary);
  const details=node('details',undefined,'operation-details');details.open=counts.fail>0;details.append(node('summary','查看各項檢查結果'));
  const list=node('ul',undefined,'check-results');for(const check of checks){const pass=successful(check.status),skip=skipped(check.status),li=node('li',undefined,pass?'pass':skip?'skip':'fail'),text=node('div');text.append(node('strong',check.label||check.name||check.id));if(check.details)text.append(node('small',checkDetails(check.details)));li.append(node('span',pass?'✓':skip?'—':'!','check-symbol'),text,node('small',pass?'通過':skip?'未檢查':'未通過'));list.append(li);}details.append(list);target.append(details);
 }
 async function update(){if(fetching)return;fetching=true;try{const wasRunning=job.status==='running',data=await api('/api/selftest');if(wasRunning&&data.status!=='running')await refresh();paint(data);}catch(e){$('selftestStage').textContent='無法取得自測狀態：'+e.message;}finally{fetching=false;}}
 action($('selftestStart'),async()=>{paint(await api('/api/selftest-start',{environment:$('selftestEnvironment').value}));await refresh();});
 action($('selftestStop'),async()=>{paint(await api('/api/selftest-stop',{}));await refresh();});
 $('selftestEnvironment').onchange=()=>paint(job);
 action($('selftestDownload'),async()=>{const report=await api('/api/selftest-report'),url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'})),a=node('a');a.href=url;a.download='sar-workbench-selftest.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
 setInterval(()=>{if(!$('welcome').hidden||job.status==='running')update();},2500);
 $('openWelcome').addEventListener('click',update);paint({});
 window.openAdvancedSelftest=()=>{window.openTools?.();$('openWelcome').click();update();panel.scrollIntoView({behavior:'smooth',block:'start'});};

 window.mintpyRecovery={render(job,target,{busy,isCurrent,onRecovered}){
  if(job.kind!=='mintpy'||job.status!=='failed')return;
  const box=node('section',undefined,'mintpy-recovery'),heading=node('div',undefined,'operation-heading'),title=node('div');
  title.append(node('h3','從完整階段接回分析'),node('p','先檢查保護副本，再建立新的分析版本。原失敗版本會保留。','muted'));heading.append(node('span','↺','operation-icon'),title);box.append(heading);
  const inspect=node('button','檢查可恢復階段'),content=node('div');inspect.disabled=busy;box.append(inspect,content);target.append(box);
  action(inspect,async()=>{
   inspect.disabled=true;content.replaceChildren(node('p','正在核對副本、來源與環境…','hint'));
   try{const report=await api(`/api/analysis-recovery?id=${encodeURIComponent(job.project_id)}&run=${encodeURIComponent(job.id)}`);if(!isCurrent())return;
    content.replaceChildren();const checks=node('ul',undefined,'check-results');for(const c of report.checks||[]){const item=typeof c==='string'?{label:c}:c;checks.append(node('li',item.label||item.name||item.detail||item.reason||JSON.stringify(item)));}if(checks.children.length)content.append(checks);
    if(!report.can_recover){content.append(node('p',report.reason||'此版本沒有通過驗證的保護副本，請保留日誌並建立新分析。','analysis-warning'));return;}
    const path=node('div',undefined,'recovery-route');path.append(node('span','原版本 · 保留'),node('b','→'),node('strong',report.stage||report.status||'完整階段'),node('b','→'),node('span','新版本 · 待確認'));content.append(path);
    if(report.bytes!=null)content.append(node('p','副本資料量：'+(report.bytes/1024/1024/1024).toFixed(2)+' GB'+(report.required_free_bytes!=null?' · 至少需 '+(report.required_free_bytes/1024/1024/1024).toFixed(2)+' GB 可用空間':'')+'。恢復時會核對檔案內容。','hint'));if(report.copy_note)content.append(node('p',report.copy_note,'hint'));
    const label=node('label',undefined,'check'),consent=node('input');consent.type='checkbox';label.append(consent,document.createTextNode('建立恢復版本，並重新確認接下來的分析關卡'));const recover=node('button','建立恢復版本 →','primary');recover.disabled=true;consent.onchange=()=>{recover.disabled=!consent.checked||Boolean(window.workbenchState?.busy);};content.append(label,recover);
    action(recover,async()=>{recover.disabled=true;try{const restored=await api('/api/analysis-recover',{id:job.project_id,run:job.id,checkpoint_id:report.checkpoint_id,reviewed:consent.checked});if(isCurrent())await onRecovered(restored);}catch(e){if(isCurrent()){content.replaceChildren(node('p',e.message,'error'));inspect.disabled=false;}throw e;}});
   }finally{if(isCurrent())inspect.disabled=false;}
  });
 }};
})();
