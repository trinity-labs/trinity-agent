const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createRequire}=require('node:module');
const {JSDOM}=require('jsdom');

const A='a'.repeat(20),B='b'.repeat(20),C='c'.repeat(20),SOURCE='d'.repeat(20);
const folder=(id,name,items=[])=>({id,name,root:'/projects/'+name,items,documents:[{name:'NOTES.md',path:'/projects/'+name+'/NOTES.md',scope:'root'}],active:id===A});
const conversation=(source=SOURCE)=>({id:'same-id',title:'Conversation',archive_project:source,archive_active:source===A,updated_at:'2026-10-17T00:00:00Z'});

function setup(t) {
  const root=path.resolve(__dirname,'..'),native=createRequire(path.join(root,'extension.js'));
  const vscode={workspace:{workspaceFolders:[{uri:{fsPath:root}}]},Uri:{joinPath:(_, ...parts)=>path.join(root,...parts)},env:{language:'fr'}};
  const sandbox={require:n=>n==='vscode'?vscode:native(n),module:{exports:{}},process:{env:{...process.env,TRINITY_AGENT_LANG:'fr'}},console,setTimeout,clearTimeout};
  vm.runInNewContext(fs.readFileSync(path.join(root,'extension.js'),'utf8')+'\nmodule.exports.Provider=TrinityAgentViewProvider;',sandbox);
  const provider=new sandbox.module.exports.Provider(root,{});
  const dom=new JSDOM(provider.getHtml({asWebviewUri:x=>x,cspSource:'local'}),{runScripts:'outside-only',pretendToBeVisual:true,url:'https://test.invalid'});
  t.after(()=>dom.window.close());
  const w=dom.window,doc=w.document,sent=[];
  w.acquireVsCodeApi=()=>({getState:()=>undefined,setState:()=>{},postMessage:m=>sent.push(JSON.parse(JSON.stringify(m)))});
  w.HTMLElement.prototype.scrollIntoView=function(){};
  w.TRINITY_TRANSLATIONS=JSON.parse(fs.readFileSync(path.join(root,'media/translations.json')));
  for(const name of ['i18n.js','purify.min.js','safe-html.js','icons.min.js','chat.js'])w.eval(fs.readFileSync(path.join(root,'media',name),'utf8'));
  const emit=data=>w.dispatchEvent(new w.MessageEvent('message',{data}));
  const archives=(projects,items=[])=>emit({type:'archives',projects,items});
  const group=id=>[...doc.querySelectorAll('.archive-project')].find(e=>e.dataset.projectId===id);
  const row=(source=SOURCE)=>[...doc.querySelectorAll('.archive-item')].find(e=>e.dataset.archiveProject===source);
  function transfer(){const data=new Map();return {effectAllowed:'none',dropEffect:'none',get types(){return [...data.keys()];},setData:(key,value)=>data.set(key,value),getData:key=>data.get(key)||''};}
  function event(element,type,data,clientY=110,relatedTarget=null) {
    const event=new w.Event(type,{bubbles:true,cancelable:true});
    Object.defineProperties(event,{dataTransfer:{value:data},clientY:{value:clientY},relatedTarget:{value:relatedTarget}});
    element.dispatchEvent(event);return event;
  }
  function target(id){const summary=group(id).querySelector('summary');summary.getBoundingClientRect=()=>({top:100,height:40,bottom:140});return summary;}
  return {w,doc,sent,emit,archives,group,row,transfer,event,target};
}

test('dragging a folder saves before/after placement, preserves expansion and restores focus',t=>{
  const ui=setup(t),{doc,archives,group,target,event,transfer,sent,emit}=ui;
  archives([folder(A,'Alpha'),folder(B,'Beta'),folder(C,'Gamma')]);group(A).open=true;sent.length=0;
  const data=transfer();event(group(C).querySelector('.project-drag-handle'),'dragstart',data);
  assert.equal(data.effectAllowed,'move');
  const over=event(target(A),'dragover',data,105);
  assert(over.defaultPrevented);assert(group(A).classList.contains('project-drop-before'));
  event(target(A),'drop',data,105);
  assert.deepEqual(sent,[{type:'moveProject',source:C,target:A,position:'before'}]);
  assert.equal(doc.getElementById('archive-list').getAttribute('aria-busy'),'true');
  emit({type:'archives_organized'});archives([folder(C,'Gamma'),folder(A,'Alpha'),folder(B,'Beta')]);
  assert.deepEqual([...doc.querySelectorAll('.archive-project')].map(g=>g.dataset.projectId),[C,A,B]);
  assert.equal(group(A).open,true);assert.equal(doc.activeElement,group(C).querySelector('.project-drag-handle'));
  assert.equal(doc.getElementById('archive-list').hasAttribute('aria-busy'),false);
  const second=transfer();event(group(C).querySelector('.project-drag-handle'),'dragstart',second);
  event(target(B),'dragover',second,139);assert(group(B).classList.contains('project-drop-after'));
  event(target(B),'drop',second,139);
  assert.deepEqual(sent.at(-1),{type:'moveProject',source:C,target:B,position:'after'});
});

