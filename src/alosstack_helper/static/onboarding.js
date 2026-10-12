// First-use guidance. All science runs use the pinned private runtime.
(()=>{
 let first=true,runtime={},plan=null,imported=null,dem=null,wbd=null,lesson=null,jobBusy=false,polling=false,terrainBounds=null,jobsSignature='';
 const names={not_installed:'尚未安裝',running:'進行中',ready:'可以使用',failed:'需要處理',interrupted:'上次中斷',cancelled:'已停止'};
 function welcome(){current=null;reviewLoaded=null;$('setup').hidden=true;$('project').hidden=true;$('welcome').hidden=false;$('crumb').textContent='開始使用';$('openWelcome').classList.add('active');$('newProject').classList.remove('active');clear();updateRuntime();window.scrollTo({top:0,behavior:'smooth'});}
 $('openWelcome').onclick=welcome;
 window.onboardingUpdate=data=>{const select=$('importEnvironment'),selected=select.value,signature=JSON.stringify(data.defaults?.environments||[]);if(select.dataset.environments!==signature){select.dataset.environments=signature;select.replaceChildren();const auto=node('option','自動選擇已安裝的環境');auto.value='';select.append(auto);for(const e of (data.defaults?.environments||[]).filter(e=>e.isce!==false)){const o=node('option',e.name+' · '+e.path);o.value=e.path;select.append(o);}if([...select.options].some(o=>o.value===selected))select.value=selected;}if(first){first=false;if(!data.projects.length)welcome();}};
 function showPanel(id){$(id).hidden=false;$(id).scrollIntoView({behavior:'smooth',block:'start'});}
 $('openLesson').onclick=()=>showPanel('lessonPanel');
 $('openImport').onclick=()=>showPanel('importPanel');
 $('openExpert').onclick=newView;
 function controls(){
  const busy=runtime.status==='running',ready=runtime.status==='ready';
  $('runtimeBadge').textContent=names[runtime.status]||'檢查中';
  $('installRuntime').hidden=ready||busy;
  $('installRuntime').disabled=!runtime.supported||!runtime.bootstrap_ready||!$('runtimeConsent').checked;
  $('installRuntime').textContent=['failed','interrupted','cancelled'].includes(runtime.status)?'重新安裝並驗證':'安裝並驗證運算工具';
  $('runtimeInstallOptions').hidden=ready||busy;
  $('recheckRuntime').hidden=!ready;$('stopRuntime').hidden=!busy;$('runtimeProgress').hidden=!busy;
  $('getDesktopApp').hidden=runtime.bootstrap_ready||ready;
  $('runtimePlatform').textContent=`${runtime.platform||''} · 可用空間 ${runtime.free_gb??'—'} GB`;
  $('runtimeHome').textContent='工具存放位置：'+(runtime.home||'')+(runtime.environment?'\n科學環境：'+runtime.environment:'');
  $('runtimeStage').textContent=runtime.stage||'正在檢查';
  if(!runtime.bootstrap_ready&&!ready)$('runtimeStage').textContent='第一次安裝請使用 Mac 啟動器；已有 ISCE2 環境也可直接建立專案。';
  $('runtimeError').hidden=!runtime.error;$('runtimeError').textContent=runtime.error||'';
  $('runtimeOutput').textContent=runtime.output||'尚未開始安裝。';
  $('runtimeChecks').replaceChildren();for(const c of runtime.checks||[])$('runtimeChecks').append(node('span',`${c.status==='PASS'?'✓':'!'} ${c.name}`,'chip'));
  $('runLesson').disabled=!ready||jobBusy;
  $('terrainStart').disabled=!ready||jobBusy||!terrainBounds;
 }
 async function updateRuntime(){
  if(polling)return;polling=true;
   try{runtime=await api('/api/onboarding');controls();recentJobs(runtime.jobs||[]);}
  catch(e){$('runtimeStage').textContent='暫時無法連線到工作台，請確認 App 仍在執行。';}
  finally{polling=false;}
 }
 $('runtimeConsent').onchange=controls;
 action($('installRuntime'),async()=>{runtime=await api('/api/setup-start',{consent:$('runtimeConsent').checked});controls();});
 action($('recheckRuntime'),async()=>{runtime=await api('/api/setup-start',{consent:true,recheck:true});controls();});
 action($('stopRuntime'),async()=>{await api('/api/setup-stop',{});await updateRuntime();});
 setInterval(()=>{if(!$('welcome').hidden||runtime.status==='running')updateRuntime();},2500);
 updateRuntime();

 function lessonResult(r){lesson=r;$('lessonStatus').textContent=`完成：${r.dates.length} 個日期、${r.pair_count} 組配對，已核對合成答案。`;$('lessonResults').hidden=false;paintLesson();}
 function importResult(r){imported=r;dem=null;wbd=null;$('guidedDem').value='';$('guidedWbd').value='';$('applyGuided').disabled=true;$('organizeImport').hidden=true;if(r.inspection){plan=r.inspection;renderImportMetadata(plan);$('useImageBounds').hidden=!plan.metadata?.bounds;}else{plan=null;$('useImageBounds').hidden=true;}$('importStatus').textContent='資料已整理。接著確認地形資料。';$('guidedTerrain').hidden=false;$('guidedTerrain').scrollIntoView({behavior:'smooth',block:'start'});}
 async function terrainResult(r){await window.onboardingTerrainReady(r.dem,r.wbd);$('terrainStatus').textContent='地形已下載、轉換並通過描述檔檢查，已帶入上方欄位。';}
 function recentJobs(jobs){
  const signature=JSON.stringify(jobs);if(signature===jobsSignature)return;jobsSignature=signature;
  $('recentPreparation').hidden=!jobs.length;$('preparationJobs').replaceChildren();
  $('stopPreparation').hidden=!jobs.some(j=>j.status==='running');
  for(const j of jobs){
   const row=node('div',undefined,'inline'),label={import:'資料匯入',demo:'教學運算',terrain:'地形資料'}[j.kind]||j.kind;
   row.append(node('p',`${label} · ${j.stage}`,'hint'));
   const b=node('button',j.status==='complete'?'使用結果':'查看進度');row.append(b);
   action(b,async()=>{
    const job=await api('/api/onboarding-job?job='+encodeURIComponent(j.id));
    if(job.status==='failed')throw Error(job.error);
    showPanel(j.kind==='demo'?'lessonPanel':'importPanel');
    if(j.kind==='terrain')$('guidedTerrain').hidden=false;
    await watchJob(job,{import:'importStatus',demo:'lessonStatus',terrain:'terrainStatus'}[j.kind],{import:importResult,demo:lessonResult,terrain:terrainResult}[j.kind]);
   });$('preparationJobs').append(row);
  }
 }
 action($('stopPreparation'),()=>api('/api/onboarding-stop',{}));

 async function watchJob(job,status,complete){
  jobBusy=true;controls();
  try{
   for(;;){
    $(status).textContent=job.stage;
    if(status==='terrainStatus'&&job.output){$('terrainOutput').hidden=false;$('terrainOutput').textContent=job.output;}
    if(job.status==='complete'){await complete(job.result);break;}
    if(job.status==='failed')throw Error(job.error||'工作未完成。');
    await new Promise(resolve=>setTimeout(resolve,1200));
    job=await api('/api/onboarding-job?job='+encodeURIComponent(job.id));
   }
  }catch(e){$(status).textContent=e.message;throw e;}
  finally{jobBusy=false;controls();}
 }
 action($('runLesson'),async()=>{
  $('lessonResults').hidden=true;
  const job=await api('/api/demo-start',{});
  await watchJob(job,'lessonStatus',lessonResult);
 });
 function paintLesson(){
  if(!lesson)return;
  let row=Math.max(0,Math.min(15,Math.round(Number($('lessonRow').value)||0))),col=Math.max(0,Math.min(19,Math.round(Number($('lessonCol').value)||0)));
  $('lessonRow').value=row;$('lessonCol').value=col;
  const canvas=$('lessonMap'),ctx=canvas.getContext('2d'),cell=20,limit=12;
  for(let y=0;y<lesson.height;y++)for(let x=0;x<lesson.width;x++){
   const v=lesson.velocity_mm_year[y][x],t=Math.min(1,Math.abs(v)/limit),white=Math.round(247*(1-t));
   ctx.fillStyle=v>=0?`rgb(${Math.round(247-64*t)},${white},${Math.round(247-206*t)})`:`rgb(${Math.round(247-213*t)},${Math.round(247-127*t)},${Math.round(247-71*t)})`;
   ctx.fillRect(x*cell,y*cell,cell,cell);
  }
  ctx.strokeStyle='#172f28';ctx.lineWidth=3;ctx.strokeRect(col*cell+2,row*cell+2,cell-4,cell-4);
  const x=lesson.dates.map(d=>`${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6)}`),values=lesson.timeseries_mm.map(d=>d[row][col]);
  Plotly.react($('lessonPlot'),[{x,y:values,type:'scatter',mode:'lines+markers',name:'MintPy 反演',line:{color:'#32664e'},hovertemplate:'%{x}<br>%{y:.3f} mm<extra></extra>'}],{title:{text:'相對第一期的 LOS 位移',font:{size:15}},margin:{t:45,r:18,b:65,l:62},xaxis:{type:'date',title:'觀測日期'},yaxis:{title:'位移（mm）',zeroline:true},paper_bgcolor:'transparent',plot_bgcolor:'#fafcf8',font:{family:'system-ui',color:'#345247'},height:340},{responsive:true,displaylogo:false,modeBarButtonsToRemove:['sendDataToCloud']});
  $('lessonPoint').textContent=`合成格網：列 ${row}、欄 ${col} · 速度 ${lesson.velocity_mm_year[row][col].toFixed(2)} mm/year。藍色遠離、紅色朝向衛星；色階 −12 ～ +12 mm/year。`;
  $('lessonAccuracy').textContent=`全圖最大位移誤差 ${lesson.max_error_mm.toExponential(2)} mm。這是和已知合成答案的差異，不是真實觀測的不確定度。`;
 }
 $('lessonRow').onchange=paintLesson;$('lessonCol').onchange=paintLesson;
 $('lessonMap').onclick=e=>{const b=e.currentTarget.getBoundingClientRect();$('lessonCol').value=Math.floor((e.clientX-b.left)/b.width*20);$('lessonRow').value=Math.floor((e.clientY-b.top)/b.height*16);paintLesson();};

 function resetPlan(){terrainBounds=null;$('terrainStart').hidden=true;$('terrainPlanText').textContent='';window.clearImportMap?.();plan=null;imported=null;dem=null;wbd=null;$('useImageBounds').hidden=true;$('guidedDem').value='';$('guidedWbd').value='';$('guidedTerrain').hidden=true;$('applyGuided').disabled=true;$('organizeImport').hidden=true;$('importInventory').replaceChildren();$('importStatus').textContent='來源已更新，請重新檢查。';}
 $('importSource').oninput=resetPlan;
 $('importEnvironment').onchange=resetPlan;
 action($('pickImportFolder'),()=>choosePath('dir',p=>{$('importSource').value=p;resetPlan();},$('importSource').value));
 action($('pickImportZip'),()=>choosePath('file',p=>{$('importSource').value=p;resetPlan();}));
 action($('scanImport'),async()=>{
  const path=$('importSource').value,environment=$('importEnvironment').value;resetPlan();$('scanImport').disabled=true;$('importStatus').textContent='正在讀取檔案描述與影像邊界，不會更動原檔…';
  try{
   const d=await api('/api/import-scan',{path,environment});if($('importSource').value!==path||$('importEnvironment').value!==environment)return;plan=d;
   renderPlan(d);
  }finally{$('scanImport').disabled=false;}
 });
 function renderPlan(d){
  plan=d;renderImportMetadata(d,async keys=>{const selected=await api('/api/import-select',{plan:d.id,keys});if(plan!==d)return;renderPlan(selected);});
  $('importStatus').textContent=`${d.dates.length} 個日期 · ${d.file_count} 個影像檔 · ${(d.bytes/1024**3).toFixed(2)} GB`;
  $('organizeImport').hidden=!d.can_import;
  $('useImageBounds').hidden=!d.metadata?.bounds;
 }
 action($('organizeImport'),async()=>{
  if(!plan)throw Error('請先檢查資料。');$('organizeImport').disabled=true;
  try{const selected=plan;const job=await api('/api/import-start',{plan:selected.id});await watchJob(job,'importStatus',r=>{if(plan===selected)importResult(r);});}
  finally{$('organizeImport').disabled=false;}
 });
 function ready(){const bounds=plan?.metadata?.bounds;const covers=t=>!bounds||t&&t.bounds[0]<=bounds[0]&&t.bounds[1]>=bounds[1]&&t.bounds[2]<=bounds[2]&&t.bounds[3]>=bounds[3];const coverageOK=covers(dem)&&covers(wbd);$('guidedReady').textContent=!coverageOK?'地形範圍未涵蓋全部影像，請重新選取或下載。':bounds?'地形需涵蓋全部影像邊界；下一頁仍會執行處理前檢查。':'影像完整範圍未確認，請自行確認 DEM／WBD 涵蓋影像後再建立專案。';const ok=!!(imported&&dem&&wbd&&coverageOK);$('applyGuided').disabled=!ok;return ok;}
 async function terrain(kind,path){
  const id=kind==='dem'?'guidedDem':'guidedWbd';$(id).value=path;$(id+'Info').textContent='正在核對地形描述…';
  if(kind==='dem')dem=null;else wbd=null;ready();
  try{const value=await api('/api/terrain-check',{path,kind});if($(id).value!==path)return;if(kind==='dem')dem=value;else wbd=value;$(id+'Info').textContent=`${value.width} × ${value.height} · 南／北／西／東 ${value.bounds.map(x=>x.toFixed(3)).join('、')}。${value.note}`;}
  catch(e){$(id+'Info').textContent=e.message;throw e;}finally{ready();}
 }
 action($('pickGuidedDem'),()=>choosePath('file',p=>terrain('dem',p)));
 action($('pickGuidedWbd'),()=>choosePath('file',p=>terrain('wbd',p)));
 $('guidedDem').onchange=()=>terrain('dem',$('guidedDem').value).catch(fail);
 $('guidedWbd').onchange=()=>terrain('wbd',$('guidedWbd').value).catch(fail);
 $('guidedDem').oninput=()=>{dem=null;ready();$('guidedDemInfo').textContent='路徑已更新，請檢查地形檔。';};
 $('guidedWbd').oninput=()=>{wbd=null;ready();$('guidedWbdInfo').textContent='路徑已更新，請檢查地形檔。';};
 action($('checkGuidedTerrain'),async()=>{await terrain('dem',$('guidedDem').value);await terrain('wbd',$('guidedWbd').value);});
 // Terrain download helper can return validated local outputs here.
 window.onboardingTerrainReady=async(d,w)=>{await terrain('dem',d);await terrain('wbd',w);};
 const boundIds=['terrainSouth','terrainNorth','terrainWest','terrainEast'];
 action($('useImageBounds'),()=>{
  const bounds=plan?.metadata?.bounds;if(!bounds)throw Error('影像邊界尚未確認。');
  const padded=[bounds[0]-.1,bounds[1]+.1,bounds[2]-.1,bounds[3]+.1];
  boundIds.forEach((id,i)=>{$(id).value=padded[i].toFixed(5);});terrainBounds=null;$('terrainStart').hidden=true;
  $('terrainPlanText').textContent='已帶入全部影像的概略外框，四邊各加 0.1° 緩衝；請核對範圍並預覽下載。不是 MintPy 裁切範圍。';
 });
 for(const id of boundIds)$(id).oninput=()=>{terrainBounds=null;$('terrainStart').hidden=true;$('terrainPlanText').textContent='範圍已修改，請重新預覽。';};
 action($('terrainPlan'),async()=>{
  if(boundIds.some(id=>!$(id).value.trim()))throw Error('請輸入完整的四個經緯度。');
  const bounds=boundIds.map(id=>Number($(id).value));const p=await api('/api/terrain-plan',{bounds});
  if(boundIds.some((id,i)=>Number($(id).value)!==bounds[i]||!$(id).value.trim()))return;terrainBounds=bounds;
  $('terrainPlanText').textContent=`${p.tiles.length} 個一度圖幅 · 約 ${(p.pixels/1e6).toFixed(1)} 百萬像素 · 建議保留 ${p.estimated_disk_gb} GB。${p.note}`;
  $('terrainStart').hidden=false;$('terrainStart').disabled=runtime.status!=='ready';
  if(runtime.status!=='ready')$('terrainPlanText').textContent+=' 請先完成上方的運算環境安裝。';
 });
 action($('terrainStart'),async()=>{
  if(!terrainBounds)throw Error('請先預覽地形範圍。');$('terrainStart').disabled=true;
  try{const job=await api('/api/terrain-start',{bounds:terrainBounds});await watchJob(job,'terrainStatus',terrainResult);}
  finally{$('terrainStart').disabled=runtime.status!=='ready';}
 });
 action($('applyGuided'),async()=>{
  if(!ready())throw Error('請先完成資料與地形檢查。');
  await refresh();newView();
  const workflow=$('guidedWorkflow').value;document.querySelector(`[data-workflow="${workflow}"]`).click();
  $('name').value=$('guidedName').value.trim()||'我的 ALOS-2 研究';
  $('root').value=runtime.home+'/projects/alos-'+Date.now().toString(36);
  $('source').value=imported.path;$('dem').value=dem.path;$('dem_geo').value=dem.path;$('wbd').value=wbd.path;
  $('env').value=runtime.environment||defaults.env;$('copy').checked=false;
  const rows=await scanDates();
  $('reference').value=rows[workflow==='alos2App'?0:Math.floor(rows.length/2)].date;
  $('reference').dispatchEvent(new Event('change'));
  if(workflow==='alosStack'){
   const pol=rows[0].polarizations.filter(p=>rows.every(r=>r.polarizations.includes(p)));
   if(pol.length){properties.polarization=pol.includes('HH')?'HH':pol[0];renderProperties();}
  }
  $('templateNote').textContent='已帶入資料與地形。多日期預選中間日期作為參考，請依影像品質確認；其餘未指定參數沿用 ISCE2 預設。建立專案後仍會先顯示摘要。';
  window.scrollTo({top:0,behavior:'smooth'});
 });
 if(window.workbenchState)window.onboardingUpdate(window.workbenchState);
})();
