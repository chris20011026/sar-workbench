/* Sampling is an explicit alosStack preparation checkpoint, not a new processor. */
(()=>{
 const settings=node('div',undefined,'sampling-settings');settings.id='samplingSettings';
 settings.innerHTML=`<h3>影格與取樣</h3><p class="hint">同一日期有多個影格時，可以只處理選定影格。相同編號只是讀取條件，仍需核對地理涵蓋。</p><div id="samplingFrameChoices" class="chips"></div><p id="samplingFrameHint" class="hint">先按「掃描日期」，即可勾選影格。</p><label class="check"><input id="samplingEnabled" type="checkbox">讀取後統一取樣（Stripmap 試用）</label><p class="hint">適用取樣率不同的 ALOS 資料，例如已轉換格式的 ALOS-4 與 ALOS-2。讀取 → 人工確認影格 → 統一取樣 → 檢查成果 → 影像匹配。取樣相同的影像會保留原樣；未啟用時沿用原流程。</p>`;
 $('stackParameters').insertBefore(settings,$('properties'));
 let scanned=[],selectionKey=null;
 function selected(){try{const raw=properties.frames||'';if(!raw||raw==='None')return [];const values=JSON.parse(raw.replace(/'/g,'"'));return Array.isArray(values)&&values.length&&values.every(x=>typeof x==='string'&&/^\d{4}$/.test(x))&&new Set(values).size===values.length?values:null;}catch{return null;}}
 function drawFrames(){
  const box=$('samplingFrameChoices');box.replaceChildren();const choices=[...new Set(scanned.flatMap(d=>d.frames||[]))].sort(),chosen=selected();
  const all=node('label',undefined,'check'),allInput=node('input');allInput.type='checkbox';allInput.checked=Array.isArray(chosen)&&!chosen.length;allInput.disabled=!scanned.length;all.append(allInput,document.createTextNode('使用全部影格'));box.append(all);
  allInput.onchange=()=>{if(allInput.checked){properties.frames='';drawFrames();}else{allInput.checked=true;}};
  for(const frame of choices){const count=scanned.filter(d=>d.frames.includes(frame)).length,l=node('label',undefined,'check'),input=node('input');input.type='checkbox';input.value=frame;input.setAttribute('data-frame',frame);input.checked=chosen?.includes(frame)||false;input.disabled=count!==scanned.length;l.append(input,document.createTextNode(`${frame} · ${count}/${scanned.length} 日期`));box.append(l);input.onchange=()=>{const values=[...box.querySelectorAll('input[data-frame]')].filter(x=>x.checked).map(x=>x.value);properties.frames=values.length?JSON.stringify(values):'';drawFrames();};}
  $('samplingFrameHint').textContent=chosen===null?'匯入的 frames 格式無法顯示；請掃描後重新勾選。':!scanned.length?(chosen.length?'XML 指定影格：'+chosen.join('、')+'。請掃描日期核對。':'先按「掃描日期」，即可勾選影格。'):chosen.some(f=>!choices.includes(f))?'XML 指定的影格不在掃描清單中，請重新選擇。':chosen.length?'本次只讀取 '+chosen.join('、')+'；未勾選的其他影格會保留在來源。':'目前讀取全部影格。若日期間影格組合不同，請選擇共同影格；不會自動猜測或拼接。';
 }
 window.samplingScanned=dates=>{scanned=dates;drawFrames();};
 window.samplingSyncFrames=drawFrames;
 window.samplingFormOptions=()=>({sampling_equalization:$('samplingEnabled').checked});
 const originalWorkflowChange=window.pairWorkflowChanged;
 window.pairWorkflowChanged=id=>{originalWorkflowChange?.(id);settings.hidden=id!=='alosStack';};
 const panel=node('section',undefined,'card sampling-review');panel.id='samplingReview';panel.hidden=true;
 panel.innerHTML=`<div class="card-heading"><div><p class="eyebrow">FRAME REVIEW · SAMPLING</p><h2>先看影像，再決定下一步</h2></div><span class="badge" id="samplingBadge"></span></div>
 <ol class="sampling-steps" aria-label="影格與取樣進度"><li id="samplingStepRead"><span>01</span>讀取資料</li><li id="samplingStepReview"><span>02</span>核對影格</li><li id="samplingStepSample"><span>03</span>統一取樣</li><li id="samplingStepMatch"><span>04</span>確認後匹配</li></ol>
 <p id="samplingSummary" class="notice"></p>
 <section class="sampling-section" aria-labelledby="samplingMapTitle"><div class="sampling-section-heading"><div><p class="eyebrow">01 / COVERAGE</p><h3 id="samplingMapTitle">這些影像涵蓋同一個地方嗎？</h3><p class="hint">用影像外框核對位置；實線為本次讀取的影格，虛線為候選影格。</p></div><button id="samplingLoadReview">更新影格地圖與清單</button></div>
 <details class="sampling-extra"><summary>加入其他影格一起比較（選用）</summary><label for="samplingExtraSource">候選影格資料夾（只讀）</label><div class="input-row"><input id="samplingExtraSource" placeholder="例如：包含 0480、0490 的原始資料夾"><button id="samplingPickExtra" type="button">選擇資料夾</button></div><p class="hint">只加入比較清單，不會改動來源或本次處理影格。選好後按「更新影格地圖與清單」。</p></details>
 <p id="samplingReviewStatus" class="hint" role="status" aria-live="polite">準備讀取影格外框與預覽清單…</p>
 <div id="samplingReviewWarnings" class="sampling-warnings"></div>
 <div id="samplingVisuals" hidden><div class="sampling-map-tools"><label for="samplingCompareDate">與參考日期比對<select id="samplingCompareDate"></select></label><label for="samplingBasemap">底圖<select id="samplingBasemap"><option value="none">不使用網路底圖</option><option value="osm">道路與地名 · OpenStreetMap</option></select></label><button id="samplingFitMap">顯示可見影格</button></div>
 <div class="sampling-map-layout"><div class="sampling-map-main"><div id="samplingFootprintMap" class="geo-map sampling-map" aria-label="影格中繼資料外框地圖"></div><p id="samplingMapPosition" class="hint sampling-map-position">影像外框以經緯度顯示。</p></div><div class="sampling-map-sidebar"><h4>顯示日期</h4><div id="samplingDateToggles" class="sampling-toggles"></div><h4>顯示影格</h4><div id="samplingFrameToggles" class="sampling-frame-toggles"></div><label class="check"><input id="samplingShowCandidates" type="checkbox" checked>顯示候選影格（虛線）</label><label class="check"><input id="samplingShowCommon" type="checkbox">本次處理日期共同外框</label><p id="samplingCoverageNote" class="hint"></p><p id="samplingMapCount" class="hint"></p></div></div>
 <p id="samplingBasemapNote" class="hint">目前只顯示本機外框；開啟道路底圖才會向 OpenStreetMap 請求圖磚，SAR 檔案留在本機。</p>
 <p class="sampling-caveat">外框來自中繼資料，只能輔助確認涵蓋區域。共同外框固定以本次處理影格計算；切換顯示不會重新設定處理範圍，也不代表影像已配準。</p>
 <details class="sampling-inventory"><summary>查看各影格的感測器、用途與提醒</summary><div id="samplingProductList"></div></details></div></section>
 <section class="sampling-section" aria-labelledby="samplingPreviewTitle"><div class="sampling-section-heading"><div><p class="eyebrow">02 / AMPLITUDE</p><h3 id="samplingPreviewTitle">並排辨識地物與影像完整性</h3><p class="hint">選兩張影像，分別載入振幅預覽。可觀察海岸、河流或大範圍缺資料。</p></div></div><p id="samplingPreviewStage" class="hint"></p>
 <div class="sampling-preview-grid"><figure class="sampling-preview"><figcaption>左側 · 參考影像</figcaption><label class="sr-only" for="samplingPreviewLeftSelect">左側振幅影像</label><select id="samplingPreviewLeftSelect" disabled></select><button id="samplingPreviewLeftLoad" disabled>載入振幅預覽</button><p id="samplingPreviewLeftStatus" role="status" aria-live="polite" class="hint">先載入上方影格清單。</p><div class="sampling-preview-axis">距離向像素 →</div><div class="sampling-preview-canvas"><span class="sampling-preview-azimuth">方位向像素 ↓</span><img id="samplingPreviewLeftImage" alt="左側雷達座標振幅預覽" hidden><span id="samplingPreviewLeftEmpty" class="sampling-preview-empty">尚未載入影像</span></div><p id="samplingPreviewLeftMeta" class="hint sampling-preview-meta"></p></figure>
 <figure class="sampling-preview"><figcaption>右側 · 比較影像</figcaption><label class="sr-only" for="samplingPreviewRightSelect">右側振幅影像</label><select id="samplingPreviewRightSelect" disabled></select><button id="samplingPreviewRightLoad" disabled>載入振幅預覽</button><p id="samplingPreviewRightStatus" role="status" aria-live="polite" class="hint">先載入上方影格清單。</p><div class="sampling-preview-axis">距離向像素 →</div><div class="sampling-preview-canvas"><span class="sampling-preview-azimuth">方位向像素 ↓</span><img id="samplingPreviewRightImage" alt="右側雷達座標振幅預覽" hidden><span id="samplingPreviewRightEmpty" class="sampling-preview-empty">尚未載入影像</span></div><p id="samplingPreviewRightMeta" class="hint sampling-preview-meta"></p></figure></div>
 <p class="sampling-caveat">預覽是降採樣的雷達座標影像，向上不代表北方，兩張圖也尚未對位。各影像分別拉伸亮度，不能直接比較輻射強度；此處不將 SLC 疊在地圖上。</p></section>
 <details class="sampling-numbers"><summary>查看取樣率、尺寸與預計空間</summary><div class="sampling-table"><table><thead><tr><th>日期／影格</th><th>輸入方式</th><th>距離取樣率 RSR（MHz）</th><th>方位向 PRF（Hz）</th><th>尺寸（寬 × 高）</th><th>處理</th></tr></thead><tbody id="samplingRows"></tbody></table></div><p class="hint">箭頭表示輸入值 → 參考日期的目標值。統一取樣間距後，仍須匹配與品質檢查。這項跨衛星流程尚未通過完整真實影像驗證。</p><details><summary>查看各影像來源</summary><div id="samplingSources"></div></details></details>
 <div id="samplingApproval" class="sampling-decision"><p class="eyebrow">03 / YOUR DECISION</p><h3>確認影格後，只執行取樣統一</h3><label for="samplingNotes">影格對應的確認依據</label><textarea id="samplingNotes" rows="3" maxlength="2000" placeholder="例如：已比對各日期的影像外框與軌道，並依課程確認使用 0480；有無拼接及排除的影格…"></textarea><label class="check"><input id="samplingConsent" type="checkbox">我已人工核對各日期與參考日期的影格地理對應，確認可以統一取樣。</label><p class="hint">尚未確定時，保留在此關卡即可。執行後會停在成果檢查，等待你另行啟動影像匹配。原始 SLC／描述檔保留在專案內。</p><button id="samplingContinue" class="primary" disabled>確認影格，僅統一取樣</button></div>
 <div id="samplingMatchApproval" class="sampling-decision" hidden><p class="eyebrow">04 / REVIEW BEFORE MATCHING</p><h3>取樣已統一，先檢查成果</h3><p class="hint">比較上方「統一取樣後」與「原始輸入」預覽，並核對取樣率與尺寸。影像匹配及後續流程尚未開始。</p><label class="check"><input id="samplingMatchConsent" type="checkbox">我已檢查取樣成果與預覽，確認要啟動影像匹配及後續流程。</label><button id="samplingMatch" class="primary" disabled>開始影像匹配與後續流程</button></div><p id="samplingConfirmed" class="hint"></p>`;
 $('project').querySelector('.card').after(panel);
 let state=null,visualKey=null,review=null,reviewAttempted=false,requestSerial=0,reviewPending=false,previewPending=false,transitionPending=false;
 let map=null,mapLayers=[],commonLayer=null,tiles=null,allBounds=null,images=new Map();
 const palette=['#277a66','#ad5d2f','#526cb3','#985b96','#7d8032','#278b9d','#a94e64','#6760a5'];
 const panes=['Left','Right'],objectURLs={Left:null,Right:null},visibleDates=new Map(),visibleFrames=new Map();
 const shortDate=d=>/^\d{6}$/.test(d||'')?`20${d.slice(0,2)}-${d.slice(2,4)}-${d.slice(4,6)}`:String(d||'日期未確認');
 const safe=()=>!window.workbenchState?.busy&&!state?.storage?.relocated&&!state?.storage?.inputs_missing;
 const inspectable=()=>safe()&&['sampling_review','sampling_ready'].includes(state?.status)&&!transitionPending;
 const localPending=()=>reviewPending||previewPending||transitionPending;
 function canProceed(){return state?.status==='sampling_review'&&safe()&&!localPending()&&$('samplingConsent').checked&&$('samplingNotes').value.trim().length>=3;}
 function canMatch(){return state?.status==='sampling_ready'&&safe()&&!localPending()&&$('samplingMatchConsent').checked;}
 function buttons(){
  $('samplingContinue').disabled=!canProceed();$('samplingMatch').disabled=!canMatch();
  $('samplingLoadReview').disabled=!inspectable()||localPending();$('samplingPickExtra').disabled=!inspectable()||localPending();$('samplingExtraSource').disabled=!inspectable()||localPending();
  for(const side of panes){const select=$(`samplingPreview${side}Select`);select.disabled=!inspectable()||localPending()||!images.size;$(`samplingPreview${side}Load`).disabled=select.disabled||!images.has(select.value);}
 }
 function errorMessage(e){return e?.message||String(e);}
 function clearPreview(side,message='選好影像後，按「載入振幅預覽」。'){
  if(objectURLs[side]){URL.revokeObjectURL(objectURLs[side]);objectURLs[side]=null;}
  const img=$(`samplingPreview${side}Image`);img.onload=null;img.onerror=null;img.removeAttribute('src');img.hidden=true;
  $(`samplingPreview${side}Empty`).hidden=false;$(`samplingPreview${side}Empty`).textContent='尚未載入影像';$(`samplingPreview${side}Meta`).textContent='';$(`samplingPreview${side}Status`).textContent=message;
 }
 function clearVisuals(){
  requestSerial++;review=null;reviewAttempted=false;images.clear();visibleDates.clear();visibleFrames.clear();
  if(map){map.remove();map=null;}mapLayers=[];commonLayer=null;tiles=null;allBounds=null;
  $('samplingVisuals').hidden=true;$('samplingReviewWarnings').replaceChildren();$('samplingProductList').replaceChildren();$('samplingDateToggles').replaceChildren();$('samplingFrameToggles').replaceChildren();
  $('samplingBasemap').value='none';$('samplingShowCommon').checked=false;$('samplingShowCandidates').checked=true;
  $('samplingReviewStatus').textContent='準備讀取影格外框與預覽清單…';
  for(const side of panes){clearPreview(side,'先載入上方影格清單。');$(`samplingPreview${side}Select`).replaceChildren();}
 }
 function checkbox(labelText,checked,onChange,cls){const label=node('label',undefined,cls||'check'),check=node('input');check.type='checkbox';check.checked=checked;check.onchange=()=>onChange(check.checked);label.append(check,document.createTextNode(labelText));return {label,check};}
 function frameProvenance(value){const labels={synthetic_compatibility_id:'影格編號由格式轉換建立，需依地理外框確認對應。',synthetic_conversion:'影格編號由格式轉換建立，需依地理外框確認對應。',scene_id_label_only:'影格編號取自產品場景識別碼，仍須核對地理對應。',filename:'影格編號取自檔名，仍須核對地理對應。',sensor:'影格編號取自感測器產品資訊，仍須核對地理對應。'};return labels[typeof value==='string'?value:value?.kind||value?.source]||'影格編號來源尚未確認，請依產品說明與地理外框核對。';}
 function productLabel(p){return `${shortDate(p.date)} · ${p.sensor||'感測器未確認'} · ${p.frame||'影格未確認'}`;}
 function visible(p){return visibleDates.get(p.date)!==false&&visibleFrames.get(p.frame)!==false&&(p.selected||$('samplingShowCandidates').checked);}
 function paintMap(fit=false){
  if(!map)return;const shown=[];
  for(const item of mapLayers){if(visible(item.product)){item.layer.addTo(map);shown.push(item.layer);}else map.removeLayer(item.layer);}
  if(commonLayer){if($('samplingShowCommon').checked)commonLayer.addTo(map);else map.removeLayer(commonLayer);}
  $('samplingMapCount').textContent=`目前顯示 ${shown.length} / ${mapLayers.length} 個有效外框。`;
  if(fit&&shown.length){const bounds=L.featureGroup(shown).getBounds();if(bounds.isValid())map.fitBounds(bounds,{padding:[28,28],maxZoom:12});}
 }
 function renderMap(data){
  if(map){map.remove();map=null;}mapLayers=[];commonLayer=null;tiles=null;
  $('samplingBasemap').value='none';$('samplingBasemapNote').textContent='目前只顯示本機外框；開啟道路底圖才會向 OpenStreetMap 請求圖磚，SAR 檔案留在本機。';
  $('samplingDateToggles').replaceChildren();$('samplingFrameToggles').replaceChildren();$('samplingProductList').replaceChildren();$('samplingCompareDate').replaceChildren();
  const products=data.products||[],dates=[...new Set(products.map(p=>p.date))].sort(),frames=[...new Set(products.map(p=>p.frame))].sort();
  const all=node('option','顯示全部日期');all.value='';$('samplingCompareDate').append(all);
  const dateInputs=[];
  for(const date of dates){const color=palette[dates.indexOf(date)%palette.length];visibleDates.set(date,true);const {label,check}=checkbox(`${shortDate(date)}${date===data.reference?' · 參考':''}`,true,value=>{visibleDates.set(date,value);$('samplingCompareDate').value='';paintMap();});label.style.setProperty('--sampling-color',color);const swatch=node('span',undefined,'sampling-swatch');swatch.setAttribute('aria-hidden','true');label.append(swatch);$('samplingDateToggles').append(label);dateInputs.push({date,check});if(date!==data.reference){const option=node('option',`${shortDate(date)} ＋ 參考日期`);option.value=date;$('samplingCompareDate').append(option);}}
  for(const frame of frames){visibleFrames.set(frame,true);const {label}=checkbox(String(frame),true,value=>{visibleFrames.set(frame,value);paintMap();});$('samplingFrameToggles').append(label);}
  $('samplingCompareDate').onchange=()=>{const selectedDate=$('samplingCompareDate').value;for(const item of dateInputs){const show=!selectedDate||item.date===data.reference||item.date===selectedDate;visibleDates.set(item.date,show);item.check.checked=show;}paintMap(true);};
  const coverage=data.coverage||{};$('samplingShowCommon').disabled=!coverage.common||!coverage.complete;$('samplingShowCommon').checked=false;
  $('samplingCoverageNote').textContent=coverage.complete?(coverage.common?'共同外框僅代表本次處理日期的概略交集。':'本次處理日期沒有可顯示的共同外框，請先檢查涵蓋與影格。'):'部分處理影格缺少有效外框，尚無完整共同覆蓋結論。';
  const footprints=products.filter(p=>p.footprint);
  for(const p of products){const item=node('div',undefined,'sampling-product');item.append(node('strong',productLabel(p)),node('span',p.selected?'本次處理':'候選比較','sampling-product-kind'));const metadata=[p.direction?({ascending:'升軌',descending:'降軌'}[p.direction]||p.direction):'軌道方向未確認',p.look?({right:'右視',left:'左視'}[p.look]||p.look):'視向未確認',p.footprint?'有中繼資料外框':'缺少外框'];item.append(node('p',metadata.join(' · '),'hint'));if(p.frame_provenance)item.append(node('p',frameProvenance(p.frame_provenance),'hint'));for(const warning of p.warnings||[])item.append(node('p',String(warning),'sampling-warning'));$('samplingProductList').append(item);}
  $('samplingVisuals').hidden=false;
  if(typeof L==='undefined'||!footprints.length){$('samplingFootprintMap').hidden=true;$('samplingMapCount').textContent=footprints.length?'地圖元件未載入；仍可查看影格清單與振幅預覽。':'尚無可繪製的影格外框；請查看影格提醒。';$('samplingFitMap').disabled=true;return;}
  $('samplingFootprintMap').hidden=false;$('samplingFitMap').disabled=false;map=L.map($('samplingFootprintMap'),{scrollWheelZoom:false,preferCanvas:true});L.control.scale({imperial:false}).addTo(map);
  const north=L.control({position:'topright'});north.onAdd=()=>node('div','↑ N','north-arrow');north.addTo(map);
  for(const p of footprints){const color=palette[dates.indexOf(p.date)%palette.length],popup=node('div');popup.append(node('strong',productLabel(p)),node('p',p.selected?'本次處理影格':'候選影格，未加入本次處理'),node('small','中繼資料外框，尚未配準'));try{const layer=L.geoJSON(p.footprint,{style:{color,weight:p.date===data.reference?3:2,dashArray:p.selected?null:'6 5',fillOpacity:p.selected?0.06:0.02}}).bindPopup(popup);mapLayers.push({product:p,layer});}catch{$('samplingReviewWarnings').append(node('p',`${productLabel(p)}：外框格式無法顯示。`,'sampling-warning'));}}
  if(coverage.complete&&coverage.common)commonLayer=L.geoJSON(coverage.common,{style:{color:'#203c37',weight:2,dashArray:'3 5',fillColor:'#9cbb81',fillOpacity:.25}});
  if(mapLayers.length){allBounds=L.featureGroup(mapLayers.map(x=>x.layer)).getBounds();paintMap(true);}else{map.setView([23.7,121],6);$('samplingFitMap').disabled=true;}
  const currentMap=map;map.on('mousemove',e=>{$('samplingMapPosition').textContent=`緯度 ${e.latlng.lat.toFixed(5)}° · 經度 ${e.latlng.lng.toFixed(5)}°`;});
  setTimeout(()=>{if(map===currentMap)map.invalidateSize();},0);
 }
 function previewOptions(data){
  images.clear();for(const product of data.products||[])for(const image of product.images||[]){if(!image.id)continue;images.set(image.id,{...image,product});}
  const available=[...images.values()],equalized=state?.status==='sampling_ready';
  for(const side of panes){const select=$(`samplingPreview${side}Select`),old=select.value;select.replaceChildren();for(const image of available){const kind=image.kind==='equalized'?'統一取樣後':image.kind==='source'?'原始輸入':image.label||'影像';const option=node('option',`${productLabel(image.product)} · ${image.polarization||''} · ${kind}`);option.value=image.id;select.append(option);}
   const candidates=available.filter(i=>i.product.selected&&(side==='Left'?i.product.date===data.reference:i.product.date!==data.reference));
   const preferred=candidates.find(i=>i.kind===(equalized?'equalized':'source'))||candidates[0]||available[0];select.value=images.has(old)?old:preferred?.id||'';clearPreview(side,available.length?'選好影像後，按「載入振幅預覽」。':'這批資料目前沒有可預覽的影像。');}
 }
 async function loadReview(){
  if(!inspectable()||localPending())return;const key=visualKey,serial=++requestSerial,id=state.id;reviewPending=true;reviewAttempted=true;buttons();$('samplingReviewStatus').textContent='正在讀取中繼資料與影像清單，請稍候…';
  const extra=$('samplingExtraSource').value.trim();
  try{const data=await api('/api/sampling-review',{id,extra_source:extra});if(key!==visualKey||serial!==requestSerial)return;if(data.plan_id!==state.sampling.plan_id)throw Error('取樣計畫已變更，請更新專案後再載入。');review=data;$('samplingReviewWarnings').replaceChildren();for(const warning of data.warnings||[])$('samplingReviewWarnings').append(node('p',String(warning),'sampling-warning'));renderMap(data);previewOptions(data);$('samplingReviewStatus').textContent=`已讀取 ${data.products?.length||0} 個影格；候選影格只供比較，不會自動加入處理。`;}
  catch(e){if(key===visualKey&&serial===requestSerial)$('samplingReviewStatus').textContent='無法讀取影格比較：'+errorMessage(e)+' 可按更新重試；人工確認尚未送出。';}
  finally{reviewPending=false;buttons();}
 }
 async function loadPreview(side){
  const select=$(`samplingPreview${side}Select`),image=images.get(select.value);if(!image||!inspectable()||localPending())return;
  const key=visualKey,serial=requestSerial,id=state.id,plan_id=state.sampling.plan_id,image_id=image.id;previewPending=true;buttons();clearPreview(side,'正在產生振幅預覽，較大的影像可能需要一些時間…');$(`samplingPreview${side}Empty`).textContent='產生預覽中…';
  try{const result=await api('/api/sampling-preview',{id,image_id,plan_id});if(key!==visualKey||serial!==requestSerial||select.value!==image_id)return;
   if(result.image_id!==image_id)throw Error('預覽影像不符，請更新清單後重試。');
   // Fetch protected local images with the API header; never put the session token in a URL.
   if(typeof result.url!=='string'||!result.url.startsWith('/api/sampling-preview-image?'))throw Error('預覽網址格式不符。');
   const response=await fetch(result.url,{headers:{'X-Token':token}});if(!response.ok)throw Error('無法載入預覽圖片，請重試。');const blob=await response.blob();
   if(key!==visualKey||serial!==requestSerial||select.value!==image_id)return;
   const url=URL.createObjectURL(blob);objectURLs[side]=url;const img=$(`samplingPreview${side}Image`);img.hidden=false;$(`samplingPreview${side}Empty`).hidden=true;img.alt=`${productLabel(image.product)} · ${image.polarization||''} · ${image.kind==='equalized'?'統一取樣後':'原始輸入'}振幅，雷達座標`;
   img.onerror=()=>{if(objectURLs[side]!==url)return;clearPreview(side,'預覽圖片無法顯示，請重新載入。');};img.src=url;
   const fraction=Number.isFinite(result.valid_fraction)?` · 有效取樣 ${(result.valid_fraction*100).toFixed(1)}%`:'';
   $(`samplingPreview${side}Status`).textContent='預覽已載入。';$(`samplingPreview${side}Meta`).textContent=`原始尺寸 ${result.source_width} × ${result.source_height} → 預覽 ${result.width} × ${result.height}${fraction}。${result.scale?' 顯示尺度：'+result.scale+'。':''}`;
  }catch(e){if(key===visualKey&&serial===requestSerial){clearPreview(side,'無法產生預覽：'+errorMessage(e));$(`samplingPreview${side}Empty`).textContent='預覽失敗，可重試';}}
  finally{previewPending=false;buttons();}
 }
 $('samplingConsent').onchange=buttons;$('samplingNotes').oninput=buttons;$('samplingMatchConsent').onchange=buttons;
 action($('samplingLoadReview'),loadReview);action($('samplingPickExtra'),()=>choosePath('dir',path=>{$('samplingExtraSource').value=path;$('samplingReviewStatus').textContent='候選資料夾已選擇，請按「更新影格地圖與清單」。';},$('samplingExtraSource').value||state?.config.source));
 $('samplingExtraSource').oninput=()=>{$('samplingReviewStatus').textContent='候選資料夾已變更，請更新影格地圖與清單。';};
 $('samplingShowCandidates').onchange=()=>paintMap();$('samplingShowCommon').onchange=()=>paintMap();action($('samplingFitMap'),()=>paintMap(true));
 $('samplingBasemap').onchange=()=>{if(!map)return;if(tiles){map.removeLayer(tiles);tiles=null;}if($('samplingBasemap').value==='osm'){tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors'}).addTo(map);tiles.on('tileerror',()=>{$('samplingBasemapNote').textContent='道路底圖目前無法載入，本機影格外框仍可比較。';});$('samplingBasemapNote').textContent='目前向 OpenStreetMap 請求觀看範圍的圖磚；SAR 檔案留在本機。';}else $('samplingBasemapNote').textContent='已關閉網路底圖；本機影格外框仍可比較。';};
 for(const side of panes){$(`samplingPreview${side}Select`).onchange=()=>clearPreview(side);action($(`samplingPreview${side}Load`),()=>loadPreview(side));}
 action($('samplingContinue'),async()=>{if(!canProceed())return;const body={id:state.id,action:'sampling_continue',plan_id:state.sampling.plan_id,frames_verified:true,frame_notes:$('samplingNotes').value.trim()};transitionPending=true;buttons();try{await api('/api/action',body);await refresh();}finally{transitionPending=false;buttons();}});
 action($('samplingMatch'),async()=>{if(!canMatch())return;const body={id:state.id,action:'sampling_match',sampling_reviewed:true};transitionPending=true;buttons();try{await api('/api/action',body);await refresh();}finally{transitionPending=false;buttons();}});
 window.samplingUpdate=(s,data)=>{
  state=s;const report=s.sampling;panel.hidden=s.config.workflow!=='alosStack'||!report||!!s.import_only;if(panel.hidden){if(visualKey){clearVisuals();visualKey=null;}return;}
  const key=s.id+':'+report.plan_id;if(selectionKey!==key){selectionKey=key;$('samplingConsent').checked=false;$('samplingNotes').value='';$('samplingMatchConsent').checked=false;$('samplingExtraSource').value='';}
  const ready=s.status==='sampling_ready',waiting=s.status==='sampling_review',sampled=ready||!!s.sampling_result,matching=!!s.sampling_match_approval,nextVisual=key+':'+(sampled?'equalized':'source');
  if(visualKey!==nextVisual){visualKey=nextVisual;clearVisuals();$('samplingMatchConsent').checked=false;}
  $('samplingApproval').hidden=!waiting;$('samplingMatchApproval').hidden=!ready;$('samplingBadge').textContent=waiting?'等待影格確認':ready?'取樣完成 · 等待匹配確認':s.sampling_approval?'已記錄人工確認':'取樣檢查';
  $('samplingStepRead').setAttribute('data-state','done');$('samplingStepReview').setAttribute('data-state',waiting?'current':s.sampling_approval?'done':'pending');$('samplingStepSample').setAttribute('data-state',sampled?'done':s.sampling_approval?'current':'pending');$('samplingStepMatch').setAttribute('data-state',matching?'done':ready?'current':'pending');
  $('samplingSummary').textContent=`參考日期 ${shortDate(report.reference)} · ${report.rows.length} 個 SLC，${report.virtual} 個以虛擬方式讀取，${report.changed} 個${sampled?'已統一取樣':'需重取樣'}。${sampled?(matching?'已記錄啟動影像匹配的確認。':'影像匹配尚未開始。'):`預計新增 ${(report.output_bytes/1024**3).toFixed(2)} GB（另需復原副本及後續運算空間）。`}`;
  $('samplingPreviewStage').textContent=sampled?'現在可選擇原始輸入與統一取樣後的影像，比較內容完整性。':'目前檢視原始輸入；完成取樣統一後，可在此檢查新的實體 SLC。';
  const tbody=$('samplingRows');tbody.replaceChildren();const sources=$('samplingSources');sources.replaceChildren();const number=(v,d)=>Number(v).toFixed(d);
  for(const row of report.rows){const tr=node('tr');const arrow=(a,b,d)=>number(a,d)+(row.changed?' → '+number(b,d):'');for(const value of [`${shortDate(row.date)}${row.date===report.reference?' · 參考':''} / ${row.frame} / s${row.swath}`,row.virtual?'虛擬 SLC（VRT）':'實體 SLC',arrow(row.rsr/1e6,row.target_rsr/1e6,6),arrow(row.prf,row.target_prf,4),`${row.width} × ${row.length}`+(row.changed?` → ${row.out_width} × ${row.out_length}`:''),row.changed?(sampled?'已統一取樣':'重新取樣'):'保留原樣'])tr.append(node('td',value));tbody.append(tr);sources.append(node('p',`${row.date} / ${row.frame}：${row.raw}`,'hint'));}
  $('samplingConfirmed').textContent=s.sampling_approval?`影格確認紀錄：${s.sampling_approval.time} · ${s.sampling_approval.notes}`:'';buttons();if(!reviewAttempted&&inspectable()&&!localPending())void loadReview();
 };
 drawFrames();
})();
