const { t } = require("./media/i18n");
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
class ArchiveStore {
  constructor(workspace, dataRoot = process.env.OVH_AGENT_DATA_ROOT || path.join(require('os').homedir(), '.ovh-agent')) {
    this.session = crypto.createHash('sha256').update(fs.realpathSync(workspace)).digest('hex').slice(0,20);
    this.dataRoot = dataRoot;
    this.root = path.join(dataRoot, 'archive', this.session);
  }
  current() { try { return fs.readFileSync(path.join(this.root,'current'),'utf8').trim(); } catch { return ''; } }
  list() {
    if (!fs.existsSync(this.root)) return [];
    return fs.readdirSync(this.root,{withFileTypes:true}).filter(e=>e.isDirectory()).flatMap(e=>{
      try {
        const meta=JSON.parse(fs.readFileSync(path.join(this.root,e.name,'meta.json'),'utf8'));
        let task; try { task=JSON.parse(fs.readFileSync(path.join(this.root,e.name,'task-state.json'),'utf8')); } catch {}
        if (!task) {
          try {
            let pending=false;
            for (const line of fs.readFileSync(path.join(this.root,e.name,'events.jsonl'),'utf8').split('\n')) {
              let event; try { event=JSON.parse(line); } catch { continue; }
              if (event.type==='user') pending=true;
              if (event.type==='response') pending=false;
            }
            if (pending) task={status:'interrupted'};
          } catch {}
        }
        return [{...meta,id:e.name,current:e.name===this.current(),resumable:['running','interrupted'].includes(task?.status)}];
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
    if (fs.existsSync(target)) throw new Error(t("Le dossier existe encore après suppression."));
    if (wasCurrent) fs.unlinkSync(path.join(this.root,'current'));
    return wasCurrent;
  }
}
module.exports={ArchiveStore};
