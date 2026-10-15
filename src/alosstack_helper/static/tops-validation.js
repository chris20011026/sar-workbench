// Evidence inventory is separate from processing and from human scientific review.
(() => {
 const card=node('details',undefined,'card tops-validation');card.id='topsValidation';card.hidden=true;
 const summary=node('summary','Sentinel-1 真實影像驗證'),intro=node('p','把原生步驟、共同 burst、對位統計和成果檔案整理在一起，再逐項記錄你的科學判讀。這裡不會啟動處理。','hint');
 const load=node('button','整理驗證紀錄'),output=node('div');output.setAttribute('aria-live','polite');
 card.append(summary,intro,load,output);$('topsWorkbench')?.after(card);
 let selected=null,serial=0,report=null,dirty=false;
 const stages={pending:'待檢查',reviewed:'已看過，尚未判定',pass:'人工判定符合',fail:'發現問題'};
 const kindNames={unknown:'尚未確認',synthetic:'合成／示範資料',real:'真實衛星影像（由我聲明）'};
 function description(label,content){const d=node('details');d.append(node('summary',label),node('pre',content));return d;}
 function inputField(label,value,multiline=false){const wrap=node('label',label),field=node(multiline?'textarea':'input');field.value=value||'';field.maxLength=multiline?4000:120;if(multiline)field.rows=3;field.addEventListener('input',()=>dirty=true);wrap.append(field);return {wrap,field};}
 function choose(label,choices,value){const wrap=node('label',label),field=node('select');for(const [v,t] of Object.entries(choices)){const o=node('option',t);o.value=v;field.append(o);}field.value=value;field.addEventListener('change',()=>dirty=true);wrap.append(field);return {wrap,field};}
 const guide=node('details',undefined,'tops-validation-guide');guide.append(node('summary','真實影像驗證需要準備什麼？'),node('p','目前保留真實資料驗證關卡。程式安裝、合成測試與影像名稱辨識不能替代兩期真實 IW SLC 的完整處理。','notice'));
 const needed=node('ol');for(const t of ['兩期同相對軌道、同升降軌且有共同極化／子波束的 Sentinel-1 IW SLC，GRD 不適用。','覆蓋完整共同 burst 的 WGS84 橢球高 DEM 與兩期精密軌道。','完成工作台的 21 個原生處理步驟，保留來源、版本、XML、日誌與成果。','在獨立資料夾以相同版本與設定執行原生 topsApp，核對共同 burst、ESD／對位、相干性、解纏、網格與相位。','在專案「Sentinel-1 真實影像驗證」保存觀察與對照證據，匯出 JSON 或可列印 HTML 供檢查。'])needed.append(node('li',t));guide.append(needed,node('p','本版不自動取得原始 SAR，不將 topsStack 解鎖，也不把人工勾選當作維護者認證。','hint'));$('topsForm')?.append(guide);
 window.topsValidationUpdate=(s,data)=>{
  const isTops=s.config.workflow==='topsApp';card.hidden=!isTops;
  if(!isTops){selected=null;serial++;return;}
  if(selected!==s.id){selected=s.id;serial++;report=null;dirty=false;output.replaceChildren();card.open=false;}
  load.disabled=s.status==='running'||Boolean(data?.busy);intro.textContent=load.disabled?'目前正在處理，完成或停止後才能整理一致的驗證紀錄。':'自動整理檔案與紀錄，再由你填寫科學觀察；不啟動處理、不改動原始成果。';
 };
 async function fetchReport(id){const generation=++serial;load.disabled=true;try{const r=await api('/api/tops-validation?id='+encodeURIComponent(id));if(selected!==id||generation!==serial)return;report=r;dirty=false;render(r);}finally{if(selected===id)load.disabled=false;}}
 action(load,async()=>{if(!selected)return;if(dirty&&!confirm('重新整理會捨棄尚未儲存的評閱文字，確定繼續？'))return;await fetchReport(selected);});
 window.openTopsValidation=async id=>{selected=id;card.hidden=false;card.open=true;card.scrollIntoView({behavior:'smooth',block:'start'});await fetchReport(id);};
 async function download(id,file){const response=await fetch('/api/tops-validation-artifact?id='+encodeURIComponent(id)+'&file='+encodeURIComponent(file),{headers:{'X-Token':token}});if(!response.ok){let message='無法匯出驗證報告';try{message=(await response.json()).error||message;}catch{}throw Error(message);}const url=URL.createObjectURL(await response.blob()),a=node('a');a.href=url;a.download=file;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1500);}
 function render(r){
  output.replaceChildren();output.append(node('p',r.message,r.status==='needs_attention'?'error':'notice'),node('p',r.notice,'hint'));
  const e=r.evidence;
  output.append(node('h3','1 · 自動整理的證據'),node('p','「已找到」只表示紀錄／檔案條件已具備，並非科學品質合格。','hint'));
  const checks=node('div',undefined,'tops-validation-checks');for(const c of e.checks){const row=node('div',undefined,'tops-validation-check');row.append(node('strong',(c.available?'已找到 · ':'待補齊 · ')+c.title),node('p',c.detail,'hint'));checks.append(row);}output.append(checks);
  const sources=node('div',undefined,'tops-validation-sources');for(const s of e.sources){const p=node('div');p.append(node('strong',s.role==='reference'?'參考影像':'比較影像'),node('p',s.name||'尚無紀錄'),node('small',`${s.date||'—'} · 軌道 ${s.relative_orbit||'—'} · ${s.direction||'—'}`,'hint'));sources.append(p);}output.append(sources);
  if(e.geometry.channels?.length){const list=node('ul');for(const c of e.geometry.channels)list.append(node('li',`IW${c.swath}：${c.count} 個共同 burst，參考從 ${c.reference_start}、比較從 ${c.secondary_start} 開始。`));output.append(list);}
  const statistics=node('div',undefined,'tops-validation-sources');for(const [key,title] of [['esd','ESD 方位向修正'],['range_coregistration','距離向修正']]){const item=e.statistics[key],box=node('div');box.append(node('strong',title));if(item){const list=node('dl');for(const [k,label] of [['median_pixels','中位數（pixel）'],['mean_pixels','平均值（pixel）'],['std_pixels','標準差（pixel）'],['sample_count','使用樣本數'],['coherence_threshold','相干性篩選門檻'],['snr_threshold','SNR 篩選門檻']])if(item.values[k]!==undefined)list.append(node('dt',label),node('dd',String(item.values[k])));box.append(list,node('small',item.meaning,'hint'));}else box.append(node('p','目前讀取範圍沒有完整、可辨識的原生日誌段落。','hint'));statistics.append(box);}output.append(statistics);
  if(e.statistics.truncated)output.append(node('p','日誌較大，目前僅讀取最後 1 MiB；較早的對位統計可能未列出，請查看原生日誌補充觀察。','hint'));
  output.append(description('查看原生步驟、網格與來源紀錄',JSON.stringify({steps:e.steps,selection:e.selection,geometry:e.geometry,resolved:e.resolved,products:e.products,results_summary:e.results_summary},null,2)));
  output.append(node('h3','2 · 人工科學評閱'),node('p','可先儲存未完成的草稿。任何「已看過／符合／問題」都需要觀察說明與評閱者；不會由檔案存在自動勾選。','hint'));
  const identity=node('div',undefined,'grid'),kind=choose('資料性質',kindNames,r.review.dataset_kind||'unknown'),reviewer=inputField('評閱者',r.review.reviewer),dataset=inputField('影像取得來源與產品說明',r.review.dataset_note,true);identity.append(kind.wrap,reviewer.wrap);output.append(identity,dataset.wrap);
  const fields={};for(const item of r.review_items){const row=node('section',undefined,'tops-validation-review'),old=r.review.items?.[item.id]||{},state=choose(item.title,stages,old.status||'pending'),note=inputField('觀察、判斷依據與需處理的問題',old.note,true);state.field.setAttribute('aria-label',item.title+'評閱狀態');note.field.setAttribute('aria-label',item.title+'觀察');row.append(state.wrap,node('p',item.help,'hint'),note.wrap);output.append(row);fields[item.id]={state:state.field,note:note.field};}
  output.append(node('h3','3 · 獨立對照的證據'),node('p','以下由評閱者提供。工作台不會執行所填指令、開啟紀錄路徑或自動計算相位差異。請說明網格、遮罩與相位參考後再報告差異。','hint'));
  const independent={};for(const [key,label] of [['record_location','獨立處理紀錄位置／報告識別'],['software_version','ISCE2 版本與執行環境'],['same_inputs_settings','相同來源、軌道、DEM 與設定如何核對'],['grid_reference_mask','比較網格、相位參考與有效遮罩'],['comparison_results','比較結果、單位、樣本數與差異判讀']]){const f=inputField(label,r.review.independent?.[key],true);independent[key]=f.field;output.append(f.wrap);}
  const feedback=node('p',r.review.saved_at?'上次儲存：'+r.review.saved_at:'尚未儲存評閱。','hint');feedback.setAttribute('role','status');const save=node('button','儲存評閱','primary'),exports=node('div',undefined,'inline');exports.append(save);for(const [file,label] of [['validation.html','匯出可列印報告'],['validation.json','匯出完整 JSON']]){const b=node('button',label);action(b,async()=>{if(dirty)throw Error('請先儲存評閱，再匯出包含最新文字的報告。');await download(r.project_id,file);});exports.append(b);}output.append(feedback,exports);
  action(save,async()=>{const id=r.project_id,generation=serial;save.disabled=true;feedback.textContent='儲存評閱…';try{const data={id,evidence_id:r.evidence_id,dataset_kind:kind.field.value,reviewer:reviewer.field.value,dataset_note:dataset.field.value,items:Object.fromEntries(Object.entries(fields).map(([key,f])=>[key,{status:f.state.value,note:f.note.value}])),independent:Object.fromEntries(Object.entries(independent).map(([key,f])=>[key,f.value]))};const saved=await api('/api/tops-validation-review',data);if(selected!==id||generation!==serial)return;report=saved;dirty=false;render(saved);}catch(error){feedback.textContent=error.message;throw error;}finally{save.disabled=false;}});
  const refs=node('details'),list=node('ul');refs.append(node('summary','上游實作與判讀範圍'));for(const ref of r.references){const li=node('li'),a=node('a',ref.title);a.href=ref.url;a.target='_blank';a.rel='noopener noreferrer';li.append(a);list.append(li);}refs.append(list);output.append(refs);
 }
})();
