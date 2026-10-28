const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),vm=require('node:vm');
const {createRequire}=require('node:module');
const {EventEmitter}=require('node:events');
const {JSDOM}=require('jsdom');
const {validMessage}=require('../security');
const root=path.resolve(__dirname,'..');

async function setup(t) {
  const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'trinity-lifecycle-'));
  const native=createRequire(path.join(root,'extension.js')),dataRoot=path.join(temporary,'data');
  const stores=native('./project-store'),archives=native('./archive-store');
  const projectStore={...stores,ProjectStore:class extends stores.ProjectStore {
    constructor(data) {super(data,temporary);}
  }};
  const archiveStore={...archives,ArchiveStore:class extends archives.ArchiveStore {
    constructor(workspace,data=dataRoot) {super(workspace,data);}
  }};
  const values={},state={get:key=>values[key],update:async(key,value)=>{values[key]=value;}};
  const settings={provider:'ovhcloud'};
  const vscode={workspace:{workspaceFolders:[{uri:{fsPath:temporary}}],getConfiguration:()=>({
    get:(key,fallback)=>settings[key]??fallback,
    update:async(key,value)=>{settings[key]=value;if(key==='provider')await provider.providerChanged();}
  })},ConfigurationTarget:{Global:1},
    Uri:{joinPath:(_, ...parts)=>path.join(root,...parts)},env:{language:'fr'}};
  const scope={require:name=>name==='vscode'?vscode:name==='./project-store'?projectStore:name==='./archive-store'?archiveStore:native(name),
    module:{exports:{}},process:{env:{OVH_AGENT_LANG:'fr'}},console,setTimeout,clearTimeout};
  vm.runInNewContext(fs.readFileSync(path.join(root,'extension.js'),'utf8')+'\nmodule.exports.Provider=OvhAgentViewProvider;',scope);
  const provider=new scope.module.exports.Provider(root,{},state);
  const sent=[],commands=[],pending=[];
  provider.showArchives=()=>{};
  provider.refreshBilling=async()=>{};
  provider.openWindow=async view=>{provider.windowLock={};view.webview.html=provider.getHtml(view.webview);};
  provider.startAgent=async()=>{
    const agent=new EventEmitter();
    Object.assign(agent,{provider:settings.provider,exitCode:null,signalCode:null,killed:false,
      stdin:{writable:true,write:line=>commands.push(JSON.parse(line))},
      kill:()=>{agent.killed=true;queueMicrotask(()=>{agent.exitCode=0;agent.emit('close');});}});
    provider.agent=agent;
    return true;
  };
  let receive,window;
  const view={webview:{asWebviewUri:x=>x,cspSource:'local',
    postMessage:data=>window?.dispatchEvent(new window.MessageEvent('message',{data})),
    onDidReceiveMessage:callback=>{receive=callback;}},onDidDispose:()=>{}};
  await provider.resolveWebviewView(view);
  const dom=new JSDOM(view.webview.html,{runScripts:'outside-only',url:'https://test.invalid'});
  window=dom.window;
  window.acquireVsCodeApi=()=>({getState:()=>undefined,setState:()=>{},postMessage:message=>{
    const copy=JSON.parse(JSON.stringify(message));sent.push(copy);pending.push(receive(copy));
  }});
  window.HTMLElement.prototype.scrollIntoView=function(){};
  window.OVH_TRANSLATIONS=JSON.parse(fs.readFileSync(path.join(root,'media/translations.json')));
  for(const name of ['i18n.js','purify.min.js','safe-html.js','icons.min.js','chat.js'])
    window.eval(fs.readFileSync(path.join(root,'media',name),'utf8'));
  const flush=async()=>{
    while(pending.length)await Promise.all(pending.splice(0));
  };
  const emit=data=>provider.consumeOutput(JSON.stringify(data)+'\n');
  const document=window.document,prompt=document.getElementById('prompt'),send=document.getElementById('send');
  const submit=text=>{prompt.value=text;send.click();};
  t.after(()=>{dom.window.close();clearTimeout(provider.archiveRefreshTimer);clearTimeout(provider.deltaTimer);fs.rmSync(temporary,{recursive:true,force:true});});
  return {provider,window,document,prompt,send,sent,commands,flush,emit,submit,temporary,values,
    settings,projects:new projectStore.ProjectStore(dataRoot)};
}

test('opening a conversation restores its provider before loading the model and context',async t=>{
  const h=await setup(t),folder=path.join(h.provider.store.root,'deep');
  fs.mkdirSync(folder,{recursive:true});
  fs.writeFileSync(path.join(folder,'meta.json'),JSON.stringify({model:'deepseek-flash',provider:'deepseek'}));
  await h.provider.startAgent();
  const old=h.provider.agent;
  await h.provider.sendControl({type:'load_archive',id:'deep'});
  assert.equal(h.settings.provider,'deepseek');
  assert(old.killed);
  assert.equal(h.provider.agent.provider,'deepseek');
  assert.equal(h.commands.at(-1).type,'load_archive');
  assert.equal(h.commands.at(-1).id,'deep');
});

