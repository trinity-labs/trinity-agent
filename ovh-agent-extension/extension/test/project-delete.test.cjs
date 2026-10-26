const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('fs'),os=require('os'),path=require('path'),vm=require('vm');
const {createRequire}=require('module'),{EventEmitter}=require('events');
const {ProjectStore}=require('../project-store'),{ArchiveStore,ArchiveReader}=require('../archive-store');
const {validMessage}=require('../security');
function setup(t){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'trinity-folder-delete-')),data=path.join(root,'data');
 const store=new ProjectStore(data,root);
 const workspace=path.join(root,'workspace');fs.mkdirSync(workspace);const source=store.identity(workspace);
 const folder=store.create(root,'folder'),other=store.create(root,'other');
 const reader=new ArchiveReader({allowedRoot:root});
 t.after(async()=>{await reader.dispose();fs.rmSync(root,{recursive:true,force:true});});
 function archive(entry,id,{target,legacy=false,current=false,running=false}={}){
  const dir=path.join(data,'archive',entry.id,id);fs.mkdirSync(dir,{recursive:true});
  fs.writeFileSync(path.join(dir,'meta.json'),JSON.stringify({workspace:entry.root,title:id,...(legacy?{previous_workspace:workspace}:{})}));
  fs.writeFileSync(path.join(dir,'events.jsonl'),'');
  if(running)fs.writeFileSync(path.join(dir,'task-state.json'),JSON.stringify({status:'running'}));
  if(current)fs.writeFileSync(path.join(dir,'../current'),id);
  if(target!==undefined)store.assign(entry.id,id,target);
  return dir;
 }
 const base={workspace,dataRoot:data,project:folder.id};
 const prepare=extra=>reader.read({...base,...extra,action:'prepareProjectDeletion'});
 const remove=plan=>reader.read({...base,action:'deleteProject',expected:plan.targets});
 return {root,data,workspace,source,folder,other,store,reader,archive,base,prepare,remove};
}
test('folder deletion follows visible assignments across sources; preserves moved-out, ungrouped, notes and workspace files',async t=>{
 const h=setup(t),{source,folder,other,archive,store}=h;
 const a=archive(source,'same',{target:folder.id,current:true}),b=archive(folder,'same',{legacy:true});
 const keep=archive(folder,'keep',{target:other.id}),outside=archive(source,'outside');
 const notes=path.join(h.data,'memory/projects',folder.id);fs.mkdirSync(notes,{recursive:true});fs.writeFileSync(path.join(notes,'NOTES.md'),'manual');
 store.activate(h.workspace,folder.root);const result=await h.remove(await h.prepare());
 assert(!result.error);assert.equal(result.deleted.length,2);assert(result.deleted.some(x=>x.source===source.id&&x.was_current));
 for(const gone of [a,b])assert(!fs.existsSync(gone));
 for(const kept of [keep,outside,folder.root,path.join(folder.root,'AGENTS.md'),path.join(notes,'NOTES.md')])assert(fs.existsSync(kept));
 assert.equal(store.list().length,1);assert.equal(store.list()[0].id,other.id);assert.equal(store.active(h.workspace),undefined);
 assert.equal(store.assignments()[folder.id+':keep'],other.id);
 assert(!fs.existsSync(path.join(h.data,'archive',source.id,'current')));
 const catalog=await h.reader.read(h.base);assert(!catalog.projects.some(p=>p.id===folder.id));
 assert.equal(catalog.projects[0].items[0].id,'keep');assert(catalog.items.some(i=>i.id==='outside'));
});
test('deleting an empty history folder leaves its physical directory intact',async t=>{
 const h=setup(t);const result=await h.remove(await h.prepare());assert(!result.error);assert.equal(result.deleted.length,0);assert(fs.existsSync(h.folder.root));assert(!h.store.list().some(p=>p.id===h.folder.id));
});
test('changed membership and an external running conversation prevent any deletion',async t=>{
 const h=setup(t),a=h.archive(h.source,'first',{target:h.folder.id}),plan=await h.prepare();
 const b=h.archive(h.source,'second',{target:h.folder.id});
 await assert.rejects(h.remove(plan),/contenu du dossier a changé/);assert(fs.existsSync(a));assert(fs.existsSync(b));
 fs.writeFileSync(path.join(b,'task-state.json'),'{"status":"running"}');
 await assert.rejects(h.prepare(),/en cours/);assert(fs.existsSync(a));assert(fs.existsSync(b));
});
test('a symbolic archive payload is rejected before deleting other conversations',async t=>{
 const h=setup(t),a=h.archive(h.source,'first',{target:h.folder.id}),b=h.archive(h.source,'second',{target:h.folder.id});
 const outside=path.join(h.root,'protected');fs.writeFileSync(outside,'preserve');fs.symlinkSync(outside,path.join(b,'messages.json'));
 await assert.rejects(h.prepare(),/symbolique/);assert(fs.existsSync(a));assert(fs.existsSync(b));assert.equal(fs.readFileSync(outside,'utf8'),'preserve');
});
test('partial deletion reports only removed archives and keeps the folder available for recovery',async t=>{
 const h=setup(t),a=h.archive(h.source,'first',{target:h.folder.id}),b=h.archive(h.folder,'second',{target:h.folder.id});
 const meta=JSON.parse(fs.readFileSync(path.join(a,'meta.json')));meta.updated_at='2026-10-26T12:00:00Z';fs.writeFileSync(path.join(a,'meta.json'),JSON.stringify(meta));
 const memory=path.join(h.data,'memory/projects',h.folder.id);fs.mkdirSync(path.join(memory,'.revision'),{recursive:true});
 const result=await h.remove(await h.prepare());assert(result.error);assert.equal(result.deleted.length,1);assert(!fs.existsSync(a));assert(fs.existsSync(b));assert(h.store.list().some(p=>p.id===h.folder.id));
});
function provider(h){
 const extension=path.resolve(__dirname,'..'),native=createRequire(path.join(extension,'extension.js'));
 const vscode={workspace:{workspaceFolders:[{uri:{fsPath:h.workspace}}]},env:{language:'fr'},window:{}};
 const scope={require:name=>name==='vscode'?vscode:native(name),module:{exports:{}},process,console,setTimeout,clearTimeout};
 vm.runInNewContext(fs.readFileSync(path.join(extension,'extension.js'),'utf8')+'\nmodule.exports.Provider=OvhAgentViewProvider;',scope);
 const state={update:async()=>{},get:()=>undefined},p=new scope.module.exports.Provider(extension,{},state);
 p.store=new ArchiveStore(h.workspace,h.data);p.archiveReader=h.reader;p.windowLock={};p.post=e=>p.events.push(e);p.events=[];p.showArchives=()=>{};
 return p;
}
test('provider waits for its writer to exit, then deletes and clears folder selection',async t=>{
 const h=setup(t),a=h.archive(h.source,'active',{target:h.folder.id,current:true,running:true}),p=provider(h);
 const agent=new EventEmitter();agent.exitCode=null;agent.signalCode=null;agent.kill=()=>{assert(fs.existsSync(a));setTimeout(()=>{agent.exitCode=0;agent.emit('exit');},10);};
 p.agent=agent;p.busy=true;p.newConversationProject=h.folder.id;p.pendingConversationProject=h.folder.id;
 await p.deleteProject(h.folder.id);assert(!fs.existsSync(a));assert.equal(p.agent,undefined);assert.equal(p.busy,false);assert.equal(p.deleting,false);assert.equal(p.newConversationProject,undefined);
 assert.deepEqual(p.events.map(e=>e.type),['project_delete_pending','archive_deleted','project_deleted']);assert(p.events[1].was_current);
});
test('failed writer shutdown cannot remove any archive or falsely report success',async t=>{
 const h=setup(t),a=h.archive(h.source,'active',{target:h.folder.id,current:true,running:true}),p=provider(h);
 p.agent={};p.stopArchiveWriter=async()=>{throw Error('shutdown failed');};
 await p.deleteProject(h.folder.id);assert(fs.existsSync(a));assert.equal(p.events.at(-1).type,'project_delete_failed');assert(!p.deleting);
});
test('project deletion messages require a canonical folder identity',()=>{
 assert(validMessage({type:'deleteProjectConfirmed',project:'a'.repeat(20)}));
 for(const project of ['../project','/home/coder','a',null])assert(!validMessage({type:'deleteProjectConfirmed',project}));
});