test('dropping a conversation into a closed folder uses its actual archive source and opens the destination',t=>{
  const {doc,archives,group,row,target,event,transfer,sent,emit}=setup(t);
  const item=conversation(),duplicate=conversation(A);
  archives([folder(A,'Alpha',[duplicate]),folder(B,'Beta')],[item]);sent.length=0;
  const data=transfer();event(row().querySelector('.conversation-drag-handle'),'dragstart',data);
  event(target(B),'dragover',data);assert(group(B).classList.contains('archive-drop-target'));
  event(target(B),'drop',data);
  assert.deepEqual(sent,[{type:'moveConversation',source:SOURCE,conversation:'same-id',target:B}]);
  emit({type:'archives_organized'});archives([folder(A,'Alpha',[duplicate]),folder(B,'Beta',[item])]);
  assert.equal(group(B).open,true);assert.equal(row().closest('.archive-project').dataset.projectId,B);
  assert.equal(doc.activeElement,row().querySelector('.archive-open'));
  assert.equal(group(B).querySelector('.project-folder-count').textContent,'1');
  assert(group(B).querySelector('.project-folder-count .icon-chat'));
  assert.equal(group(B).querySelector('.project-folder-count').getAttribute('aria-label'),'1 conversation');
  assert.equal(group(B).querySelectorAll('.project-document').length,1);
  assert.equal(row(A).closest('.archive-project').dataset.projectId,A);
});

test('conversations can move between folders and back outside projects, including an initially empty outside section',t=>{
  const {doc,archives,group,row,target,event,transfer,sent,emit}=setup(t);
  const item=conversation();archives([folder(A,'Alpha',[item]),folder(B,'Beta')]);sent.length=0;
  let data=transfer();event(row().querySelector('.conversation-drag-handle'),'dragstart',data);event(target(B),'drop',data);
  assert.equal(sent.at(-1).target,B);
  emit({type:'archives_organized'});archives([folder(A,'Alpha'),folder(B,'Beta',[item])]);
  data=transfer();event(row().querySelector('.conversation-drag-handle'),'dragstart',data);
  assert(doc.getElementById('archive-list').classList.contains('dragging-conversation'));
  const outside=doc.querySelector('.archive-ungrouped');assert(outside.classList.contains('archive-ungrouped-empty'));
  event(outside,'dragover',data);assert(outside.classList.contains('archive-drop-target'));
  event(outside,'drop',data);
  assert.deepEqual(sent.at(-1),{type:'moveConversation',source:SOURCE,conversation:'same-id',target:null});
  emit({type:'archives_organized'});archives([folder(A,'Alpha'),folder(B,'Beta')],[item]);
  assert(row().closest('.archive-ungrouped'));assert.equal(group(B).querySelector('.project-folder-count').textContent,'0');
  assert(group(B).querySelector('.project-folder-count .icon-chat'));
  assert.equal(group(B).querySelector('.project-folder-count').getAttribute('aria-label'),'0 conversations');
});

test('external drops, same-folder drops, action controls and canceled drags cannot change classification',t=>{
  const {w,doc,archives,group,row,target,event,transfer,sent}=setup(t);
  archives([folder(A,'Alpha',[conversation(A)]),folder(B,'Beta')]);sent.length=0;
  const external=transfer();external.setData('application/x-trinity-conversation-organizer','{"kind":"conversation"}');event(target(B),'drop',external);
  assert.equal(sent.length,0);
  let data=transfer();assert(event(row(A).querySelector('.archive-delete'),'dragstart',data).defaultPrevented);
  assert(event(group(A).querySelector('.project-caret'),'dragstart',data).defaultPrevented);
  assert.equal(data.types.length,0);
  data=transfer();event(row(A).querySelector('.conversation-drag-handle'),'dragstart',data);event(target(A),'dragover',data);assert.equal(data.dropEffect,'none');event(target(A),'drop',data);
  assert.equal(sent.length,0);
  data=transfer();event(group(A).querySelector('.project-drag-handle'),'dragstart',data);
  w.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape'}));event(target(B),'drop',data);
  assert.equal(sent.length,0);assert.equal(doc.querySelector('.archive-drag-source'),null);
});

