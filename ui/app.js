'use strict';
const $ = id => document.getElementById(id);
const state = {ready:false, asking:false, uploading:false, docRequest:0, docCount:0};
const make = (tag, className, text) => { const el=document.createElement(tag); if(className) el.className=className; if(text!==undefined) el.textContent=text; return el; };
async function api(url, options={}) {
  const response=await fetch(url, options);
  let data; try { data=await response.json(); } catch { throw new Error('The app returned an unreadable response. Please try again.'); }
  if(!response.ok) throw new Error(typeof data.error==='string' ? data.error : 'That didn’t go through. Please try again.');
  return data;
}
function updateSend() { $('send').disabled=!state.ready || state.asking || state.uploading || !$('question').value.trim(); }
function updateWelcome() {
  const hasDocs=state.docCount>0;
  $('welcome-description').replaceChildren(
    document.createTextNode(hasDocs ? 'Ask in your own words.' : 'Add your documents, then ask in your own words.'),
    make('br'),document.createTextNode('We’ll help you find the answer and where it came from.'));
  $('welcome-hint').textContent=hasDocs ? 'Your library is ready. Ask a question to get started.' : 'Start with a few documents in your library.';
}
async function refreshStatus() {
  try {
    const data=await api('/api/status');
    state.ready=data.model==='ready' && !data.busy;
    const label=data.model==='ready' ? (data.busy?'Working…':'Model ready') : data.model==='error'?'Model unavailable':'Model loading…';
    $('model-status').className='model-status '+(data.model==='ready'?'ready':data.model==='error'?'error':'');
    $('model-status').replaceChildren(make('i'), document.createTextNode(label));
    $('doc-count').textContent=Number.isFinite(data.docs)?String(data.docs):'—';
    if(Number.isFinite(data.docs)){state.docCount=data.docs;updateWelcome();}
    $('compose-note').textContent=data.model==='error'?'The local model needs attention. Your documents are still here.':data.model==='loading'?'Getting the local model ready. You can browse your documents while you wait.':'Answers grounded in your library. Always check the source.';
  } catch {
    state.ready=false; $('model-status').className='model-status error';
    $('model-status').replaceChildren(make('i'),document.createTextNode('Reconnecting…'));
    $('compose-note').textContent='Connecting to your local app. We’ll try again shortly.';
  }
  updateSend();
}
function treeNode(node) {
  if(node.kind==='folder') {
    const details=make('details'); details.open=true;
    const summary=make('summary'); summary.append(make('span','','▱'),make('span','',node.name || 'Documents')); summary.title=(node.summary || '').replace(/\s+/g,' ');
    const children=make('div','children'); for(const child of node.children || []) children.append(treeNode(child));
    details.append(summary,children); return details;
  }
  const button=make('button'); button.type='button'; button.title=(node.summary || node.path || '').replace(/\s+/g,' ');
  button.append(make('span','file-icon','▤'),make('span','',(node.name || node.path || 'Untitled').replace(/\.md$/i,'')));
  button.addEventListener('click',()=>openDoc(node.path)); return button;
}
async function refreshTree() {
  try {
    const root=await api('/api/tree'); const nodes=root.kind==='folder' ? root.children || [] : [root];
    $('tree').replaceChildren(...(nodes.length?nodes.map(treeNode):[make('p','muted','A home for your documents. Add a few to get started.')]));
  } catch { const retry=make('button','','Library couldn’t load. Try again');retry.addEventListener('click',refreshTree);$('tree').replaceChildren(retry); }
}
function message(role,text,extraClass='') {
  $('welcome').hidden=true;
  const article=make('article',`message ${role} ${extraClass}`); article.append(make('div','message-label',role==='user'?'You':'Mhizva'),make('div','message-content',text));
  $('messages').append(article); scrollToLatest(); return article;
}
function scrollToLatest() { requestAnimationFrame(()=>{ $('conversation').scrollTop=$('conversation').scrollHeight; }); }
$('ask-form').addEventListener('submit',async event=>{
  event.preventDefault(); const question=$('question').value.trim(); if(!question || !state.ready || state.asking || state.uploading)return;
  state.asking=true; updateSend(); $('conversation').setAttribute('aria-busy','true');
  message('user',question); $('question').value=''; $('question').style.height='auto';
  const pending=message('assistant','Mhizva is looking through your documents','thinking');
  try {
    const result=await api('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question})});
    pending.classList.remove('thinking');
    pending.querySelector('.message-content').textContent=result.answer || (result.found?'Here’s what I found in your documents.':'I couldn’t find that in your documents. Try adding a relevant document or asking another way.');
    if(Array.isArray(result.quotes)&&result.quotes.length) {
      const sources=make('div','sources');
      result.quotes.forEach((quote,i)=>{const button=make('button','source');button.type='button';button.append(make('strong','',`${i+1}. ${quote.title || quote.path || 'Source'}`),make('span','',quote.text || 'Open document'));button.addEventListener('click',()=>openDoc(quote.path,quote.text));sources.append(button);});
      pending.append(sources);
    }
    if(Array.isArray(result.trace)&&result.trace.length) {
      const details=make('details','trace');details.append(make('summary','','How I found this'));
      for(const step of result.trace)details.append(make('p','',`${step.index || 'Library'} → ${(step.chosen || []).join(', ') || 'No matching branch'}`));
      pending.append(details);
    }
  } catch(error) { pending.classList.remove('thinking');pending.classList.add('error');pending.querySelector('.message-content').textContent=error.message; }
  finally { state.asking=false;$('conversation').setAttribute('aria-busy','false');await refreshStatus();scrollToLatest();$('question').focus(); }
});
$('question').addEventListener('input',()=>{ $('question').style.height='auto';$('question').style.height=Math.min($('question').scrollHeight,160)+'px';updateSend(); });
$('question').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('ask-form').requestSubmit();}});
async function upload(files) {
  if(!files.length)return;
  if(state.uploading || state.asking){$('upload-status').textContent='Please wait for the current task to finish, then add your documents.';return;}
  state.uploading=true;updateSend();
  $('upload-status').replaceChildren();
  try {
    for(let index=0;index<files.length;index++) {
      const file=files[index];const line=make('div','',`Filing ${index+1} of ${files.length}: ${file.name}…`);$('upload-status').append(line);
      const form=new FormData();form.append('files',file);
      try {
        const result=await api('/api/upload',{method:'POST',body:form});
        const outcomes=[];
        for(const item of result.filed || []){const folder=(item.path || '').split('/').slice(0,-1).join('/');outcomes.push(`✓ ${item.name} · Filed into ${folder || 'your library'}`);}
        for(const item of result.errors || [])outcomes.push(`${item.name}: ${item.error}`);
        line.textContent=outcomes.join('\n') || `${file.name}: No filing result was returned. Please check your library.`;
        line.style.whiteSpace='pre-wrap';
      } catch(error){line.textContent=`${file.name}: ${error.message}`;}
    }
  } finally {state.uploading=false;$('files').value='';await Promise.all([refreshTree(),refreshStatus()]);}
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
  $('document-title').textContent='Opening document…';$('document-path').textContent=path || '';$('document-body').replaceChildren();$('passage-note').hidden=true;
  if(!$('document-dialog').open)$('document-dialog').showModal();
  try {
    const doc=await api('/api/doc?path='+encodeURIComponent(path));if(request!==state.docRequest)return;
    $('document-title').textContent=doc.title || 'Document';$('document-path').textContent=doc.path || path;$('document-body').replaceChildren(renderMarkdown(doc.markdown));
    if(quote&&!highlightPassage($('document-body'),quote)){$('passage-note').textContent='The exact passage couldn’t be highlighted. You can read the full source below.';$('passage-note').hidden=false;}
  } catch(error){if(request===state.docRequest){$('document-title').textContent='Couldn’t open this document';$('document-body').textContent=error.message;}}
}
$('close-document').addEventListener('click',()=>{$('document-dialog').close();state.docRequest++;});
$('document-dialog').addEventListener('cancel',()=>{state.docRequest++;});
Promise.all([refreshStatus(),refreshTree()]);
setInterval(refreshStatus,4000);
