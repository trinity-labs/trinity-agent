const { t } = require("./media/i18n");
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const os = require('os');
const {invalidateProjectMemory,assertStoragePath,readStorageFile} = require('./project-store');

function archiveCacheBudget({environment=process.env,runtime=process,system=os,retainedBytes=0}={}) {
  const mib=1024*1024;
  const observed=(read,fallback)=>{
    try {const value=read();return Number.isFinite(value)&&value>=0?value:fallback;} catch {return fallback;}
  };
  const hostTotal=observed(()=>system.totalmem(),512*mib);
  const hostFree=observed(()=>system.freemem(),128*mib);
  const constrained=observed(()=>runtime.constrainedMemory(),0);
  const total=constrained>0?Math.min(hostTotal,constrained):hostTotal;
  // Modern Node reports usable memory inside cgroups. On older runtimes do not
  // confuse host free RAM with this container's available allowance.
  const fallback=Math.min(hostFree,total/4,128*mib);
  const available=Math.min(total,hostFree,observed(()=>runtime.availableMemory(),fallback));
  const reserve=Math.min(256*mib,total/10);
  const headroom=Math.max(0,Math.min(total,available+Math.max(0,retainedBytes))-reserve);
  const automatic=Math.floor(headroom/(64*mib))*mib;
  const raw=environment.OVH_AGENT_ARCHIVE_CACHE_MB;
  const configured=raw===undefined||raw==='auto'||raw===''?NaN:Number(raw);
  return Number.isFinite(configured)&&configured>=0?Math.min(automatic,configured*mib):automatic;
}

