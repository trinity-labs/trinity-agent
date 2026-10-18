'use strict';
const fs=require('fs'), path=require('path'), crypto=require('crypto');
function writeAtomic(file,content) {
  if(fs.existsSync(file)&&fs.lstatSync(file).isSymbolicLink())throw new Error('Fichier symbolique refusé.');
  const temporary=file+'.'+crypto.randomUUID();
  try {fs.writeFileSync(temporary,content,{mode:0o600,flag:'wx'});fs.renameSync(temporary,file);}
  finally {if(fs.existsSync(temporary))fs.unlinkSync(temporary);}
}
function invalidateProjectMemory(dataRoot, project) {
  if(!/^[a-f0-9]{20}$/.test(project))throw new Error('Projet mémoire invalide.');
  const root=path.join(dataRoot,'memory/projects',project);
  for(let cursor=root;cursor!==dataRoot;cursor=path.dirname(cursor)) {
    if(fs.existsSync(cursor)&&fs.lstatSync(cursor).isSymbolicLink())throw new Error('Dossier mémoire symbolique refusé.');
  }
  fs.mkdirSync(root,{recursive:true,mode:0o700});
  writeAtomic(path.join(root,'.revision'),crypto.randomUUID().replace(/-/g,''));
  const namespace=crypto.createHash('sha256').update(fs.realpathSync(dataRoot)).digest('hex').slice(0,20);
  const cache=process.env.OVH_AGENT_MEMORY_INDEX_DIR || path.join(process.env.XDG_CACHE_HOME || path.join(require('os').homedir(),'.cache'),'ovh-agent/project-memory');
  for(const file of [path.join(root,'MEMORY.md'),path.join(root,'INDEX.md'),path.join(root,'REGRESSIONS.md'),path.join(dataRoot,'memory/AGGREGATE.md'),path.join(cache,namespace,project,'index.json.gz')]) {
    try {fs.unlinkSync(file);} catch(error) {if(error.code!=='ENOENT')throw error;}
  }
}
class ProjectStore {
  constructor(dataRoot, allowedRoot='/home/coder') {
    this.dataRoot=dataRoot;
    this.root=fs.realpathSync(allowedRoot);
    this.file=path.join(dataRoot,'projects.json');
  }
  validate(folder) {
    const root=fs.realpathSync(folder), relative=path.relative(this.root,root);
    if(relative==='..'||relative.startsWith('..'+path.sep)||path.isAbsolute(relative)||!fs.statSync(root).isDirectory()) throw new Error('Dossier projet hors de l’espace autorisé.');
    return root;
  }
  entries() {
    try {
      if(fs.lstatSync(this.file).isSymbolicLink())throw new Error('Registre symbolique refusé.');
      const entries=JSON.parse(fs.readFileSync(this.file,'utf8'));
      if(!Array.isArray(entries))throw new Error('Registre de projets invalide.');
      return entries;
    }
    catch(error){if(error.code==='ENOENT')return [];throw error;}
  }
  list() {
    return this.entries().filter(p=>{try{return this.validate(p.root)===p.root;}catch{return false;}});
  }
  catalog() {
    const entries=new Map();
    for(const project of this.list()) {
      for(const folder of [project.root,...(Array.isArray(project.archiveSources)?project.archiveSources:[])]) {
        try {const source=this.identity(folder);entries.set(source.id,{...project,...source,name:project.name});} catch {}
      }
    }
    const archive=path.join(this.dataRoot,'archive');
    if(!fs.existsSync(archive))return [...entries.values()];
    for(const dir of fs.readdirSync(archive,{withFileTypes:true})) {
      if(!dir.isDirectory() || !/^[a-f0-9]{20}$/.test(dir.name) || entries.has(dir.name))continue;
      const project=path.join(archive,dir.name);
      for(const conversation of fs.readdirSync(project,{withFileTypes:true})) {
        if(!conversation.isDirectory())continue;
        const file=path.join(project,conversation.name,'meta.json');
        try {
          if(fs.lstatSync(file).isSymbolicLink())continue;
          const root=this.validate(JSON.parse(fs.readFileSync(file,'utf8')).workspace);
          const id=crypto.createHash('sha256').update(root).digest('hex').slice(0,20);
          if(id!==dir.name)continue;
          entries.set(id,{id,name:path.basename(root),root});break;
        } catch {}
      }
    }
    return [...entries.values()];
  }
  documents(entry) {
    const root=this.validate(entry.root);
    const documents=fs.readdirSync(root,{withFileTypes:true}).filter(item=>item.isFile()&&/\.md$/i.test(item.name)).map(item=>({name:item.name,path:path.join(root,item.name),scope:'root'}));
    const memory=path.join(this.dataRoot,'memory/projects',entry.id);
    for(const name of ['NOTES.md','MEMORY.md','INDEX.md','REGRESSIONS.md']) {
      const file=path.join(memory,name);
      if(fs.existsSync(file)&&!fs.lstatSync(file).isSymbolicLink()&&fs.statSync(file).isFile())documents.push({name,path:file,scope:'memory'});
    }
    return documents;
  }
  document(entry,name,scope) {
    const document=this.documents(entry).find(item=>item.name===name&&item.scope===scope);
    if(!document)throw new Error('Document projet introuvable.');
    const expected=scope==='root'?this.validate(entry.root):path.join(fs.realpathSync(this.dataRoot),'memory/projects',entry.id);
    if(path.dirname(fs.realpathSync(document.path))!==expected)throw new Error('Document symbolique refusé.');
    return document.path;
  }
  applyPendingMoves() {
    const file=path.join(this.dataRoot,'pending-project-moves.json');
    if(!fs.existsSync(file))return [];
    if(fs.lstatSync(file).isSymbolicLink())throw new Error('Plan symbolique refusé.');
    const moves=JSON.parse(fs.readFileSync(file,'utf8')),remaining=[];
    if(!Array.isArray(moves))throw new Error('Plan de classement invalide.');
    for(const move of moves) {
      try {this.attach(move.source,move.conversation,move.target,{includeInterrupted:move.includeInterrupted===true});}
      catch(error) {remaining.push({...move,error:String(error.message)});}
    }
    if(remaining.length)writeAtomic(file,JSON.stringify(remaining,null,2));else fs.unlinkSync(file);
    return remaining;
  }
  identity(folder) {
    const root=this.validate(folder);
    return {id:crypto.createHash('sha256').update(root).digest('hex').slice(0,20),name:path.basename(root),root};
  }
  register(folder) {
    const identity=this.identity(folder), entries=this.entries();
    const existing=entries.find(p=>p.id===identity.id);
    if(existing)return existing;
    const entry={...identity};entries.push(entry);
    fs.mkdirSync(path.dirname(this.file),{recursive:true,mode:0o700});
    if(fs.existsSync(this.file)&&fs.lstatSync(this.file).isSymbolicLink())throw new Error('Registre symbolique refusé.');
    writeAtomic(this.file,JSON.stringify(entries,null,2));
    return entry;
  }
  update(id, {name,color,root}) {
    if(name===undefined && color===undefined && root===undefined)throw new Error('Modification de projet vide.');
    if(name!==undefined && (typeof name!=='string'||!name.trim()||name.trim().length>80||/[\x00-\x1f\x7f]/.test(name)))throw new Error('Nom de projet invalide.');
    if(color!==undefined && (typeof color!=='string'||!/^#[a-f0-9]{6}$/i.test(color)))throw new Error('Couleur de projet invalide.');
    if(root!==undefined && (typeof root!=='string'||!path.isAbsolute(root)||root.length>4096||/[\x00-\x1f\x7f]/.test(root)))throw new Error('Chemin de projet invalide.');
    // Keep the menu position and customization while preserving archive source identities.
    const entries=this.entries(),entry=entries.find(p=>p.id===id);
    if(!entry)throw new Error('Projet introuvable.');
    const target=root===undefined?this.identity(entry.root):this.identity(root);
    const changed=target.root!==entry.root;
    if(changed && entries.some(p=>p.id!==id && (p.id===target.id || p.root===target.root)))throw new Error('Ce dossier appartient déjà à un projet.');
    if(name!==undefined)entry.name=name.trim();
    if(color!==undefined)entry.color=color.toLowerCase();
    if(!changed) {writeAtomic(this.file,JSON.stringify(entries,null,2));return entry;}
    const assignments=this.assignments();
    for(const key of Object.keys(assignments))if(assignments[key]===id)assignments[key]=target.id;
    // Preserve legacy attachments that were grouped implicitly in their source folder.
    const archive=path.join(this.dataRoot,'archive',id);
    if(fs.existsSync(archive)) {
      if(fs.lstatSync(archive).isSymbolicLink())throw new Error('Archive symbolique refusée.');
      for(const folder of fs.readdirSync(archive,{withFileTypes:true})) {
        if(!folder.isDirectory())continue;
        const key=id+':'+folder.name;
        if(Object.hasOwn(assignments,key))continue;
        try {
          const file=path.join(archive,folder.name,'meta.json');
          if(fs.lstatSync(file).isSymbolicLink())continue;
          const meta=JSON.parse(fs.readFileSync(file,'utf8'));
          if(typeof meta.previous_workspace==='string' && meta.previous_workspace && meta.previous_workspace!==entry.root)assignments[key]=target.id;
        } catch(error) {if(!['ENOENT'].includes(error.code) && !(error instanceof SyntaxError))throw error;}
      }
    }
    entry.archiveSources=[...new Set([...(entry.archiveSources || []),entry.root])].filter(folder=>folder!==target.root);
    entry.root=target.root;entry.id=target.id;
    const file=path.join(this.dataRoot,'project-conversations.json');
    const previous=fs.existsSync(file)?fs.readFileSync(file):null;
    writeAtomic(file,JSON.stringify(assignments,null,2));
    try {writeAtomic(this.file,JSON.stringify(entries,null,2));}
    catch(error) {if(previous===null)fs.unlinkSync(file);else writeAtomic(file,previous);throw error;}
    return entry;
  }
  reorder(source, target, position) {
    if(!/^[a-f0-9]{20}$/.test(source)||!/^[a-f0-9]{20}$/.test(target)||!['before','after'].includes(position))throw new Error('Ordre des projets invalide.');
    // Keep temporarily unavailable projects in the registry. Reorder by stable
    // anchors rather than replacing it with a potentially stale UI snapshot.
    const entries=this.entries();
    const available=new Set(this.list().map(entry=>entry.id));
    if(!available.has(source)||!available.has(target))throw new Error('Projet introuvable.');
    if(source===target)return;
    const from=entries.findIndex(entry=>entry.id===source),[entry]=entries.splice(from,1);
    const to=entries.findIndex(entry=>entry.id===target)+(position==='after'?1:0);
    entries.splice(to,0,entry);
    writeAtomic(this.file,JSON.stringify(entries,null,2));
  }
  assignments() {
    try {
      const file=path.join(this.dataRoot,'project-conversations.json');
      if(fs.lstatSync(file).isSymbolicLink())throw new Error('Registre symbolique refusé.');
      const entries=JSON.parse(fs.readFileSync(file,'utf8'));
      if(!entries||typeof entries!=='object'||Array.isArray(entries))throw new Error('Classement invalide.');
      return entries;
    } catch(error) {if(error.code==='ENOENT')return {};throw error;}
  }
  assignedProject(source, conversation, assignments=this.assignments()) {
    const key=source.id+':'+conversation.id;
    if(Object.hasOwn(assignments,key))return assignments[key];
    // Legacy attachments moved the archive and recorded its original workspace.
    // A folder alone is never evidence that the user chose to group a conversation.
    return typeof conversation.previous_workspace==='string' && conversation.previous_workspace
      && conversation.previous_workspace!==source.root ? source.id : null;
  }
  assign(source, conversation, target) {
    if(!/^[a-f0-9]{20}$/.test(source)||!/^[A-Za-z0-9_-]+$/.test(conversation))throw new Error('Conversation invalide.');
    if(target!==null&&!this.list().some(entry=>entry.id===target))throw new Error('Projet introuvable.');
    const root=path.join(this.dataRoot,'archive',source),folder=path.join(root,conversation);
    if(fs.lstatSync(folder).isSymbolicLink()||path.dirname(fs.realpathSync(folder))!==fs.realpathSync(root))throw new Error('Archive invalide.');
    const entries=this.assignments(),key=source+':'+conversation;
    // Keep an explicit opt-out so a legacy attachment cannot reappear after removal.
    entries[key]=target;
    writeAtomic(path.join(this.dataRoot,'project-conversations.json'),JSON.stringify(entries,null,2));
  }
  active(workspace) {
    const file=path.join(this.dataRoot,'active-projects.json');
    try {
      if(fs.lstatSync(file).isSymbolicLink())return undefined;
      const root=JSON.parse(fs.readFileSync(file,'utf8'))[fs.realpathSync(workspace)];
      if(typeof root!=='string')return undefined;
      const validated=this.validate(root);
      return this.list().find(p=>p.root===validated) || this.identity(validated);
    } catch {return undefined;}
  }
  activate(workspace,folder,{register=true}={}) {
    const entry=register?this.register(folder):this.identity(folder),file=path.join(this.dataRoot,'active-projects.json');
    fs.mkdirSync(this.dataRoot,{recursive:true,mode:0o700});
    let selections={};
    try {selections=JSON.parse(fs.readFileSync(file,'utf8'));}catch(error){if(error.code!=='ENOENT')throw error;}
    if(!selections || typeof selections!=='object' || Array.isArray(selections))throw new Error('Sélection de projet invalide.');
    selections[fs.realpathSync(workspace)]=entry.root;
    writeAtomic(file,JSON.stringify(selections,null,2));
    return entry;
  }
  create(parent,name) {
    if(!/^[\p{L}\p{N}][\p{L}\p{N}_. -]{0,79}$/u.test(name)||name==='.'||name==='..')throw new Error('Nom de projet invalide.');
    const folder=path.join(this.validate(parent),name);
    fs.mkdirSync(folder,{recursive:false});
    fs.writeFileSync(path.join(folder,'AGENTS.md'),'# Contexte du projet\n\n## Objectif\nÀ renseigner avec l’utilisateur.\n\n## Architecture\nÀ confirmer depuis les fichiers.\n\n## Vérification\nDocumenter les commandes réellement disponibles et leur périmètre.\n\n## Conventions\nPréserver les modifications existantes. Ne pas enregistrer de secrets.\n',{flag:'wx'});
    fs.writeFileSync(path.join(folder,'.gitignore'),'.agent-tools/\n.env\n',{flag:'wx'});
    return this.register(folder);
  }
  attach(sourceWorkspace, conversation, targetWorkspace, {includeInterrupted=false}={}) {
    if(!/^[A-Za-z0-9_-]+$/.test(conversation))throw new Error('Conversation invalide.');
    const source=this.identity(sourceWorkspace), target=this.register(targetWorkspace);
    if(source.id===target.id) {this.assign(source.id,conversation,target.id);return target;}
    const fromRoot=path.join(this.dataRoot,'archive',source.id), toRoot=path.join(this.dataRoot,'archive',target.id);
    const from=path.join(fromRoot,conversation), to=path.join(toRoot,conversation);
    if(fs.lstatSync(from).isSymbolicLink()||path.dirname(fs.realpathSync(from))!==fs.realpathSync(fromRoot))throw new Error('Archive invalide.');
    let task;try{task=JSON.parse(fs.readFileSync(path.join(from,'task-state.json'),'utf8'));}catch(error){if(error.code!=='ENOENT')throw error;}
    if(task?.status==='running' || (task?.status==='interrupted'&&!includeInterrupted))throw new Error('Termine la tâche interrompue avant de rattacher cette conversation.');
    const oldTask=task?fs.readFileSync(path.join(from,'task-state.json'),'utf8'):null;
    if(task?.verification_id) {
      if(!/^[a-f0-9]{32}$/.test(task.verification_id))throw new Error('Identifiant de vérification invalide.');
      const journal=path.join(from,'verification',task.verification_id,'journal.json');
      for(const file of [path.join(from,'verification'),path.dirname(journal),journal]) {
        try {if(fs.lstatSync(file).isSymbolicLink())throw new Error('Journal symbolique refusé.');}catch(error){if(error.code!=='ENOENT')throw error;}
      }
      if(fs.existsSync(journal)) {
        const verification=JSON.parse(fs.readFileSync(journal,'utf8'));
        if(!['passed','rolled_back'].includes(verification.status))throw new Error('Restaurer ou vérifier les modifications avant le rattachement.');
      } else if(task.status!=='completed')throw new Error('Journal de vérification introuvable.');
      task={...task,previous_verification_id:task.verification_id,previous_workspace:source.root,verification_id:''};
    }
    if(fs.existsSync(to))throw new Error('Cette conversation existe déjà dans le projet cible.');
    fs.mkdirSync(toRoot,{recursive:true,mode:0o700});
    if(fs.lstatSync(toRoot).isSymbolicLink())throw new Error('Archive cible invalide.');
    if(fs.lstatSync(path.join(from,'meta.json')).isSymbolicLink())throw new Error('Métadonnées symboliques refusées.');
    const oldMeta=fs.readFileSync(path.join(from,'meta.json'),'utf8');
    const meta=JSON.parse(oldMeta);
    meta.previous_workspace=meta.workspace;meta.workspace=target.root;
    fs.renameSync(from,to);
    try {
      writeAtomic(path.join(to,'meta.json'),JSON.stringify(meta,null,2));
      if(task)writeAtomic(path.join(to,'task-state.json'),JSON.stringify(task,null,2));
      writeAtomic(path.join(toRoot,'current'),conversation);
    } catch(error) {
      writeAtomic(path.join(to,'meta.json'),oldMeta);if(oldTask)writeAtomic(path.join(to,'task-state.json'),oldTask);fs.renameSync(to,from);throw error;
    }
    try {if(fs.readFileSync(path.join(fromRoot,'current'),'utf8').trim()===conversation)fs.unlinkSync(path.join(fromRoot,'current'));}catch(error){if(error.code!=='ENOENT')throw error;}
    const legacy=path.join(this.dataRoot,'sessions',source.id+'.json'), messages=path.join(to,'messages.json');
    if(fs.existsSync(legacy)&&fs.existsSync(messages)&&fs.readFileSync(legacy).equals(fs.readFileSync(messages)))fs.unlinkSync(legacy);
    invalidateProjectMemory(this.dataRoot,source.id);
    invalidateProjectMemory(this.dataRoot,target.id);
    this.assign(target.id,conversation,target.id);
    return target;
  }
}
module.exports={ProjectStore,invalidateProjectMemory};
