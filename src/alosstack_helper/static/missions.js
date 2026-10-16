// Separate workflow panel. Never changes an existing ISCE2 project's settings.
(()=>{
 const panel=node('section');panel.id='missionsPanel';panel.hidden=true;
 panel.innerHTML=`<div class="eyebrow">RESEARCH WORKFLOW · EXPERIMENTAL</div><h1 id="missionTitle">NISAR 雙日期</h1>
 <p class="intro">從資料檢查開始，保留每次設定與執行紀錄。NISAR 使用獨立的 ISCE3；ALOS 系列繼續使用 ISCE2。</p>
 <div class="mission-tabs" role="tablist" aria-label="衛星流程"><button id="missionNisarTab" role="tab" aria-selected="true">NISAR 雙日期</button><button id="missionAlosTab" role="tab" aria-selected="false">ALOS-2／ALOS-4</button></div>
 <div id="missionNisar"><div class="notice"><strong>實驗功能</strong><p>已提供資料健檢、設定產生及執行入口；完整 RSLC 科學處理仍待實際驗證。結果需核對對位、相干性、解纏連通區與電離層相位。預設使用 CPU，不會啟用 Apple GPU。</p></div>
 <form id="nisarForm" class="card"><h2>1. 選擇資料與儲存位置</h2><p class="hint">每次工作使用新資料夾，讀取原始影像，保留中間結果。資料量很大，檢查通過後才會開放開始運算。</p>
 <div id="nisarPaths"></div><label>工作名稱<input id="nisarName" value="NISAR 雙日期研究" maxlength="100"></label>
 <label>ISCE3 環境<select id="nisarEnv"></select></label><p id="nisarEnvNote" class="hint"></p>
 <details><summary>自行指定環境位置</summary><label>Conda 環境資料夾<span class="input-row"><input id="nisarCustomEnv" placeholder="選擇包含 bin/python 的環境"><button type="button" id="nisarPickEnv">選資料夾</button></span></label></details>
 <h2>2. 處理設定</h2><div id="nisarFields" class="grid"></div><p class="hint">解纏使用 SNAPHU；其餘細項沿用已核對的 ISCE3 版本預設。這一版沒有雷達影像裁切或完整參數編輯器。</p>
 <button type="submit" class="primary" id="nisarCheck">檢查資料並產生設定</button><p id="nisarNotice" role="status" aria-live="polite"></p></form>
 <div class="card"><div class="card-heading"><h2>工作紀錄</h2><button id="nisarReload">更新</button></div><div id="nisarJobs" class="chips"></div></div><div id="nisarDetail"></div></div>
 <div id="missionAlos" hidden><div class="card"><h2>ALOS-2／ALOS-4 相容性檢查</h2><p>先核對資料世代、日期、極化與取樣率，再決定格式轉換、雙日期或多日期處理。轉換檔名不代表影格已能配對。</p>
 <label>包含各日期的資料夾<span class="input-row"><input id="missionAlosPath"><button id="missionAlosPick">選資料夾</button></span></label><label>ISCE2 環境<select id="missionAlosEnv"></select></label><button id="missionAlosCheck" class="primary">檢查 ALOS 資料</button><p id="missionAlosStatus" role="status"></p><div id="missionAlosReport"></div></div><div id="missionConverter"></div></div>`;
 document.querySelector('main').append(panel);
 let fields=[],selected=null,timer=null,fetching=false,shownStamp='',report=null;
 const paths=[['reference','較早日期 · NISAR RSLC（.h5）','file'],['secondary','較晚日期 · NISAR RSLC（.h5）','file'],['dem','DEM（具地理參考的單波段 raster）','file'],['water','水體距離圖（選填，二元 0/1 遮罩不適用）','file'],['root','新專案資料夾（選上層後自動建立名稱）','dir']];
 for(const [key,text,mode] of paths){const label=node('label',text),row=node('span',undefined,'input-row'),input=node('input'),button=node('button',mode==='dir'?'選上層資料夾':'選檔案');input.id='nisar_'+key;input.required=key!=='water';button.type='button';action(button,()=>choosePath(mode,p=>{input.value=key==='root'?p+'/nisar-'+new Date().toISOString().replace(/[-:.TZ]/g,'').slice(0,14):p;},input.value||defaults.home));row.append(input,button);label.append(row);$('nisarPaths').append(label);}
 window.openNisarJob=async id=>{selected=id;shownStamp='';await window.openNisar();tab(false);$('nisarDetail').scrollIntoView({behavior:'smooth',block:'start'});};
 window.closeMissions=()=>{panel.hidden=true;window.closeAlosPreparation?.();$('openMissions').classList.remove('active');if(timer){clearInterval(timer);timer=null;}};
 function tab(alos){$('missionTitle').textContent=alos?'ALOS-2／ALOS-4 資料準備':'NISAR 雙日期';$('crumb').textContent='建立專案／'+$('missionTitle').textContent;$('missionAlos').hidden=!alos;$('missionNisar').hidden=alos;$('missionNisarTab').setAttribute('aria-selected',String(!alos));$('missionAlosTab').setAttribute('aria-selected',String(alos));}
 $('missionNisarTab').onclick=()=>tab(false);$('missionAlosTab').onclick=()=>window.openAlosPreparation().catch(fail);
 function environments(select,rows,flag){const old=select.value;select.replaceChildren();for(const e of rows.filter(e=>e[flag])){const op=node('option',e.name+' · '+e.path);op.value=e.path;select.append(op);}if([...select.options].some(o=>o.value===old))select.value=old;}
 window.openNisar=async(prefill={})=>{
  window.closeAlosPreparation?.();
  window.leaveWorkspace?.('newProject');
  current=null;window.closeLibrary?.();$('setup').hidden=true;$('project').hidden=true;$('welcome').hidden=true;panel.hidden=false;$('crumb').textContent='建立專案／NISAR 與 ALOS-4';for(const id of ['newProject','openWelcome'])$(id).classList.remove('active');$('newProject').classList.add('active');clear();
  const [state,data]=await Promise.all([api('/api/state'),api('/api/nisar')]);environments($('nisarEnv'),state.defaults.environments,'isce3');environments($('missionAlosEnv'),state.defaults.environments,'isce');
  $('nisarEnvNote').textContent=`本版核對版本：ISCE3 ${data.version}。`+($('nisarEnv').options.length?'沿用既有環境，這個檢查不會安裝套件。':'尚未找到 ISCE3；請指定已安裝的環境。ISCE2 一鍵安裝目前不包含此實驗模組。');
  if(!fields.length){fields=data.fields;for(const f of fields){const label=node('label',f.label),input=node(f.type==='choice'||f.type==='bool'?'select':'input');input.id='nisarOption_'+f.key;if(f.type==='choice'||f.type==='bool'){for(const value of f.type==='bool'?[false,true]:f.choices){const op=node('option',f.type==='bool'?(value?'開啟':'關閉'):value);op.value=String(value);input.append(op);}}else{input.type='number';input.min=f.min;input.max=f.max;input.step=f.type==='int'?'1':'any';}input.value=String(f.value);label.append(input,node('small',f.help,'hint'));$('nisarFields').append(label);}}
  for(const [k,v] of Object.entries(prefill))if($('nisar_'+k))$('nisar_'+k).value=v;
  if(Object.keys(prefill).length)tab(false);
  await update();if(!timer)timer=setInterval(()=>{if(!panel.hidden)update().catch(e=>{$('nisarNotice').textContent='更新暫時失敗：'+e.message;});},3000);
 };
 action($('openMissions'),()=>window.openNisar());action($('nisarPickEnv'),()=>choosePath('dir',p=>{$('nisarCustomEnv').value=p;},defaults.home));
 function config(){const c={name:$('nisarName').value,environment:$('nisarCustomEnv').value.trim()||$('nisarEnv').value,options:{}};for(const [k] of paths)c[k]=$('nisar_'+k).value.trim();for(const f of fields){const v=$('nisarOption_'+f.key).value;c.options[f.key]=f.type==='bool'?v==='true':v;}return c;}
 $('nisarForm').onsubmit=async e=>{e.preventDefault();$('nisarCheck').disabled=true;clear();try{const s=await api('/api/nisar-create',config());selected=s.id;shownStamp='';$('nisarNotice').textContent='正在檢查中繼資料、覆蓋與 ISCE3 設定；尚未開始 SAR 運算。';await update();}catch(e){$('nisarNotice').textContent=e.message;fail(e);}finally{$('nisarCheck').disabled=false;}};
 action($('nisarReload'),async()=>{shownStamp='';await update();});
 const names={checking:'檢查中',ready:'等待確認',running:'運算中',complete:'運算結束',failed:'檢查／運算失敗',stopped:'已停止',interrupted:'服務曾中斷',orphaned:'前次程序仍執行中'};
 async function update(){if(fetching)return;fetching=true;try{const data=await api('/api/nisar');$('nisarJobs').replaceChildren();for(const s of data.jobs){const b=node('button',s.name+' · '+names[s.status]);b.setAttribute('aria-pressed',String(selected===s.id));action(b,async()=>{selected=s.id;shownStamp='';await update();});$('nisarJobs').append(b);}if(!data.jobs.length)$('nisarJobs').textContent='還沒有 NISAR 工作。';if(selected)render(await api('/api/nisar?job='+selected));}finally{fetching=false;}}
 function render(s){const stamp=s.id+s.status+s.stage+s.error;if(stamp===shownStamp){const pre=$('nisarLog');if(pre){const bottom=pre.scrollHeight-pre.scrollTop-pre.clientHeight<50;pre.textContent=s.log||'等待開始';if(bottom)pre.scrollTop=pre.scrollHeight;}return;}shownStamp=stamp;report=s;$('nisarNotice').textContent='目前工作：'+names[s.status]+'。詳細結果在下方工作紀錄。';
  const box=$('nisarDetail');box.replaceChildren();const card=node('div',undefined,'card');card.append(node('span',names[s.status],'badge'),node('h2',s.config.name),node('p',s.stage),node('p',s.config.root,'hint library-path'));
  if(s.error)card.append(node('p',s.error,'error'));
  if(s.report){const r=s.report,dl=node('dl',undefined,'summary');for(const [label,value] of [['ISCE3',r.environment.isce3],['參考日期',r.products[0].metadata.zeroDopplerStartTime],['比較日期',r.products[1].metadata.zeroDopplerStartTime],['頻段／極化',Object.entries(r.resolved.frequencies).map(([f,p])=>f+' '+p.join('/')).join('；')],['輸出投影','EPSG:'+r.resolved.epsg],['暫存空間保守估計',(r.disk_estimate_bytes/1024**3).toFixed(1)+' GiB'],['磁碟可用空間（檢查時）',(r.free_bytes/1024**3).toFixed(1)+' GiB']])dl.append(node('dt',label),node('dd',value));card.append(dl);for(const w of r.warnings)card.append(node('p',w,'hint'));
   if(s.status==='ready'){const label=node('label',undefined,'check'),check=node('input'),run=node('button','開始 NISAR 運算','primary');check.type='checkbox';label.append(check,document.createTextNode('我已閱讀資料、空間估計與實驗功能限制，確認開始這次運算。'));run.disabled=true;check.onchange=()=>run.disabled=!check.checked||!r.disk_ok;action(run,async()=>{run.disabled=true;try{await api('/api/nisar-run',{id:s.id,reviewed:check.checked});shownStamp='';await update();}finally{run.disabled=!check.checked||!r.disk_ok;}});card.append(label,run);}
  }
  if(['running','checking','orphaned'].includes(s.status)){const stop=node('button','停止這個工作');action(stop,()=>api('/api/nisar-stop',{}));card.append(stop,node('p','關閉分頁不會停止運算；請保持電腦清醒、外接磁碟連線與本機服務運作。','hint'));}
  if(s.status==='complete'){const view=node('button','查看 GUNW 成果地圖','primary');action(view,()=>window.viewNisarProduct(s.result.product.path));card.append(view,node('p','這是輸出結構檢查通過；仍需人工確認科學品質。','notice'));}
  if(['failed','stopped','interrupted'].includes(s.status))card.append(node('p','已保留日誌與半成品。本版不自動續跑或覆寫；修正後請選擇新的專案資料夾。','hint'));
  const details=node('details'),summary=node('summary','執行日誌'),pre=node('pre',s.log||'等待開始');pre.id='nisarLog';details.open=['checking','running','orphaned'].includes(s.status);details.append(summary,pre);card.append(details);box.append(card);
 }
 action($('missionAlosPick'),()=>choosePath('dir',p=>{$('missionAlosPath').value=p;},defaults.home));
 action($('missionAlosCheck'),async()=>{
  $('missionAlosCheck').disabled=true;$('missionAlosStatus').textContent='正在讀取 LED 與檢查跨衛星相容性…';$('missionAlosReport').replaceChildren();
  try{const r=await api('/api/alos-compatibility',{path:$('missionAlosPath').value,environment:$('missionAlosEnv').value});window.renderAlosCompatibility(r,$('missionAlosReport'));$('missionAlosStatus').textContent='檢查完成，未執行原始資料處理。';}finally{$('missionAlosCheck').disabled=false;}
 });
 window.renderAlosCompatibility=(r,box)=>{for(const c of r.checks)box.append(node('div',(c.status==='pass'?'✓ ':c.status==='error'?'✕ ':'△ ')+c.title+'：'+c.detail,'health-row '+c.status));const table=node('table'),head=node('tr');for(const label of ['日期','衛星','影格數','極化','取樣率 MHz'])head.append(node('th',label));table.append(head);for(const d of r.dates){const row=node('tr');for(const v of [d.date,d.sensors.join(' / '),d.count,d.polarizations.join(' / '),d.rates.join(' / ')])row.append(node('td',String(v)));table.append(row);}const scroller=node('div',undefined,'mission-table');scroller.append(table);box.append(scroller,node('p',r.next,'notice'));};
 const conversion=$('missionConverter');conversion.className='card';
 conversion.innerHTML=`<h2>ALOS-4 格式轉換</h2><p>選一份完整的高解析度 Level 1.1 產品。使用 <a href="https://www.eorc.jaxa.jp/ALOS/en/alos-4/a4_data_e.htm" target="_blank" rel="noopener">JAXA P3toP2snap（2025-09-25）</a> 的已核對版本，IMG 逐行轉換以降低記憶體需求。第三方工具請自行從官方來源取得並解壓。</p>
 <form id="conversionForm"><div id="conversionPaths"></div><p class="hint">使用上方選取的 ISCE2 環境。原始影像不會修改，輸出目錄需全新；這一步不會執行干涉或多日期反演。</p><button type="submit" class="primary" id="conversionCheck">檢查轉換條件</button><p id="conversionNotice" role="status"></p></form>
 <h3>轉換紀錄</h3><div id="conversionJobs" class="chips"></div><div id="conversionDetail"></div>`;
 let conversionSelected=null,conversionStamp='',conversionFetching=false;
 for(const [key,text] of [['source','單一 ALOS-4 產品資料夾'],['tool','解壓後的 P3toP2snap 工具資料夾'],['root','新輸出資料夾（選上層後建立名稱）']]){const label=node('label',text),row=node('span',undefined,'input-row'),input=node('input'),button=node('button','選資料夾');input.id='conversion_'+key;input.required=true;button.type='button';action(button,()=>choosePath('dir',p=>{input.value=key==='root'?p+'/alos4-converted-'+new Date().toISOString().replace(/[-:.TZ]/g,'').slice(0,14):p;},defaults.home));row.append(input,button);label.append(row);$('conversionPaths').append(label);}
 $('conversionForm').onsubmit=async e=>{e.preventDefault();$('conversionCheck').disabled=true;try{const c={environment:$('missionAlosEnv').value};for(const k of ['source','tool','root'])c[k]=$('conversion_'+k).value.trim();const s=await api('/api/alos4-conversion-create',c);conversionSelected=s.id;conversionStamp='';$('conversionNotice').textContent='正在核對工具版本、記錄長度與磁碟需求…';await updateConversions();}catch(e){$('conversionNotice').textContent=e.message;fail(e);}finally{$('conversionCheck').disabled=false;}};
 async function updateConversions(){if(panel.hidden||$('missionAlos').hidden||conversionFetching)return;conversionFetching=true;try{
  const data=await api('/api/alos4-conversion');$('conversionJobs').replaceChildren();for(const s of data.jobs){const b=node('button',s.name+' · '+names[s.status]);action(b,async()=>{conversionSelected=s.id;conversionStamp='';await updateConversions();});$('conversionJobs').append(b);}
  if(!conversionSelected)return;const s=await api('/api/alos4-conversion?job='+conversionSelected),stamp=s.id+s.status+s.error;if(stamp===conversionStamp){if($('conversionLog'))$('conversionLog').textContent=s.log;return;}conversionStamp=stamp;
  $('conversionNotice').textContent='目前工作：'+names[s.status]+'。';const box=$('conversionDetail');box.replaceChildren(node('h3',names[s.status]),node('p',s.config.root,'hint library-path'));if(s.error)box.append(node('p',s.error,'error'));
  if(s.report){const r=s.report;box.append(node('p',`${r.mode} · ${r.images.length} 個極化 · 每份 ${r.images[0].pixels} × ${r.images[0].lines} 像素 · 空間估計 ${(r.disk_estimate_bytes/1024**3).toFixed(1)} GiB`));for(const w of r.warnings)box.append(node('p',w,'hint'));
   if(s.status==='ready'){const label=node('label',undefined,'check'),check=node('input'),run=node('button','開始轉換，建立副本','primary');check.type='checkbox';label.append(check,document.createTextNode('確認輸出位置與格式轉換限制；轉換後仍需檢查科學相容性。'));run.disabled=true;check.onchange=()=>run.disabled=!check.checked;action(run,async()=>{run.disabled=true;try{await api('/api/alos4-conversion-run',{id:s.id,reviewed:check.checked});conversionStamp='';await updateConversions();}finally{run.disabled=!check.checked;}});box.append(label,run);}
  }
  if(['checking','running','orphaned'].includes(s.status)){const stop=node('button','停止轉換工作');action(stop,()=>api('/api/alos4-conversion-stop',{}));box.append(stop);}
  if(s.status==='complete'){box.append(node('p','已完成格式副本；尚未驗證此產品的干涉品質。','notice'));const view=node('button','在資料瀏覽器檢查副本');action(view,()=>window.viewNisarProduct(s.result.output));box.append(view);}
  const details=node('details'),pre=node('pre',s.log||'等待開始');pre.id='conversionLog';details.append(node('summary','轉換日誌'),pre);details.open=s.status==='failed';box.append(details);
 }finally{conversionFetching=false;}}
 window.openAlosLegacyPreparation=async(path='',id=null)=>{await window.openNisar();tab(true);if(path)$('missionAlosPath').value=path;if(id){conversionSelected=id;conversionStamp='';}await updateConversions();if(id)$('conversionDetail').scrollIntoView({behavior:'smooth',block:'start'});};
 window.openAlosPreparation=async(path='',id=null)=>id||!window.openAlosPreparationWorkspace?window.openAlosLegacyPreparation(path,id):window.openAlosPreparationWorkspace(path);
 $('missionAlosTab').addEventListener('click',()=>updateConversions().catch(fail));setInterval(()=>{if(!panel.hidden&&!$('missionAlos').hidden)updateConversions().catch(e=>{$('conversionNotice').textContent=e.message;});},3000);
})();