class ArchiveStore {
  constructor(workspace, dataRoot = process.env.OVH_AGENT_DATA_ROOT || path.join(require('os').homedir(), '.ovh-agent')) {
    this.session = crypto.createHash('sha256').update(fs.realpathSync(workspace)).digest('hex').slice(0,20);
    assertStoragePath(dataRoot);
    this.dataRoot = path.resolve(dataRoot);
    this.root = path.join(dataRoot, 'archive', this.session);
    this.cache = new Map();
    this.cacheBytes = 0;
    this.cacheLimit = archiveCacheBudget();
    this.nextCacheRefresh=Date.now()+30000;
  }
  refreshCacheBudget() {
    if(Date.now()<this.nextCacheRefresh)return;
    this.cacheLimit=archiveCacheBudget({retainedBytes:this.cacheBytes});
    this.nextCacheRefresh=Date.now()+30000;
    while(this.cache.size&&this.cacheBytes>this.cacheLimit){
      const [key,removed]=this.cache.entries().next().value;
      this.cache.delete(key);this.cacheBytes-=removed.size;
    }
  }
  readCached(file, parse = JSON.parse) {
    this.refreshCacheBudget();
    const relative=path.relative(this.root,file);
    if(relative==='..'||relative.startsWith('..'+path.sep)||path.isAbsolute(relative))throw new Error('Lecture hors archive refusée.');
    assertStoragePath(file);
    const stat = fs.statSync(file, {bigint:true});
    if (!stat.isFile() || stat.size > 32n * 1024n * 1024n) throw new Error("Archive trop volumineuse ou non régulière.");
    const signature = [stat.dev,stat.ino,stat.size,stat.mtimeNs,stat.ctimeNs].join(':');
    const old = this.cache.get(file);
    if (old?.signature === signature) {
      this.cache.delete(file); this.cache.set(file, old);
      return old.value;
    }
    if (old) { this.cache.delete(file); this.cacheBytes -= old.size; }
    const value = parse(readStorageFile(file, 'utf8'));
    const after = fs.statSync(file, {bigint:true});
    const size = Buffer.byteLength(JSON.stringify(value) ?? '') * 4 + 512;
    if (signature === [after.dev,after.ino,after.size,after.mtimeNs,after.ctimeNs].join(':') && size <= this.cacheLimit) {
      while (this.cache.size && this.cacheBytes + size > this.cacheLimit) {
        const [key, removed] = this.cache.entries().next().value;
        this.cache.delete(key); this.cacheBytes -= removed.size;
      }
      this.cache.set(file, {signature,value,size}); this.cacheBytes += size;
    }
    return value;
  }
  current() { try { assertStoragePath(path.join(this.root,'current')); return readStorageFile(path.join(this.root,'current'),'utf8',4096).trim(); } catch { return ''; } }
  has(id) {
    if (!/^[0-9A-Za-z_-]+$/.test(String(id || ''))) return false;
    try {
      const meta=this.readCached(path.join(this.root,id,'meta.json'));
      return !!meta && typeof meta==='object' && !Array.isArray(meta);
    } catch { return false; }
  }
  transcript(id) {
    // Read-only view of one conversation, usable while the backend is busy.
    if (!/^[0-9A-Za-z_-]+$/.test(String(id || ''))) throw new Error(t("Identifiant de conversation invalide."));
    const folder = path.join(this.root, id);
    assertStoragePath(folder);
    if (!fs.existsSync(folder) || fs.lstatSync(folder).isSymbolicLink()
        || path.dirname(fs.realpathSync(folder)) !== fs.realpathSync(this.root)) {
      throw new Error(t("Conversation introuvable."));
    }
    const base = fs.realpathSync(folder);
    const events = this.readCached(path.join(folder, 'events.jsonl'), text => {
      const items = [];
      for (const line of String(text).split('\n')) {
        let event;
        try { event = JSON.parse(line); } catch { continue; }
        if (!event || typeof event !== 'object' || Array.isArray(event)) continue;
        if (!['user', 'response', 'error', 'file_change', 'tool_start', 'tool_end', 'model_changed', 'verification_check'].includes(event.type)) continue;
        items.push(event);
      }
      return items;
    });
    return events.slice(-400).map(event => {
      const item = {
        type: event.type, timestamp: event.timestamp,
        text: typeof event.text === 'string' ? event.text.slice(0, 200000) : '',
        tool: event.tool, title: event.title,
        detail: typeof event.detail === 'string' ? event.detail.slice(0, 4000) : '',
        success: event.success === true, passed: event.passed === true,
        resume: event.resume === true,
        from_model: event.from_model, to_model: event.to_model, check: event.check,
        duration_ms: Number.isFinite(event.duration_ms) ? event.duration_ms : undefined,
      };
      if (event.type === 'user' && Array.isArray(event.images)) {
        item.images = event.images.slice(0, 5).flatMap(image => {
          try {
            const file = path.resolve(base, String(image.file || ''));
            if (!file.startsWith(base + path.sep)) return [];
            const media = String(image.media_type || '');
            if (!['image/jpeg', 'image/png', 'image/webp', 'image/gif'].includes(media)) return [];
            const stat = fs.statSync(file);
            if (!stat.isFile() || stat.size > 10 * 1024 * 1024) return [];
            return [{ name: String(image.name || path.basename(file)), media_type: media,
                      data_url: `data:${media};base64,${fs.readFileSync(file).toString('base64')}` }];
          } catch { return []; }
        });
      }
      return item;
    });
  }
  list() {
    assertStoragePath(this.root);
    if (!fs.existsSync(this.root)) return [];
    const current = this.current();
    return fs.readdirSync(this.root,{withFileTypes:true}).filter(e=>e.isDirectory()).flatMap(e=>{
      try {
        const meta=this.readCached(path.join(this.root,e.name,'meta.json'));
        let task; try { task=this.readCached(path.join(this.root,e.name,'task-state.json')); } catch {}
        let title=typeof meta.title==='string' ? meta.title.trim() : '';
        if (!task || !title || title==='Nouvelle conversation') {
          try {
            const state=this.readCached(path.join(this.root,e.name,'events.jsonl'), text=>{
              let pending=false, title='';
              for (const line of text.split('\n')) {
                let event; try { event=JSON.parse(line); } catch { continue; }
                if (!event || typeof event!=='object') continue;
                if (event.type==='user') {
                  pending=true;
                  if (!title) title=String(event.text||'').replace(/\s+/g,' ').trim().slice(0,90) || (event.images?.length ? 'Analyse cette image.' : '');
                }
                if (event.type==='response') pending=false;
              }
              return {pending,title};
            });
            if (!task && state.pending) task={status:'interrupted'};
            if (!title || title==='Nouvelle conversation') title=state.title || 'Nouvelle conversation';
          } catch {}
        }
        return [{...meta,title:title||'Nouvelle conversation',id:e.name,current:e.name===current,resumable:['running','interrupted'].includes(task?.status)}];
      } catch { return []; }
    }).sort((a,b)=>String(b.updated_at||'').localeCompare(String(a.updated_at||'')));
  }
  removal(id) {
    if (!/^[0-9A-Za-z_-]+$/.test(id)) throw new Error(t("Identifiant de conversation invalide."));
    const target=path.join(this.root,id);
    assertStoragePath(target);
    if (!fs.existsSync(target)) throw new Error(t("Conversation introuvable."));
    if (fs.lstatSync(target).isSymbolicLink() || path.dirname(fs.realpathSync(target))!==fs.realpathSync(this.root)) throw new Error(t("Chemin de conversation invalide."));
    const wasCurrent=this.current()===id;
    const legacy=path.join(this.dataRoot,'sessions',this.session+'.json');
    const messages=path.join(target,'messages.json');
    assertStoragePath(legacy);assertStoragePath(messages);
    assertStoragePath(path.join(this.root,'current'));
    assertStoragePath(path.join(this.dataRoot,'memory/projects',this.session));
    return {target,wasCurrent,legacy,messages};
  }
  remove(id) {
    const {target,wasCurrent,legacy,messages}=this.removal(id);
    if (fs.existsSync(legacy)&&fs.existsSync(messages)&&readStorageFile(legacy).equals(readStorageFile(messages))) fs.unlinkSync(legacy);
    invalidateProjectMemory(this.dataRoot,this.session);
    fs.rmSync(target,{recursive:true,force:false});
    this.cache.clear(); this.cacheBytes = 0;
    if (fs.existsSync(target)) throw new Error(t("Le dossier existe encore après suppression."));
    if (wasCurrent) fs.unlinkSync(path.join(this.root,'current'));
    return wasCurrent;
  }
}
module.exports={ArchiveStore,archiveCacheBudget};

