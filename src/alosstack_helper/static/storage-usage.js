// Storage inventories are explicit, cancellable and separate from SAR processing.
(()=>{
 const dialog=$('projectManager'), info=$('managerInformation');
 const section=node('section',undefined,'usage-panel');section.id='managerUsage';section.hidden=true;
 section.setAttribute('role','tabpanel');section.setAttribute('aria-labelledby','managerUsageTab');
 section.innerHTML=`<div class="usage-heading"><div><h3>專案空間</h3><p class="hint">查看資料放在哪裡、各占多少空間。</p></div><span class="badge">唯讀統計</span></div>
 <label class="usage-check"><input type="checkbox" id="usageIncludeExternal">一起統計專案外的影像與地形資料</label>
 <p class="hint">僅掃描這個專案及勾選的來源。大型資料夾或 NAS 可能需要較久，可隨時停止。</p>
 <div class="usage-actions"><button id="usageStart" class="primary">開始統計</button><button id="usageStop" hidden>停止統計</button></div>
 <p id="usageError" class="error" role="alert"></p><div id="usageStatus" role="status" aria-live="polite"></div>
 <div id="usageReport"></div>
 <details class="usage-explanation"><summary>這些數字怎麼計算？</summary><p>檔案容量是檔案內容的大小，使用二進位單位（GiB）。硬連結的相同內容只計算一次；資料夾內的符號連結不追蹤，若它是已設定的外部來源，可勾選上方選項另外統計。</p><p>恢復副本可能與原檔共享磁碟區塊，稀疏檔案也可能尚未配置完整空間。因此，檔案容量及磁碟配置量都不等於刪除後能釋放的空間。這個面板只統計，不會刪除任何資料。</p><p>統計期間資料仍可能變動；數字代表該次讀取的結果。跨入另一磁碟的子目錄會略過並提示。外部來源可能由多個專案共用，不算在專案內合計。</p></details>`;
 dialog.append(section);
 let key=null,epoch=0,reading=false,changing=false,last='';
 const tabs=[$('managerInfoTab'),$('managerUsageTab')];
 function select(index){tabs.forEach((b,i)=>{b.setAttribute('aria-selected',String(index===i));b.tabIndex=index===i?0:-1;});info.hidden=index!==0;section.hidden=index!==1;}
 tabs.forEach((b,i)=>{b.onclick=()=>select(i);b.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?1:1-i;select(next);tabs[next].focus();}};});
 function size(value){if(!Number.isFinite(value))return '—';const units=['B','KiB','MiB','GiB','TiB','PiB'];let i=0;while(value>=1024&&i<units.length-1){value/=1024;i++;}return new Intl.NumberFormat('zh-TW',{maximumFractionDigits:i?2:0}).format(value)+' '+units[i];}
 const number=v=>new Intl.NumberFormat('zh-TW').format(v||0);
 const date=v=>new Date(v*1000).toLocaleString('zh-TW',{hour12:false});
 const statusNames={checking:'正在檢查位置',running:'正在統計',complete:'統計完成',partial:'統計不完整',cancelled:'已停止統計',interrupted:'上次統計中斷',failed:'未完成統計'};
 const sourceNames={not_scanned:'未勾選統計',in_project:'已計入專案',shared:'已包含在其他來源',unavailable:'無法讀取',complete:'已統計',partial:'部分統計'};
 function metric(label,value,detail){const box=node('div',undefined,'usage-metric');box.append(node('span',label),node('strong',value));if(detail)box.append(node('small',detail));return box;}
 function draw(data){
  const r=data.report,active=r&&['checking','running'].includes(r.status),ours=data.busy===key;
  $('usageStart').disabled=Boolean(data.busy)||changing;$('usageStart').textContent=r?'重新統計':'開始統計';
  $('usageStop').hidden=!ours;$('usageStop').disabled=changing;$('usageIncludeExternal').disabled=ours||changing;
  const status=$('usageStatus');status.replaceChildren();
  if(!r){status.append(node('p',data.busy?'另一個專案正在統計，完成或停止後即可開始。':'尚未統計。按「開始統計」後，結果會保留在本機，磁碟離線時仍可查看。','hint'));$('usageReport').replaceChildren();return;}
  const label=node('span',statusNames[r.status]||r.status,'ws-status '+(r.status==='complete'?'complete':active?'running':'unavailable'));status.append(label);
  status.append(node('p',active?`已讀取 ${number(r.visited)} 個項目 · 執行 ${Math.max(0,Math.round((r.updated-r.started)))} 秒`:`統計時間：${date(r.finished||r.updated)} · ${r.status==='complete'?'已保存這次結果':'目前顯示已讀取的部分結果'}`,'hint'));
  if(active)status.append(node('p',r.current,'usage-current'));
  if(data.location_changed)status.append(node('p','這是重新連結前的位置紀錄，請在新位置重新統計。','notice'));
  if(r.reason)status.append(node('p',r.reason,'notice'));
  const box=$('usageReport');box.replaceChildren();
  const metrics=node('div',undefined,'usage-metrics');metrics.append(metric('專案內檔案容量',size(r.project.bytes),`${number(r.project.files)} 個檔案項目`),metric('外部來源檔案容量',r.include_external?size(r.external.bytes):'尚未統計','另外列出，可能由多個專案共用'));box.append(metrics);
  for(const disk of r.disks||[]){const card=node('div',undefined,'usage-disk');const line=node('div',undefined,'usage-disk-heading');line.append(node('strong',disk.label),node('span','可用 '+size(disk.free)));card.append(line,node('p',`磁碟總容量 ${size(disk.total)} · ${date(disk.checked)}`,'hint'));const bar=node('meter');bar.min=0;bar.max=disk.total||1;bar.value=Math.max(0,disk.total-disk.free);bar.setAttribute('aria-label',disk.label+' 已使用空間');card.append(bar);box.append(card);}
  const disks=[...box.children].filter(child=>child.classList.contains('usage-disk'));
  const table=node('table',undefined,'usage-table'),head=node('thead'),hr=node('tr');for(const label of ['資料類別','檔案容量','檔案數'])hr.append(node('th',label));head.append(hr);table.append(head);const body=node('tbody');
  for(const row of Object.values(r.categories)){const tr=node('tr'),name=node('td'),bar=node('div',undefined,'usage-bar'),fill=node('span');fill.style.width=(r.project.bytes?100*row.bytes/r.project.bytes:0)+'%';bar.setAttribute('aria-hidden','true');bar.append(fill);name.append(node('span',row.label),bar);tr.append(name,node('td',size(row.bytes)),node('td',number(row.files)));body.append(tr);}table.append(body);box.append(table,...disks);
  const extras=node('details',undefined,'usage-explanation');extras.append(node('summary','查看來源位置與統計細節'),node('p','專案位置：'+r.root,'ws-path'));
  extras.append(node('p',`專案內略過 ${number(r.project.links)} 個符號連結；去除 ${number(r.project.hardlinks)} 個重複硬連結。磁碟配置量：${r.project.allocation_unknown?'無法完整取得':size(r.project.allocated)}（不等於可釋放空間）。`,'hint'));
  for(const source of r.sources||[]){const row=node('div',undefined,'storage-path-row');row.append(node('strong',source.label+' · '+(sourceNames[source.status]||source.status)),node('p',source.path,'ws-path'));if(['complete','partial'].includes(source.status))row.append(node('p',size(source.bytes)));if(source.counted_under)row.append(node('p','合併計入：'+source.counted_under,'ws-path'));if(source.reason)row.append(node('p',source.reason,'hint'));extras.append(row);}box.append(extras);
  if(r.error_count){const errors=node('details',undefined,'usage-explanation usage-warning');errors.open=true;errors.append(node('summary',`${number(r.error_count)} 個位置未完整統計`));for(const error of r.errors||[])errors.append(node('p',error.message+' '+error.path,'ws-path'));if(r.error_count>r.errors.length)errors.append(node('p','僅列出前 20 項問題。'));box.append(errors);}
 }
 async function refreshUsage(){if(!key||reading||changing)return;reading=true;const id=key,version=epoch;try{const data=await api('/api/storage-usage?id='+encodeURIComponent(id));if(id!==key||version!==epoch)return;const encoded=JSON.stringify(data);if(encoded!==last){if(!last&&data.report)$('usageIncludeExternal').checked=data.report.include_external;last=encoded;draw(data);}}catch(e){if(id===key&&version===epoch)$('usageError').textContent=e.message;}finally{reading=false;}}
 window.openStorageUsage=id=>{epoch++;key=id;last='';select(0);$('usageIncludeExternal').checked=false;$('usageError').textContent='';$('usageReport').replaceChildren();$('usageStatus').textContent='正在讀取本機統計紀錄…';$('usageStart').disabled=true;$('usageStop').hidden=true;refreshUsage();};
 window.closeStorageUsage=()=>{epoch++;key=null;};
 for(const [id,url] of [['usageStart','/api/storage-usage-start'],['usageStop','/api/storage-usage-stop']])$(id).onclick=async()=>{
  if(!key||changing)return;const selected=key,version=++epoch;changing=true;$('usageError').textContent='';$('usageStart').disabled=true;$('usageStop').disabled=true;
  try{const data=await api(url,{id:selected,include_external:$('usageIncludeExternal').checked});if(selected===key&&version===epoch){last='';draw(data);}}
  catch(e){if(selected===key&&version===epoch)$('usageError').textContent=e.message;}
  finally{changing=false;last='';refreshUsage();}
 };
 setInterval(()=>{if(dialog.open&&!section.hidden&&!document.hidden)refreshUsage();},1200);
})();
