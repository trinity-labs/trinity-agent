const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {createRequire}=require('node:module');
const {JSDOM}=require('jsdom');
const {validMessage}=require('../security');
const root=path.resolve(__dirname,'..');
function provider() {
  const native=createRequire(path.join(root,'extension.js'));
  const vscode={workspace:{workspaceFolders:[{uri:{fsPath:root}}]},Uri:{joinPath:(_, ...parts)=>path.join(root,...parts)},env:{language:'fr'}};
  const sandbox={require:n=>n==='vscode'?vscode:native(n),module:{exports:{}},process:{env:{...process.env,TRINITY_AGENT_LANG:'fr'}},console,setTimeout,clearTimeout};
  vm.runInNewContext(fs.readFileSync(path.join(root,'extension.js'),'utf8')+'\nmodule.exports.Provider=TrinityAgentViewProvider;',sandbox);
  return new sandbox.module.exports.Provider(root,{});
}
const task=(id,mode='queue')=>({id,mode,text:'Task '+id});
function setup(t) {
  const dom=new JSDOM(provider().getHtml({asWebviewUri:x=>x,cspSource:'local'}),{runScripts:'outside-only',pretendToBeVisual:true,url:'https://test.invalid'});
  t.after(()=>dom.window.close());
  const w=dom.window,doc=w.document,sent=[];
  w.acquireVsCodeApi=()=>({getState:()=>undefined,setState:()=>{},postMessage:m=>sent.push(JSON.parse(JSON.stringify(m)))});
  w.HTMLElement.prototype.scrollIntoView=function(){};
  w.TRINITY_TRANSLATIONS=JSON.parse(fs.readFileSync(path.join(root,'media/translations.json')));
  for(const name of ['i18n.js','purify.min.js','safe-html.js','icons.min.js','chat.js'])w.eval(fs.readFileSync(path.join(root,'media',name),'utf8'));
  const emit=data=>w.dispatchEvent(new w.MessageEvent('message',{data}));
  const items=values=>emit({type:'followups',conversation:'test',items:values});
  const box=doc.getElementById('followups');
  const row=id=>[...box.querySelectorAll('.followup-row')].find(e=>e.dataset.followupId===id);
  const handle=id=>row(id).querySelector('.followup-drag-handle');
  const target=id=>{const element=row(id);element.getBoundingClientRect=()=>({top:50,bottom:90,height:40});return element;};
  function transfer(){const data=new Map();return {effectAllowed:'none',dropEffect:'none',get types(){return [...data.keys()];},setData:(key,value)=>data.set(key,value),getData:key=>data.get(key)||''};}
  function event(element,type,data,clientY=55,relatedTarget=null){
    const event=new w.Event(type,{bubbles:true,cancelable:true});
    Object.defineProperties(event,{dataTransfer:{value:data},clientY:{value:clientY},relatedTarget:{value:relatedTarget}});
    element.dispatchEvent(event);return event;
  }
  const key=(id,key)=>handle(id).dispatchEvent(new w.KeyboardEvent('keydown',{key,altKey:true,bubbles:true,cancelable:true}));
  emit({type:'status',conversation:'test',text:'Working'});
  sent.length=0;
  return {w,doc,sent,emit,items,box,row,handle,target,transfer,event,key};
}

test('reorder bridge validates anchors and preserves paused/running state',async()=>{
  const move={type:'followup',conversation:'test',action:'reorder',id:'a',target:'b',position:'after'};
  assert(validMessage(move));
  for(const fields of [{id:'../a'},{target:'a'},{target:'../b'},{target:undefined},{position:'middle'},{position:undefined},{conversation:'../test'}])assert(!validMessage({...move,...fields}));
  const p=provider(),written=[];
  p.openWindow=async()=>{p.windowLock={};};
  p.ensureAgent=async()=>true;
  p.agent={stdin:{write:value=>written.push(JSON.parse(value))}};
  const view={webview:{onDidReceiveMessage:fn=>{view.receive=fn;}},onDidDispose:()=>{}};
  await p.resolveWebviewView(view);
  for(const busy of [false,true]){
    p.busy=busy;
    await view.receive(move);
    assert.deepEqual(written.at(-1),{...move,full_access:false});
    assert.equal(p.busy,busy);
    for(const action of ['list','edit','remove']){
      await p.sendControl({type:'followup',action});assert.equal(p.busy,busy);
    }
  }
  p.busy=false;
  await p.sendControl({type:'followup',action:'continue'});assert.equal(p.busy,true);
});