// Directory scans and legacy transcript parsing must never run on VS Code's
// shared extension-host thread. Reuse the same stores and path validation here.
const { Worker, isMainThread, parentPort, workerData } = require('node:worker_threads');
class ArchiveReader {
  constructor({allowedRoot='/home/coder'}={}) {
    this.allowedRoot=allowedRoot;this.pending=new Map();this.sequence=0;
  }
  read(request) {
    if(this.disposed)return Promise.reject(new Error('Archive reader closed.'));
    if(!this.worker) {
      const worker=this.worker=new Worker(__filename,{workerData:{archiveCatalog:true,allowedRoot:this.allowedRoot}});
      const fail=error=>{
        if(this.worker!==worker)return;
        this.worker=undefined;
        for(const entry of this.pending.values())entry.reject(error);
        this.pending.clear();
      };
      worker.on('message',message=>{
        const entry=this.pending.get(message.id);if(!entry)return;
        this.pending.delete(message.id);
        if(!this.pending.size)worker.unref();
        if(message.error)entry.reject(new Error(message.error));else entry.resolve(message.result);
      });
      worker.on('error',fail);
      worker.on('exit',()=>fail(new Error('Archive reader stopped.')));
    }
    const id=++this.sequence;this.worker.ref();
    return new Promise((resolve,reject)=>{
      this.pending.set(id,{resolve,reject});
      try {this.worker.postMessage({id,request});}
      catch(error) {this.pending.delete(id);reject(error);if(!this.pending.size)this.worker.unref();}
    });
  }
  dispose() {
    this.disposed=true;
    for(const entry of this.pending.values())entry.reject(new Error('Archive reader closed.'));
    this.pending.clear();
    const worker=this.worker;this.worker=undefined;
    return worker?.terminate();
  }
}
module.exports.ArchiveReader=ArchiveReader;

