'use strict';
const $ = id => document.getElementById(id);
const t = (...args) => window.MhizvaI18n.t(...args);
const completedAnswers = [];
const state = {ready:false, asking:false, uploading:false, docRequest:0, docCount:0};
const make = (tag, className, text) => { const el=document.createElement(tag); if(className) el.className=className; if(text!==undefined) el.textContent=text; return el; };
async function api(url, options={}) {
  const response=await fetch(url, options);
  let data; try { data=await response.json(); } catch { throw new Error(t('unreadable')); }
  if(!response.ok) throw new Error(typeof data.error==='string' ? data.error : t('requestError'));
  return data;
}
function updateSend() { $('send').disabled=!state.ready || state.asking || state.uploading || !$('question').value.trim(); }
function updateWelcome() {
  const hasDocs=state.docCount>0;
  $('welcome-description').replaceChildren(
    document.createTextNode(hasDocs ? t('welcomeReady') : t('welcomeEmpty')),
    make('br'),document.createTextNode(t('welcomeHelp')));
  $('welcome-hint').textContent=hasDocs ? t('hintReady') : t('hintEmpty');
}
async function refreshStatus() {
  try {
    const data=await api('/api/status');
    state.ready=data.model==='ready' && !data.busy;
    const label=data.model==='ready' ? (data.busy?t('working'):t('ready')) : data.model==='error'?t('unavailable'):t('loading');
    $('model-status').className='model-status '+(data.model==='ready'?'ready':data.model==='error'?'error':'');
    $('model-status').replaceChildren(make('i'), document.createTextNode(label));
    $('demo-notice').hidden=!['stand-in','fake','demo'].includes(data.mode);
    $('doc-count').textContent=Number.isFinite(data.docs)?String(data.docs):'—';
    if(Number.isFinite(data.docs)){state.docCount=data.docs;updateWelcome();}
    $('compose-note').textContent=data.model==='error'?t('modelError'):data.model==='loading'?t('modelLoading'):t('sourceReminder');
  } catch {
    state.ready=false; $('model-status').className='model-status error';
    $('model-status').replaceChildren(make('i'),document.createTextNode(t('reconnecting')));
    $('compose-note').textContent=t('connectionError');
  }
  updateSend();
}
function treeNode(node) {
  if(node.kind==='folder') {
    const details=make('details'); details.open=true;
    const summary=make('summary'); summary.append(make('span','','▱'),make('span','',node.name || t('documents'))); summary.title=(node.summary || '').replace(/\s+/g,' ');
    const children=make('div','children'); for(const child of node.children || []) children.append(treeNode(child));
    details.append(summary,children); return details;
  }
  const button=make('button'); button.type='button'; button.title=(node.summary || node.path || '').replace(/\s+/g,' ');
  button.append(make('span','file-icon','▤'),make('span','',(node.name || node.path || t('untitled')).replace(/\.md$/i,'')));
  button.addEventListener('click',()=>openDoc(node.path)); return button;
}
async function refreshTree() {
  try {
    const root=await api('/api/tree'); const nodes=root.kind==='folder' ? root.children || [] : [root];
    $('tree').replaceChildren(...(nodes.length?nodes.map(treeNode):[make('p','muted',t('libraryEmpty'))]));
  } catch { const retry=make('button','',t('libraryRetry'));retry.addEventListener('click',refreshTree);$('tree').replaceChildren(retry); }
}
function message(role,text,extraClass='') {
  $('welcome').hidden=true;
  const article=make('article',`message ${role} ${extraClass}`); article.append(make('div','message-label',role==='user'?t('you'):'Mhizva'),make('div','message-content',text));
  $('messages').append(article); scrollToLatest(); return article;
}
function renderAnswer(article,result) {
  article.replaceChildren(make('div','message-label','Mhizva'),make('div','message-content',result.answer || t(result.found?'found':'notFound')));
  if(result.table||result.chart||result.coverage)article.append(window.renderMhizvaResult(result,openDoc));
  if(Array.isArray(result.quotes)&&result.quotes.length) {
    const sources=make('div','sources');
    result.quotes.forEach((quote,i)=>{const button=make('button','source');button.type='button';button.append(make('strong','',(i+1)+'. '+(quote.title || quote.path || t('source'))),make('span','',quote.text || t('openDocument')));button.addEventListener('click',()=>openDoc(quote.path,quote.text));sources.append(button);});
    article.append(sources);
  }
  if(Array.isArray(result.trace)&&result.trace.length) {
    const details=make('details','trace');details.append(make('summary','',t('trace')));
    for(const step of result.trace)details.append(make('p','',(step.index || t('library'))+' → '+((step.chosen || []).join(', ') || t('noBranch'))));
    article.append(details);
  }
}
function scrollToLatest() { requestAnimationFrame(()=>{ $('conversation').scrollTop=$('conversation').scrollHeight; }); }
$('ask-form').addEventListener('submit',async event=>{
  event.preventDefault(); const question=$('question').value.trim(); if(!question || !state.ready || state.asking || state.uploading)return;
  state.asking=true; updateSend(); $('conversation').setAttribute('aria-busy','true');
  message('user',question); $('question').value=''; $('question').style.height='auto';
  const pending=message('assistant',t('thinking'),'thinking');
  try {
    const result=await api('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question})});
    pending.classList.remove('thinking');
    renderAnswer(pending,result);
    completedAnswers.push([pending,result]);
  } catch(error) { pending.classList.remove('thinking');pending.classList.add('error');pending.querySelector('.message-content').textContent=error.message; }
  finally { state.asking=false;$('conversation').setAttribute('aria-busy','false');await refreshStatus();scrollToLatest();$('question').focus(); }
});
$('question').addEventListener('input',()=>{ $('question').style.height='auto';$('question').style.height=Math.min($('question').scrollHeight,160)+'px';updateSend(); });
$('question').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('ask-form').requestSubmit();}});
const uploadReport=[];
let uploadWarning=false;
function renderUploads() {
  $('upload-status').replaceChildren();
  if(uploadWarning)$('upload-status').append(make('div','',t('waitUpload')));
  for(const entry of uploadReport) {
    const line=make('div');line.style.whiteSpace='pre-wrap';
    if(entry.pending)line.textContent=t('filing',entry);
    else if(entry.error)line.textContent=entry.name+': '+entry.error;
    else {
      const outcomes=[];
      for(const item of entry.result.filed || []){const folder=(item.path || '').split('/').slice(0,-1).join('/');outcomes.push(t('filed',{name:item.name,folder:folder || t('yourLibrary')}));}
      for(const item of entry.result.errors || [])outcomes.push(item.name+': '+item.error);
      line.textContent=outcomes.join('\n') || t('noFiling',{name:entry.name});
    }
    $('upload-status').append(line);
  }
}
async function upload(files) {
  if(!files.length)return;
  if(state.uploading || state.asking){uploadWarning=true;renderUploads();return;}
  state.uploading=true;updateSend();uploadReport.length=0;uploadWarning=false;
  try {
    for(let index=0;index<files.length;index++) {
      const file=files[index],entry={pending:true,current:index+1,total:files.length,name:file.name};uploadReport.push(entry);renderUploads();
      const form=new FormData();form.append('files',file);
      try{entry.result=await api('/api/upload',{method:'POST',body:form});}catch(error){entry.error=error.message;}
      entry.pending=false;renderUploads();
    }
  }finally{state.uploading=false;$('files').value='';await Promise.all([refreshTree(),refreshStatus()]);}
}
$('files').addEventListener('change',()=>upload(Array.from($('files').files)));
let dragDepth=0;
const drop=$('drop-zone');
for(const name of ['dragenter','dragover','dragleave','drop'])drop.addEventListener(name,event=>event.preventDefault());
drop.addEventListener('dragenter',()=>{dragDepth++;drop.classList.add('dragging');});
drop.addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;drop.classList.remove('dragging');}});
drop.addEventListener('drop',event=>{dragDepth=0;drop.classList.remove('dragging');upload(Array.from(event.dataTransfer.files));});
window.addEventListener('dragover',event=>{if(Array.from(event.dataTransfer.types).includes('Files'))event.preventDefault();});
window.addEventListener('drop',event=>{if(Array.from(event.dataTransfer.types).includes('Files'))event.preventDefault();});
// Render text nodes only: imported documents never become executable HTML.
function inline(parent,text) {
  const pattern=/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[([^\]]+)\]\(([^)]+)\))/g;let start=0;let match;
  while((match=pattern.exec(text))) {
    parent.append(document.createTextNode(text.slice(start,match.index)));const value=match[0];
    if(value[0]==='`')parent.append(make('code','',value.slice(1,-1)));
    else if(value.startsWith('**'))parent.append(make('strong','',value.slice(2,-2)));
    else if(value[0]==='*')parent.append(make('em','',value.slice(1,-1)));
    else {const label=make('span','',match[2]);label.title=match[3];parent.append(label);}
    start=pattern.lastIndex;
  }
  parent.append(document.createTextNode(text.slice(start)));
}
function renderMarkdown(markdown) {
  const fragment=document.createDocumentFragment();let paragraph=[];let list=null;let code=null;
  const flush=()=>{if(paragraph.length){const p=make('p');inline(p,paragraph.join(' '));fragment.append(p);paragraph=[];}};
  for(const line of String(markdown || '').replace(/\r/g,'').split('\n')) {
    if(/^\s*```/.test(line)){flush();list=null;if(code){fragment.append(code);code=null;}else code=make('pre');continue;}
    if(code){code.append(document.createTextNode(line+'\n'));continue;}
    if(!line.trim()){flush();list=null;continue;}
    const heading=line.match(/^(#{1,6})\s+(.+)$/);const bullet=line.match(/^\s*(?:[-+*]|\d+\.)\s+(.+)$/);
    if(heading){flush();list=null;const h=make('h'+heading[1].length);inline(h,heading[2]);fragment.append(h);}
    else if(bullet){flush();const tag=/^\s*\d/.test(line)?'ol':'ul';if(!list||list.tagName.toLowerCase()!==tag){list=make(tag);fragment.append(list);}const li=make('li');inline(li,bullet[1]);list.append(li);}
    else if(/^>\s?/.test(line)){flush();list=null;const quote=make('blockquote');inline(quote,line.replace(/^>\s?/,''));fragment.append(quote);}
    else{list=null;paragraph.push(line);}
  }
  flush();if(code)fragment.append(code);return fragment;
}
function highlightPassage(root,quote) {
  const normalize=text=>text.replace(/\s+/g,' ').trim().toLocaleLowerCase();const needle=normalize(quote);if(!needle)return false;
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);const nodes=[];let combined='';
  while(walker.nextNode()){const node=walker.currentNode;nodes.push({node,start:combined.length});combined+=node.textContent+' ';}
  // Keep a source offset for every normalized character to match across formatting nodes.
  let normalized='';const offsets=[];let wasSpace=true;
  for(let i=0;i<combined.length;i++){if(/\s/.test(combined[i])){if(!wasSpace){normalized+=' ';offsets.push(i);}wasSpace=true;}else{const lower=combined[i].toLocaleLowerCase();normalized+=lower;for(let j=0;j<lower.length;j++)offsets.push(i);wasSpace=false;}}
  const at=normalized.indexOf(needle);if(at<0)return false;
  const start=offsets[at],end=offsets[at+needle.length-1]+1;let first=null;
  for(const entry of nodes){const a=Math.max(0,start-entry.start),b=Math.min(entry.node.length,end-entry.start);if(a>=b)continue;const range=document.createRange();range.setStart(entry.node,a);range.setEnd(entry.node,b);const mark=make('mark');range.surroundContents(mark);if(!first)first=mark;}
  if(first)requestAnimationFrame(()=>first.scrollIntoView({block:'center'}));return Boolean(first);
}
async function openDoc(path,quote='') {
  const request=++state.docRequest;
  $('document-title').textContent=t('openingDocument');$('document-path').textContent=path || '';$('document-body').replaceChildren();$('passage-note').hidden=true;
  if(!$('document-dialog').open)$('document-dialog').showModal();
  try {
    const doc=await api('/api/doc?path='+encodeURIComponent(path));if(request!==state.docRequest)return;
    $('document-title').textContent=doc.title || t('document');$('document-path').textContent=doc.path || path;$('document-body').replaceChildren(renderMarkdown(doc.markdown));
    if(quote&&!highlightPassage($('document-body'),quote)){$('passage-note').textContent=t('missingPassage');$('passage-note').hidden=false;}
  } catch(error){if(request===state.docRequest){$('document-title').textContent=t('documentError');$('document-body').textContent=error.message;}}
}
$('close-document').addEventListener('click',()=>{$('document-dialog').close();state.docRequest++;});
$('document-dialog').addEventListener('cancel',()=>{state.docRequest++;});
function applyLanguage() {
  const I=window.MhizvaI18n;
  document.documentElement.lang=I.language==='zh'?'zh-CN':'en';document.title=t('pageTitle');$('language').value=I.language;
  document.querySelectorAll('[data-i18n]').forEach(node=>node.textContent=t(node.dataset.i18n));
  document.querySelectorAll('[data-i18n-aria]').forEach(node=>node.setAttribute('aria-label',t(node.dataset.i18nAria)));
  document.querySelectorAll('[data-i18n-placeholder]').forEach(node=>node.setAttribute('placeholder',t(node.dataset.i18nPlaceholder)));
  document.querySelectorAll('.message.user .message-label').forEach(node=>node.textContent=t('you'));
  document.querySelectorAll('.message.thinking .message-content').forEach(node=>node.textContent=t('thinking'));
  updateWelcome();renderUploads();
  for(const [article,result] of completedAnswers)renderAnswer(article,result);
}
$('language').addEventListener('change',()=>window.MhizvaI18n.setLanguage($('language').value));
window.addEventListener('mhizva-language',()=>{applyLanguage();refreshStatus();refreshTree();});
applyLanguage();
Promise.all([refreshStatus(),refreshTree()]);
setInterval(refreshStatus,4000);