test('queued and steering tasks move up/down with insertion indicators and restored focus',t=>{
  const {doc,sent,emit,items,box,row,handle,target,transfer,event}=setup(t);
  items([task('a'),task('b','steer'),task('c')]);
  let data=transfer();event(handle('c'),'dragstart',data);
  assert.equal(data.effectAllowed,'move');assert(row('c').classList.contains('followup-drag-source'));
  assert(event(target('a'),'dragover',data,51).defaultPrevented);
  assert(row('a').classList.contains('followup-drop-before'));
  event(target('a'),'drop',data,51);
  assert.deepEqual(sent.at(-1),{type:'followup',conversation:'test',action:'reorder',id:'c',target:'a',position:'before'});
  assert.equal(box.getAttribute('aria-busy'),'true');
  items([task('c'),task('a'),task('b','steer')]);
  emit({type:'followup_ack',conversation:'test',action:'reorder',id:'c'});
  assert.equal(doc.activeElement,handle('c'));assert(!box.hasAttribute('aria-busy'));
  assert.deepEqual([...box.querySelectorAll('.followup-row')].map(e=>e.dataset.followupId),['c','a','b']);
  data=transfer();event(row('c'),'dragstart',data);event(target('b'),'dragover',data,89);
  assert(row('b').classList.contains('followup-drop-after'));
  event(target('b'),'drop',data,89);assert.equal(sent.at(-1).position,'after');
  items([task('a'),task('b','steer'),task('c')]);emit({type:'followup_ack',action:'reorder',id:'c'});
  data=transfer();event(row('b'),'dragstart',data);event(target('a'),'drop',data,51);
  assert.equal(sent.at(-1).id,'b');assert.equal(sent.at(-1).target,'a');
  assert.equal(doc.querySelector('#followup-mode'),null);assert.equal(doc.getElementById('stop').hidden,false);
});

test('keyboard ordering respects boundaries and does not resume the queue after Stop',t=>{
  const {doc,sent,emit,items,handle,key}=setup(t);
  items([task('a'),task('b')]);key('a','ArrowUp');assert.equal(sent.length,0);
  doc.getElementById('stop').click();assert.equal(sent.at(-1).type,'cancel');
  emit({type:'idle'});sent.length=0;
  handle('a').focus();key('a','ArrowDown');
  assert.deepEqual(sent,[{type:'followup',conversation:'test',action:'reorder',id:'a',target:'b',position:'after'}]);
  items([task('b'),task('a')]);emit({type:'followup_ack',action:'reorder',id:'a'});
  assert.equal(doc.activeElement,handle('a'));
  assert.match(doc.getElementById('followups').textContent,/Exécuter la file d’attente/);
  key('a','ArrowDown');assert.equal(sent.length,1);
});

test('refreshes wait for drag completion and consumed tasks cannot be restored by stale drops',t=>{
  const {w,sent,emit,items,row,target,transfer,event,box}=setup(t);
  items([task('a'),task('b'),task('c')]);const source=row('c');const data=transfer();
  event(source,'dragstart',data);
  items([task('a'),task('b')]);assert(source.isConnected);
  event(target('a'),'drop',data);assert.equal(sent.length,0);assert.equal(row('c'),undefined);
  assert.match(w.document.getElementById('status').textContent,/déjà démarré/);
  const next=transfer();event(row('a'),'dragstart',next);items([task('a')]);event(w,'dragend',next);
  assert.equal(box.querySelectorAll('.followup-row').length,1);
  items([task('a'),task('b')]);event(row('a'),'dragstart',transfer());
  emit({type:'status',conversation:'different',text:'Working'});
  assert.equal(box.querySelector('.followup-drag-source'),null);
});