if(!isMainThread && workerData?.archiveCatalog) {
  const {ProjectStore}=require('./project-store');
  const projectArchives=new Map();
  let store;
  parentPort.on('message',({id,request})=>{
    try {
      const {workspace,dataRoot,selected}=request;
      const allowedRoot=workerData.allowedRoot;
      if(!store || store.dataRoot!==dataRoot || store.workspace!==workspace) {
        projectArchives.clear();store=new ArchiveStore(workspace,dataRoot);store.workspace=workspace;
      }
      const registry=new ProjectStore(store.dataRoot, allowedRoot);
      const entries=registry.catalog();
      if(!entries.some(p=>p.id===store.session))entries.unshift({id:store.session,name:path.basename(workspace),root:workspace});
      const assignments=registry.assignments(),items=[];
      const projects=registry.list().map(entry=>({...entry,selected:entry.id===selected,active:entry.id===store.session,documents:registry.documents(entry),items:[]}));
      const groups=new Map(projects.map(entry=>[entry.id,entry]));
      for(const entry of entries) {
        const active=entry.id===store.session;
        let archive=store;
        if(!active) {
          archive=projectArchives.get(entry.id);
          if(!archive) {
            archive=new ArchiveStore(entry.root,store.dataRoot);
            if(projectArchives.size>=8)projectArchives.delete(projectArchives.keys().next().value);
            projectArchives.set(entry.id,archive);
          }
        }
        for(const item of archive.list()) {
          const group=groups.get(registry.assignedProject(entry,item,assignments));
          const row={...item,archive_project:entry.id,archive_active:active};
          (group?group.items:items).push(row);
        }
      }
      const newest=(a,b)=>String(b.updated_at||'').localeCompare(String(a.updated_at||''));
      items.sort(newest);for(const project of projects)project.items.sort(newest);
      if(['prepareProjectDeletion','deleteProject'].includes(request.action)) {
        const project=groups.get(request.project);
        if(!project)throw new Error('Projet introuvable.');
        const targets=project.items.map(item=>({source:item.archive_project,id:item.id}));
        const signature=items=>JSON.stringify(items.map(item=>item.source+':'+item.id).sort());
        if(request.action==='deleteProject' && signature(targets)!==signature(request.expected || [])) {
          throw new Error('Le contenu du dossier a changé. Vérifie les conversations puis confirme à nouveau.');
        }
        const archives=new Map([[store.session,store],...projectArchives]);
        // Validate the entire selection before removing its first archive.
        // Never resolve deletion from the workspace path supplied by a webview.
        for(const target of targets) {
          let archive=archives.get(target.source);
          if(!archive) {
            const source=entries.find(entry=>entry.id===target.source);
            archive=new ArchiveStore(source.root,dataRoot);archives.set(target.source,archive);
          }
          const {target:folder}=archive.removal(target.id);
          let task;
          try {task=JSON.parse(readStorageFile(path.join(folder,'task-state.json'),'utf8'));}catch(error){if(error.code!=='ENOENT')throw error;}
          if(task?.status==='running' && !(request.writer?.source===target.source && request.writer?.id===target.id)) {
            throw new Error('Une conversation du dossier est en cours dans un autre agent. Arrête-la avant de supprimer ce dossier.');
          }
        }
        if(request.action==='prepareProjectDeletion') {
          parentPort.postMessage({id,result:{project:project.id,targets}});return;
        }
        const deleted=[];let error;
        try {
          for(const target of targets) {
            const archive=archives.get(target.source),wasCurrent=archive.current()===target.id;
            try {archive.remove(target.id);}
            catch(failure) {
              // An error updating the current pointer after rm must still clear
              // the deleted conversation in the UI, and remain a reported error.
              if(!fs.existsSync(path.join(archive.root,target.id)))deleted.push({...target,was_current:wasCurrent});
              throw failure;
            }
            deleted.push({...target,was_current:wasCurrent});
          }
          registry.remove(project.id);
        } catch(failure) {error=String(failure.message);}
        parentPort.postMessage({id,result:{project:project.id,deleted,...(error?{error}:{})}});return;
      }
      parentPort.postMessage({id,result:{type:'archives',items,projects,archive_root:store.root}});
    } catch(error) {parentPort.postMessage({id,error:String(error.message)});}
  });
}
