// Read-only acquisition inventory and metadata outlines, independent of MintPy AOI.
(()=>{
 let map=null;
 const direction={ascending:'升軌',descending:'降軌'},look={right:'右視',left:'左視'};
 const prettyDate=d=>'20'+d.slice(0,2)+'-'+d.slice(2,4)+'-'+d.slice(4,6);
 const colors=['#236e62','#bc6b2c','#526cb3','#99559b','#748129','#318aa1'];
 window.clearImportMap=()=>{if(map){map.remove();map=null;}};
 window.renderImportMetadata=(data,selectGroup,targetOverride)=>{
  window.clearImportMap();const target=targetOverride||$('importInventory');target.replaceChildren();
  const metadata=data.metadata,products=metadata?.products||[];
  const selectedProducts=[];
  const wrap=node('div',undefined,'onboard-table'),table=node('table'),head=node('tr');
  if(products.length&&selectGroup)head.append(node('th','納入'));
  for(const label of products.length?['觀測日期 / Frame','極化','升降軌 / 視向','模式 / 軌道圈次','檢查結果']:['日期','Frames','極化','產品模式'])head.append(node('th',label));table.append(head);
  if(products.length){
   for(const p of [...products].sort((a,b)=>(a.date+a.frame).localeCompare(b.date+b.frame))){
    const tr=node('tr');if(selectGroup){const td=node('td'),check=node('input');check.type='checkbox';check.checked=true;check.value=p.key;check.setAttribute('aria-label',`納入 ${prettyDate(p.date)} Frame ${p.frame}`);td.append(check);tr.append(td);selectedProducts.push(check);}
    const status=p.errors.length?'資料衝突':p.warnings.length?'需確認':p.footprint?'已核對描述':'範圍未確認';
    for(const v of [`${prettyDate(p.date)} / ${p.frame}`,p.polarizations.join('、')||'未確認',`${direction[p.direction]||'未確認'} / ${look[p.look]||'未確認'}`,`${p.product_mode} / ${p.orbit||'未確認'}`])tr.append(node('td',v));
    const td=node('td'),details=node('details'),summary=node('summary',status);details.append(summary,node('p',`LED：${p.identity} · ${p.acquisition||'時間未確認'} · 感測器模式碼 ${p.sensor_mode||'未確認'} · ${p.sensor_id||''}`));
    for(const msg of [...p.errors,...p.warnings])details.append(node('p',msg));td.append(details);tr.append(td);table.append(tr);
   }
  }else for(const r of data.dates){const tr=node('tr');for(const v of [r.date,r.frames.join('、'),r.polarizations.join('、'),r.modes.join('、')])tr.append(node('td',v));table.append(tr);}
  wrap.append(table);target.append(wrap);
  if(products.length&&selectGroup){const use=node('button','使用勾選影像重新核對');target.append(use,node('p','可取消不需要的日期或 frame；重新核對仍會檢查至少兩個日期與 frame 配套。要回到完整清單，請再次按「檢查這批資料」。','hint'));action(use,async()=>{const keys=selectedProducts.filter(c=>c.checked).map(c=>c.value);if(!keys.length)throw Error('請至少勾選一個場景。');use.disabled=true;try{await selectGroup(keys);}finally{use.disabled=false;}});}
  for(const message of new Set([...data.problems,...data.warnings,...(metadata?.warnings||[])]))target.append(node('p',message,'onboard-problem'));
  if(metadata?.environment)target.append(node('p','描述檔讀取環境：'+metadata.environment,'hint import-environment'));
  if(metadata?.groups?.length>1&&selectGroup){
   const groups=node('div',undefined,'import-groups');groups.append(node('h3','選擇候選組，再重新核對'));
   for(const g of metadata.groups){const b=node('button',`${direction[g.direction]||'方向未確認'} · ${look[g.look]||'視向未確認'} · ${g.mode} · ${g.sensor_id||'感測器未確認'} · ${g.keys.length} 個場景`);action(b,async()=>{b.disabled=true;try{await selectGroup(g.keys);}finally{b.disabled=false;}});groups.append(b);}target.append(groups);
  }
  const footprints=products.filter(p=>p.footprint);
  target.append(node('p',metadata?.note||'只有檔名清單，尚未讀取正式描述檔。','hint'));
  if(!footprints.length){target.append(node('p','目前沒有可繪製的邊界。ZIP 整理完成後會補讀；其他情況請查看各列的「檢查結果」。','notice'));return;}
  const heading=node('div',undefined,'onboard-heading');heading.append(node('h3','影像在哪裡？'),node('span','概略影像邊界','badge'));target.append(heading);
  const toolbar=node('div',undefined,'geo-toolbar'),canvas=node('div',undefined,'import-map'),info=node('p','未載入網路底圖；可選擇道路與地名。點選邊界查看日期與 frame。','hint');target.append(toolbar,canvas,info);
  const dates=node('select');dates.setAttribute('aria-label','顯示影像日期');const all=node('option','所有日期');all.value='';dates.append(all);
  for(const date of [...new Set(footprints.map(p=>p.date))].sort()){const o=node('option',prettyDate(date));o.value=date;dates.append(o);}
  const base=node('select');base.setAttribute('aria-label','匯入地圖底圖');for(const [v,t] of [['none','離線座標底圖'],['osm','道路與地名（OpenStreetMap）']]){const o=node('option',t);o.value=v;base.append(o);}
  const fit=node('button','顯示全部範圍');toolbar.append(dates,base,fit);
  map=L.map(canvas,{scrollWheelZoom:false});const currentMap=map;
  const tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:18,attribution:'© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors'});
  base.onchange=()=>{if(base.value==='osm'){tiles.addTo(currentMap);info.textContent='底圖會向 OpenStreetMap 請求目前的地圖範圍；SAR 檔案留在本機。';}else{currentMap.removeLayer(tiles);info.textContent='離線座標底圖；SAR 檔案留在本機。';}};
  tiles.on('tileerror',()=>{info.textContent='網路底圖暫時不可用；本機邊界與座標仍可查看。';});L.control.scale({imperial:false}).addTo(map);
  const layers=[];const sortedDates=[...new Set(footprints.map(p=>p.date))].sort();
  for(const p of footprints){const color=colors[sortedDates.indexOf(p.date)%colors.length];const popup=node('div');popup.append(node('b',`${prettyDate(p.date)} · Frame ${p.frame}`),node('p',`${direction[p.direction]||'未確認'} / ${look[p.look]||'未確認'} · ${p.polarizations.join('、')}`),node('small','影像記錄邊界，不是有效形變範圍'));
   const layer=L.geoJSON(p.footprint,{style:{color,weight:2,fillOpacity:.04}}).bindPopup(popup).addTo(map);layers.push({date:p.date,layer});}
  const extent=L.featureGroup(layers.map(x=>x.layer)).getBounds();map.fitBounds(extent,{padding:[24,24]});
  if(metadata.common){const common=L.geoJSON(metadata.common,{style:{color:'#35472a',dashArray:'5 6',weight:2,fillOpacity:.16}});const label=node('label',undefined,'check'),check=node('input');check.type='checkbox';label.append(check,document.createTextNode('顯示所有日期的共同覆蓋（概略）'));toolbar.append(label);check.onchange=()=>check.checked?common.addTo(currentMap):currentMap.removeLayer(common);}
  dates.onchange=()=>{for(const item of layers){if(!dates.value||item.date===dates.value)item.layer.addTo(currentMap);else currentMap.removeLayer(item.layer);}const visible=layers.filter(x=>!dates.value||x.date===dates.value);currentMap.fitBounds(L.featureGroup(visible.map(x=>x.layer)).getBounds(),{padding:[24,24]});};
  action(fit,()=>currentMap.fitBounds(extent,{padding:[24,24]}));map.on('mousemove',e=>{canvas.setAttribute('aria-label',`影像覆蓋地圖：${e.latlng.lat.toFixed(4)}°N，${e.latlng.lng.toFixed(4)}°E`);});
  setTimeout(()=>{if(map===currentMap)map.invalidateSize();},100);
  if(metadata.bounds)target.append(node('p',`全部影像外框：南 ${metadata.bounds[0].toFixed(4)}°／北 ${metadata.bounds[1].toFixed(4)}°／西 ${metadata.bounds[2].toFixed(4)}°／東 ${metadata.bounds[3].toFixed(4)}°。`,'hint'));
  target.append(node('p','共同覆蓋是各日期影像邊界的交集，尚未考慮水域、相干性或解纏品質。DEM 應涵蓋全部影像；MintPy 研究範圍會在後續另外設定。','notice'));
 };
})();