test('external drags, action controls, unchanged order and cancelled gestures do not move tasks',t=>{
  const {w,sent,items,row,target,transfer,event,box}=setup(t);
  items([task('a'),task('b')]);const external=transfer();external.setData('application/x-trinity-followup','{}');
  event(target('a'),'drop',external);assert.equal(sent.length,0);
  assert(event(row('a').querySelector('[title="Modifier"]'),'dragstart',transfer()).defaultPrevented);
  let data=transfer();event(row('a'),'dragstart',data);event(target('b'),'drop',data,51);assert.equal(sent.length,0);
  data=transfer();event(row('b'),'dragstart',data);w.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape'}));event(target('a'),'drop',data);
  assert.equal(sent.length,0);assert.equal(box.querySelector('.followup-drag-source'),null);
});

test('ordering acknowledgements and failures cannot clear a pending edit or unlock duplicate submission',t=>{
  const {doc,sent,emit,items,row,key,box}=setup(t);
  items([task('a'),task('b')]);row('a').querySelector('[title="Modifier"]').click();
  const prompt=doc.getElementById('prompt'),send=doc.getElementById('send');prompt.value='Edited';send.click();
  key('a','ArrowDown');emit({type:'followup_error',conversation:'test',action:'reorder',id:'a',text:'Message already started or removed'});
  assert.equal(prompt.value,'Edited');assert(send.disabled);assert(!box.hasAttribute('aria-busy'));
  assert.equal(sent.at(-1).action,'list');assert.match(doc.getElementById('status').textContent,/Ordre non enregistré/);
  key('a','ArrowDown');items([task('b'),task('a')]);
  emit({type:'followup_ack',conversation:'other',action:'reorder',id:'a'});assert(box.hasAttribute('aria-busy'));
  emit({type:'followup_ack',conversation:'test',action:'reorder',id:'a'});
  emit({type:'followup_ack',conversation:'test',action:'steer',id:'a'});
  assert.equal(prompt.value,'Edited');assert(send.disabled);
  emit({type:'followup_ack',conversation:'test',action:'edit',id:'a'});
  assert.equal(prompt.value,'');assert(!send.disabled);
});

test('task drag autoscroll stops when leaving the list and ending the drag',async t=>{
  const {w,items,box,row,target,transfer,event}=setup(t);
  items([task('a'),task('b')]);box.getBoundingClientRect=()=>({top:0,bottom:160});
  const data=transfer();event(row('a'),'dragstart',data);event(target('b'),'dragover',data,155);
  await new Promise(resolve=>setTimeout(resolve,45));assert(box.scrollTop>0);
  event(box,'dragleave',data,155,w.document.body);const stopped=box.scrollTop;
  await new Promise(resolve=>setTimeout(resolve,35));assert.equal(box.scrollTop,stopped);
  event(w,'dragend',data);await new Promise(resolve=>setTimeout(resolve,35));assert.equal(box.scrollTop,stopped);
});

test('backend failure and Stop release a pending reorder so it can be retried',t=>{
  const {doc,sent,emit,items,key,box}=setup(t);
  items([task('a'),task('b')]);key('a','ArrowDown');
  assert.equal(box.getAttribute('aria-busy'),'true');
  emit({type:'error',text:'Backend unavailable'});assert(!box.hasAttribute('aria-busy'));
  key('a','ArrowDown');assert.equal(box.getAttribute('aria-busy'),'true');
  doc.getElementById('stop').click();assert(!box.hasAttribute('aria-busy'));assert.equal(sent.at(-1).type,'cancel');
  key('a','ArrowDown');assert.equal(sent.at(-1).action,'reorder');
});
