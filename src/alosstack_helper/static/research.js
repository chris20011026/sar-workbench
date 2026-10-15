// Read-only comparison and report creation. Scientific runs stay immutable.
(()=>{
 const host=node('section',undefined,'card research-studio');host.id='researchStudio';host.hidden=true;
 host.innerHTML=`<div class="research-heading"><div><div class="eyebrow">RESULTS & REPRODUCIBILITY</div><h2>比較成果，整理研究紀錄</h2><p class="muted">從已完成的 MintPy 分析選擇版本。比較結果另存，不改動原始分析。</p></div><span class="badge">研究成果</span></div>
 <div class="research-tabs" role="tablist" aria-label="成果工具"><button id="researchCompareTab" role="tab" aria-controls="researchComparePanel" aria-selected="true" class="selected">比較成果</button><button id="researchReportTab" role="tab" aria-controls="researchReportPanel" aria-selected="false" tabindex="-1">研究報告</button></div>
 <div id="researchEmpty" class="research-empty" hidden></div>
 <div id="researchComparePanel" role="tabpanel" aria-labelledby="researchCompareTab"><div class="research-selectors"><label>分析 A<select id="researchLeft"></select></label><label>分析 B<select id="researchRight"></select></label></div><div class="research-actions"><button id="researchCompareStart" class="primary">建立成果比較</button><button id="researchCompareStop" hidden>停止比較</button><label class="grow">已儲存的比較<select id="researchPast"><option value="">選擇比較紀錄</option></select></label></div><p id="researchCompareStatus" class="research-status" role="status" aria-live="polite"></p><div id="researchComparison"></div></div>
 <div id="researchReportPanel" role="tabpanel" aria-labelledby="researchReportTab" hidden><div class="research-report-form"><label>主要分析<select id="researchReportRun"></select></label><div class="research-selectors"><label>報告標題<input id="researchReportTitle" maxlength="160"></label><label>作者（選填）<input id="researchReportAuthor" maxlength="120" placeholder="姓名或研究團隊"></label></div><label>研究者說明（選填）<textarea id="researchReportNotes" maxlength="12000" placeholder="研究問題、判讀結果及需要進一步確認的事項"></textarea></label><label class="check"><input id="researchReportCompare" type="checkbox">附上目前完成的兩版本比較</label><label class="check"><input id="researchReportPoint" type="checkbox">附上目前選取的原始點位曲線</label><p id="researchReportSelection" class="hint"></p><label class="check"><input id="researchReportPaths" type="checkbox">包含本機完整資料路徑（不勾選時只保留檔名）</label></div><div class="research-actions"><button id="researchReportStart" class="primary">產生可編輯研究報告</button><button id="researchReportStop" hidden>停止整理</button></div><p class="hint">提供 Word、可列印網頁與 JSON 設定快照。報告整理可核對的紀錄，科學結論由研究者填寫。</p><p id="researchReportStatus" role="status" aria-live="polite"></p><div id="researchReports" class="research-history"></div></div>`;
 $('analysisWorkbench').after(host);
 let key=null,runs=[],comparison=null,compareId=null,reportId=null,timer=null,generation=0,pointTicket=0,loading=false,busy=false;
 let urls=[],maps=[],selectedPoint=null,chart=null,signature='',historySignature='',renderEpoch=0,historyTicket=0;
 const $r=id=>document.getElementById(id),button=(text,fn,cls)=>{const b=node('button',text,cls);action(b,fn);return b;};
 const runLabel=r=>(r.options?.name||r.name||r.created||r.id)+' · '+r.id;
 const progressText=p=>typeof p==='object'?[p.stage,p.detail].filter(Boolean).join(' · '):p;
 function setTab(report){$r('researchReportPanel').hidden=!report;$r('researchComparePanel').hidden=report;for(const [id,selected] of [['researchReportTab',report],['researchCompareTab',!report]]){const b=$r(id);b.classList.toggle('selected',selected);b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1;}setTimeout(refreshMapSizes,60);updateReportSelection();}
 $r('researchCompareTab').onclick=()=>setTab(false);$r('researchReportTab').onclick=()=>setTab(true);
 for(const id of ['researchCompareTab','researchReportTab'])$r(id).onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const report=e.key==='End'||(e.key!=='Home'&&id==='researchCompareTab');setTab(report);$r(report?'researchReportTab':'researchCompareTab').focus();}};
 function cleanup(){renderEpoch++;pointTicket++;for(const m of maps){geoMaps.delete(m);m.remove();}maps=[];for(const u of urls)URL.revokeObjectURL(u);urls=[];if(chart&&chart.classList.contains('js-plotly-plot'))Plotly.purge(chart);chart=null;selectedPoint=null;$r('researchComparison').replaceChildren();}
 async function blob(path){const r=await fetch(path,{headers:{'X-Token':token}});if(!r.ok){const e=await r.json().catch(()=>({}));throw Error(e.error||'無法讀取衍生成果');}return r.blob();}
 function compPath(file,c=comparison,project=c?.project_id||key){return '/api/comparison-artifact?'+new URLSearchParams({id:project,comparison:c.id,file});}
 async function compUrl(file,c,isCurrent=()=>true){const u=URL.createObjectURL(await blob(compPath(file,c)));if(!isCurrent()){URL.revokeObjectURL(u);return null;}urls.push(u);return u;}
 async function downloadReport(report,file){const content=await blob('/api/research-report-artifact?'+new URLSearchParams({id:key,report,file}));downloadBlob(content,file,content.type);}
 function updateControls(){const enough=runs.length>=2; $r('researchCompareStart').disabled=!enough||busy||$r('researchLeft').value===$r('researchRight').value;$r('researchReportStart').disabled=!runs.length||busy;}
 function fillSelect(id,items){const s=$r(id),old=s.value;s.replaceChildren();for(const r of items){const o=node('option',runLabel(r));o.value=r.id;if(r.available===false){o.disabled=true;o.textContent+=' · '+r.reason;}s.append(o);}if(items.some(r=>r.id===old))s.value=old;}
 window.researchUpdate=async(s,data)=>{
  host.hidden=!(s.status==='complete'||s.status==='imported')||s.config.workflow!=='alosStack'&&s.config.workflow!==undefined;
  if(host.hidden){if(key&&key!==s.id){generation++;cleanup();key=null;}return;}
  busy=data.busy;
  if(key!==s.id){key=s.id;generation++;signature='';historySignature='';compareId=null;reportId=null;comparison=null;cleanup();$r('researchReportTitle').value=(s.display_name||s.config.name||'SAR')+' 研究成果報告';$r('researchReportNotes').value='';$r('researchReportCompare').checked=false;$r('researchReportPoint').checked=false;$r('researchCompareStatus').textContent='';$r('researchReportStatus').textContent='';$r('researchPast').replaceChildren(node('option','選擇比較紀錄'));$r('researchPast').firstChild.value='';$r('researchReports').replaceChildren();}
  if(loading)return;loading=true;const ticket=generation,project=key;
  try{const d=await api('/api/comparison-candidates?id='+encodeURIComponent(project));if(ticket!==generation)return;runs=d.runs||d.candidates||[];const stamp=JSON.stringify(runs);if(stamp!==signature){signature=stamp;for(const id of ['researchLeft','researchRight','researchReportRun'])fillSelect(id,runs);if(runs.length>1&&$r('researchLeft').value===$r('researchRight').value)$r('researchRight').selectedIndex=1;}
   $r('researchEmpty').hidden=runs.length>=2;$r('researchEmpty').textContent=runs.length?'目前只有一個完成的分析，可以先產生研究報告；建立第二個分析版本後即可比較。':'完成 MintPy 分析後，這裡會提供成果比較與報告。';updateControls();await loadHistory(ticket);updateReportSelection();
  }catch(e){if(ticket===generation)$r('researchCompareStatus').textContent=e.message;}finally{loading=false;}
 };
 for(const id of ['researchLeft','researchRight'])$r(id).onchange=updateControls;
 $r('researchReportRun').onchange=updateReportSelection;
 function currentReportPoint(){const run=$r('researchReportRun').value;if(selectedPoint){const p=selectedPoint[comparison?.left?.id===run?'left':'right'];if(p?.available!==false&&p?.analysis_id===run)return p;}if(typeof lastPoint!=='undefined'&&lastPoint?.analysis_id===run)return lastPoint;return null;}
 function updateReportSelection(){const run=$r('researchReportRun').value,p=currentReportPoint(),canCompare=comparison?.status==='complete'&&[comparison.left?.id,comparison.right?.id].includes(run);$r('researchReportCompare').disabled=!canCompare;if(!canCompare)$r('researchReportCompare').checked=false;$r('researchReportPoint').disabled=!p;if(!p)$r('researchReportPoint').checked=false;$r('researchReportSelection').textContent=p?`可附上點位：原始行 ${p.y}、列 ${p.x}。產生時會重新讀取數值。`:'要附上曲線，先在所選分析或比較地圖選取一個有效點位。';}
 async function loadHistory(ticket=generation){const [comp,reports]=await Promise.all([api('/api/comparisons?id='+encodeURIComponent(key)),api('/api/research-reports?id='+encodeURIComponent(key))]);if(ticket!==generation)return;const list=comp.comparisons||comp.items||[];const old=$r('researchPast').value;$r('researchPast').replaceChildren(node('option','選擇比較紀錄'));$r('researchPast').firstChild.value='';for(const c of list){const o=node('option',(c.created||c.id)+' · '+({complete:'已完成',running:'比較中',queued:'準備中',failed:'未完成',cancelled:'已停止'}[c.status]||c.status));o.value=c.id;$r('researchPast').append(o);}const selected=comparison?.id||compareId||old;if(list.some(c=>c.id===selected))$r('researchPast').value=selected;
  const saved=reports.reports||[],stamp=JSON.stringify(saved);if(stamp!==historySignature){historySignature=stamp;const box=$r('researchReports');box.replaceChildren(node('h3','報告紀錄'));if(!saved.length)box.append(node('p','尚未產生報告。','hint'));for(const r of saved){const row=node('div');row.append(node('strong',r.title),node('p',r.created+' · '+({complete:'已完成',running:'整理中',failed:'產生失敗',cancelled:'已停止'}[r.status]||r.status),'hint'));if(r.error)row.append(node('p',r.error,'error'));if(r.status==='complete'){const bar=node('div',undefined,'research-downloads');for(const [file,title] of [['report.docx','可編輯 Word'],['report.html','可列印網頁'],['report.json','設定與來源 JSON'],['research-report.zip','下載完整報告包']])bar.append(button(title,()=>downloadReport(r.id,file)));row.append(bar);}box.append(row);}}
 }
 function poll(){clearTimeout(timer);if(!compareId&&!reportId)return;timer=setTimeout(async()=>{const ticket=generation,selection=historyTicket,requestedCompare=compareId;try{if(requestedCompare){const c=await api('/api/comparison?'+new URLSearchParams({id:key,comparison:requestedCompare}));if(ticket!==generation||requestedCompare!==compareId)return;$r('researchCompareStatus').textContent=c.status==='failed'?(c.error||'比較未完成。'):c.status==='cancelled'?'比較已停止。':c.status==='complete'?'比較完成。':progressText(c.progress)||c.message||'正在核對來源與計算共同有效像素…';if(!['running','queued'].includes(c.status)){if(c.status==='complete'&&selection===historyTicket&&(!comparison||comparison.id===c.id))await renderComparison(c,ticket);compareId=null;$r('researchCompareStop').hidden=true;}}
    if(reportId){const r=await api('/api/research-report?'+new URLSearchParams({id:key,report:reportId}));if(ticket!==generation)return;$r('researchReportStatus').textContent=r.status==='complete'?'報告已完成，可在下方下載。':r.status==='failed'?r.error:'正在整理圖表、設定與來源紀錄…';if(r.status!=='running'){reportId=null;$r('researchReportStop').hidden=true;}}
    if(ticket===generation){await loadHistory(ticket);await refresh();}
   }catch(e){if(ticket===generation)$r('researchCompareStatus').textContent=e.message;}finally{if(ticket===generation)poll();}},1800);}
 action($r('researchCompareStart'),async()=>{const project=key,ticket=generation;historyTicket++;cleanup();comparison=null;$r('researchCompareStart').disabled=true;const c=await api('/api/comparison-start',{id:project,left:$r('researchLeft').value,right:$r('researchRight').value});if(ticket!==generation)return;compareId=c.id;$r('researchCompareStop').hidden=false;$r('researchCompareStatus').textContent='正在核對來源與比較條件…';poll();await refresh();});
 action($r('researchCompareStop'),async()=>{if(compareId)await api('/api/comparison-stop',{id:key,comparison:compareId});});
 $r('researchPast').onchange=async()=>{const id=$r('researchPast').value;if(!id)return;const ticket=generation,selection=++historyTicket;cleanup();comparison=null;updateReportSelection();try{const c=await api('/api/comparison?'+new URLSearchParams({id:key,comparison:id}));if(ticket!==generation||selection!==historyTicket)return;if(c.status==='complete')await renderComparison(c,ticket);else{$r('researchCompareStatus').textContent=c.error||progressText(c.progress)||c.status;if(['running','queued'].includes(c.status)){compareId=id;poll();}}}catch(e){if(ticket===generation&&selection===historyTicket)fail(e);}};
 action($r('researchReportStart'),async()=>{updateReportSelection();const ticket=generation,p=currentReportPoint();const r=await api('/api/research-report-start',{id:key,run:$r('researchReportRun').value,title:$r('researchReportTitle').value,author:$r('researchReportAuthor').value,notes:$r('researchReportNotes').value,include_paths:$r('researchReportPaths').checked,comparison:$r('researchReportCompare').checked?comparison.id:null,point:$r('researchReportPoint').checked&&p?{y:p.y,x:p.x}:null});if(ticket!==generation)return;reportId=r.id;$r('researchReportStop').hidden=false;$r('researchReportStatus').textContent='正在產生報告…';poll();await refresh();});
 action($r('researchReportStop'),async()=>{if(reportId)await api('/api/research-report-stop',{id:key,report:reportId});});
 const checkLabels={grid:'原生經緯度格網',provenance:'來源處理產物',reference_point:'空間參考點',reference_date:'時間參考日期',units:'速度與位移單位',los:'衛星視線與軌道',time_model:'速度時間模型',fit_dates:'實際擬合日期',timeseries_dates:'時間序列日期',common_pixels:'共同有效像素'};
 const settingLabels={name:'分析名稱',reference_date:'時間參考日期',reference_coherence:'參考點最低相干性',temporal_coherence:'時間相干性門檻',deramp:'移除空間斜坡',dem_error:'DEM 誤差校正',dem_order:'DEM 校正多項式階數',weight:'反演加權方法',redundancy:'最低網路冗餘',memory:'記憶體上限（GiB）',subset:'原生像素裁切範圍',subset_lalo:'經緯度裁切範圍',start_date:'分析起始日期',end_date:'分析結束日期',excluded:'排除配對',excluded_dates:'排除日期',retained_dates:'保留日期',used:'採用配對','mintpy.timeFunc.polynomial':'時間模型多項式階數','mintpy.timeFunc.periodic':'週期項（年）','mintpy.timeFunc.stepDate':'階躍事件日期','mintpy.timeFunc.exp':'指數衰減項','mintpy.timeFunc.log':'對數衰減項','mintpy.timeFunc.startDate':'速度擬合起始日期','mintpy.timeFunc.endDate':'速度擬合結束日期','mintpy.timeFunc.excludeDate':'速度擬合排除日期','mintpy.timeFunc.uncertaintyQuantification':'速度不確定度方法','mintpy.troposphericDelay.method':'對流層校正方法','mintpy.solidEarthTides':'固體潮校正','mintpy.unwrapError.method':'解纏錯誤修正','mintpy.topographicResidual':'DEM 誤差校正','mintpy.networkInversion.weightFunc':'網路反演加權方法','mintpy.networkInversion.minTempCoh':'時間相干性門檻'};
 const numeric=(v,d=3)=>v===null||v===undefined||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('zh-TW',{maximumFractionDigits:d});
 const dateLabel=v=>v?isoDate(String(v)):'未記錄';
 function friendly(v,present=true){
  if(!present)return '未設定';if(v===null||v===undefined)return '未指定';if(v===true)return '啟用';if(v===false)return '關閉';
  if(typeof v==='number')return numeric(v,6);if(Array.isArray(v))return v.length?v.map(x=>friendly(x)).join('、'):'無';
  if(typeof v==='object')return Object.entries(v).map(([k,x])=>(settingLabels[k]||k)+'：'+friendly(x)).join('；')||'無';
  const words={no:'不使用',none:'未指定',false:'關閉',true:'啟用',auto:'沿用程式預設',var:'變異數加權',coh:'相干性加權',fim:'Fisher 資訊加權',linear:'平面斜坡',quadratic:'二次曲面',residue:'模型殘差',bootstrap:'重抽樣'};
  return /^\d{6}$|^\d{8}$/.test(String(v))?dateLabel(v):(words[String(v).toLowerCase()]||String(v));
 }
 function makeTable(headers,rows){
  const table=node('table'),head=node('thead'),hr=node('tr'),body=node('tbody');for(const text of headers){const cell=node('th',text);cell.scope='col';hr.append(cell);}head.append(hr);
  for(const values of rows){const row=node('tr');values.forEach((v,i)=>{const cell=node(i===0?'th':'td');if(i===0)cell.scope='row';if(v instanceof Node)cell.append(v);else cell.textContent=String(v);row.append(cell);});body.append(row);}
  table.append(head,body);const wrap=node('div',undefined,'table-scroll');wrap.append(table);return wrap;
 }
 function referenceText(ref){return ref?'行 '+ref.y+'／列 '+ref.x+'；日期 '+dateLabel(ref.date):'參考基準未完整記錄';}
 function modelText(run){const m=run?.model;if(!m)return '時間模型未完整記錄；僅並列檢視成果。';const dates=m.dates||[];return (m.velocity_meaning||(m.nonlinear?'LOS 時間模型的一次項係數':'LOS 線性擬合速度'))+(dates.length?' 擬合 '+dates.length+' 期：'+dateLabel(dates[0])+' 至 '+dateLabel(dates.at(-1))+'。':'');}
 function summaryTables(target,c){
  const a=c.left.statistics||{},b=c.right.statistics||{};target.append(node('h3','有效範圍與品質'));
  target.append(makeTable(['指標','A · '+c.left.name,'B · '+c.right.name],[
   ['有效像素',numeric(a.valid_pixels,0),numeric(b.valid_pixels,0)],['格網總像素',numeric(a.total_pixels,0),numeric(b.total_pixels,0)],
   ['有效比例',a.total_pixels?numeric(a.valid_pixels/a.total_pixels*100,1)+'%':'—',b.total_pixels?numeric(b.valid_pixels/b.total_pixels*100,1)+'%':'—'],
   ['平均時間相干性（0–1）',numeric(a.temporal_coherence?.mean),numeric(b.temporal_coherence?.mean)],
   ['平均速度不確定度',numeric(a.velocity_std?.mean)+' '+c.left.unit,numeric(b.velocity_std?.mean)+' '+c.right.unit],
   ['空間與時間參考',referenceText(c.left.reference),referenceText(c.right.reference)],
   ['最終時序檔',c.left.selected_timeseries||'未記錄',c.right.selected_timeseries||'未記錄']
  ]));target.append(node('p','相干性與不確定度用於品質判讀，不能單獨當作準確率或選出較好的設定。','hint'));
  if(c.coverage)target.append(makeTable(['共同原生格網覆蓋','像素數'],[['A、B 皆有效',numeric(c.coverage.shared,0)],['只有 A 有效',numeric(c.coverage.left_only,0)],['只有 B 有效',numeric(c.coverage.right_only,0)],['兩側均無效',numeric(c.coverage.neither,0)]]));
  target.append(node('h3','速度數值統計'));
  target.append(makeTable(['指標','A（'+c.left.unit+'）','B（'+c.right.unit+'）',...(c.difference?.available?['B − A（mm/year）']:[])],
   Object.entries({mean:'平均',std:'標準差',rms:'均方根',min:'最小',max:'最大'}).map(([k,label])=>[label,numeric(a.velocity?.[k]),numeric(b.velocity?.[k]),...(c.difference?.available?[numeric(c.difference.statistics?.[k])]:[])])));
  target.append(node('p','A、B 統計各自有效範圍；B − A 統計共同有效像素。平均值不同也可能受到覆蓋範圍影響。','hint'));
 }
 async function renderComparison(c,ticket){
  if(ticket!==generation)return;cleanup();comparison=c;const epoch=renderEpoch,isCurrent=()=>ticket===generation&&epoch===renderEpoch&&comparison?.id===c.id;
  const target=$r('researchComparison'),compat=c.compatibility||{};$r('researchCompareStatus').textContent=c.colour_scale?.shared?'比較完成 · A 與 B 使用共同色階':'比較完成 · 單位未能統一，兩側色階各自呈現';
  target.append(node('p',compat.allow_difference?'格網、參考基準與模型核對通過，可查看 B − A 差值。':'可並排查看；目前不符合直接相減條件。','notice'));
  for(const why of compat.reasons||[])target.append(node('p',typeof why==='string'?why:why.reason||'比較條件未通過','analysis-warning'));
  if(c.colour_scale?.shared)target.append(node('p','A／B 共同色階：'+numeric(c.colour_scale.min)+' 至 '+numeric(c.colour_scale.max)+' '+c.colour_scale.unit+'。超出色階的值顯示為端點色；完整統計保留這些值。','hint'));
  const mapGrid=node('div',undefined,'research-map-grid');target.append(mapGrid);const mapList=[];
  for(const side of ['left','right']){
   const run=c[side],image=run?.map;if(!image)continue;const card=node('div',undefined,'research-map-card');card.append(node('h3',(side==='left'?'A · ':'B · ')+(run.name||run.id)),node('p',modelText(run),'hint'));mapGrid.append(card);
   await drawMap(card,image,side,c,ticket,mapList,isCurrent);if(!isCurrent())return;
   card.append(node('p','參考基準：'+referenceText(run.reference),'hint'),node('p',run.direction||'LOS 正負方向未記錄。','hint'));
  }
  if(compat.allow_difference&&c.difference?.map){const card=node('div',undefined,'research-map-card research-diff');card.append(node('h3','B − A · 共同有效像素'),node('p','正值表示 B 比 A 大；差值使用自己的對稱色階。','hint'));target.append(card);await drawMap(card,c.difference.map,'left',c,ticket,mapList,isCurrent);if(!isCurrent())return;}
  if(mapList.length>1){
   const bounds=L.latLngBounds([]);for(const map of mapList)bounds.extend(map._researchBounds);for(const map of mapList)map.fitBounds(bounds,{padding:[20,20],animate:false});
   let syncing=false;for(const map of mapList)map.on('moveend zoomend',()=>{if(syncing||!isCurrent())return;syncing=true;try{for(const other of mapList)if(other!==map&&(!other.getCenter().equals(map.getCenter(),1e-9)||other.getZoom()!==map.getZoom()))other.setView(map.getCenter(),map.getZoom(),{animate:false});}finally{syncing=false;}});
   target.append(node('p','地理圖共用觀看範圍；移動或縮放任一張，其他圖會同步。圓框標示各分析的空間參考點。','hint'));
  }
  const pointBox=node('div',undefined,'research-point');pointBox.append(node('h3','同一位置的位移曲線'));const hint=node('p','點選地圖中的有效像素；原生格網相同時，同時讀取兩個版本。','hint');hint.id='researchPointInfo';
  chart=node('div',undefined,'research-chart');pointBox.append(hint,chart);const refs=node('div');refs.id='researchPointReferences';pointBox.append(refs);
  const csv=button('下載點位 CSV',()=>{if(!selectedPoint)throw Error('請先選取有效像素。');const rows=['analysis,date,displacement_mm,latitude,longitude,y,x,reference_date,reference_y,reference_x'];for(const side of ['left','right']){const p=selectedPoint[side];if(p?.available)for(let i=0;i<p.dates.length;i++)rows.push([p.analysis_id,p.dates[i],p.displacement_mm[i],p.latitude,p.longitude,p.y,p.x,p.reference?.date||'',p.reference?.y??'',p.reference?.x??''].join(','));}downloadBlob(rows.join('\n'),'comparison-point.csv','text/csv;charset=utf-8');});csv.id='researchPointCsv';csv.disabled=true;pointBox.append(csv);target.append(pointBox);summaryTables(target,c);
  const details=node('details',undefined,'research-detail');details.append(node('summary','查看科學核對條件與設定差異'));
  details.append(node('h3','科學核對條件'),makeTable(['核對項目','狀態','說明'],(compat.checks||[]).map(v=>[checkLabels[v.code]||v.code,v.passed?'通過':'未通過',v.reason||(v.code==='grid'?'完整原生經緯度一致':'兩個版本的必要紀錄一致')])));
  const changes=c.options_diff||[];details.append(node('h3','處理設定差異'));
  if(changes.length)details.append(makeTable(['設定','A','B'],changes.map(v=>{const raw=v.key.replace(/^mintpy_overrides\./,''),label=node('span',settingLabels[raw]||'進階設定');label.append(node('code',raw,'mint-key'));return [label,friendly(v.left,v.left_present!==false),friendly(v.right,v.right_present!==false)];})));
  else details.append(node('p','已記錄的要求設定相同。','hint'));
  details.append(node('p','此表比較儲存的要求設定；科學核對另外使用實際 HDF5 與時間模型紀錄。相同 auto 不保證不同軟體版本採用相同預設值。','hint'));
  target.append(details,node('p','差值用於觀察處理方法的影響，不代表真實誤差；地圖為抽樣展示，統計與點位使用原生資料。','hint'));updateReportSelection();setTimeout(()=>{if(isCurrent())refreshMapSizes();},100);
 }
 async function drawMap(card,im,side,c,ticket,mapList,isCurrent=()=>ticket===generation){
  try{
   const uri=await compUrl(im.file,c,isCurrent);if(!uri||!isCurrent())return;
   if(im.coordinate_system==='EPSG:3857'&&im.bounds&&im.native_index_file){
    const el=node('div',undefined,'research-map');card.append(el);const {map,base}=geoBase(el,im.bounds);map._researchBounds=L.latLngBounds(im.bounds);map._researchSide=side;maps.push(map);mapList.push(map);
    base.options[0].textContent='離線（不載入底圖）';card.append(labeled('底圖',base),node('p',im.meaning||'地理預覽使用最近鄰顯示，點位回查原生像素。','hint map-status'));L.imageOverlay(uri,im.bounds,{opacity:.85,interactive:false}).addTo(map);
    const ref=c[side]?.reference;if(ref&&Number.isFinite(ref.latitude)&&Number.isFinite(ref.longitude))L.circleMarker([ref.latitude,ref.longitude],{radius:7,color:'#263d35',weight:2,fill:false}).bindTooltip(node('span',(side==='left'?'A':'B')+' 空間參考點')).addTo(map);
    const response=await fetch(compPath(im.native_index_file,c),{headers:{'X-Token':token}});if(!response.ok)throw Error('無法載入地圖位置索引。');const buffer=await response.arrayBuffer();if(!isCurrent())return;
    if(buffer.byteLength!==im.width*im.height*4)throw Error('地圖位置索引尺寸不符。');const idx=new DataView(buffer);
    map.on('click',e=>{if(!isCurrent())return;const pt=map.options.crs.project(e.latlng),tr=im.transform,sw=map.options.crs.project(L.latLng(im.bounds[0])),ne=map.options.crs.project(L.latLng(im.bounds[1]));
     const x=Array.isArray(tr)?Math.floor((pt.x-tr[0])/tr[1]):Math.floor((pt.x-sw.x)/(ne.x-sw.x)*im.width),y=Array.isArray(tr)?Math.floor((pt.y-tr[3])/tr[5]):Math.floor((ne.y-pt.y)/(ne.y-sw.y)*im.height);
     if(x<0||y<0||x>=im.width||y>=im.height){clearPoint('超出比較圖範圍。');return;}const n=idx.getInt32((y*im.width+x)*4,true),width=im.native_shape?.[1]||c[side]?.native_shape?.[1];
     if(n<0||!width){clearPoint('此位置沒有原生像素對應。');return;}selectPoint(Math.floor(n/width),n%width,side,c);
    });
   }else{
    const img=node('img');img.src=uri;img.alt='原生雷達座標預覽';img.className='native-preview';
    img.onclick=e=>{if(!isCurrent())return;const rect=img.getBoundingClientRect(),stride=im.preview_stride||1,y=Math.min(im.height-1,Math.max(0,Math.floor((e.clientY-rect.top)/rect.height*im.height))),x=Math.min(im.width-1,Math.max(0,Math.floor((e.clientX-rect.left)/rect.width*im.width)));selectPoint(y*stride,x*stride,side,c);};
    card.append(img,node('p',im.warning||'原生雷達座標預覽；此成果未提供可用的地理預覽。','hint'));
   }
   if(!isCurrent())return;const legend=node('div',undefined,'research-legend'),labels=node('div',undefined,'research-legend-text');legend.setAttribute('role','img');legend.setAttribute('aria-label','由藍色負值、白色零到紅色正值');
   labels.append(node('span',numeric(im.min,2)),node('span','0 · '+(im.unit||c[side]?.unit||'未確認單位')),node('span',numeric(im.max,2)));card.append(legend,labels);if(im.preview_stride>1)card.append(node('p','畫面每 '+im.preview_stride+' 個原生像素取樣；完整統計與原生點位不降採樣。','hint'));
  }catch(error){if(isCurrent())card.append(node('p','地圖暫時無法顯示：'+error.message,'analysis-warning'));}
 }
 function clearPoint(message){
  pointTicket++;selectedPoint=null;for(const map of maps)if(map._researchMarker){map.removeLayer(map._researchMarker);map._researchMarker=null;}
  if(chart){if(chart.classList.contains('js-plotly-plot'))Plotly.purge(chart);chart.replaceChildren();}
  if($r('researchPointInfo'))$r('researchPointInfo').textContent=message;if($r('researchPointReferences'))$r('researchPointReferences').replaceChildren();if($r('researchPointCsv'))$r('researchPointCsv').disabled=true;updateReportSelection();
 }
 async function selectPoint(y,x,side,c){
  if(comparison?.id!==c.id||!chart)return;clearPoint('正在讀取原始像素…');const ticket=pointTicket,gen=generation,project=key,target=chart,isCurrent=()=>ticket===pointTicket&&gen===generation&&comparison?.id===c.id&&chart===target&&target.isConnected;
  try{
   const d=await api('/api/comparison-point',{id:project,comparison:c.id,y,x,side});if(!isCurrent())return;selectedPoint=d;const both=d.left?.available&&d.right?.available,sameReference=both&&JSON.stringify(d.left.reference)===JSON.stringify(d.right.reference)&&d.left.reference!==null;
   const traces=[];for(const [which,color,prefix] of [['left','#245c49','A'],['right','#a65b36','B']]){const p=d[which];if(p?.available)traces.push({x:p.dates.map(isoDate),y:p.displacement_mm,name:prefix+' · '+(c[which]?.name||p.analysis_id)+(both&&!sameReference?'（各自基準）':''),mode:'lines+markers',line:{color},marker:{size:6},hovertemplate:'%{x|%Y-%m-%d}<br>%{y:.2f} mm<extra>%{fullData.name}</extra>'});}
   if(!traces.length)throw Error('兩個版本在此位置都沒有有效曲線。');if(d.difference)traces.push({x:d.difference.dates.map(isoDate),y:d.difference.displacement_mm,name:'B − A（共同基準）',mode:'lines+markers',visible:'legendonly',line:{color:'#585a66',dash:'dot'},hovertemplate:'%{x|%Y-%m-%d}<br>差值 %{y:.2f} mm<extra></extra>'});
   const p=d[side]?.available?d[side]:d.left?.available?d.left:d.right;$r('researchPointInfo').textContent='緯度 '+numeric(p.latitude,6)+'，經度 '+numeric(p.longitude,6)+' · 原生行 '+p.y+'／列 '+p.x+(d.same_coordinate?' · 相同原生位置':' · 另一格網未自動配對');
   const refs=$r('researchPointReferences');refs.replaceChildren();for(const which of ['left','right']){const v=d[which];refs.append(node('p',(which==='left'?'A':'B')+'：'+(v?.available?'參考 '+referenceText(v.reference)+'；點位速度 '+numeric(v.velocity_mm_year)+' mm/year':v?.reason||'無有效資料'),'hint'));}
   if(both&&!sameReference)refs.append(node('p','兩條曲線各自使用原有參考基準，縱向距離不能直接當作位移差；未自動平移或重新設零。','analysis-warning'));else if(d.difference)refs.append(node('p','點選圖例「B − A」可另外顯示同基準的位移差值。','hint'));
   if(d.reasons?.length)refs.append(node('p','未提供差值的原因：'+d.reasons.join('；'),'hint'));for(const map of maps){if(!d.same_coordinate&&map._researchSide!==side)continue;map._researchMarker=L.circleMarker([p.latitude,p.longitude],{radius:5,color:'#f3bb44',weight:2,fillColor:'#21392e',fillOpacity:1}).addTo(map);}
   await Plotly.newPlot(target,traces,{height:370,margin:{l:70,r:24,t:55,b:72},paper_bgcolor:'#f7f9f3',plot_bgcolor:'#f7f9f3',xaxis:{type:'date',title:'觀測日期',tickformat:'%Y-%m'},yaxis:{title:both&&!sameReference?'各自基準的 LOS 位移（mm）':'相對 LOS 位移（mm）',zeroline:true},legend:{orientation:'h',y:1.2,font:{size:11}},hovermode:'closest'},{responsive:true,displaylogo:false,toImageButtonOptions:{format:'png',filename:'analysis-comparison',scale:2}});
   if(!isCurrent())return;$r('researchPointCsv').disabled=false;updateReportSelection();
  }catch(e){if(isCurrent())clearPoint(e.message);}
 }
})();
