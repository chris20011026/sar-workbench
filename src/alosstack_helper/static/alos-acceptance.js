/* Explicit post-correction and final-product review. Never starts processing
   merely by displaying a report; scientific decisions require fresh evidence. */
(()=>{
 const host=$('project');if(!host)return;
 const panel=node('section',undefined,'card alos-acceptance');panel.id='alosAcceptance';panel.hidden=true;
 panel.innerHTML=`<div class="card-heading"><div><p class="eyebrow">REVIEW BEFORE CONTINUING</p><h2 id="alosAcceptanceTitle"></h2></div><span id="alosAcceptanceBadge" class="badge"></span></div><p id="alosAcceptanceSummary" class="notice"></p><div id="alosAcceptanceMetrics" class="metric-grid"></div><div id="alosAcceptanceIssues"></div><details id="alosReviewGuide" open><summary>這個關卡要看什麼？</summary><div id="alosAcceptanceGuide"></div></details><div class="input-row"><label for="alosAcceptancePair">檢視配對<select id="alosAcceptancePair"></select></label><button id="alosAcceptanceRefresh" type="button">更新成果檢查</button></div><p id="alosAcceptanceMessage" role="status" aria-live="polite" class="hint"></p><div id="alosAcceptancePreviews" class="sampling-preview-grid"></div><details><summary>完整成果清單與檢查依據</summary><div id="alosAcceptanceTable" class="sampling-table"></div><div id="alosAcceptanceWarnings"></div></details><div id="alosAcceptanceDecision" class="sampling-decision"><h3 id="alosAcceptanceNext"></h3><label for="alosAcceptanceNotes">判讀紀錄與仍需追蹤的問題</label><textarea id="alosAcceptanceNotes" rows="3" maxlength="2000" placeholder="例如：已比對校正前後條紋與缺值區；記錄仍需追蹤的配對…"></textarea><label class="check"><input id="alosAcceptanceReviewed" type="checkbox"><span id="alosAcceptanceConsent"></span></label><button id="alosAcceptanceContinue" class="primary" disabled></button></div><p id="alosAcceptanceApproval" class="hint"></p>`;
 host.querySelector('.card').after(panel);
 let state=null,kind=null,report=null,key=null,pending=false;let previewSerial=0;
 const labels={before:'校正前 · 包裹相位（rad）',after:'校正後 · 包裹相位（rad）',correction:'套用的電離層差值（rad）',unwrapped:'解纏相位（rad）',masked:'遮罩後解纏相位（rad）',coherence:'相干性（0–1）'};
 function safe(){return !pending&&!window.workbenchState?.busy&&!state?.storage?.relocated&&!state?.storage?.inputs_missing;}
 function waiting(){return state?.status===(kind==='ion'?'ion_correction_review':'output_review');}
 function controls(){const okay=safe();$('alosAcceptanceRefresh').disabled=!okay||!(waiting()||state?.status==='complete');$('alosAcceptanceContinue').disabled=!okay||!waiting()||!report?.can_continue||!$('alosAcceptanceReviewed').checked||$('alosAcceptanceNotes').value.trim().length<3;}
 function color(t,cyclic){t=Math.min(1,Math.max(0,t));if(cyclic){const h=t*6,c=1-Math.abs(h%2-1);return [[255,255*c,0],[255*c,255,0],[0,255,255*c],[0,255*c,255],[255*c,0,255],[255,0,255*c]][Math.min(5,Math.floor(h))];}return t<.5?[50+410*t,95+320*t,160+190*t]:[255,255-340*(t-.5),255-360*(t-.5)];}
 function preview(title,im,limits,cyclic=false){const figure=node('figure',undefined,'sampling-preview');figure.append(node('figcaption',title));if(!im?.values){figure.append(node('p','無可顯示的影像；請查看檢查清單。','error'));return figure;}
  const canvas=node('canvas');canvas.width=im.width;canvas.height=im.height;canvas.style.width='100%';canvas.style.maxHeight='400px';canvas.style.objectFit='contain';canvas.setAttribute('role','img');canvas.setAttribute('aria-label',title+'，規則抽樣預覽');const ctx=canvas.getContext('2d'),pixels=ctx.createImageData(im.width,im.height),lo=limits?.[0]??im.min??0,hi=limits?.[1]??im.max??1;
  im.values.forEach((v,i)=>{const rgb=v===null?[224,230,223]:color((v-lo)/(hi-lo||1),cyclic);pixels.data.set([...rgb.map(Math.round),255],4*i);});ctx.putImageData(pixels,0,0);figure.append(canvas,node('p',`色階 ${lo.toFixed(3)} ～ ${hi.toFixed(3)} · ${im.width} × ${im.height} 規則抽樣；灰色為零值／無資料。`,'hint'));return figure;
 }
 async function draw(){let row=report?.rows?.find(x=>x.pair===$('alosAcceptancePair').value);const box=$('alosAcceptancePreviews'),ticket=++previewSerial,currentKey=key;box.replaceChildren();if(!row)return;
  if(Object.values(row.products||{}).some(p=>!p.preview?.values)){
   box.append(node('p','正在讀取所選配對的抽樣預覽…','hint'));
   try{const data=await api('/api/alos-review-preview',{id:state.id,kind,pair:row.pair,report_id:report.report_id});if(ticket!==previewSerial||currentKey!==key)return;row=data.row;box.replaceChildren();}
   catch(error){if(ticket===previewSerial&&currentKey===key)box.replaceChildren(node('p','預覽無法載入：'+error.message,'error'));return;}
  }

  for(const message of row.errors||[])box.append(node('p',message,'error'));for(const message of row.warnings||[])box.append(node('p',message,'notice'));
  if(kind==='ion'){for(const which of ['before','after'])box.append(preview(labels[which],row.products?.[which]?.preview,[-Math.PI,Math.PI],true));const max=Math.max(Math.abs(row.correction?.min||0),Math.abs(row.correction?.max||0))||1;box.append(preview(labels.correction,row.correction,[-max,max]));}
  else{const a=row.products?.unwrapped?.preview,b=row.products?.masked?.preview;const vals=[a?.min,b?.min,a?.max,b?.max].filter(Number.isFinite),limits=vals.length?[Math.min(...vals),Math.max(...vals)]:[0,1];for(const which of ['unwrapped','masked','coherence'])box.append(preview(labels[which],row.products?.[which]?.preview,which==='coherence'?[0,1]:limits));}
 }
 function table(){const target=$('alosAcceptanceTable'),table=node('table'),head=node('tr');for(const t of ['配對','完整性','影像與尺寸','有限抽樣比例'])head.append(node('th',t));table.append(head);
  for(const row of report?.rows||[]){const tr=node('tr'),products=Object.entries(row.products||{});tr.append(node('td',row.pair),node('td',row.errors?.length?row.errors.join('；'):'已讀取預期產物'),node('td',products.map(([k,p])=>`${labels[k]}：${p.width} × ${p.height}`).join('\n')),node('td',products.map(([k,p])=>`${labels[k]}：${(100*p.finite_fraction).toFixed(1)}%（${p.samples} 點）`).join('\n')));table.append(tr);}target.replaceChildren(table);
  if(report?.geometry)target.append(node('p',`LOS 幾何：${report.geometry.width} × ${report.geometry.height}，${report.geometry.bands} 波段（角度）。`,'hint'));
 }
 window.alosAcceptanceUpdate=(s,data)=>{state=s;kind=s.status==='ion_correction_review'?'ion':s.output_acceptance?'outputs':s.ion_correction_review?'ion':null;report=kind?s[kind==='ion'?'ion_correction_review':'output_acceptance']:null;
  panel.hidden=!report||s.config.workflow!=='alosStack'||!!s.import_only;if(panel.hidden){key=null;return;}
  const next=s.id+':'+kind+':'+report.report_id;if(next!==key){key=next;$('alosAcceptanceReviewed').checked=false;$('alosAcceptanceNotes').value='';$('alosAcceptanceMessage').textContent='';$('alosAcceptancePair').replaceChildren();for(const row of report.rows||[]){const option=node('option',row.pair+(row.errors?.length?' · 需檢查':''));option.value=row.pair;$('alosAcceptancePair').append(option);}draw();table();}
  const ion=kind==='ion',approval=s[(ion?'ion_correction_review':'output_acceptance')+'_approval'],atGate=waiting();
  $('alosAcceptanceTitle').textContent=atGate?(ion?'電離層校正已套用，先比較再繼續':'cmd4 已結束，驗收實際成果'):(ion?'電離層校正判讀紀錄':'cmd4 成果驗收紀錄');
  $('alosAcceptanceBadge').textContent=atGate?'等待人工判讀':approval?'已記錄人工判讀':'檢查紀錄';
  let summary;
  if(atGate)summary=ion?'目前停在解纏之前。先比較校正前後與實際套用的電離層差值，再決定是否執行 cmd4。':'程式執行結束、成果結構檢查與人工判讀是三件事。確認成果後，才開啟後續品質分析與 MintPy。';
  else if(!ion&&approval&&s.status==='complete')summary='成果已人工驗收，可以進行品質分析與 MintPy。此處保留驗收時的證據與判讀紀錄；人工接受不等於所有科學品質已獲驗證。';
  else if(s.status==='running')summary='目前正在進行後續處理。此處保留先前的成果檢查'+(approval?'與人工判讀':'')+'紀錄，實際進度請看上方專案狀態。';
  else if(s.status==='failed')summary='後續處理已停止，需要檢查日誌。此處是先前的成果檢查'+(approval?'與人工判讀':'')+'紀錄，不代表後續步驟已完成。';
  else summary='這是先前的成果檢查'+(approval?'與人工判讀':'')+'紀錄；目前階段請以上方專案狀態為準。';
  $('alosAcceptanceSummary').textContent=summary;
  const metrics=$('alosAcceptanceMetrics');metrics.replaceChildren();for(const [value,label] of [[report.summary.expected_pairs,'預期配對'],[report.summary.readable_pairs,'可讀取配對'],[report.can_continue?'已通過':'待處理','結構／套用檢查'],[approval?'已記錄':'尚未確認','人工判讀']]){const card=node('div',undefined,'metric');card.append(node('strong',String(value)),node('small',label));metrics.append(card);}
  $('alosAcceptanceIssues').replaceChildren();for(const msg of report.errors||[])$('alosAcceptanceIssues').append(node('p',msg,'error'));if(!report.can_continue)$('alosAcceptanceIssues').append(node('p','有產物缺失或檢查不符，暫不能繼續。請看下方清單與執行日誌；更新檢查不會重新執行運算。','error'));
  const guide=$('alosAcceptanceGuide');guide.replaceChildren();const checks=ion?['三張圖使用同一雷達網格；前後相位固定為 −π 到 π，方便比較條紋與突變。','確認有效陸地沒有新增大範圍缺值或接縫，電離層差值不應把明顯雜訊帶進成果。','條紋減少不代表校正一定正確；保留可能的形變訊號，必要時回查原始電離層估計與低相干區。']:['對照未遮罩與遮罩解纏相位，注意斷裂、孤立區塊、明顯跳變及缺值。','結合相干性判斷可用區域；數值連續與檔案齊全不等於科學結果可靠。','這些是相位與 LOS 幾何資料。後續 MintPy 還需確認網路、參考點、日期與校正選擇。'];const list=node('ol');for(const c of checks)list.append(node('li',c));guide.append(list,node('p',ion?'預覽為雷達座標，向上不代表北方。':'地理網格已核對；此處僅顯示抽樣影像，未疊加底圖。','hint'));
  $('alosAcceptanceWarnings').replaceChildren();for(const warning of report.warnings||[])$('alosAcceptanceWarnings').append(node('p',warning,'hint'));
  $('alosAcceptanceDecision').hidden=!waiting();$('alosAcceptanceNext').textContent=ion?'確認後，只接續解纏與地理編碼':'完成驗收，保留判讀紀錄';$('alosAcceptanceConsent').textContent=ion?'我已比對校正前後及限制，確認執行 cmd4。':'我已檢查成果與品質限制，確認可進行後續分析。';$('alosAcceptanceContinue').textContent=ion?'確認校正結果，執行 cmd4':'記錄驗收，開啟後續分析';$('alosAcceptanceApproval').textContent=approval?`人工判讀：${approval.time} · ${approval.notes}`:'';controls();};
 $('alosAcceptancePair').onchange=()=>void draw();$('alosAcceptanceReviewed').onchange=controls;$('alosAcceptanceNotes').oninput=controls;
 action($('alosAcceptanceRefresh'),async()=>{if(!safe())return;const id=state.id;pending=true;controls();$('alosAcceptanceMessage').textContent='正在唯讀核對現有成果…';try{await api('/api/alos-review',{id,kind});if(id===state.id){$('alosAcceptanceReviewed').checked=false;$('alosAcceptanceNotes').value='';await refresh();$('alosAcceptanceMessage').textContent='檢查已更新，請重新確認判讀。';}}finally{pending=false;controls();}});
 action($('alosAcceptanceContinue'),async()=>{if($('alosAcceptanceContinue').disabled)return;const body={id:state.id,action:kind==='ion'?'ion_correction_continue':'output_accept',report_id:report.report_id,reviewed:true,notes:$('alosAcceptanceNotes').value.trim()};pending=true;controls();try{await api('/api/action',body);await refresh();}finally{pending=false;controls();}});
})();
