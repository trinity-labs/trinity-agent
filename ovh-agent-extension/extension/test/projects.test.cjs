const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('fs'), os=require('os'),path=require('path');
const {ProjectStore}=require('../project-store');
function setup(t){const root=fs.mkdtempSync(path.join(os.tmpdir(),'agent-projects-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));return {root,store:new ProjectStore(path.join(root,'data'),root)};}
test('create project, instructions, canonical registry and persistence',t=>{
 const {root,store}=setup(t),project=store.create(root,'Mon projet');
 assert(fs.readFileSync(path.join(project.root,'AGENTS.md'),'utf8').includes('Vérification'));
 assert.equal(store.register(project.root).id,project.id);assert.equal(store.list().length,1);
 assert.equal(new ProjectStore(path.join(root,'data'),root).list()[0].root,project.root);
 assert.throws(()=>store.create(root,'Mon projet'));
});
test('folder ordering persists across reload, customization and adding another project',t=>{
 const {root,store}=setup(t),a=store.create(root,'a'),b=store.create(root,'b'),c=store.create(root,'c');
 store.reorder(c.id,a.id,'before');store.reorder(b.id,c.id,'after');
 assert.deepEqual(store.list().map(p=>p.id),[c.id,b.id,a.id]);
 store.update(b.id,{name:'Custom B',color:'#123456'});
 const d=store.create(root,'d'),loaded=new ProjectStore(store.dataRoot,root);
 assert.deepEqual(loaded.list().map(p=>p.id),[c.id,b.id,a.id,d.id]);
 assert.equal(loaded.list()[1].color,'#123456');
 const before=fs.readFileSync(store.file,'utf8');
 for(const args of [[a.id,'f'.repeat(20),'before'],[a.id,b.id,'inside'],['../escape',b.id,'after']])assert.throws(()=>store.reorder(...args));
 store.reorder(a.id,a.id,'before');assert.equal(fs.readFileSync(store.file,'utf8'),before);
});
test('reordering preserves unavailable projects and refuses symbolic registries',t=>{
 const {root,store}=setup(t),a=store.create(root,'a'),offline=store.create(root,'offline'),b=store.create(root,'b');
 fs.rmSync(offline.root,{recursive:true});store.reorder(b.id,a.id,'before');
 assert.deepEqual(JSON.parse(fs.readFileSync(store.file)).map(p=>p.id),[b.id,a.id,offline.id]);
 const saved=store.file+'.saved';fs.renameSync(store.file,saved);fs.symlinkSync(saved,store.file);
 assert.throws(()=>store.reorder(a.id,b.id,'before'),/symbolique/);
});
test('project traversal and symlink escapes are rejected',t=>{
 const {root,store}=setup(t);fs.symlinkSync(os.tmpdir(),path.join(root,'outside'));
 for(const name of ['../outside','.', '..','a/b'])assert.throws(()=>store.create(root,name));
 assert.throws(()=>store.register(path.join(root,'outside')));
});
test('attach completed conversation moves its memory and changes its workspace',t=>{
 const {root,store}=setup(t),a=store.create(root,'one'),b=store.create(root,'two');
 const origin=path.join(root,'data/archive',a.id,'conversation');fs.mkdirSync(origin,{recursive:true});
 fs.writeFileSync(path.join(origin,'meta.json'),JSON.stringify({workspace:a.root,title:'Keep title'}));
 fs.writeFileSync(path.join(origin,'events.jsonl'),'historical evidence');fs.writeFileSync(path.join(path.dirname(origin),'current'),'conversation');
 store.attach(a.root,'conversation',b.root);
 const target=path.join(root,'data/archive',b.id,'conversation');
 assert(!fs.existsSync(origin));assert.equal(fs.readFileSync(path.join(target,'events.jsonl'),'utf8'),'historical evidence');
 const meta=JSON.parse(fs.readFileSync(path.join(target,'meta.json')));assert.equal(meta.workspace,b.root);assert.equal(meta.previous_workspace,a.root);
 assert.equal(fs.readFileSync(path.join(path.dirname(target),'current'),'utf8'),'conversation');
});
test('attach refuses unfinished tasks and existing destinations',t=>{
 const {root,store}=setup(t),a=store.create(root,'one'),b=store.create(root,'two');
 const origin=path.join(root,'data/archive',a.id,'conversation');fs.mkdirSync(origin,{recursive:true});
 fs.writeFileSync(path.join(origin,'meta.json'),'{}');fs.writeFileSync(path.join(origin,'task-state.json'),'{"status":"interrupted"}');
 assert.throws(()=>store.attach(a.root,'conversation',b.root));assert(fs.existsSync(origin));
 fs.unlinkSync(path.join(origin,'task-state.json'));fs.mkdirSync(path.join(root,'data/archive',b.id,'conversation'),{recursive:true});
 assert.throws(()=>store.attach(a.root,'conversation',b.root));
});
test('opening a saved conversation restores its workspace and persists across provider recreation',async t=>{
 const {root,store}=setup(t), project=store.create(root,'target');
 const extension=path.resolve(__dirname,'..'),{createRequire}=require('module'),vm=require('vm');
 const native=createRequire(path.join(extension,'extension.js'));const values={};let picks=0;
 const vscode={workspace:{workspaceFolders:[{uri:{fsPath:root}}]},env:{language:'fr'},window:{showQuickPick:async()=>{picks++;return {root:project.root};},showInformationMessage:()=>{},showErrorMessage:message=>assert.fail(message)}};
 const sandbox={require:name=>name==='vscode'?vscode:name==='./project-store'?{ProjectStore:class extends ProjectStore{constructor(data){super(data,root);}}}:name==='./archive-store'?{...native(name),ArchiveStore:class extends (native(name).ArchiveStore){constructor(workspace,data=store.dataRoot){super(workspace,data);}}}:native(name),module:{exports:{}},process,console,setTimeout,clearTimeout};
 vm.runInNewContext(fs.readFileSync(path.join(extension,'extension.js'),'utf8')+'\nmodule.exports.Provider=OvhAgentViewProvider;',sandbox);
 const Provider=sandbox.module.exports.Provider,state={get:key=>values[key],update:async(key,value)=>{values[key]=value;}};
 const provider=new Provider(extension,{},state);provider.windowLock={};provider.pickProject=items=>vscode.window.showQuickPick(items);provider.store.dataRoot=path.join(root,'data');
 const calls=[];provider.stopAgent=async()=>calls.push('stop');provider.ensureAgent=async()=>{
   calls.push(provider.workspace);
   provider.agent={stdin:{write:line=>{
     const command=JSON.parse(line);assert.equal(command.type,'load_archive');assert.equal(command.id,'saved');
     assert.equal(provider.workspace,project.root);
   }}};
   return true;
 };provider.showArchives=()=>calls.push(provider.store.session);
 const folder=path.join(store.dataRoot,'archive',project.id,'saved');fs.mkdirSync(folder,{recursive:true});
 fs.writeFileSync(path.join(folder,'meta.json'),JSON.stringify({workspace:project.root,title:'Saved conversation'}));
 fs.writeFileSync(path.join(folder,'../current'),'saved');
 await provider.openProject(project.id,'saved');
 assert.equal(provider.workspace,project.root);assert.equal(provider.store.session,project.id);
 assert.deepEqual(calls,['stop',project.id,project.root]);assert.equal(new Provider(extension,{},state).workspace,project.root);
 provider.busy=true;await provider.selectProject();assert.equal(picks,0);
});
test('deleting a conversation removes generated memory and disk index, preserving manual notes',t=>{
 const {root,store}=setup(t),project=store.create(root,'one');
 const {ArchiveStore}=require('../archive-store'),archives=new ArchiveStore(project.root,store.dataRoot);
 const conversation=path.join(archives.root,'one');fs.mkdirSync(conversation,{recursive:true});
 fs.writeFileSync(path.join(conversation,'meta.json'),'{}');
 const memory=path.join(store.dataRoot,'memory/projects',project.id);fs.mkdirSync(memory,{recursive:true});
 for(const name of ['NOTES.md','MEMORY.md','INDEX.md'])fs.writeFileSync(path.join(memory,name),'content');
 fs.writeFileSync(path.join(store.dataRoot,'memory/AGGREGATE.md'),'derived');
 const previous=process.env.OVH_AGENT_MEMORY_INDEX_DIR;process.env.OVH_AGENT_MEMORY_INDEX_DIR=path.join(root,'cache');
 t.after(()=>{if(previous===undefined)delete process.env.OVH_AGENT_MEMORY_INDEX_DIR;else process.env.OVH_AGENT_MEMORY_INDEX_DIR=previous;});
 const namespace=require('crypto').createHash('sha256').update(fs.realpathSync(store.dataRoot)).digest('hex').slice(0,20);
 const index=path.join(process.env.OVH_AGENT_MEMORY_INDEX_DIR,namespace,project.id,'index.json.gz');fs.mkdirSync(path.dirname(index),{recursive:true});fs.writeFileSync(index,'derived');
 archives.remove('one');
 assert(fs.existsSync(path.join(memory,'NOTES.md')));assert(fs.existsSync(path.join(memory,'.revision')));
 for(const file of [index,path.join(memory,'MEMORY.md'),path.join(memory,'INDEX.md'),path.join(store.dataRoot,'memory/AGGREGATE.md')])assert(!fs.existsSync(file));
});
test('attaching conversation invalidates both projects derived memory',t=>{
 const {root,store}=setup(t),a=store.create(root,'one'),b=store.create(root,'two');
 const origin=path.join(store.dataRoot,'archive',a.id,'conversation');fs.mkdirSync(origin,{recursive:true});fs.writeFileSync(path.join(origin,'meta.json'),'{}');
 for(const project of [a,b]) {const dir=path.join(store.dataRoot,'memory/projects',project.id);fs.mkdirSync(dir,{recursive:true});fs.writeFileSync(path.join(dir,'MEMORY.md'),'obsolete');}
 store.attach(a.root,'conversation',b.root);
 for(const project of [a,b])assert(!fs.existsSync(path.join(store.dataRoot,'memory/projects',project.id,'MEMORY.md')));
});
test('project menu aggregation sends selected project IDs without a prompt to the model',async t=>{
 const {root,store}=setup(t),a=store.create(root,'one'),b=store.create(root,'two');
 const extension=path.resolve(__dirname,'..'),{createRequire}=require('module'),vm=require('vm'),native=createRequire(path.join(extension,'extension.js'));
 let calls=0;const vscode={workspace:{workspaceFolders:[{uri:{fsPath:a.root}}]},env:{language:'fr'},window:{showQuickPick:async(items,options)=>++calls===1?{action:'aggregate'}:items.filter(p=>p.id===b.id),showErrorMessage:message=>assert.fail(message)}};
 const sandbox={require:name=>name==='vscode'?vscode:name==='./project-store'?{ProjectStore:class extends ProjectStore{constructor(data){super(data,root);}}}:name==='./archive-store'?{...native(name),ArchiveStore:class extends (native(name).ArchiveStore){constructor(workspace,data=store.dataRoot){super(workspace,data);}}}:native(name),module:{exports:{}},process,console,setTimeout,clearTimeout};
 vm.runInNewContext(fs.readFileSync(path.join(extension,'extension.js'),'utf8')+'\nmodule.exports.Provider=OvhAgentViewProvider;',sandbox);
 const provider=new sandbox.module.exports.Provider(extension,{});provider.windowLock={};provider.pickProject=items=>vscode.window.showQuickPick(items);provider.store.dataRoot=store.dataRoot;
 let sent;provider.sendControl=async value=>sent=value;
 await provider.selectProject();assert.equal(sent.type,'memory_export');assert.deepEqual(Array.from(sent.projects),[b.id]);
});

function typedPicker(t) {
 const {root,store}=setup(t),extension=path.resolve(__dirname,'..'),{createRequire}=require('module'),vm=require('vm'),native=createRequire(path.join(extension,'extension.js'));
 const events={change:new Set(),accept:new Set(),hide:new Set()};
 const subscribe=kind=>fn=>{events[kind].add(fn);return {dispose:()=>events[kind].delete(fn)};};
 const picker={value:'',items:[],selectedItems:[],activeItems:[],disposed:false,
  onDidChangeValue:subscribe('change'),onDidAccept:subscribe('accept'),onDidHide:subscribe('hide'),show(){},dispose(){this.disposed=true;},
  type(value){this.value=value;this.selectedItems=[];this.activeItems=[];for(const fn of events.change)fn(value);},
  enter(){for(const fn of [...events.accept])fn();},cancel(){for(const fn of [...events.hide])fn();}};
 const vscode={workspace:{workspaceFolders:[{uri:{fsPath:root}}]},env:{language:'fr'},window:{createQuickPick:()=>picker,showErrorMessage:message=>assert.fail(message)}};
 const sandbox={require:name=>name==='vscode'?vscode:name==='./project-store'?{ProjectStore:class extends ProjectStore{constructor(data){super(data,root);}}}:name==='./archive-store'?{...native(name),ArchiveStore:class extends (native(name).ArchiveStore){constructor(workspace,data=store.dataRoot){super(workspace,data);}}}:native(name),module:{exports:{}},process,console,setTimeout,clearTimeout};
 vm.runInNewContext(fs.readFileSync(path.join(extension,'extension.js'),'utf8')+'\nmodule.exports.Provider=OvhAgentViewProvider;',sandbox);
 const values={},state={get:key=>values[key],update:async(key,value)=>values[key]=value};
 const provider=new sandbox.module.exports.Provider(extension,{},state);provider.windowLock={};provider.store.dataRoot=store.dataRoot;
 provider.archiveReader=new (native('./archive-store').ArchiveReader)({allowedRoot:root});t.after(()=>provider.archiveReader.dispose());
 const calls=[];provider.stopAgent=async()=>calls.push('stop');provider.ensureAgent=async()=>{calls.push(provider.workspace);return true;};provider.showArchives=()=>calls.push('archives');
 return {root,store,provider,picker,calls,values,events,state};
}
test('Enter on a pasted folder persists it without changing the current conversation context',async t=>{
 const {root,store,provider,picker,calls,values,events}=typedPicker(t);
 const target=path.join(root,'existing folder');fs.mkdirSync(target);
 const selecting=provider.selectProject();picker.type(target);
 assert.equal(picker.items[0].root,target);assert.equal(picker.items[0].alwaysShow,true);assert.equal(store.list().length,0);
 picker.enter();await selecting;
 assert.equal(provider.workspace,root);assert.equal(values['ovhAgent.projectRoot'],undefined);
 assert.deepEqual(calls,['archives']);assert.equal(store.list()[0].root,target);
 assert(picker.disposed);assert(Object.values(events).every(set=>set.size===0));
});
test('invalid pasted paths stay editable and cannot stop or switch the active project',async t=>{
 const {root,store,provider,picker,calls}=typedPicker(t),file=path.join(root,'file');fs.writeFileSync(file,'x');
 fs.symlinkSync(os.tmpdir(),path.join(root,'escape'));
 const selecting=provider.selectProject();
 for(const target of [path.join(root,'missing'),file,os.tmpdir(),path.join(root,'escape')]) {
  picker.type(target);picker.enter();
  assert(!picker.disposed);assert.equal(picker.items[0].invalid,true);assert.equal(provider.workspace,root);
  assert.equal(calls.length,0);assert.equal(store.list().length,0);
 }
 const target=path.join(root,'valid');fs.mkdirSync(target);picker.type('"'+target+'"');picker.enter();await selecting;
 assert.equal(provider.workspace,root);assert.equal(store.list()[0].root,target);
});
test('canceling a typed project leaves the current project untouched and disposes listeners',async t=>{
 const {root,provider,picker,calls,events}=typedPicker(t);
 const selecting=provider.selectProject();picker.type(root);picker.cancel();await selecting;
 assert.equal(provider.workspace,root);assert.equal(calls.length,0);assert.equal(provider.switchingProject,false);
 assert(picker.disposed);assert(Object.values(events).every(set=>set.size===0));
});
test('relative folder paths and normal action selections are both accepted',async t=>{
 const {root,store,provider,picker}=typedPicker(t),target=path.join(root,'relative');fs.mkdirSync(target);
 const selecting=provider.pickProject([{label:'Profile',action:'profile'}],store);
 picker.type('./relative');picker.enter();assert.equal((await selecting).root,target);
 const normal=typedPicker(t),choice={label:'Profile',action:'profile'};
 const result=normal.provider.pickProject([choice],normal.store);normal.picker.type('Profile');normal.picker.selectedItems=[choice];normal.picker.enter();
 assert.equal((await result).action,'profile');
});

test('project catalog discovers existing archives without assigning them to another project',t=>{
 const {root,store}=setup(t),folder=path.join(root,'old project');fs.mkdirSync(folder);
 const id=require('crypto').createHash('sha256').update(folder).digest('hex').slice(0,20);
 const conversation=path.join(store.dataRoot,'archive',id,'one');fs.mkdirSync(conversation,{recursive:true});fs.writeFileSync(path.join(conversation,'meta.json'),JSON.stringify({workspace:folder}));
 assert.equal(store.list().length,0);assert.equal(store.catalog()[0].root,folder);
 fs.writeFileSync(path.join(conversation,'meta.json'),JSON.stringify({workspace:root}));assert.equal(store.catalog().length,0);
});
test('active project preference is persisted per VS Code workspace',t=>{
 const {root,store}=setup(t),a=store.create(root,'one'),b=store.create(root,'two');
 store.activate(root,a.root);store.activate(a.root,b.root);
 assert.equal(store.active(root).root,a.root);assert.equal(store.active(a.root).root,b.root);
 fs.rmSync(a.root,{recursive:true});assert.equal(store.active(root),undefined);
});

test('a remembered folder without a saved conversation cannot become the startup context',t=>{
 const {root,store,provider,state,values}=typedPicker(t),project=store.create(root,'folder');
 store.activate(root,project.root);values['ovhAgent.projectRoot']=project.root;
 const reload=()=>new provider.constructor(path.resolve(__dirname,'..'),{},state);
 assert.equal(reload().workspace,root);
 const folder=path.join(store.dataRoot,'archive',project.id);fs.mkdirSync(folder,{recursive:true});
 fs.writeFileSync(path.join(folder,'current'),'missing');
 assert.equal(reload().workspace,root);
 delete values['ovhAgent.projectRoot'];assert.equal(reload().workspace,root);
});

test('new conversation resets the backend workspace and persisted context without modifying old archives',async t=>{
 const {root,store,provider,state,values}=typedPicker(t),project=store.create(root,'last-project');
 const folder=path.join(store.dataRoot,'archive',project.id,'saved');fs.mkdirSync(folder,{recursive:true});
 const meta=JSON.stringify({workspace:project.root,title:'Keep this context'});
 fs.writeFileSync(path.join(folder,'meta.json'),meta);fs.writeFileSync(path.join(folder,'../current'),'saved');
 fs.writeFileSync(path.join(folder,'messages.json'),'["previous project history"]');
 provider.sendControl=async()=>{};await provider.openProject(project.id,'saved');
 assert.equal(provider.workspace,project.root);
 const starts=[],writes=[],posted=[];
 provider.post=message=>posted.push(message);
 provider.ensureAgent=async()=>{
  starts.push({workspace:provider.workspace,session:provider.store.session});
  provider.agent={stdin:{write:line=>writes.push(JSON.parse(line))}};return true;
 };
 await provider.clearConversation();
 assert.equal(starts[0].workspace,root);assert.equal(starts[0].session,store.identity(root).id);
 assert.equal(writes[0].type,'clear');assert.equal(provider.store.dataRoot,store.dataRoot);
 assert.equal(values['ovhAgent.projectRoot'],root);assert.equal(store.active(root).root,root);
 assert.equal(store.list().length,1); // resetting context must not register an implicit folder
 const reload=()=>new provider.constructor(path.resolve(__dirname,'..'),{},state);
 assert.equal(reload().workspace,root);
 delete values['ovhAgent.projectRoot'];assert.equal(reload().workspace,root);
 assert.equal(fs.readFileSync(path.join(folder,'meta.json'),'utf8'),meta);
 assert.equal(fs.readFileSync(path.join(folder,'messages.json'),'utf8'),'["previous project history"]');
 assert.equal(fs.readFileSync(path.join(folder,'../current'),'utf8'),'saved');
 provider.consumeOutput('{"type":"cleared","conversation":"new"}\n');
 assert.equal(posted.at(-1).workspace,root);
 await provider.openProject(project.id,'saved');assert.equal(provider.workspace,project.root);
});
test('opening an archived conversation switches project before sending its archive ID',async t=>{
 const {root,store,provider,calls}=typedPicker(t),project=store.create(root,'target');
 const conversation=path.join(store.dataRoot,'archive',project.id,'saved');fs.mkdirSync(conversation,{recursive:true});fs.writeFileSync(path.join(conversation,'meta.json'),JSON.stringify({workspace:project.root,title:'A conversation'}));
 provider.sendControl=async message=>calls.push({workspace:provider.workspace,...message});
 await provider.openProject(project.id,'saved');
 assert.equal(provider.workspace,project.root);
 assert.deepEqual(calls.at(-1),{workspace:project.root,type:'load_archive',id:'saved'});
 assert.equal(store.active(root).root,project.root);
});
test('history groups only explicit choices, preserving ungrouped archives with identical IDs',async t=>{
 const {root,store,provider}=typedPicker(t),project=store.create(root,'other');store.register(root);
 for(const entry of store.list()) {
  const folder=path.join(store.dataRoot,'archive',entry.id,'same-id');fs.mkdirSync(folder,{recursive:true});fs.writeFileSync(path.join(folder,'meta.json'),JSON.stringify({workspace:entry.root,title:entry.name}));
 }
 provider.store=new (require('../archive-store').ArchiveStore)(root,store.dataRoot);
 let posted;provider.post=message=>posted=message;await Object.getPrototypeOf(provider).showArchives.call(provider);
 assert.equal(posted.projects.length,2);assert.equal(posted.items.length,2);
 assert(posted.projects.every(group=>group.items.length===0));
 store.assign(project.id,'same-id',project.id);
 await Object.getPrototypeOf(provider).showArchives.call(provider);
 assert.equal(posted.items.length,1);assert.equal(posted.items[0].archive_project,provider.store.session);
 assert.equal(posted.projects.find(group=>group.id===project.id).items.length,1);
 store.assign(project.id,'same-id',null);
 await Object.getPrototypeOf(provider).showArchives.call(provider);
 assert.equal(posted.items.length,2);assert(posted.projects.every(group=>group.items.length===0));
});
test('project history actions validate their IDs and reject traversal',()=>{
 const {validMessage}=require('../security');
 assert(validMessage({type:'openProject',project:'a'.repeat(20),conversation:'saved'}));
 assert(validMessage({type:'newProjectConversation',project:'a'.repeat(20)}));
 assert(!validMessage({type:'newProjectConversation',project:'../outside'}));
 assert(!validMessage({type:'openProject',project:'../outside'}));
 assert(!validMessage({type:'openProject',project:'a'.repeat(20),conversation:'../saved'}));
 assert(validMessage({type:'assignProject',source:'a'.repeat(20),conversation:'saved'}));
 assert(!validMessage({type:'assignProject',source:'a'.repeat(20),conversation:'../saved'}));
 assert(validMessage({type:'updateProject',project:'a'.repeat(20),name:'Mon projet',color:'#aabbcc'}));
 assert(validMessage({type:'updateProject',project:'a'.repeat(20),color:'#aabbcc',requestId:'project-1'}));
 assert(!validMessage({type:'updateProject',project:'a'.repeat(20)}));
 assert(!validMessage({type:'updateProject',project:'a'.repeat(20),color:'#aabbcc',requestId:'../bad'}));
 for(const patch of [{name:''},{name:'x'.repeat(81)},{color:'red;display:none'},{color:null}])assert(!validMessage({type:'updateProject',project:'a'.repeat(20),name:'Projet',color:'#aabbcc',...patch}));
 const move={type:'moveConversation',source:'a'.repeat(20),conversation:'saved',target:'b'.repeat(20)};
 assert(validMessage(move));assert(validMessage({...move,target:null}));
 for(const change of [{target:undefined},{target:'../outside'},{source:['a'.repeat(20)]},{conversation:'../saved'},{conversation:'x'.repeat(129)}])assert(!validMessage({...move,...change}));
 const order={type:'moveProject',source:'a'.repeat(20),target:'b'.repeat(20),position:'before'};
 assert(validMessage(order));assert(validMessage({...order,position:'after'}));
 for(const change of [{target:order.source},{target:null},{position:'inside'},{source:'../outside'}])assert(!validMessage({...order,...change}));
});

test('history keeps chosen folder order when the active project changes',async t=>{
 const {root,store,provider}=typedPicker(t),a=store.create(root,'a'),b=store.create(root,'b'),c=store.create(root,'c');
 store.reorder(c.id,a.id,'before');store.activate(root,b.root);
 provider.workspace=b.root;provider.store=new (require('../archive-store').ArchiveStore)(b.root,store.dataRoot);
 let posted;provider.post=message=>posted=message;
 await Object.getPrototypeOf(provider).showArchives.call(provider);
 assert.deepEqual(Array.from(posted.projects,p=>p.id),[c.id,a.id,b.id]);
 assert.equal(posted.projects[2].active,true);
});

test('drop assignment moves only the chosen conversation, preserving archive files and active work',t=>{
 const {root,store,provider}=typedPicker(t),a=store.create(root,'a'),b=store.create(root,'b');
 const originals=[];
 for(const entry of [a,b]) {
   const folder=path.join(store.dataRoot,'archive',entry.id,'same');fs.mkdirSync(folder,{recursive:true});
   for(const [name,content] of [['meta.json',JSON.stringify({workspace:entry.root,title:entry.name})],['task-state.json','{"status":"running"}'],['events.jsonl','preserved events'],['messages.json','[]']]) {
     const file=path.join(folder,name);fs.writeFileSync(file,content);originals.push([file,content]);
   }
 }
 provider.busy=true;const posted=[];provider.post=message=>posted.push(message);
 provider.organizeArchives({type:'moveConversation',source:a.id,conversation:'same',target:b.id});
 assert.equal(posted.at(-1).type,'archives_organized');
 assert.equal(store.assignments()[a.id+':same'],b.id);
 assert.equal(store.assignments()[b.id+':same'],undefined);
 assert.equal(provider.workspace,root);assert.equal(provider.busy,true);
 for(const [file,content] of originals)assert.equal(fs.readFileSync(file,'utf8'),content);
 provider.organizeArchives({type:'moveConversation',source:a.id,conversation:'same',target:null});
 assert.equal(store.assignments()[a.id+':same'],null);
 const before=fs.readFileSync(path.join(store.dataRoot,'project-conversations.json'),'utf8');
 provider.organizeArchives({type:'moveConversation',source:a.id,conversation:'missing',target:b.id});
 assert.equal(posted.at(-1).type,'archive_organization_failed');
 provider.organizeArchives({type:'moveConversation',source:a.id,conversation:'same',target:'f'.repeat(20)});
 assert.equal(posted.at(-1).type,'archive_organization_failed');
 assert.equal(fs.readFileSync(path.join(store.dataRoot,'project-conversations.json'),'utf8'),before);
 provider.windowLock=null;
 provider.organizeArchives({type:'moveConversation',source:a.id,conversation:'same',target:b.id});
 assert.equal(store.assignments()[a.id+':same'],null);
});

test('folder reorder bridge saves before refreshing history and reports failures',async t=>{
 const {root,store,provider}=typedPicker(t),a=store.create(root,'a'),b=store.create(root,'b');
 const posted=[];provider.post=message=>posted.push(message);
 provider.showArchives=()=>Object.getPrototypeOf(provider).showArchives.call(provider);
 provider.organizeArchives({type:'moveProject',source:b.id,target:a.id,position:'before'});await provider.archivesPromise;
 assert.equal(posted[0].type,'archives_organized');
 assert.deepEqual(Array.from(posted[1].projects,p=>p.id),[b.id,a.id]);
 provider.organizeArchives({type:'moveProject',source:b.id,target:'f'.repeat(20),position:'before'});await provider.archivesPromise;
 assert.equal(posted.at(-2).type,'archive_organization_failed');
 assert.deepEqual(Array.from(posted.at(-1).projects,p=>p.id),[b.id,a.id]);
});

test('custom titles and colors survive registration and activation without renaming folders',t=>{
 const {root,store}=setup(t),project=store.create(root,'folder');
 store.update(project.id,{name:'Mon titre personnalisé',color:'#AB12CD'});
 store.register(project.root);store.activate(root,project.root);
 const saved=new ProjectStore(store.dataRoot,root).list()[0];
 assert.equal(saved.name,'Mon titre personnalisé');assert.equal(saved.color,'#ab12cd');assert.equal(saved.root,project.root);
 assert(fs.existsSync(path.join(project.root,'AGENTS.md')));
 assert.throws(()=>store.update(project.id,{name:'',color:'#ffffff'}));
 assert.throws(()=>store.update(project.id,{name:'Test',color:'invalid'}));
});

test('folder appearance survives a disconnected mount while registering or updating another folder',t=>{
 for(const action of ['register','update']) {
  const {root,store}=setup(t),offline=store.create(root,'offline'),online=store.create(root,'online');
  store.update(offline.id,{name:'Mon serveur',color:'#AB12CD'});
  const original=store.list()[0];fs.renameSync(offline.root,offline.root+'.disconnected');
  assert.equal(store.list().length,1);
  if(action==='register')store.create(root,'new');else store.update(online.id,{name:'Autre titre',color:'#112233'});
  assert.deepEqual(JSON.parse(fs.readFileSync(store.file)).find(p=>p.id===offline.id),original);
  fs.renameSync(offline.root+'.disconnected',offline.root);
  const reloaded=new ProjectStore(store.dataRoot,root);
  assert.deepEqual(reloaded.register(offline.root),original);
  assert.deepEqual(reloaded.activate(root,offline.root),original);
 }
});

test('a color-only update preserves the latest saved title and survives a new store',t=>{
 const {root,store}=setup(t),project=store.create(root,'folder');
 store.update(project.id,{name:'Titre enregistré ailleurs',color:'#123456'});
 store.update(project.id,{color:'#ABCDEF'});
 const loaded=new ProjectStore(store.dataRoot,root).list()[0];
 assert.equal(loaded.name,'Titre enregistré ailleurs');assert.equal(loaded.color,'#abcdef');
 const before=fs.readFileSync(store.file,'utf8');
 for(const patch of [{},{name:''},{color:null},{color:'red'},{name:'\n',color:'#aabbcc'}])assert.throws(()=>store.update(project.id,patch));
 assert.equal(fs.readFileSync(store.file,'utf8'),before);
 const target=store.file+'.saved';fs.renameSync(store.file,target);fs.symlinkSync(target,store.file);
 assert.throws(()=>store.update(project.id,{color:'#000000'}),/symbolique/);
 assert.throws(()=>store.register(project.root),/symbolique/);
 assert.equal(fs.readFileSync(target,'utf8'),before);
});

test('project updates acknowledge disk persistence and report write failures without a false success',async t=>{
 const {root,store,provider}=typedPicker(t),project=store.create(root,'folder'),posted=[];
 provider.post=message=>posted.push(message);
 provider.showArchives=()=>Object.getPrototypeOf(provider).showArchives.call(provider);
 provider.updateProject({project:project.id,color:'#AB12CD',requestId:'color-1'});await provider.archivesPromise;
 assert.equal(posted[0].type,'project_updated');assert.equal(posted[0].requestId,'color-1');
 const reloaded=new ProjectStore(store.dataRoot,root).list()[0];
 assert.equal(reloaded.color,'#ab12cd');assert.equal(posted[0].project.color,reloaded.color);
 assert.equal(posted[1].type,'archives');assert.equal(posted[1].projects[0].color,reloaded.color);
 posted.length=0;
 const target=store.file+'.saved';fs.renameSync(store.file,target);fs.mkdirSync(store.file);
 provider.updateProject({project:project.id,color:'#000000',requestId:'color-2'});await provider.archivesPromise;
 assert.equal(posted[0].type,'project_update_failed');assert.equal(posted[0].requestId,'color-2');
 assert(!posted.some(message=>message.type==='project_updated'));
 assert.equal(JSON.parse(fs.readFileSync(target))[0].color,'#ab12cd');
});

test('classification preserves archive bytes and supports moving and removing a choice',t=>{
 const {root,store}=setup(t),a=store.create(root,'one'),b=store.create(root,'two'),source=store.identity(root);
 const archive=path.join(store.dataRoot,'archive',source.id,'saved');fs.mkdirSync(archive,{recursive:true});
 const meta=JSON.stringify({workspace:root,title:'Unchanged'});fs.writeFileSync(path.join(archive,'meta.json'),meta);
 fs.writeFileSync(path.join(archive,'task-state.json'),'{"status":"interrupted"}');
 store.assign(source.id,'saved',a.id);
 assert.equal(new ProjectStore(store.dataRoot,root).assignments()[source.id+':saved'],a.id);
 store.assign(source.id,'saved',b.id);
 assert.equal(store.assignments()[source.id+':saved'],b.id);
 assert.equal(fs.readFileSync(path.join(archive,'meta.json'),'utf8'),meta);
 assert.equal(fs.readFileSync(path.join(archive,'task-state.json'),'utf8'),'{"status":"interrupted"}');
 assert.equal(store.list().length,2);
 store.assign(source.id,'saved',null);assert.equal(store.assignments()[source.id+':saved'],null);
 assert.throws(()=>store.assign(source.id,'saved','a'.repeat(20)));
 assert.throws(()=>store.assign(source.id,'../saved',a.id));
});

test('legacy attachments retain their group while ordinary archives stay ungrouped; opt-out persists',async t=>{
 const {root,store,provider}=typedPicker(t),project=store.create(root,'target');
 for(const [id,previous_workspace] of [['attached',root],['ordinary',undefined]]) {
  const folder=path.join(store.dataRoot,'archive',project.id,id);fs.mkdirSync(folder,{recursive:true});
  fs.writeFileSync(path.join(folder,'meta.json'),JSON.stringify({workspace:project.root,previous_workspace,title:id}));
 }
 let posted;provider.post=message=>posted=message;
 const refresh=()=>Object.getPrototypeOf(provider).showArchives.call(provider);
 await refresh();assert.deepEqual(Array.from(posted.projects[0].items,item=>item.id),['attached']);
 assert.deepEqual(Array.from(posted.items,item=>item.id),['ordinary']);
 store.assign(project.id,'attached',null);await refresh();
 assert.equal(posted.projects[0].items.length,0);assert.equal(posted.items.length,2);
 const reloaded=new ProjectStore(store.dataRoot,root);
 assert.equal(reloaded.assignedProject(project,{id:'attached',previous_workspace:root}),null);
 store.assign(project.id,'attached',project.id);await refresh();assert.equal(posted.projects[0].items.length,1);
});

test('new project colors inherit branding until explicitly customized',t=>{
 const {root,store}=setup(t),project=store.create(root,'default color');
 assert.equal(project.color,undefined);assert.equal(store.register(project.root).color,undefined);
 store.update(project.id,{name:'Custom color',color:'#123456'});
 assert.equal(store.register(project.root).color,'#123456');
});

test('opening an unregistered archive does not create a project or classify its conversations',async t=>{
 const {root,store,provider}=typedPicker(t),folder=path.join(root,'unregistered');fs.mkdirSync(folder);
 const source=store.identity(folder),archive=path.join(store.dataRoot,'archive',source.id,'saved');fs.mkdirSync(archive,{recursive:true});
 fs.writeFileSync(path.join(archive,'meta.json'),JSON.stringify({workspace:folder}));
 provider.sendControl=async()=>{};await provider.openProject(source.id,'saved');
 assert.equal(provider.workspace,folder);assert.equal(store.list().length,0);assert.deepEqual(store.assignments(),{});
 let posted;provider.post=message=>posted=message;await Object.getPrototypeOf(provider).showArchives.call(provider);
 assert.equal(posted.projects.length,0);assert.equal(posted.items.length,1);
});

test('project documents expose root Markdown and generated memory, refusing symlink escapes',t=>{
 const {root,store}=setup(t),project=store.create(root,'one');fs.writeFileSync(path.join(project.root,'codex.md'),'Context');
 fs.symlinkSync(path.join(root,'outside.md'),path.join(project.root,'escape.md'));fs.writeFileSync(path.join(root,'outside.md'),'outside');
 const documents=store.documents(project);assert(documents.some(d=>d.name==='codex.md'));assert(!documents.some(d=>d.name==='escape.md'));
 assert.equal(store.document(project,'codex.md','root'),path.join(project.root,'codex.md'));
 assert.throws(()=>store.document(project,'../outside.md','root'));
});
test('authorized historical transfers preserve interrupted state and detach only terminal verification journals',t=>{
 const {root,store}=setup(t),a=store.create(root,'one'),b=store.create(root,'two'),id='a'.repeat(32);
 const origin=path.join(store.dataRoot,'archive',a.id,'old'),journal=path.join(origin,'verification',id,'journal.json');fs.mkdirSync(path.dirname(journal),{recursive:true});
 fs.writeFileSync(path.join(origin,'meta.json'),JSON.stringify({workspace:a.root}));fs.writeFileSync(path.join(origin,'task-state.json'),JSON.stringify({status:'interrupted',verification_id:id,prompt:'Original request'}));
 fs.writeFileSync(journal,JSON.stringify({workspace:a.root,status:'pending'}));
 assert.throws(()=>store.attach(a.root,'old',b.root,{includeInterrupted:true}));assert(fs.existsSync(origin));
 fs.writeFileSync(journal,JSON.stringify({workspace:a.root,status:'rolled_back'}));
 fs.writeFileSync(path.join(store.dataRoot,'pending-project-moves.json'),JSON.stringify([{source:a.root,target:b.root,conversation:'old',includeInterrupted:true}]));
 assert.equal(store.applyPendingMoves().length,0);
 const target=path.join(store.dataRoot,'archive',b.id,'old'),state=JSON.parse(fs.readFileSync(path.join(target,'task-state.json')));
 assert.equal(state.status,'interrupted');assert.equal(state.verification_id,'');assert.equal(state.previous_verification_id,id);assert.equal(state.prompt,'Original request');
 assert.equal(JSON.parse(fs.readFileSync(path.join(target,'verification',id,'journal.json'))).workspace,a.root);
 assert(!fs.existsSync(path.join(store.dataRoot,'pending-project-moves.json')));
});

test('deleting an archive in another source preserves the active conversation with the same id',async t=>{
 const {root,store,provider}=typedPicker(t),{ArchiveStore}=require('../archive-store');
 const other=store.create(root,'other'),remote=new ArchiveStore(other.root,store.dataRoot),id='same-id';
 for(const archive of [provider.store,remote]) {
  fs.mkdirSync(path.join(archive.root,id),{recursive:true});
  fs.writeFileSync(path.join(archive.root,id,'meta.json'),JSON.stringify({id,title:'Keep source distinct'}));
  fs.writeFileSync(path.join(archive.root,'current'),id);
 }
 const posted=[];provider.post=message=>posted.push(message);
 provider.agent={kill:()=>assert.fail('The active agent must not be stopped')};
 await provider.deleteArchive(id,remote.session);
 assert(fs.existsSync(path.join(provider.store.root,id)));
 assert.equal(provider.store.current(),id);
 assert.equal(fs.existsSync(path.join(remote.root,id)),false);
 const result=posted.find(message=>message.type==='archive_deleted');
 assert.equal(result.source,remote.session);assert.equal(result.active,false);assert.equal(result.was_current,false);
 assert.equal(provider.workspace,root);
});

test('switching conversation source preserves the live webview instead of recreating history',async t=>{
 const {root,store,provider}=typedPicker(t),project=store.create(root,'destination');
 let replaced=false;provider.view={webview:{set html(value){replaced=true;}}};
 provider.sendControl=async command=>assert.equal(command.id,'chosen');
 await provider.activateProject(project,'chosen');
 assert.equal(replaced,false);assert.equal(provider.workspace,project.root);
});

test('editing a project path preserves ordering, appearance and archive source assignments',t=>{
 const {root,store}=setup(t),old=store.create(root,'old'),other=store.create(root,'other');
 store.update(old.id,{name:'Custom title',color:'#123abc'});
 const target=path.join(root,'new');fs.mkdirSync(target);fs.writeFileSync(path.join(target,'README.md'),'new documents');
 const archive=path.join(store.dataRoot,'archive',old.id);
 for(const [id,meta] of [['chosen',{}],['loose',{}],['legacy',{previous_workspace:other.root}],['excluded',{previous_workspace:other.root}]]) {
  fs.mkdirSync(path.join(archive,id),{recursive:true});fs.writeFileSync(path.join(archive,id,'meta.json'),JSON.stringify(meta));
 }
 store.assign(old.id,'chosen',old.id);store.assign(old.id,'excluded',null);
 const updated=store.update(old.id,{root:target});
 assert.equal(updated.id,store.identity(target).id);assert.equal(updated.name,'Custom title');assert.equal(updated.color,'#123abc');
 assert.deepEqual(store.list().map(p=>p.id),[updated.id,other.id]);
 assert.equal(store.assignedProject(old,{id:'chosen'}),updated.id);
 assert.equal(store.assignedProject(old,{id:'legacy',previous_workspace:other.root}),updated.id);
 assert.equal(store.assignedProject(old,{id:'excluded',previous_workspace:other.root}),null);
 assert.equal(store.assignedProject(old,{id:'loose'}),null);
 assert.equal(store.catalog().find(p=>p.id===old.id).root,old.root);
 assert.equal(store.catalog().find(p=>p.id===updated.id).root,target);
 assert(fs.existsSync(path.join(archive,'chosen','meta.json')));
 assert.equal(store.documents(updated).find(d=>d.name==='README.md').path,path.join(target,'README.md'));
 const reloaded=new ProjectStore(store.dataRoot,root);assert.equal(reloaded.list()[0].root,target);
 const third=path.join(root,'third');fs.mkdirSync(third);const again=store.update(updated.id,{root:third});
 assert.equal(store.assignedProject(old,{id:'chosen'}),again.id);assert(store.catalog().some(p=>p.id===old.id));
});

test('invalid and duplicate project paths leave the registry unchanged; failed commit restores assignments',t=>{
 const {root,store}=setup(t),a=store.create(root,'a'),b=store.create(root,'b');
 const original=fs.readFileSync(store.file,'utf8');
 for(const target of [b.root,root+'/missing','relative',os.tmpdir(),null]) {
  assert.throws(()=>store.update(a.id,{root:target}));assert.equal(fs.readFileSync(store.file,'utf8'),original);
 }
 const newRoot=path.join(root,'new');fs.mkdirSync(newRoot);
 const assignments=path.join(store.dataRoot,'project-conversations.json');fs.writeFileSync(assignments,JSON.stringify({[a.id+':conversation']:a.id}));
 const before=fs.readFileSync(assignments,'utf8'),rename=fs.renameSync;
 try {
  fs.renameSync=(source,target)=>{if(target===store.file)throw new Error('Simulated write failure');return rename(source,target);};
  assert.throws(()=>store.update(a.id,{root:newRoot}),/Simulated/);
 } finally {fs.renameSync=rename;}
 assert.equal(fs.readFileSync(assignments,'utf8'),before);assert.equal(fs.readFileSync(store.file,'utf8'),original);
});

test('archive assignment and attachment reject symlinked parents before moving data',t=>{
 const {root,store}=setup(t),a=store.create(root,'source'),b=store.create(root,'target');
 const outside=path.join(root,'outside');fs.mkdirSync(path.join(outside,'one'),{recursive:true});
 fs.writeFileSync(path.join(outside,'one','meta.json'),JSON.stringify({workspace:a.root}));
 fs.mkdirSync(path.join(store.dataRoot,'archive'),{recursive:true});
 fs.symlinkSync(outside,path.join(store.dataRoot,'archive',a.id));
 assert.throws(()=>store.assign(a.id,'one',b.id),/symbolique/);
 assert.throws(()=>store.attach(a.root,'one',b.root),/symbolique/);
 assert(fs.existsSync(path.join(outside,'one','meta.json')));
 assert(!fs.existsSync(path.join(store.dataRoot,'archive',b.id,'one')));
});

test('selected folder becomes the workspace and group of the next new conversation',async t=>{
 const {root,store,provider,values}=typedPicker(t),project=store.create(root,'selected-folder');
 provider.ensureAgent=async()=>{
  provider.agent={stdin:{write:line=>{
   assert.equal(JSON.parse(line).type,'clear');
   const folder=path.join(store.dataRoot,'archive',project.id,'new');
   fs.mkdirSync(folder,{recursive:true});fs.writeFileSync(path.join(folder,'meta.json'),JSON.stringify({id:'new',workspace:project.root}));
   fs.writeFileSync(path.join(folder,'../current'),'new');
  }}};return true;
 };
 await provider.newProjectConversation(project.id);
 assert.equal(values['ovhAgent.newConversationProject'],project.id);
 assert.equal(provider.workspace,project.root);
 assert.equal(provider.store.session,project.id);
 provider.consumeOutput('{"type":"cleared","conversation":"new"}\n');
 assert.equal(store.assignments()[project.id+':new'],project.id);
 assert.equal(provider.pendingConversationProject,undefined);
});


test('a removed selected folder cannot silently create a conversation in the IDE root',async t=>{
 const {provider,calls}=typedPicker(t),posted=[];
 provider.newConversationProject='f'.repeat(20);provider.post=e=>posted.push(e);
 await provider.clearConversation();
 assert.equal(posted.at(-1).type,'error');assert.equal(calls.length,0);
});

test('opening another conversation in the active project reuses the backend and avoids catalogue scans',async t=>{
 const {root,store,provider,calls}=typedPicker(t),project=store.register(root);
 for(const id of ['first','second']) {
  const folder=path.join(store.dataRoot,'archive',project.id,id);fs.mkdirSync(folder,{recursive:true});
  fs.writeFileSync(path.join(folder,'meta.json'),JSON.stringify({workspace:root,title:id}));
 }
 t.mock.method(ProjectStore.prototype,'catalog',()=>assert.fail('No project scan is needed for the active project'));
 t.mock.method(provider.store,'list',()=>assert.fail('Opening one conversation must not enumerate archives'));
 const backend={};provider.agent=backend;
 provider.sendControl=async message=>calls.push({...message});
 await provider.openProject(project.id,'first');await provider.openProject(project.id,'second');
 assert.deepEqual(calls,[{type:'load_archive',id:'first'},{type:'load_archive',id:'second'}]);
 assert.equal(provider.agent,backend);assert.equal(provider.workspace,root);
});

test('archive restoration renders Markdown only for textual replies',t=>{
 const {provider}=typedPicker(t),rendered=[],posted=[];
 provider.renderMarkdown=text=>{rendered.push(text);return `<p>${text}</p>`;};
 provider.post=message=>posted.push(message);
 provider.consumeOutput(JSON.stringify({type:'archive_loaded',messages:[
  {type:'user',text:'Request'},{type:'tool_start',id:'a',title:'Read'},
  {type:'tool_end',id:'a',success:true,detail:'Result'},
  {type:'response',text:'Answer'},{type:'partial_response',text:'Draft'},
  {type:'error',text:'Failure'}
 ]})+'\n');
 assert.deepEqual(rendered,['Answer','Draft','Failure']);
 assert.equal(posted[0].messages.length,6);
 assert.equal(posted[0].messages[2].detail,'Result');
 assert.equal(posted[0].messages[4].html,'<p>Draft</p>');
});
