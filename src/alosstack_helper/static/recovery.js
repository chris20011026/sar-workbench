/* Phase recovery is explicit and never triggered by polling or page reload. */
(() => {
 const box=node('details',undefined,'card recovery-center');box.id='recoveryCenter';
 const title=node('summary','恢復中心 · ALOS-2 多日期'), description=node('p',undefined,'hint');
 const toggle=node('input');toggle.type='checkbox';toggle.id='recoveryEnabled';
 const toggleLabel=node('label',undefined,'check');toggleLabel.append(toggle,document.createTextNode('保存主要階段開始前的復原副本'));
 const storage=node('p','會增加磁碟讀取與儲存需求；優先使用 APFS 獨立副本，其他磁碟使用完整複製。每個專案只保留最新階段前副本；失敗半成品另外保留。','hint');
 const diagnose=node('button','診斷中斷原因與復原條件'), output=node('div');output.setAttribute('aria-live','polite');
 box.append(title,description,toggleLabel,storage,diagnose,output);
 $('project').insertBefore(box,$('project').querySelector('.project-settings'));
 let selected=null, stateKey=null, serial=0;
 window.recoveryUpdate=s=>{
   const stack=(s.config.workflow||'alosStack')==='alosStack'&&!s.import_only;box.hidden=!stack;if(!stack)return;
   const next=s.id+':'+s.status+':'+(s.recovery?.id||'')+':'+(s.error||'');
   if(stateKey!==next){stateKey=next;output.replaceChildren();serial++;if(['new','ready','review','failed'].includes(s.status))box.open=true;}
   selected=s;toggle.checked=!!s.recovery_enabled;
   toggle.disabled=!!window.workbenchState?.busy||!['new','ready','review'].includes(s.status);
   diagnose.disabled=!!window.workbenchState?.busy;
   description.textContent=s.status==='failed'?'工作已停止。先查看診斷，再決定是否還原階段前副本。':s.recovery_enabled?'復原保護已開啟。下次主要階段執行前會保存副本，空間或驗證不足時會停止。':'本專案尚未開啟復原保護。可在開始運算前或人工判讀時啟用；無法補建過去失敗前的副本。';
   if(s.status==='failed')box.open=true;
 };
 toggle.addEventListener('change',async()=>{const key=selected?.id;if(!key)return;toggle.disabled=true;try{await api('/api/recovery-enable',{id:key,enabled:toggle.checked});await refresh();}catch(e){fail(e);toggle.checked=!!selected?.recovery_enabled;}finally{if(selected?.id===key)toggle.disabled=!!window.workbenchState?.busy||!['new','ready','review'].includes(selected.status);}});
 action(diagnose,async()=>{
   const id=selected?.id, generation=++serial;if(!id)return;
   diagnose.disabled=true;output.replaceChildren(node('p','正在讀取診斷…'));
   try{
     const d=await api('/api/recovery?id='+encodeURIComponent(id));
     if(selected?.id!==id||generation!==serial)return;
     output.replaceChildren(node('h3',d.diagnosis.title),node('p',d.diagnosis.advice));
     output.append(node('p','中斷位置：'+(d.phase||'尚無紀錄')+'；最後完成：'+(d.last_completed||'尚無紀錄'),'hint'));
     if(d.error)output.append(node('p',d.error,'error'));
     if(d.blocked)output.append(node('p',d.blocked,'notice'));
     if(d.can_recover){
       output.append(node('p','將回到「'+d.snapshot.phase+'」開始前，重跑該階段，再依原流程執行。'+(selected.ion?.estimate===false?'本專案關閉電離層，後續執行 cmd4。':'仍保留原設定要求的人工判讀。'),'notice'));
       output.append(node('p',d.notice,'hint'),node('p','副本邏輯大小：約 '+(d.snapshot.bytes/1024**3).toFixed(2)+' GB。半成品保留位置：'+d.preserved,'hint'));
       const consent=node('input');consent.type='checkbox';consent.id='recoveryConsent';
       const label=node('label',undefined,'check');label.append(consent,document.createTextNode('我了解會將半成品另外保留，並從上述階段開始前重跑；副本建立前已完成的階段保留。'));
       const go=node('button','驗證副本並復原續跑','primary');go.disabled=true;
       consent.onchange=()=>go.disabled=!consent.checked;output.append(label,go);
       action(go,async()=>{if(!consent.checked||selected?.id!==id)throw Error('請重新診斷並確認。');go.disabled=true;try{await api('/api/action',{id,action:'recover',snapshot:d.snapshot.id,confirmed:true});await refresh();}catch(e){go.disabled=false;throw e;}});
     }
     const logs=node('details');logs.append(node('summary','查看最後執行紀錄'),node('pre',d.log_tail||'尚無日誌'));output.append(logs);
   }catch(e){if(selected?.id===id&&generation===serial)output.replaceChildren(node('p','診斷暫時無法讀取：'+e.message,'error'));}
   finally{if(selected?.id===id)diagnose.disabled=!!window.workbenchState?.busy;}
 });
})();
