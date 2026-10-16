/* Sampling is an explicit alosStack preparation checkpoint, not a new processor. */
(()=>{
 const settings=node('div',undefined,'sampling-settings');settings.id='samplingSettings';
 settings.innerHTML=`<h3>影格與取樣</h3><p class="hint">同一日期有多個影格時，可以只處理選定影格。相同編號只是讀取條件，仍需核對地理涵蓋。</p><div id="samplingFrameChoices" class="chips"></div><p id="samplingFrameHint" class="hint">先按「掃描日期」，即可勾選影格。</p><label class="check"><input id="samplingEnabled" type="checkbox">讀取後統一取樣（Stripmap 試用）</label><p class="hint">適用取樣率不同的 ALOS 資料，例如已轉換格式的 ALOS-4 與 ALOS-2。讀取 → 人工確認影格 → 統一取樣 → 影像匹配。取樣相同的影像會保留原樣；未啟用時沿用原流程。</p>`;
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
 panel.innerHTML=`<div class="card-heading"><div><p class="eyebrow">READ → REVIEW → SAMPLE → MATCH</p><h2>影格對應與取樣檢查</h2></div><span class="badge" id="samplingBadge"></span></div><p id="samplingSummary" class="notice"></p><div class="sampling-table"><table><thead><tr><th>日期／影格</th><th>輸入方式</th><th>距離取樣率 RSR（MHz）</th><th>方位向 PRF（Hz）</th><th>尺寸（寬 × 高）</th><th>處理</th></tr></thead><tbody id="samplingRows"></tbody></table></div><p class="hint">箭頭表示目前值 → 參考日期的目標值。影格相同編號不代表地理位置已對齊；本步驟只統一取樣間距，後續仍須匹配與品質檢查。這項跨衛星流程尚未通過完整真實影像驗證。</p><details><summary>查看各影像來源</summary><div id="samplingSources"></div></details><div id="samplingApproval"><label for="samplingNotes">影格對應的確認依據</label><textarea id="samplingNotes" rows="3" maxlength="2000" placeholder="例如：已比對各日期的影像外框與軌道，並依課程確認使用 0480；有無拼接及排除的影格…"></textarea><label class="check"><input id="samplingConsent" type="checkbox">我已人工核對各日期與參考日期的影格地理對應，確認可以繼續。</label><p class="hint">尚未確定時，保留在此關卡即可。重取樣會產生新的實體 SLC，原始 SLC／描述檔保留在專案內。</p><button id="samplingContinue" class="primary" disabled>確認影格，統一取樣並繼續 →</button></div><p id="samplingConfirmed" class="hint"></p>`;
 $('project').querySelector('.card').after(panel);
 let state=null;
 function canProceed(){return state?.status==='sampling_review'&&!window.workbenchState?.busy&&!state.storage?.relocated&&!state.storage?.inputs_missing&&$('samplingConsent').checked&&$('samplingNotes').value.trim().length>=3;}
 function button(){ $('samplingContinue').disabled=!canProceed(); }
 $('samplingConsent').onchange=button;$('samplingNotes').oninput=button;
 action($('samplingContinue'),async()=>{if(!canProceed())return;const id=state.id;const body={id,action:'sampling_continue',plan_id:state.sampling.plan_id,frames_verified:true,frame_notes:$('samplingNotes').value.trim()};$('samplingContinue').disabled=true;try{await api('/api/action',body);await refresh();}finally{button();}});
 window.samplingUpdate=(s,data)=>{
  state=s;const report=s.sampling;panel.hidden=s.config.workflow!=='alosStack'||!report||!!s.import_only;if(panel.hidden)return;
  const key=s.id+':'+report.plan_id;if(selectionKey!==key){selectionKey=key;$('samplingConsent').checked=false;$('samplingNotes').value='';}
  const waiting=s.status==='sampling_review';$('samplingApproval').hidden=!waiting;$('samplingBadge').textContent=waiting?'等待人工確認':s.sampling_approval?'已記錄人工確認':'取樣檢查';
  $('samplingSummary').textContent=`參考日期 ${report.reference} · ${report.rows.length} 個 SLC，${report.virtual} 個以虛擬方式讀取，${report.changed} 個需重取樣。預計新增 ${(report.output_bytes/1024**3).toFixed(2)} GB（另需復原副本及後續運算空間）。`;
  const tbody=$('samplingRows');tbody.replaceChildren();const sources=$('samplingSources');sources.replaceChildren();
  const number=(v,d)=>Number(v).toFixed(d);for(const row of report.rows){const tr=node('tr');const arrow=(a,b,d)=>number(a,d)+(row.changed?' → '+number(b,d):'');for(const value of [`${row.date}${row.date===report.reference?' · 參考':''} / ${row.frame} / s${row.swath}`,row.virtual?'虛擬 SLC（VRT）':'實體 SLC',arrow(row.rsr/1e6,row.target_rsr/1e6,6),arrow(row.prf,row.target_prf,4),`${row.width} × ${row.length}`+(row.changed?` → ${row.out_width} × ${row.out_length}`:''),row.changed?'重新取樣':'保留原樣'])tr.append(node('td',value));tbody.append(tr);sources.append(node('p',`${row.date} / ${row.frame}：${row.raw}`,'hint'));}
  $('samplingConfirmed').textContent=s.sampling_approval?`確認紀錄：${s.sampling_approval.time} · ${s.sampling_approval.notes}`:'';button();
 };
 drawFrames();
})();