test('history updates wait until drag completion and keep the newest refresh',t=>{
  const {w,archives,group,row,event,transfer,sent}=setup(t);
  archives([folder(A,'Old')],[conversation()]);sent.length=0;
  const source=row(),data=transfer();event(source.querySelector('.conversation-drag-handle'),'dragstart',data);
  archives([folder(A,'Intermediate')],[conversation()]);archives([folder(A,'Latest'),folder(B,'New')],[conversation()]);
  assert(source.isConnected);assert.equal(group(A).querySelector('.project-folder-name').textContent,'Old');
  event(w,'dragend',data);
  assert.equal(group(A).querySelector('.project-folder-name').textContent,'Latest');assert(group(B));assert.equal(sent.length,0);
});

test('keyboard folder ordering shares persistence and failures restore normal interaction',t=>{
  const {w,doc,archives,group,event,transfer,sent,emit}=setup(t);
  archives([folder(A,'Alpha'),folder(B,'Beta')]);sent.length=0;
  const handle=group(A).querySelector('.project-drag-handle');handle.focus();
  handle.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowDown',altKey:true,bubbles:true,cancelable:true}));
  assert.deepEqual(sent.at(-1),{type:'moveProject',source:A,target:B,position:'after'});
  const data=transfer();assert(event(group(B).querySelector('.project-drag-handle'),'dragstart',data).defaultPrevented);
  emit({type:'archive_organization_failed',text:'Sauvegarde indisponible <img>'});archives([folder(A,'Alpha'),folder(B,'Beta')]);
  assert.equal(doc.activeElement,group(A).querySelector('.project-drag-handle'));
  assert.match(doc.getElementById('archive-notice').textContent,/Sauvegarde indisponible/);
  assert.equal(doc.getElementById('archive-notice').querySelector('img'),null);
  assert.equal(doc.getElementById('archive-list').hasAttribute('aria-busy'),false);
  assert.deepEqual([...doc.querySelectorAll('.archive-project')].map(g=>g.dataset.projectId),[A,B]);
  assert.equal(event(group(B).querySelector('.project-drag-handle'),'dragstart',transfer()).defaultPrevented,false);
});

test('drag autoscroll stops on drag end',async t=>{
  const {w,doc,archives,row,target,event,transfer}=setup(t);
  archives([folder(A,'Alpha'),folder(B,'Beta')],[conversation()]);
  const panel=doc.getElementById('archive-panel'),list=doc.getElementById('archive-list');list.getBoundingClientRect=()=>({top:0,bottom:300});
  const data=transfer();event(row().querySelector('.conversation-drag-handle'),'dragstart',data);event(target(B),'dragover',data,290);
  await new Promise(resolve=>setTimeout(resolve,60));assert(list.scrollTop>0);assert.equal(panel.scrollTop,0);
  event(w,'dragend',data);const stopped=list.scrollTop;
  await new Promise(resolve=>setTimeout(resolve,40));assert.equal(list.scrollTop,stopped);
});


test('only grips start dragging; conversation titles still open their own archive',t=>{
  const {archives,group,row,event,transfer,sent}=setup(t);
  archives([folder(A,'Alpha',[conversation(A)]),folder(B,'Beta')],[conversation()]);sent.length=0;
  for(const element of [group(A).querySelector('summary'),group(A).querySelector('.project-folder-name'),row(),row().querySelector('.archive-open'),row().querySelector('.archive-open strong')]) {
    const data=transfer();assert(event(element,'dragstart',data).defaultPrevented);
    assert.equal(data.types.length,0);assert.equal(element.draggable,false);
  }
  for(const item of [row(),row(A)]) {
    const handle=item.querySelector('.conversation-drag-handle');
    assert.equal(handle.draggable,true);assert.match(handle.getAttribute('aria-label'),/Conversation/);
    handle.click();assert.equal(sent.length,0);
  }
  row().querySelector('.archive-open').click();
  assert.deepEqual(sent.at(-1),{type:'openProject',project:SOURCE,conversation:'same-id'});
});

test('archive heading centers its icon beside the title and mount subtitle',t=>{
  const {doc}=setup(t);
  const heading=doc.querySelector('.archive-heading');
  assert(heading.querySelector(':scope > .archive-heading-icon > .archive-title-icon'));
  assert(heading.querySelector(':scope > .archive-heading-text > .archive-title'));
  assert(heading.querySelector(':scope > .archive-heading-text > .archive-mount #archive-path'));
  assert(doc.querySelector('#archive-banner > #close-history'));
});

