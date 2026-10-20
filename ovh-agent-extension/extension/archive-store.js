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
  remove(id) {
    if (!/^[0-9A-Za-z_-]+$/.test(id)) throw new Error(t("Identifiant de conversation invalide."));
    const target=path.join(this.root,id);
    assertStoragePath(target);
    if (!fs.existsSync(target)) throw new Error(t("Conversation introuvable."));
    if (fs.lstatSync(target).isSymbolicLink() || path.dirname(fs.realpathSync(target))!==fs.realpathSync(this.root)) throw new Error(t("Chemin de conversation invalide."));
    const wasCurrent=this.current()===id;
    const legacy=path.join(this.dataRoot,'sessions',this.session+'.json');
    const messages=path.join(target,'messages.json');
    assertStoragePath(legacy);assertStoragePath(messages);
    if (fs.existsSync(legacy)&&fs.existsSync(messages)&&readStorageFile(legacy).equals(readStorageFile(messages))) fs.unlinkSync(legacy);
    fs.rmSync(target,{recursive:true,force:false});
    invalidateProjectMemory(this.dataRoot,this.session);
    this.cache.clear(); this.cacheBytes = 0;
    if (fs.existsSync(target)) throw new Error(t("Le dossier existe encore après suppression."));
    if (wasCurrent) fs.unlinkSync(path.join(this.root,'current'));
    return wasCurrent;
  }
}
module.exports={ArchiveStore,archiveCacheBudget};