test('session rejection releases navigation and replaces the rejected backend',async t=>{
  const h=await setup(t);
  await h.provider.startAgent();
  const rejected=h.provider.agent;
  h.provider.busy=true;h.provider.clearingConversation=true;
  h.emit({type:'session_busy',text:'Session already used'});
  assert.equal(h.provider.busy,false);assert.equal(h.provider.clearingConversation,false);
  await h.provider.sendControl({type:'load_archive',id:'existing'});
  assert(rejected.killed);
  assert.notEqual(h.provider.agent,rejected);
  assert.equal(h.commands.at(-1).type,'load_archive');
});

test('a failed backend startup or control write reports archive failure and allows another attempt',async t=>{
  const h=await setup(t);const start=h.provider.startAgent;
  h.window.dispatchEvent(new h.window.MessageEvent('message',{data:{type:'archives',items:[{id:'existing',title:'Existing'}]}}));
  h.provider.startAgent=async()=>false;
  h.document.querySelector('[data-archive-id="existing"] .archive-open').click();await h.flush();
  assert.equal(h.document.getElementById('messages').hasAttribute('aria-busy'),false);
  h.provider.startAgent=start;
  h.document.querySelector('[data-archive-id="existing"] .archive-open').click();await h.flush();
  assert.equal(h.commands.at(-1).type,'load_archive');
  h.emit({type:'archive_open_failed',text:'Failed load'});
  h.provider.agent.stdin.write=()=>{throw new Error('Closed pipe');};
  h.document.querySelector('[data-archive-id="existing"] .archive-open').click();await h.flush();
  assert.equal(h.document.getElementById('messages').hasAttribute('aria-busy'),false);
  assert.equal(h.provider.busy,false);
});

for(const restoration of ['archive_loaded','snapshot_loaded'])test(`${restoration} during backend startup preserves the submitted request and queue semantics`,async t=>{
  const h=await setup(t);
  h.submit('Nouvelle demande');
  await h.flush();
  assert.equal(h.commands.filter(m=>m.type==='prompt').length,1);
  h.emit({type:'ready',conversation:'conversation',model:'old-model'});
  h.emit({type:'task_interrupted',id:'conversation'});
  h.emit({type:restoration,automatic:true,id:'conversation',model:'old-model',
    messages:[{type:'user',text:'Ancienne demande'},{type:'response',text:'Ancienne réponse'}],
    html:'<article class="message user"><div class="content">Ancienne demande</div></article>',
    has_context:true});
  const users=()=>[...h.document.querySelectorAll('.message.user .content')].map(node=>node.textContent);
  assert.deepEqual(users(),['Ancienne demande','Nouvelle demande']);
  assert.equal(h.document.querySelectorAll('.message.working').length,1);
  assert(!h.document.getElementById('selected-model').textContent.includes('old-model'));
  h.submit('Complément pendant démarrage');
  await h.flush();
  const next=h.commands.at(-1);
  assert.equal(next.type,'followup');assert.equal(next.action,'add');
  assert.equal(h.commands.filter(m=>m.type==='prompt').length,1);
  h.emit({type:'followup_ack',conversation:'conversation',action:'add',id:next.id});
  h.emit({type:'status',conversation:'conversation',text:'Travail'});
  h.emit({type:'response',text:'Réponse nouvelle'});
  assert.deepEqual(users(),['Ancienne demande','Nouvelle demande']);
  assert.equal(h.document.querySelectorAll('.message.working').length,0);
});