test('mount usage follows its path, survives late ready and hides unavailable values',t=>{
  const {doc,emit}=setup(t);
  const used=doc.getElementById('archive-used'),separator=doc.getElementById('archive-mount-separator');
  const update=(root,bytes,stored=512_000_000)=>emit({type:'archives',items:[],projects:[],archive_root:root,archive_stored_bytes:stored,archive_used_bytes:bytes});
  assert(used.hidden && separator.hidden);
  update('/archives/first',12_400_000_000);
  assert.match(used.textContent,/512\s+MB\s+\/\s+12,4\s+GB/);
  assert.equal(used.hidden,false);assert.equal(separator.textContent,'|');assert.equal(separator.hidden,false);
  assert.equal(doc.getElementById('archive-path').nextElementSibling,separator);
  emit({type:'ready',archive_root:'/archives/first',model:'Qwen3.8-27B'});
  assert.equal(used.hidden,false,'Late ready for the same volume preserves measured usage');
  emit({type:'ready',archive_root:'/archives/second',model:'Qwen3.8-27B'});
  assert(used.hidden && separator.hidden,'A new path must never keep the old volume usage');
  update('/archives/second',0,0);assert.match(used.textContent,/^0\s+B\s+\/\s+0\s+B$/);assert.equal(used.hidden,false);
  for(const invalid of [null,-1,NaN,Infinity,'42']) {
    update('/archives/second',invalid,invalid);assert(used.hidden && separator.hidden);assert.equal(used.textContent,'');
  }
});

test('delete controls retain source identity across confirmation and updates',t=>{
 const {doc,archives,row,sent,emit}=setup(t);
 archives([folder(A,'Alpha',[conversation(A)])],[conversation()]);sent.length=0;
 const dialog=doc.getElementById('delete-dialog');dialog.showModal=()=>{};dialog.close=()=>{};
 assert(row().querySelector('.archive-delete'));assert(row(A).querySelector('.archive-delete'));
 assert.equal(doc.querySelector('.archive-view'),null);
 row().querySelector('.archive-delete').click();doc.getElementById('delete-confirm').click();
 assert.deepEqual(sent.at(-1),{type:'deleteArchiveConfirmed',id:'same-id',source:SOURCE});
 emit({type:'archive_delete_pending',id:'same-id',source:SOURCE});
 assert(row().classList.contains('deleting'));assert(!row(A).classList.contains('deleting'));
 emit({type:'archive_deleted',id:'same-id',source:SOURCE,active:false,was_current:false});
 assert.equal(row(),undefined);assert(row(A));
});

test('folder order refreshes while an editor is open without discarding its draft',t=>{
 const {doc,archives,group,target,event,transfer,sent,emit}=setup(t);
 archives([folder(A,'Alpha'),folder(B,'Beta'),folder(C,'Gamma')]);
 group(A).querySelector('.project-edit').click();
 const editor=group(A).querySelector('.project-editor'),input=editor.querySelector('input[type=text]');input.value='Unsaved name';
 const data=transfer();event(group(C).querySelector('.project-drag-handle'),'dragstart',data);event(target(A),'drop',data,105);
 assert.deepEqual(sent.at(-1),{type:'moveProject',source:C,target:A,position:'before'});
 emit({type:'archives_organized'});archives([folder(C,'Gamma'),folder(A,'Alpha'),folder(B,'Beta')]);
 assert.deepEqual([...doc.querySelectorAll('.archive-project')].map(g=>g.dataset.projectId),[C,A,B]);
 assert.equal(group(A).querySelector('.project-editor'),editor);assert.equal(editor.hidden,false);assert.equal(input.value,'Unsaved name');
 assert.equal(doc.activeElement,group(C).querySelector('.project-drag-handle'));
 editor.querySelector('button[type=button]').click();
 assert.deepEqual([...doc.querySelectorAll('.archive-project')].map(g=>g.dataset.projectId),[C,A,B]);
});

test('webview blur during native dragging does not cancel a valid folder drop',t=>{
 const {w,archives,group,target,event,transfer,sent}=setup(t);
 archives([folder(A,'Alpha'),folder(B,'Beta')]);sent.length=0;
 const data=transfer();event(group(B).querySelector('.project-drag-handle'),'dragstart',data);
 w.dispatchEvent(new w.Event('blur'));
 event(target(A),'drop',data,105);
 assert.deepEqual(sent.at(-1),{type:'moveProject',source:B,target:A,position:'before'});
});

test('folder handles use the current identity after a project path changes',t=>{
 const {archives,group,target,event,transfer,sent,emit,w}=setup(t);
 archives([folder(A,'Alpha'),folder(B,'Beta')]);
 group(A).querySelector('.project-edit').click();
 const editor=group(A).querySelector('.project-editor');editor.querySelector('.project-path-input').value='/new';
 editor.dispatchEvent(new w.Event('submit',{cancelable:true}));const request=sent.at(-1);
 emit({type:'project_updated',previousId:A,requestId:request.requestId,project:{...folder(C,'Alpha'),root:'/new'}});
 const data=transfer();event(group(C).querySelector('.project-drag-handle'),'dragstart',data);event(target(B),'drop',data,139);
 assert.deepEqual(sent.at(-1),{type:'moveProject',source:C,target:B,position:'after'});
});
