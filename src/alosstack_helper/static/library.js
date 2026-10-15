// Read-only source library. Does not create a processing project or alter inputs.
(()=>{
 const panel=node('section');panel.id='libraryPanel';panel.hidden=true;
 panel.innerHTML=`<div class="eyebrow">LOCAL DATA LIBRARY</div><h1>先認識資料，再開始研究。</h1>
 <p class="intro">辨識 ALOS-2／ALOS-4 與 NISAR，直接查看已有的 GUNW 成果。掃描不會執行資料夾裡的腳本或改寫來源。</p>
 <div class="card"><h2>選擇資料</h2><label>資料夾或 NISAR HDF5 檔案<span class="input-row"><input id="libPath" placeholder="選取課程資料夾，或單一 .h5"><button id="libFolder">選資料夾</button><button id="libFile">選檔案</button></span></label>
 <label>讀取資料的科學環境<select id="libEnv"></select></label><p class="hint">沿用已安裝的環境，不會安裝套件。NISAR 預覽需 h5py、pyproj、Pillow、matplotlib；ALOS 中繼資料另需 ISCE2。</p>
 <button id="libScan" class="primary">掃描與檢查</button><p id="libStatus" role="status" aria-live="polite"></p><details><summary>最近的掃描</summary><div id="libRecent" class="chips"></div><p class="hint">僅保存清單與預覽；原始資料不會複製。來源移動或修改後需重新掃描。</p></details></div>
 <div id="libReport"></div>
 <div id="libViewer" class="card" hidden><div class="eyebrow">NISAR · GUNW</div><h2>成果地圖</h2><p id="libSource" class="hint library-path"></p>
 <div class="grid"><label>圖層<select id="libLayer"></select></label><label>最低相干性（0 表示不額外篩選）<input id="libThreshold" type="number" min="0" max="1" step="0.05" value="0"></label></div>
 <div class="inline"><button id="libRender" class="primary">載入圖層</button><button id="libFit" disabled>回到完整範圍</button><label>影像透明度<input id="libOpacity" type="range" min="0" max="1" step="0.05" value="0.8"></label></div>
 <label class="check"><input type="checkbox" id="libBasemap">顯示 OpenStreetMap 底圖（需要連網，會向圖磚服務請求目前地圖範圍）</label>
 <p id="libMapStatus" role="status" aria-live="polite"></p><div id="libMap" class="geo-map"></div><div id="libScale" class="library-scale"></div><p id="libLegend" class="hint"></p><p id="libPoint" class="notice" aria-live="polite">載入後點地圖，可讀取原始網格最近像素的數值。</p>
 <p class="hint">這是單一配對的產品檢視，沒有時間序列。相位包含多種訊號與誤差，不能直接解讀為垂直位移；連通區之間也可能有不同的解纏基準。</p></div>`;
 document.querySelector('main').append(panel);
 let report=null,productIndex=null,map=null,overlay=null,marker=null,tiles=null,url=null,bounds=null,shown=null,epoch=0,pointSeq=0;
 const controls=['libScan','libFolder','libFile','libEnv','libRender','libLayer','libThreshold'];
 function busy(value){controls.forEach(id=>$(id).disabled=value);}
 function clearPreview(){epoch++;pointSeq++;shown=null;bounds=null;$('libFit').disabled=true;if(marker){marker.remove();marker=null;}if(overlay){overlay.remove();overlay=null;}if(url){URL.revokeObjectURL(url);url=null;}$('libScale').style.background='';$('libLegend').textContent='';$('libPoint').textContent='請載入目前選取的圖層。';}
 window.closeLibrary=()=>{panel.hidden=true;$('openLibrary').classList.remove('active');};
 async function recent(){const v=await api('/api/library');$('libRecent').replaceChildren();for(const s of v.scans){const b=node('button',s.root+' · '+s.count+' 份產品');b.classList.add('library-path');action(b,async()=>{busy(true);try{showReport(await api('/api/library?scan='+s.id));}finally{busy(false);}});$('libRecent').append(b);}if(!v.scans.length)$('libRecent').textContent='還沒有掃描紀錄。';}
 action($('openLibrary'),async()=>{
  current=null;panel.hidden=false;$('setup').hidden=true;$('project').hidden=true;$('welcome').hidden=true;$('crumb').textContent='資料與成果瀏覽';$('newProject').classList.remove('active');$('openWelcome').classList.remove('active');$('openLibrary').classList.add('active');
  const data=await api('/api/state'),old=$('libEnv').value;$('libEnv').replaceChildren();for(const e of data.defaults.environments){const o=node('option',e.name+' · '+e.path);o.value=e.path;$('libEnv').append(o);}if([...$('libEnv').options].some(o=>o.value===(old||data.defaults.env)))$('libEnv').value=old||data.defaults.env;
  await recent();if(map)setTimeout(()=>map.invalidateSize(),0);
 });
 action($('libFolder'),()=>choosePath('dir',p=>{$('libPath').value=p;},$('libPath').value||defaults.home));
 action($('libFile'),()=>choosePath('file',p=>{$('libPath').value=p;},$('libPath').value||defaults.home));
 action($('libScan'),async()=>{
  if(!$('libPath').value.trim())throw Error('請先選擇資料');
  busy(true);clearPreview();$('libReport').replaceChildren();$('libViewer').hidden=true;$('libStatus').textContent='正在讀取中繼資料與檢查設定，未執行科學處理…';
  try{showReport(await api('/api/library-scan',{path:$('libPath').value.trim(),environment:$('libEnv').value}));await recent();}
  catch(e){$('libStatus').textContent='掃描未完成：'+e.message;throw e;}finally{busy(false);}
 });
 function warning(container,text){container.append(node('p',text,'notice'));}
 function showReport(v){
  clearPreview();report=v;productIndex=null;$('libViewer').hidden=true;$('libStatus').textContent=`已找到 ${v.products.length} 份產品、${v.configs.length} 份設定；${v.errors.length} 個讀取問題。`;
  const box=$('libReport');box.replaceChildren();const card=node('div',undefined,'card');card.append(node('h2','資料清單'),node('p',v.root,'hint library-path'));for(const w of v.warnings)warning(card,w);const filterLabel=node('label','顯示產品'),filter=node('select');for(const [value,label] of [['','全部產品'],['GUNW','NISAR 成果 GUNW'],['RSLC','NISAR 輸入 RSLC'],['ALOS-2','ALOS-2'],['ALOS-4','ALOS-4']]){const o=node('option',label);o.value=value;filter.append(o);}filterLabel.append(filter);card.append(filterLabel);filter.onchange=()=>card.querySelectorAll('.library-product').forEach(el=>{el.hidden=!!filter.value&&el.dataset.sensor!==filter.value&&el.dataset.type!==filter.value;});
  for(const [i,p] of v.products.entries()){
   const row=node('article',undefined,'library-product'),m=p.metadata||{};row.dataset.sensor=p.sensor;row.dataset.type=p.type;row.append(node('h3',p.sensor+' · '+p.type));
   const date=p.date||m.zeroDopplerStartTime||[m.referenceZeroDopplerStartTime,m.secondaryZeroDopplerStartTime].filter(Boolean).join(' → ')||'日期尚未確認';
   row.append(node('p',date),node('p',p.path,'hint library-path'));
   row.append(node('p',p.type==='CEOS'?`${p.images} 份 IMG · ${(p.polarizations||[]).join('／')} · 取樣率 ${m.range_sampling_mhz??'未確認'} MHz`:`軌道 ${m.trackNumber??'未確認'} · 影格 ${m.frameNumber??'未確認'} · ${m.orbitPassDirection||'未確認'} · `+Object.entries(m.polarizations||{}).map(([f,ps])=>f+': '+ps.join('/')).join('；'),'hint'));
   for(const w of p.warnings||[])row.append(node('p',w,'hint'));
   if(p.type==='GUNW'&&p.layers.length){const b=node('button','查看成果地圖','primary');action(b,()=>openProduct(i));row.append(b);}
   else row.append(node('span',p.type==='RSLC'?'可作為未來 ISCE3 流程的輸入；目前僅辨識':p.sensor==='ALOS-4'?'跨衛星處理待驗證':'若要處理，請另建立 ALOS 專案並完成既有健檢','badge'));
   card.append(row);
  }
  for(const pair of v.nisar_pairs||[]){const d=node('div',undefined,'notice');d.append(node('strong','NISAR 配對初步檢查'),node('p',pair.products.map(i=>v.products[i].metadata.zeroDopplerStartTime||'日期未知').join(' ↔ ')));d.append(node('p',pair.issues.length?pair.issues.join('；'):'軌道、影格、升降軌一致；共同頻段／極化：'+Object.entries(pair.common).map(([f,p])=>f+': '+p.join('/')).join('；')),node('p',pair.note,'hint'));card.append(d);}for(const e of v.errors)warning(card,e.path+'：'+e.message);box.append(card);
  if(v.configs.length){const c=node('div',undefined,'card');c.append(node('h2','設定檔檢查'),node('p','僅列出問題，不會改寫 XML／YAML 或執行課程腳本。','hint'));for(const cfg of v.configs){const d=node('details');d.append(node('summary',cfg.path));for(const w of cfg.warnings)warning(d,w);if(!cfg.warnings.length)d.append(node('p','未發現本版檢查涵蓋的路徑或格式問題；不代表參數已通過完整科學驗證。','hint'));const pre=node('pre',JSON.stringify(cfg.properties,null,2));d.append(pre);c.append(d);}box.append(c);}
 }
 function openProduct(i){clearPreview();productIndex=i;$('libViewer').hidden=false;const p=report.products[i];$('libSource').textContent=p.path;$('libLayer').replaceChildren();for(const l of p.layers){const o=node('option',l.label);o.value=l.key;$('libLayer').append(o);}$('libThreshold').value='0';$('libMapStatus').textContent='選擇圖層後按「載入圖層」。';$('libViewer').scrollIntoView({behavior:'smooth'});if(map)map.invalidateSize();}
 function request(){return {scan:report.id,product:productIndex,layer:$('libLayer').value,threshold:Number($('libThreshold').value)};}
 $('libLayer').onchange=$('libThreshold').oninput=()=>{clearPreview();$('libMapStatus').textContent='設定已改變，請重新載入圖層。';};
 function basemap(){if(!map)return;if($('libBasemap').checked){if(!tiles){tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© OpenStreetMap contributors',maxZoom:19});tiles.on('tileerror',()=>{$('libMapStatus').textContent='底圖暫時無法取得；成果影像仍可檢視。';});}tiles.addTo(map);}else if(tiles)tiles.remove();}
 $('libBasemap').onchange=basemap;$('libOpacity').oninput=()=>overlay?.setOpacity(Number($('libOpacity').value));$('libFit').onclick=()=>map&&bounds&&map.fitBounds(bounds);
 action($('libRender'),async()=>{
  const req=request();if(!Number.isFinite(req.threshold)||req.threshold<0||req.threshold>1)throw Error('相干性門檻需介於 0 與 1');
  clearPreview();const generation=epoch;busy(true);$('libMapStatus').textContent='正在建立抽樣預覽並轉換地圖座標，請稍候…';
  try{
   const v=await api('/api/library-preview',req);const response=await fetch('/api/library-image?preview='+v.id,{headers:{'X-Token':token}});if(!response.ok)throw Error('無法載入預覽圖片');const blob=await response.blob();if(epoch!==generation)return;
   if(!map){map=L.map('libMap',{preferCanvas:true}).setView([24,121],6);map.on('click',async e=>{
    if(!shown)return;const seq=++pointSeq,snapshot=shown;if(marker)marker.remove();marker=L.circleMarker(e.latlng,{radius:6,color:'#ffffff',weight:2,fillColor:'#163d35',fillOpacity:1}).addTo(map);$('libPoint').textContent='正在讀取原始像素…';
    try{const value=await api('/api/library-point',{...snapshot,lat:e.latlng.lat,lon:e.latlng.lng});if(seq!==pointSeq||snapshot!==shown)return;$('libPoint').textContent=`${e.latlng.lat.toFixed(5)}°N, ${e.latlng.lng.toFixed(5)}°E · `+(value.value===null?value.reason:`${value.value.toFixed(4)} ${value.unit}（行 ${value.row}、列 ${value.col}，0 起算）`);}
    catch(err){if(seq===pointSeq)$('libPoint').textContent=err.message;}
   });}
   url=URL.createObjectURL(blob);bounds=v.bounds;overlay=L.imageOverlay(url,bounds,{opacity:Number($('libOpacity').value),alt:v.layer.label+' 預覽'}).addTo(map);shown=req;basemap();map.invalidateSize();map.fitBounds(bounds);$('libFit').disabled=false;
   $('libScale').style.background='linear-gradient(to right,'+v.colors.join(',')+')';$('libLegend').textContent=v.layer.kind==='category'?'連通區編號使用循環色盤，超過 20 區會重複顏色；點圖讀取原始編號。':`${v.range[0].toFixed(3)} ～ ${v.range[1].toFixed(3)} ${v.layer.unit} · `+(['phase','positive'].includes(v.layer.kind)?'色階取預覽有效值第 2～98 百分位；超出範圍以端點顏色表示。':'');
   $('libMapStatus').textContent=`${v.layer.label} · 來源 EPSG:${v.epsg} · 每 ${v.step} 像素抽樣 · 有效抽樣 ${(v.valid_fraction*100).toFixed(1)}%。${v.note}`;$('libPoint').textContent='點地圖讀取原始網格最近像素；顯示圖為抽樣預覽。';
  }catch(e){if(epoch===generation){$('libMapStatus').textContent='預覽未完成：'+e.message;throw e;}}finally{busy(false);}
 });
})();
