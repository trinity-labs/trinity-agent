const { t } = require("./media/i18n");
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
class ArchiveStore {
  constructor(workspace, dataRoot = process.env.OVH_AGENT_DATA_ROOT || path.join(require('os').homedir(), '.ovh-agent')) {
    this.session = crypto.createHash('sha256').update(fs.realpathSync(workspace)).digest('hex').slice(0,20);
    this.dataRoot = dataRoot;
    this.root = path.join(dataRoot, 'archive', this.session);
    this.cache = new Map();
    this.cacheBytes = 0;
    const configured = Number(process.env.OVH_AGENT_ARCHIVE_CACHE_MB ?? 16);
    this.cacheLimit = (Number.isFinite(configured) ? Math.max(0, configured) : 16) * 1024 * 1024;
  }
  readCached(file, parse = JSON.parse) {
    const stat = fs.statSync(file, {bigint:true});
    const signature = [stat.dev,stat.ino,stat.size,stat.mtimeNs,stat.ctimeNs].join(':');
    const old = this.cache.get(file);
    if (old?.signature === signature) {
      this.cache.delete(file); this.cache.set(file, old);
      return old.value;
    }
    if (old) { this.cache.delete(file); this.cacheBytes -= old.size; }
    const value = parse(fs.readFileSync(file, 'utf8'));
    const after = fs.statSync(file, {bigint:true});
    const size = Buffer.byteLength(JSON.stringify(value) ?? '') * 4 + 512;
    if (signature === [after.dev,after.ino,after.size,after.mtimeNs,after.ctimeNs].join(':') && size <= this.cacheLimit) {
      while (this.cache.size && (this.cacheBytes + size > this.cacheLimit || this.cache.size >= 512)) {
        const [key, removed] = this.cache.entries().next().value;
        this.cache.delete(key); this.cacheBytes -= removed.size;
      }
      this.cache.set(file, {signature,value,size}); this.cacheBytes += size;
    }
    return value;
  }
  current() { try { return fs.readFileSync(path.join(this.root,'current'),'utf8').trim(); } catch { return ''; } }
  list() {
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
    if (!fs.existsSync(target)) throw new Error(t("Conversation introuvable."));
    if (fs.lstatSync(target).isSymbolicLink() || path.dirname(fs.realpathSync(target))!==fs.realpathSync(this.root)) throw new Error(t("Chemin de conversation invalide."));
    const wasCurrent=this.current()===id;
    const legacy=path.join(this.dataRoot,'sessions',this.session+'.json');
    const messages=path.join(target,'messages.json');
    if (fs.existsSync(legacy)&&fs.existsSync(messages)&&fs.readFileSync(legacy).equals(fs.readFileSync(messages))) fs.unlinkSync(legacy);
    fs.rmSync(target,{recursive:true,force:false});
    this.cache.clear(); this.cacheBytes = 0;
    if (fs.existsSync(target)) throw new Error(t("Le dossier existe encore après suppression."));
    if (wasCurrent) fs.unlinkSync(path.join(this.root,'current'));
    return wasCurrent;
  }
}
module.exports={ArchiveStore};
