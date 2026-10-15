// The installed ISCE2 builds these pairs; the browser only displays the report.
(()=>{
 const date=d=>`20${d.slice(0,2)}-${d.slice(2,4)}-${d.slice(4,6)}`;
 function network(target,data,title,reference){
  if(!data)return;
  const block=node('section',undefined,'confirmation-network');block.append(node('h3',title),node('p',`${data.pairs.length} 組配對 · ${data.dates.length} 個來源日期 · ${data.components.length} 個連通群`,'notice'));
  target.append(block);
  if(!data.pairs.length){block.append(node('p','此網路沒有配對；若電離層估計已關閉，這是預期結果。','hint'));return;}
  const canvas=node('div',undefined,'confirmation-plot');block.append(canvas);
  const x=[],y=[],hover=[];
  for(const pair of data.pairs){const [a,b]=pair.split('-');x.push(date(a),date(b),null);y.push(data.dates.indexOf(a),data.dates.indexOf(b),null);hover.push(pair,pair,'');}
  const traces=[{x,y,text:hover,hovertemplate:'%{text}<extra></extra>',type:'scatter',mode:'lines',line:{color:'#94b4a6',width:1.5}},
   {x:data.dates.map(date),y:data.dates.map((_,i)=>i),text:data.dates.map(d=>`${date(d)}${d===reference?' · 對位參考日期':''}${data.isolated.includes(d)?' · 未配對':''}`),type:'scatter',mode:'markers',hovertemplate:'%{text}<extra></extra>',marker:{size:11,color:data.dates.map(d=>data.isolated.includes(d)?'#b64d3f':d===reference?'#bb7a24':'#235c4d')}}];
  requestAnimationFrame(()=>Plotly.newPlot(canvas,traces,{height:300,margin:{l:25,r:20,t:15,b:55},showlegend:false,paper_bgcolor:'#fbfcf8',plot_bgcolor:'#fbfcf8',xaxis:{type:'date',title:{text:'觀測日期'},tickformat:'%Y-%m'},yaxis:{visible:false},hovermode:'closest'},{responsive:true,displayModeBar:false}));
  block.append(node('p','橫軸是真實觀測時間；縱向只是排列日期，不是垂直基線。金色點為對位參考日期，紅色點為未配對日期。可拖曳縮放，雙擊恢復。','hint'));
  if(!data.connected)block.append(node('p',`網路未全部連通。未配對：${data.isolated.map(date).join('、')||'無，但存在分離群'}。請核對日期／配對篩選。`,'error'));
  const details=node('details'),table=node('table'),head=node('tr');details.append(node('summary','查看配對清單與時間間隔'));head.append(node('th','配對'),node('th','間隔（天）'));table.append(head);
  for(const pair of data.pairs){const [a,b]=pair.split('-'),tr=node('tr');tr.append(node('td',date(a)+' → '+date(b)),node('td',String(Math.round((Date.parse(date(b))-Date.parse(date(a)))/86400000))));table.append(tr);}details.append(table);block.append(details);
 }
 window.renderConfirmation=(target,r,edit)=>{
  target.replaceChildren();
  const c=r.config||{},resolved=r.resolved||{};
  if((c.workflow||'alosStack')==='alosStack'){
   target.append(node('p',r.status==='ready'?'以下為已產生指令中的配對，即將執行科學運算。':r.status?'以下為保存的處理紀錄。':'以下為所選 ISCE2 解析設定後的預覽。建立專案不會立即開始科學運算。','notice'));
   if(r.legacy)target.append(node('p','此專案建立於確認頁推出前，沒有當時的輸入快照；只能顯示既有設定與產生的配對。','hint'));
   const cards=node('div',undefined,'confirmation-grid');target.append(cards);
   for(const [label,value] of [['對位參考日期',c.reference?date(c.reference):'未選擇'],['極化',resolved.polarization||c.properties?.polarization||'尚未解析'],['一般配對後續日期數',resolved.subsequent??'尚未解析'],['電離層配對後續日期數',resolved.estimate===false?'不執行':resolved.subsequent_ion??'尚未解析'],['估計電離層',resolved.estimate===undefined?'尚未解析':resolved.estimate?'啟用':'關閉'],['套用電離層校正',resolved.estimate===undefined?'尚未解析':resolved.estimate&&resolved.apply?'啟用':'不執行']]){const card=node('div',undefined,'confirmation-stat');card.append(node('small',label),node('strong',String(value)));cards.append(card);}
   if(edit){const b=node('button','返回修改日期、配對或電離層設定');b.type='button';b.onclick=edit;target.append(b);}
   target.append(node('p','「後續日期數」不是配對總數。更多配對會增加運算與磁碟需求；時間間隔長也可能降低相干性，不能只用配對數判斷品質。','hint'));
   const tabs=node('div',undefined,'inline'),net=node('div');target.append(tabs,net);
   let active=null;for(const [key,title] of [['network','一般干涉網路'],['ion_network','電離層網路']]){const b=node('button',title);b.type='button';b.onclick=()=>{if(active)active.classList.remove('selected');active=b;b.classList.add('selected');for(const p of net.querySelectorAll('.confirmation-plot'))Plotly.purge(p);net.replaceChildren();network(net,r[key],title,c.reference);};tabs.append(b);if(key==='network')b.click();}
   if(r.generated)target.append(node('p',r.generated.matched?'✓ 已核對：產生指令的配對與確認頁一致。':'指令與原預覽不一致，請檢查。',r.generated.matched?'notice':'error'));
   if(r.inventory){const details=node('details'),view=node('div');details.append(node('summary','影像清單、位置與共同覆蓋'),view);target.append(details);let loaded=false;details.ontoggle=()=>{if(details.open&&!loaded){loaded=true;window.renderImportMetadata(r.inventory,null,view);}};}
   const paths=node('details'),dl=node('dl');paths.append(node('summary','資料路徑、軟體版本與完整參數'),dl);target.append(paths);
   for(const [k,v] of Object.entries({'資料來源':c.source,'專案位置':c.root,'資料使用方式':c.copy?'複製並驗證':'直接讀取原始資料','執行環境':c.env,'對位 DEM':c.dem,'地理編碼 DEM':c.dem_geo,'水體遮罩':c.wbd,...c.properties}))dl.append(node('dt',labels[k]||k),node('dd',String(v??'')));
   const packages=(r.packages||[]).filter(p=>['isce2','mintpy','python','numpy','gdal','snaphu'].includes(p.name));for(const p of packages)paths.append(node('p',`${p.name} ${p.version} · ${p.build} · ${p.subdir}`,'hint'));
   paths.append(node('p','建立後會保存這份確認報告、完整 Conda 套件版本與輸入檔案大小／修改時間；小型描述檔和配對程式另存 SHA-256。未對全部大型影像做內容雜湊，也不保證可重建已被修改的外部環境。','hint'));
  }
  target.append(node('h3',r.status?'保存的檢查紀錄（執行時會重新健檢）':'執行前檢查'));
  for(const row of r.checks||[]){const div=node('div',undefined,'health-row '+row.status);div.append(node('strong',({pass:'✓ 已確認',warning:'△ 請確認',error:'✕ 需修正'}[row.status])+' · '+row.title),node('p',row.detail,'hint'));target.append(div);}
 };
 const card=node('div',undefined,'card'),heading=node('div',undefined,'card-heading'),open=node('button','查看處理確認紀錄');heading.append(node('h2','這次處理會做什麼？'),open);card.append(heading,node('p','查看影像範圍、配對網路、設定與準備階段的核對結果。','hint'));$('project').insertBefore(card,$('project').querySelector('.project-settings'));
 let project=null;
 window.confirmationProject=s=>{project=s;card.hidden=!!s.import_only||['alos2App','topsApp'].includes(s.config.workflow);open.disabled=s.status==='running';};
 const dialog=node('dialog',undefined,'confirmation-dialog'),body=node('div'),footer=node('div',undefined,'inline'),close=node('button','返回'),start=node('button','確認並開始運算','primary'),ack=node('label',undefined,'check'),checkbox=node('input');checkbox.type='checkbox';ack.append(checkbox,document.createTextNode('我已核對配對與設定，準備開始運算。'));footer.append(close,start);dialog.append(node('h2','處理確認'),body,ack,footer);document.body.append(dialog);close.onclick=()=>dialog.close();let pending=null;checkbox.onchange=()=>start.disabled=!checkbox.checked;
 async function show(id,run){pending=null;start.hidden=!run;ack.hidden=!run;checkbox.checked=false;start.disabled=true;body.replaceChildren(node('p','正在讀取保存的確認紀錄…'));dialog.showModal();try{const r=await api('/api/confirmation-saved',{id});if(!dialog.open)return;window.renderConfirmation(body,r);if(run&&r.status!=='ready')throw Error('專案狀態已改變，請返回重新整理。');pending=id;}catch(e){body.replaceChildren(node('p',e.message,'error'));throw e;}}
 action(open,()=>show(project.id,false));
 window.confirmProcessing=id=>show(id,true);
 action(start,async()=>{if(!pending||!checkbox.checked)return;const id=pending;start.disabled=true;try{await api('/api/action',{id,action:'run'});dialog.close();await refresh();}finally{checkbox.onchange();}});
})();