test('Stop before follow-up acknowledgement unlocks the draft and retries its original identity',async t=>{
  const h=await setup(t);
  h.emit({type:'ready',conversation:'conversation',model:'auto-balanced'});
  h.emit({type:'status',conversation:'conversation',text:'Travail'});
  h.submit('Complément conservé');await h.flush();
  const original=h.commands.find(m=>m.type==='followup'&&m.action==='add');
  assert(original);assert(h.send.disabled);
  h.document.getElementById('stop').click();
  h.prompt.dispatchEvent(new h.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
  await h.flush();
  assert(!h.send.disabled);assert.equal(h.prompt.value,'Complément conservé');
  assert.equal(h.commands.filter(m=>m.type==='followup'&&m.action==='add').length,1);
  h.send.click();await h.flush();
  const retry=h.commands.at(-1);
  assert.equal(retry.type,'followup');assert.equal(retry.id,original.id);
  h.emit({type:'followup_ack',conversation:'conversation',action:'add',id:retry.id,already_started:true,running:false,resumable:true});
  assert.equal(h.prompt.value,'');assert(!h.send.disabled);
  assert.equal(h.provider.busy,false);
  assert.match(h.document.getElementById('status').textContent,/déjà démarré/);
});

test('a persisted follow-up reconciles its interrupted acknowledgement without sending a copy',async t=>{
  const h=await setup(t);
  h.emit({type:'ready',conversation:'conversation',model:'auto-balanced'});
  h.emit({type:'status',conversation:'conversation',text:'Travail'});
  h.submit('Déjà conservé');await h.flush();
  const original=h.commands.find(m=>m.type==='followup'&&m.action==='add');
  h.document.getElementById('stop').click();await h.flush();
  h.emit({type:'followups',conversation:'conversation',items:[{id:original.id,text:original.text,mode:'queue'}]});
  assert.equal(h.prompt.value,'');assert(!h.send.disabled);
  assert.equal(h.commands.filter(m=>m.type==='followup'&&m.action==='add').length,1);
});

test('new conversation gates both the composer and extension until the clear acknowledgement',async t=>{
  const h=await setup(t);
  h.document.getElementById('clear').click();
  h.prompt.value='Attendre la nouvelle conversation';
  h.prompt.dispatchEvent(new h.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
  await h.flush();
  await h.provider.sendPrompt('Direct stale request',true,'auto-balanced',[]);
  assert(h.send.disabled);
  assert.deepEqual(h.commands.map(m=>m.type),['clear']);
  assert.equal(h.prompt.value,'Attendre la nouvelle conversation');
  h.emit({type:'ready',conversation:'old',model:'auto-balanced'});
  h.emit({type:'cleared',conversation:'new'});
  assert(!h.send.disabled);
  h.send.click();await h.flush();
  assert.equal(h.commands.at(-1).type,'prompt');
  assert.equal(h.commands.at(-1).text,'Attendre la nouvelle conversation');
});

test('an interrupted conversation creation releases its controls and preserves the draft',async t=>{
  const h=await setup(t);
  h.document.getElementById('clear').click();await h.flush();
  h.prompt.value='Brouillon à conserver';
  h.emit({type:'idle'});
  assert(!h.document.getElementById('clear').disabled);
  assert(!h.send.disabled);assert.equal(h.provider.clearingConversation,false);
  assert.equal(h.prompt.value,'Brouillon à conserver');
  assert.match(h.document.getElementById('status').textContent,/Création de conversation interrompue/);
});

test('editing a selected project path updates persisted selection before creating the next conversation',async t=>{
  const h=await setup(t),before=path.join(h.temporary,'before'),after=path.join(h.temporary,'after');
  fs.mkdirSync(before);fs.mkdirSync(after);
  const selected=h.projects.register(before);
  await h.provider.selectConversationFolder(selected);
  h.provider.pendingConversationProject=selected.id;
  await h.provider.updateProject({project:selected.id,root:after,requestId:'update'});
  const replacement=h.projects.identity(after).id;
  assert.equal(h.provider.newConversationProject,replacement);
  assert.equal(h.provider.pendingConversationProject,replacement);
  assert.equal(h.values['ovhAgent.newConversationProject'],replacement);
  await h.provider.clearConversation();
  assert.equal(h.provider.workspace,after);
  assert.equal(h.commands.at(-1).type,'clear');
});

test('access preference stays distinct from active rights and resume transmits current explicit choices',async t=>{
  const h=await setup(t),access=h.document.getElementById('access');
  h.emit({type:'ready',conversation:'conversation',model:'auto-balanced'});
  h.emit({type:'status',conversation:'conversation',text:'Travail'});
  h.emit({type:'access_mode',full_access:true});
  access.click();
  assert.match(access.textContent,/En cours.*Accès complet.*Prochaine demande.*Demander l’accès/);
  assert(access.classList.contains('full'),'active full access remains visibly marked');
  h.emit({type:'access_mode',full_access:true});
  assert.match(access.textContent,/Prochaine demande.*Demander l’accès/,'backend acknowledgement must not overwrite preference');
  h.emit({type:'idle'});
  h.emit({type:'task_interrupted',id:'conversation'});
  h.emit({type:'models',items:[{id:'chosen-model',kind:'chat',selectable:true}]});
  h.document.getElementById('model-picker').click();
  h.document.querySelector('.model-item').click();
  h.emit({type:'model_selected',model:'chosen-model'});
  h.send.click();await h.flush();
  const resume=h.commands.find(m=>m.type==='resume_archive');
  assert.equal(resume.full_access,false);assert.equal(resume.model,'chosen-model');
  h.emit({type:'archive_loaded',id:'conversation',messages:[],model:'old-model'});
  assert(h.document.getElementById('selected-model').textContent.includes('chosen-model'));
  h.emit({type:'access_mode',full_access:false});
  assert(!access.classList.contains('full'));
  for(const bad of [{id:'conversation'},{id:'conversation',fullAccess:'false',model:'auto-balanced'},{id:'conversation',fullAccess:false,model:'../invalid'}])
    assert(!validMessage({type:'resumeArchive',...bad}));
});
