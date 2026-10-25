// A preparation guide only. Navigation never submits a processing or installation job.
(()=>{
 'use strict';
 const guide=window.SAR_STARTER_GUIDE;
 if(!guide?.workflows?.length)return;
 const make=(tag,text,cls)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;};
 const button=(text,handler,cls)=>{const el=make('button',text,cls);el.type='button';el.onclick=handler;return el;};
 const steps=['確認工具','準備資料','安排資料夾','準備清單','選擇下一步'];
 const statuses={implemented:'已提供流程',trial:'試驗中',planned:'尚未開放執行',preparation:'資料準備流程',experimental:'實驗性流程',import:'匯入既有成果'};
 const dialog=make('dialog',undefined,'starter-dialog');dialog.id='gettingStartedDialog';dialog.setAttribute('aria-labelledby','gettingStartedTitle');
 const head=make('div',undefined,'starter-head'),headText=make('div');
 headText.append(make('p','START HERE · 從零開始','starter-eyebrow'));
 const title=make('h2',guide.title||'第一次處理 SAR？從這裡開始。');title.id='gettingStartedTitle';title.tabIndex=-1;headText.append(title);
 const close=button('關閉',()=>dialog.close(),'starter-close');close.id='gettingStartedClose';close.setAttribute('aria-label','關閉從零開始指引');head.append(headText,close);
 const route=make('ol',undefined,'starter-route');route.setAttribute('aria-label','資料準備的五個步驟');
 const routeButtons=steps.map((label,i)=>{const li=make('li'),b=button('',()=>setStep(i));b.id=`gettingStartedStep${i}`;b.append(make('span',String(i+1).padStart(2,'0'),'starter-step-number'),make('span',label));b.setAttribute('aria-label',`第 ${i+1} 步：${label}`);li.append(b);route.append(li);return b;});
 const context=make('div',undefined,'starter-context'),contextLabel=make('label','這次想做什麼？');contextLabel.htmlFor='gettingStartedWorkflow';
 const select=make('select');select.id='gettingStartedWorkflow';
 for(const item of guide.workflows){const opt=make('option',item.name);opt.value=item.id;select.append(opt);}
 const status=make('span',undefined,'starter-status');status.id='gettingStartedStatus';context.append(contextLabel,select,status);
 const content=make('div',undefined,'starter-content');content.id='gettingStartedContent';
 const error=make('p',undefined,'starter-error');error.id='gettingStartedError';error.hidden=true;error.setAttribute('role','alert');
 const footer=make('div',undefined,'starter-footer'),previous=button('← 上一步',()=>setStep(step-1)),position=make('span',undefined,'starter-position'),next=button('下一步 →',()=>setStep(step+1),'primary');
 previous.id='gettingStartedPrevious';next.id='gettingStartedNext';position.id='gettingStartedPosition';position.setAttribute('aria-live','polite');footer.append(previous,position,next);
 dialog.append(head,route,context,content,error,footer);document.body.append(dialog);
 let step=0,workflow=guide.workflows.find(item=>item.id==='alosStack')||guide.workflows[0],checked=new Set(),folder=0,opener=null,restoreFocus=true,busy=false;
 select.value=workflow.id;
 select.onchange=()=>{const chosen=guide.workflows.find(item=>item.id===select.value);if(!chosen||chosen===workflow)return;workflow=chosen;checked.clear();folder=0;render();};
 dialog.addEventListener('close',()=>{if(restoreFocus&&opener?.isConnected!==false)opener?.focus?.({preventScroll:true});});
 function details(summary,items){const box=make('details',undefined,'starter-details');box.append(make('summary',summary));for(const item of items||[])box.append(make('p',item));return box;}
 function intro(kicker,heading,description){const box=make('div',undefined,'starter-section-heading'),h=make('h3',heading);h.id='gettingStartedSectionTitle';h.tabIndex=-1;box.append(make('p',kicker,'starter-eyebrow'),h);if(description)box.append(make('p',description,'starter-description'));content.append(box);}
 function actionButton(item,primary=false){const b=button(item.label,()=>navigate(item.action),primary?'primary':'');b.dataset.starterAction=item.action;return b;}
 function linkSource(item){const row=make('div',undefined,'starter-source'),link=make('a',item.label);if(/^https:\/\//i.test(item.url||'')){link.href=item.url;link.target='_blank';link.rel='noopener noreferrer';}row.append(link);if(item.detail)row.append(make('p',item.detail));return row;}
 function note(text){return make('p',text,'starter-note');}
 function glossary(){if(!guide.glossary?.length)return;const terms=details('看不懂的名詞？SAR、SLC、DEM、WBD…',[]);for(const item of guide.glossary){const p=make('p');p.append(make('strong',item.term+'：'),make('span',item.detail));terms.append(p);}content.append(terms);}
 function renderTools(){
  intro('01 / GET READY','先打開工作台，再確認運算工具。',guide.intro);
  const tiles=make('div',undefined,'starter-tool-grid');
  for(const [i,item] of (guide.toolSteps||[]).entries()){
   const card=make('article',undefined,'starter-tool-card');card.append(make('span',String(i+1).padStart(2,'0'),'starter-tool-number'),make('h4',item.title),make('p',item.detail));
   if(item.action&&item.label)card.append(actionButton(item));tiles.append(card);
  }
  content.append(tiles);
  const split=make('div',undefined,'starter-two-paths');
  for(const item of [{title:'還沒有衛星資料',detail:'先用工作台的合成小範例熟悉操作；它不是實際衛星觀測。',action:'demo',label:'先跑小範例'},{title:'已經拿到資料',detail:'下一步選擇資料種類，確認需要哪些檔案、該放在哪裡。',label:'查看資料需求 →'}]){
   const card=make('article');card.append(make('h4',item.title),make('p',item.detail));card.append(item.action?actionButton(item):button(item.label,()=>setStep(1)));split.append(card);
  }
  content.append(split,note('這份指引只會帶你前往功能頁。安裝、下載、匯入與運算都需要在該頁另外確認。'));
 }
 function renderData(){
  intro('02 / YOUR DATA','先選資料，再選適合的處理流程。',workflow.summary);
  const unsure=make('div',undefined,'starter-identify-shortcut');unsure.append(make('span','不確定資料種類？'),actionButton({action:'identify',label:'先辨識我的資料'}));content.append(unsure);
  if(workflow.statusDetail)content.append(note(workflow.statusDetail));
  const required=(workflow.requirements||[]).filter(item=>item.required),optional=(workflow.requirements||[]).filter(item=>!item.required);
  const count=make('div',undefined,'starter-counts');
  count.append(make('span',`${required.length} 項必要資料`,'starter-required'),make('span',`${optional.length} 項選用／依需求`));content.append(count);
  const grid=make('div',undefined,'starter-requirements');
  for(const item of workflow.requirements||[]){const card=make('article',undefined,'starter-requirement');card.append(make('span',item.required?'必要':'依需求',item.required?'starter-tag required':'starter-tag'),make('h4',item.title),make('p',item.detail));grid.append(card);}
  content.append(grid);
  if(workflow.status==='planned')content.append(note('這個模式目前沒有執行入口。你可以先準備資料，或切換至已提供的流程；不能從這份指引開始運算。'));
  if(workflow.sources?.length){const sources=details('資料從哪裡取得？官方來源與下載提醒',[]);for(const item of workflow.sources)sources.append(linkSource(item));content.append(sources);}
  if(workflow.notes?.length)content.append(details('選資料時要留意什麼？',workflow.notes));
  glossary();
 }
 function renderFolders(){
  intro('03 / KEEP IT ORGANIZED','原始資料、地形、處理成果，各有自己的位置。','下方是建議的放法，不是你電腦的實際檔案。點選資料夾可查看用途。');
  const layout=make('div',undefined,'starter-folder-layout'),tree=make('div',undefined,'starter-folder-tree'),info=make('div',undefined,'starter-folder-info');
  tree.setAttribute('role','group');tree.setAttribute('aria-label','資料夾配置範例');
  tree.append(make('p','資料位置一覽（範例）','starter-tree-root'));
  const folders=workflow.folders||[];if(folder>=folders.length)folder=0;
  const folderButtons=[];
  function selectFolder(index){folder=index;for(const [i,b] of folderButtons.entries())b.setAttribute('aria-pressed',String(i===folder));info.replaceChildren();const item=folders[folder];if(!item)return;info.append(make('span','這裡放什麼？','starter-eyebrow'),make('h4',item.role),make('code',item.path),make('p',item.detail));}
  for(const [i,item] of folders.entries()){const b=button('',()=>selectFolder(i),'starter-folder');b.id=`gettingStartedFolder${i}`;b.append(make('span',item.path,'starter-folder-path'),make('small',item.role));b.setAttribute('aria-controls','gettingStartedFolderInfo');folderButtons.push(b);tree.append(b);}
  info.id='gettingStartedFolderInfo';info.setAttribute('aria-live','polite');layout.append(tree,info);content.append(layout);selectFolder(folder);
  if(guide.folderPlan?.tree){const example=details(guide.folderPlan.title||'展開完整資料夾範例',[]);example.append(make('pre',guide.folderPlan.tree));content.append(example);}
  if(guide.folderPlan?.notes?.length)content.append(details('外接硬碟、路徑與自動產生的檔案',guide.folderPlan.notes));
 }
 function renderChecklist(){
  intro('04 / MY PREPARATION','用這張清單整理你手邊的準備。','這是你自行勾選的準備紀錄，不會檢查檔案，也不會替你通過影格對應、品質或科學判讀關卡。');
  const summary=make('div',undefined,'starter-check-summary'),label=make('strong'),progress=make('progress');progress.max=workflow.checklist?.length||1;progress.setAttribute('aria-label','自行勾選的準備進度');
  label.id='gettingStartedChecklistCount';label.setAttribute('aria-live','polite');summary.append(label,progress);content.append(summary);
  const list=make('div',undefined,'starter-checklist');
  function update(){label.textContent=`已自行勾選 ${checked.size} / ${workflow.checklist?.length||0} 項`;progress.value=checked.size;}
  for(const item of workflow.checklist||[]){const row=make('label'),input=make('input');input.type='checkbox';input.id=`gettingStartedCheck-${item.id}`;input.checked=checked.has(item.id);input.onchange=()=>{if(input.checked)checked.add(item.id);else checked.delete(item.id);update();};row.append(input,make('span',item.label));list.append(row);}
  content.append(list,note('切換處理流程會清空這張清單。即使全部勾選，仍須到實際資料檢查頁確認；未勾完也可以繼續閱讀指引。'));update();
 }
 function renderNext(){
  intro('05 / YOUR NEXT STEP','選擇最符合你現在進度的入口。',workflow.summary);
  const summary=make('div',undefined,'starter-next-summary');summary.append(make('span',statuses[workflow.status]||workflow.status,'starter-tag'),make('h4',workflow.name),make('p',`個人準備清單：自行勾選 ${checked.size} / ${workflow.checklist?.length||0} 項。尚未執行資料驗證。`));content.append(summary);
  if(workflow.statusDetail)summary.append(make('p',workflow.statusDetail));
  const actions=make('div',undefined,'starter-next-actions');
  if(workflow.status==='planned'||!workflow.next?.length)actions.append(note('此流程尚未開放執行。請在上方切換流程，或先前往「工具與環境」熟悉工作台。'));
  else for(const [i,item] of workflow.next.entries())actions.append(actionButton(item,i===0));
  content.append(actions);
  const shortcuts=make('div',undefined,'starter-shortcuts');shortcuts.append(make('p','也可以從這裡開始','starter-eyebrow'));
  const buttons=make('div',undefined,'starter-shortcut-buttons');
  for(const item of [{action:'tools',label:'開啟工具與環境'},{action:'demo',label:'先跑小範例'},{action:'identify',label:'辨識我的資料'},{action:'legacy',label:'匯入已完成成果'},{action:'portable',label:'匯入可攜分析包'}])buttons.append(actionButton(item));
  shortcuts.append(buttons);content.append(shortcuts,note('開啟入口後，先讀取資料並查看檢查結果。這裡不會自動建立專案、啟動運算，或改動原始資料。'));
  glossary();
 }
 function render(){
  context.hidden=step===0;select.value=workflow.id;status.textContent=statuses[workflow.status]||workflow.status;status.dataset.status=workflow.status;
  content.replaceChildren();[renderTools,renderData,renderFolders,renderChecklist,renderNext][step]();
  routeButtons.forEach((b,i)=>{if(i===step)b.setAttribute('aria-current','step');else b.removeAttribute('aria-current');});
  previous.hidden=step===0;next.hidden=step===steps.length-1;position.textContent=`${step+1} / ${steps.length}`;
 }
 function setStep(value){step=Math.min(steps.length-1,Math.max(0,value));error.hidden=true;render();dialog.scrollTop=0;document.getElementById('gettingStartedSectionTitle')?.focus({preventScroll:true});}
 function activate(id){const target=document.getElementById(id);if(!target||typeof target.click!=='function')throw Error('這個入口尚未載入，請重新整理工作台後再試。');target.click();}
 function call(name){if(typeof window[name]!=='function')throw Error('這個入口尚未載入，請重新整理工作台後再試。');return window[name]();}
 async function navigate(action){
  if(busy)return;busy=true;error.hidden=true;
  // This closed list intentionally contains navigation only, never processing endpoints.
  const navigation={tools:()=>activate('openWelcome'),demo:()=>{activate('openWelcome');activate('openLesson');},'alos-import':()=>call('openAlosImport'),identify:()=>call('openWorkspaceCreate'),legacy:()=>call('openLegacyImport'),portable:()=>call('openPortableImport'),alos4:()=>call('openAlosPreparation'),nisar:async()=>{await call('openNisar');activate('missionNisarTab');}};
  try{if(!navigation[action])throw Error('找不到這個指引入口。');restoreFocus=false;dialog.close();await navigation[action]();}
  catch(e){restoreFocus=true;if(!dialog.open)dialog.showModal();error.textContent=e.message||'暫時無法開啟入口，請再試一次。';error.hidden=false;error.tabIndex=-1;error.focus({preventScroll:true});}
  finally{busy=false;}
 }
 window.openGettingStarted=(options={})=>{
  if(!dialog.open)opener=document.activeElement;
  const wanted=guide.workflows.find(item=>item.id===options.workflow);
  if(wanted&&wanted!==workflow){workflow=wanted;checked.clear();folder=0;}
  restoreFocus=true;step=Number.isInteger(options.step)?Math.min(4,Math.max(0,options.step)):0;error.hidden=true;render();
  if(!dialog.open)dialog.showModal();dialog.scrollTop=0;title.focus({preventScroll:true});
 };
})();
