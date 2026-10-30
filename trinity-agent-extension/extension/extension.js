const { svg: uiIcon } = require("./media/icons.min.js");
const vscode = require("vscode");
const i18n = require("./media/i18n");
const { t } = i18n;
i18n.setLanguage(i18n.resolveLanguage(process.env.OVH_AGENT_LANG, [vscode.env.language]));
const { ArchiveStore, ArchiveReader } = require("./archive-store");
const { ProjectStore, assertStoragePath, readStorageFile } = require("./project-store");
const { acquireWindowLock, releaseWindowLock } = require("./window-lock");
const { spawn, execFile } = require("child_process");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { buildChatHtml } = require("./webview-html");
const { validMessage, localLink, escapeHtml: escapeBrand } = require("./security");
const { renderMarkdown, createMarkdownRenderer } = require("./markdown-renderer");
const { BillingChannel } = require("./billing-channel");
const { NdjsonDecoder } = require("./backend-protocol");
const { resolveBackendPath } = require("./backend-path");

const BRAND_NAME = [process.env.OVH_AGENT_NAME, require("./package.json").displayName, "TRINITY Agent"]
  .map(name => String(name || "").replace(/[\x00-\x1f\x7f]/g, "").trim().slice(0, 80)).find(Boolean);


function agentPath() {
  const configured = process.env.OVH_AGENT_PATH || vscode.workspace.getConfiguration?.("ovhAgent").get("backendPath") || "/home/coder/TRINITY-DOCKER/var/docker/docker_ide/trinity-agent.sh";
  return resolveBackendPath(configured, fs.existsSync);
}
const OVH_SECRET_DIRECTORY = "/home/coder/TRINITY-DOCKER/etc/trinity/docker-trinity";
const BILLING_SECRETS = {
  project: "OVH_CLOUD_PROJECT", applicationKey: "OVH_APPLICATION_KEY",
  applicationSecret: "OVH_APPLICATION_SECRET", consumerKey: "OVH_CONSUMER_KEY",
  baseUrl: "OVH_API_BASE_URL",
};
function readServerSecret(name) {
  for (const file of [path.join("/run/secrets", name), path.join(OVH_SECRET_DIRECTORY, name)]) {
    try { return fs.readFileSync(file, "utf8").trim(); }
    catch (error) { if (error.code !== "ENOENT") throw error; }
  }
  return "";
}
function persistServerSecret(name, value) {
  const secret = String(value || "").trim();
  if (!secret || /[\r\n\0]/.test(secret)) throw new Error(t("Secret OVH invalide."));
  const file = path.join(OVH_SECRET_DIRECTORY, name);
  // Preserve the inode used by Docker's bind-mounted file secrets.
  const fd = fs.openSync(file, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_TRUNC | fs.constants.O_NOFOLLOW, 0o600);
  try {
    fs.fchmodSync(fd, 0o600);
    fs.writeFileSync(fd, secret + "\n");
    fs.fsyncSync(fd);
  } finally { fs.closeSync(fd); }
  return secret;
}
function inferenceProvider() {
  return vscode.workspace.getConfiguration?.("ovhAgent")?.get?.("provider", process.env.OVH_AGENT_PROVIDER || "ovhcloud") === "deepseek" ? "deepseek" : "ovhcloud";
}
function inferenceSecretName() { return inferenceProvider() === "deepseek" ? "DEEPSEEK_API_KEY" : "OVH_AI_API_KEY"; }
function readPersistentKey() { return readServerSecret(inferenceSecretName()); }
function persistKey(value) { return persistServerSecret(inferenceSecretName(), value); }


class OvhAgentViewProvider {
  constructor(extensionUri, secrets, state) {
    this.state = state;
    const initial = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || require("os").homedir();
    this.initialWorkspace = initial;
    this.workspace = initial;
    this.extensionUri = extensionUri;
    this.secrets = secrets;
    this.view = undefined;
    this.agent = undefined;
    this.decoder = new NdjsonDecoder();
    this.responseText = "";
    this.renderMarkdown = createMarkdownRenderer();
    this.billingChannel = new BillingChannel();
    this.startPromise = undefined;
    this.deleting = false;
    this.providerRestartPending = false;
    this.pendingModel = state?.get?.("ovhAgent.pendingModel") || undefined;
    this.pendingArchiveId = undefined;
    this.store = new ArchiveStore(this.workspace);
    try {
      const remembered=state?.get("ovhAgent.projectRoot") ?? new ProjectStore(this.store.dataRoot).active(initial)?.root;
      if(remembered && remembered!==initial) {
        const archive=new ArchiveStore(remembered,this.store.dataRoot);
        // Only a saved conversation can restore a project context, never a folder selection alone.
        const current = archive.current();
        if (/^[0-9A-Za-z_-]+$/.test(current) && archive.readCached(path.join(archive.root,current,"meta.json"))) {this.workspace=remembered;this.store=archive;}
      }
    } catch {}
    this.newConversationProject = state?.get("ovhAgent.newConversationProject");
  }

  async resolveWebviewView(webviewView) {
    this.view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")],
    };
    webviewView.webview.onDidReceiveMessage(async (message) => {
      if (!validMessage(message)) return;
      if (this.view !== webviewView) return;
      if (message.type === "retryWindow") { await this.openWindow(webviewView); return; }
      if (!this.windowLock) return;
      if (message.type === "prompt") await this.sendPrompt(message.text, message.fullAccess, message.model, message.images);
      if (message.type === "followup") await this.sendControl({
        type: "followup", action: message.action, conversation: message.conversation,
        id: message.id, text: message.text, mode: message.mode, target: message.target, position: message.position,
        full_access: Boolean(message.fullAccess), model: message.model,
      });
      if (message.type === "clear") await this.clearConversation();
      if (message.type === "cancel") await this.stopAgent();
      if (message.type === "setPerformance") await this.setPerformance(message.mode);
      if (message.type === "selectProject") await this.selectProject();
      if (message.type === "clearProjectSelection") await this.clearConversationFolder();
      if (message.type === "openProject") await this.openProject(message.project, message.conversation);
      if (message.type === "newProjectConversation") await this.newProjectConversation(message.project);
      if (message.type === "openProjectDocument") await this.openProjectDocument(message);
      if (message.type === "assignProject") await this.assignProject(message);
      if (["moveProject","moveConversation"].includes(message.type)) this.organizeArchives(message);
      if (message.type === "updateProject") await this.updateProject(message);
      if (message.type === "configureKey") await this.configureKey();
      if (message.type === "billing") await this.refreshBilling(true);
      if (message.type === "billingRefresh") await this.refreshBilling(false);
      if (message.type === "approval") this.sendApproval(message.id, message.allow);
      if (message.type === "openLink") await this.openLink(message.href);
      if (message.type === "listArchives") this.requestArchivesRefresh();
      if (message.type === "listModels") await this.sendControl({ type: "list_models", refresh: Boolean(message.refresh) });
      if (message.type === "selectModel") await this.selectModel(String(message.model || ""), String(message.provider || ""));
      if (message.type === "deleteArchiveConfirmed") await this.deleteArchive(message.id, message.source);
      if (message.type === "deleteProjectConfirmed") await this.deleteProject(message.project);
      if (message.type === "resumeArchive") await this.sendControl({
        type: "resume_archive", id: message.id, full_access: message.fullAccess, model: message.model,
      });
      if (message.type === "loadArchive") await this.sendControl({ type: "load_archive", id: message.id });
      if (message.type === "snapshot" && !this.deleting && this.agent && message.conversation && message.conversation === this.store.current()) await this.sendControl({
        type: "snapshot",
        conversation: message.conversation,
        html: String(message.html || ""),
        full_access: Boolean(message.fullAccess),
        model: String(message.model || "auto-balanced"),
        archive_revision: message.archive_revision,
      });
      if (message.type === "webviewReady") { i18n.setLanguage(i18n.resolveLanguage(process.env.OVH_AGENT_LANG, [message.language, vscode.env.language])); this.showArchives(); void this.refreshBilling(false); }
    });
    webviewView.onDidDispose(() => {
      if (this.view !== webviewView) return;
      this.view = undefined;
      void this.closeWindow();
    });
    await this.openWindow(webviewView);
  }

  async openWindow(view) {
    if (this.windowOpening) return this.windowOpening;
    this.windowOpening = (async () => {
      if (this.windowClosing) await this.windowClosing;
      if (this.view !== view) return;
      let errorText = "";
      try { this.windowLock ||= await acquireWindowLock(this.store.dataRoot); }
      catch (error) { errorText = error.message; }
      if (this.view !== view) {
        await releaseWindowLock(this.windowLock);
        this.windowLock = undefined;
        return;
      }
      view.webview.html = this.windowLock ? this.getHtml(view.webview) : this.getLockedHtml(errorText);
    })().finally(() => { this.windowOpening = undefined; });
    return this.windowOpening;
  }

  closeWindow() {
    if (this.windowClosing) return this.windowClosing;
    this.windowClosing = (async () => {
      await this.stopAgent(false);
      this.renderMarkdown.clear();
      clearTimeout(this.archiveRefreshTimer);this.archiveRefreshTimer=undefined;
      const lock = this.windowLock;
      this.windowLock = undefined;
      await releaseWindowLock(lock);
    })().finally(() => { this.windowClosing = undefined; });
    return this.windowClosing;
  }

  getLockedHtml(errorText = "") {
    const nonce = crypto.randomBytes(16).toString("hex");
    const title = escapeBrand(t("Agent déjà ouvert"));
    const message = escapeBrand(errorText || t("Une autre fenêtre utilise déjà {0}. Ferme l’autre fenêtre VS Code, puis réessaie ici.", BRAND_NAME));
    return `<!DOCTYPE html><html><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';"><style nonce="${nonce}">body { padding: 20px; color: var(--vscode-foreground); background: var(--vscode-sideBar-background); font: 13px/1.5 var(--vscode-font-family); } button { padding: 6px 12px; border: 0; border-radius: 4px; color: var(--vscode-button-foreground); background: var(--vscode-button-background); cursor: pointer; } button:not(:disabled):hover { background: var(--vscode-button-hoverBackground); } button:focus-visible { outline: 2px solid var(--vscode-focusBorder); outline-offset: 2px; } button:disabled { opacity: .5; cursor: default; }</style></head><body><h2>${title}</h2><p>${message}</p><button id="retry">${escapeBrand(t("Réessayer"))}</button><script nonce="${nonce}">const vscode=acquireVsCodeApi(); document.getElementById('retry').addEventListener('click',()=>{document.getElementById('retry').disabled=true;vscode.postMessage({type:'retryWindow'});});</script></body></html>`;
  }

  post(message) {
    if (this.view) this.view.webview.postMessage(message);
  }

  async editProfile() {
    const file = path.join(this.store.dataRoot, "MASTER.md");
    assertStoragePath(file);
    fs.mkdirSync(this.store.dataRoot, {recursive:true, mode:0o700});
    if (!fs.existsSync(file)) fs.writeFileSync(file, "# Contexte utilisateur\n\nPréférences et contraintes communes à tous les projets. Ne pas enregistrer de secrets.\n", {flag:"wx",mode:0o600});
    await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(file));
  }

  pickProject(items, projects) {
    return new Promise(resolve => {
      const picker = vscode.window.createQuickPick();
      picker.title = t("Projet actif de l’agent");
      picker.placeholder = t("Choisir un projet ou coller un chemin de dossier, puis Entrée");
      picker.matchOnDescription = true;
      picker.items = items;
      const typedPath = () => {
        let value = picker.value.trim();
        if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) value = value.slice(1,-1);
        if (value.startsWith('~/')) value = path.join(require('os').homedir(), value.slice(2));
        return path.isAbsolute(value) || /^\.\.?\//.test(value) ? path.resolve(this.workspace,value) : undefined;
      };
      const subscriptions = [];
      let finished = false;
      const finish = selection => {
        if (finished) return;
        finished = true;
        for (const subscription of subscriptions) subscription.dispose();
        picker.dispose();
        resolve(selection);
      };
      subscriptions.push(picker.onDidChangeValue(() => {
        const root = typedPath();
        if (root) {
          const entry = {label:t("Ouvrir ce dossier"),description:root,root,alwaysShow:true};
          picker.items = [entry];
          picker.activeItems = [entry];
        } else picker.items = items;
      }));
      subscriptions.push(picker.onDidAccept(() => {
        const root = typedPath();
        if (root) {
          try { finish({root:projects.validate(root)}); }
          catch (error) {
            picker.items = [{label:t("Dossier inaccessible ou non autorisé"),description:root,detail:String(error.message),alwaysShow:true,invalid:true}];
            picker.activeItems = [];
          }
          return;
        }
        const selected = picker.selectedItems[0] || picker.activeItems[0];
        if (selected && !selected.invalid) finish(selected);
      }));
      subscriptions.push(picker.onDidHide(() => finish(undefined)));
      picker.show();
    });
  }

  async selectProject() {
    if (!this.windowLock) {
      vscode.window.showInformationMessage(t("Ouvre la fenêtre Agent active pour choisir un projet."));
      return;
    }
    if (this.deleting || this.busy || this.switchingProject || this.startPromise) {
      vscode.window.showInformationMessage(t("Termine ou arrête la tâche avant de changer de projet."));
      return;
    }
    this.switchingProject = true;
    try {
      const projects = new ProjectStore(this.store.dataRoot);
      const selected = await this.pickProject([
        {label:t("Ouvrir un dossier existant"), action:"open"},
        {label:t("Créer un projet"), action:"create"},
        ...(this.store.current() ? [{label:t("Classer cette conversation dans un projet"), action:"assign"}] : []),
        {label:t("Modifier mon contexte utilisateur"), action:"profile"},
        {label:t("Modifier la mémoire du projet"), action:"memory"},
        {label:t("Agréger les mémoires des projets"), action:"aggregate"},
        ...projects.list().map(p=>({label:p.name, description:p.root, root:p.root})),
        ...(vscode.workspace.workspaceFolders || []).map(p=>({label:p.name || path.basename(p.uri.fsPath),description:p.uri.fsPath,root:p.uri.fsPath})),
      ], projects);
      if (!selected) return;
      if (selected.action === "assign") {await this.assignProject({source:this.store.session,conversation:this.store.current()});return;}
      if (selected.action === "profile") { await this.editProfile(); return; }
      if (selected.action === "memory") {
        const folder=path.join(this.store.dataRoot,"memory/projects",this.store.session);
        assertStoragePath(folder);
        fs.mkdirSync(folder,{recursive:true,mode:0o700});
        const file=path.join(folder,"NOTES.md");
        assertStoragePath(file);
        if(!fs.existsSync(file))fs.writeFileSync(file,"# Notes durables du projet\n\nDécisions et faits sourcés. Ne pas enregistrer de secrets.\n",{flag:"wx",mode:0o600});
        await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(file));
        return;
      }
      if (selected.action === "aggregate") {
        const entries=new Map(projects.catalog().map(p=>[p.id,{label:p.name,description:p.root,id:p.id,picked:p.id===this.store.session}]));
        entries.set(this.store.session,{label:path.basename(this.workspace),description:this.workspace,id:this.store.session,picked:true});
        const archive=path.join(this.store.dataRoot,"archive");
        if(fs.existsSync(archive))for(const dir of fs.readdirSync(archive,{withFileTypes:true})) {
          if(dir.isDirectory() && /^[a-f0-9]{20}$/.test(dir.name) && !entries.has(dir.name)) {
            let name=dir.name;
            try {name=JSON.parse(readStorageFile(path.join(this.store.dataRoot,"memory/projects",dir.name,"project.json"),"utf8")).workspace || name;} catch {}
            entries.set(dir.name,{label:name,description:dir.name,id:dir.name});
          }
        }
        const picked=await vscode.window.showQuickPick([...entries.values()],{canPickMany:true,title:t("Projets à agréger en Markdown")});
        if(picked?.length)await this.sendControl({type:"memory_export",projects:picked.map(p=>p.id)});
        return;
      }
      let entry;
      if (selected.root) entry = projects.register(selected.root);
      else {
        const picked = await vscode.window.showOpenDialog({canSelectFiles:false,canSelectFolders:true,canSelectMany:false,title:selected.action === "create" ? t("Dossier parent du projet") : t("Dossier du projet")});
        if (!picked?.length) return;
        if (selected.action === "create") {
          const name = await vscode.window.showInputBox({title:t("Nom du projet")});
          if (!name) return;
          entry = projects.create(picked[0].fsPath, name.trim());
        } else entry = projects.register(picked[0].fsPath);
      }
      if (selected.action === "attach") {
        await this.stopAgent(false);
        entry = projects.attach(this.workspace, this.store.current(), entry.root);
      }
      // Selection chooses the destination of the next conversation, not the current task.
      await this.selectConversationFolder(entry);
      this.showArchives();
    } catch (error) {
      vscode.window.showErrorMessage(String(error.message));
    } finally {
      this.switchingProject = false;
    }
  }

  async selectConversationFolder(entry) {
    this.newConversationProject = entry.id;
    await this.state?.update("ovhAgent.newConversationProject", entry.id);
    this.post({type:"project_selected",project:entry.id});
  }

  async clearConversationFolder() {
    this.newConversationProject = undefined;
    await this.state?.update("ovhAgent.newConversationProject", undefined);
    this.post({type:"project_selected",project:null});
  }

  async newProjectConversation(id) {
    if (!this.windowLock || this.deleting || this.clearingConversation) return;
    const entry = new ProjectStore(this.store.dataRoot).catalog().find(project => project.id === id);
    if (!entry) {
      this.post({type:"error", text:t("Le dossier sélectionné n’existe plus. Sélectionne un dossier avant de créer la conversation.")});
      return;
    }
    await this.selectConversationFolder(entry);
    await this.clearConversation();
  }

  async activateProject(entry, conversation) {
    if (conversation && entry.root === this.workspace) {
      // Navigating this project's history does not require restarting Python.
      await this.sendControl({type:"load_archive",id:conversation});
      return;
    }
    await this.stopAgent(false);
    this.busy = false;
    const dataRoot=this.store.dataRoot;
    new ProjectStore(dataRoot).activate(this.initialWorkspace,entry.root,{register:!conversation});
    this.workspace=entry.root;
    this.store=new ArchiveStore(entry.root,dataRoot);
    await this.state?.update("ovhAgent.projectRoot",entry.root);
    // Keep the webview and expanded history folders alive while switching backends.
    if (!conversation) await this.ensureAgent();
    this.showArchives();
    if(conversation)await this.sendControl({type:"load_archive",id:conversation});
  }

  async openProject(id, conversation) {
    if(!this.windowLock) {this.post({type:"archive_open_failed",id:conversation});return;}
    if((conversation && this.busy) || this.switchingProject || this.startPromise || this.deleting) {
      this.post({type:"archive_open_failed",id:conversation});
      vscode.window.showInformationMessage(t("Termine ou arrête la tâche avant de changer de projet."));return;
    }
    this.switchingProject=true;
    try {
      const projects=new ProjectStore(this.store.dataRoot);
      const entry=id===this.store.session ? {id,root:this.workspace} : projects.catalog().find(p=>p.id===id);
      if(!entry)throw new Error(t("Projet introuvable."));
      if(conversation && !(entry.root===this.workspace ? this.store : new ArchiveStore(entry.root,this.store.dataRoot)).has(conversation))throw new Error(t("Conversation introuvable."));
      if(conversation)await this.activateProject(entry,conversation);
      else {projects.register(entry.root);await this.selectConversationFolder(entry);this.showArchives();}
    } catch(error) {this.post({type:"archive_open_failed",id:conversation,text:String(error.message)});vscode.window.showErrorMessage(String(error.message));}
    finally {this.switchingProject=false;}
  }

  async openProjectDocument(message) {
    try {
      const projects=new ProjectStore(this.store.dataRoot);
      const entry=projects.list().find(p=>p.id===message.project) || projects.catalog().find(p=>p.id===message.project) || (message.project===this.store.session ? {id:this.store.session,root:this.workspace} : undefined);
      if(!entry)throw new Error(t("Projet introuvable."));
      const file=projects.document(entry,message.name,message.scope);
      await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(file));
    } catch(error) {vscode.window.showErrorMessage(String(error.message));}
  }

  async assignProject(message) {
    if(this.deleting || !this.windowLock)return;
    try {
      const projects=new ProjectStore(this.store.dataRoot);
      const source=projects.catalog().find(p=>p.id===message.source) || (message.source===this.store.session?projects.identity(this.workspace):undefined);
      const conversation=source&&new ArchiveStore(source.root,this.store.dataRoot).list().find(item=>item.id===message.conversation);
      if(!conversation)throw new Error(t("Conversation introuvable."));
      const current=projects.assignedProject(source,conversation);
      const selected=await vscode.window.showQuickPick([
        {label:t("Aucun projet"),id:null},
        ...projects.list().map(p=>({label:p.name,description:p.root,id:p.id,picked:p.id===current})),
        {label:t("Choisir un dossier de projet…"),action:"register"},
      ],{title:t("Classer cette conversation dans un projet")});
      if(!selected)return;
      let target=selected.id;
      if(selected.action==="register") {
        const picked=await vscode.window.showOpenDialog({canSelectFiles:false,canSelectFolders:true,canSelectMany:false,title:t("Dossier du projet")});
        if(!picked?.length)return;
        target=projects.register(picked[0].fsPath).id;
      }
      projects.assign(message.source,message.conversation,target);
      this.showArchives();
    } catch(error) {vscode.window.showErrorMessage(String(error.message));}
  }

  async updateProject(message) {
    if(this.deleting)return;
    try {
      const project=new ProjectStore(this.store.dataRoot).update(message.project,message);
      if (project.id !== message.project) {
        if (this.newConversationProject === message.project) {
          this.newConversationProject = project.id;
          await this.state?.update("ovhAgent.newConversationProject", project.id);
        }
        if (this.pendingConversationProject === message.project) this.pendingConversationProject = project.id;
      }
      this.post({type:'project_updated',project,previousId:message.project,requestId:message.requestId});
    } catch(error) {
      this.post({type:'project_update_failed',project:message.project,requestId:message.requestId,text:String(error.message)});
    }
    this.showArchives();
  }

  organizeArchives(message) {
    if(this.deleting || !this.windowLock || !validMessage(message))return;
    try {
      const projects=new ProjectStore(this.store.dataRoot);
      if(message.type==='moveProject')projects.reorder(message.source,message.target,message.position);
      else if(message.type==='moveConversation') {
        const source=projects.catalog().find(entry=>entry.id===message.source)
          || (message.source===this.store.session?projects.identity(this.workspace):undefined);
        if(!source || !new ArchiveStore(source.root,this.store.dataRoot).list().some(item=>item.id===message.conversation))throw new Error(t('Conversation introuvable.'));
        projects.assign(message.source,message.conversation,message.target);
      } else return;
      this.post({type:'archives_organized'});
    } catch(error) {
      this.post({type:'archive_organization_failed',text:t('Classement impossible : {0}',error.message)});
    }
    this.showArchives();
  }

  async configureKey() {
    const apiKey = await vscode.window.showInputBox({
      title: inferenceProvider() === "deepseek" ? "DeepSeek API key" : t("Clé OVHcloud AI Endpoints"),
      prompt: t("La clé sera conservée sur le serveur dans un fichier privé, même après redémarrage."),
      password: true,
      ignoreFocusOut: true,
    });
    if (!apiKey) return false;
    try {
      persistKey(apiKey);
    } catch {
      this.post({ type: "error", text: t("Impossible d’enregistrer la clé API sur le serveur. Vérifie les droits du dossier de secrets serveur.") });
      return false;
    }
    await this.secrets.delete(inferenceProvider() === "deepseek" ? "ovhAgent.deepseekApiKey" : "ovhAgent.apiKey");
    this.stopAgent(false);
    this.post({ type: "configured" });
    return true;
  }

  async billingCredentials() {
    const credentials = Object.fromEntries(Object.entries(BILLING_SECRETS).map(([key, name]) => [key, readServerSecret(name)]));
    if (!credentials.project || !credentials.applicationKey || !credentials.applicationSecret || !credentials.consumerKey) return undefined;
    credentials.baseUrl ||= "https://eu.api.ovh.com/1.0";
    return credentials;
  }

  async configureBilling() {
    if (inferenceProvider() === "deepseek") return this.refreshBilling(false);
    const region = await vscode.window.showQuickPick([
      { label: "Europe (OVHcloud)", description: "eu.api.ovh.com", baseUrl: "https://eu.api.ovh.com/1.0" },
      { label: "Canada (OVHcloud)", description: "ca.api.ovh.com", baseUrl: "https://ca.api.ovh.com/1.0" },
      { label: t("États-Unis (OVHcloud US)"), description: "api.us.ovhcloud.com", baseUrl: "https://api.us.ovhcloud.com/1.0" },
    ], { title: t("Facturation OVHcloud · région du compte"), ignoreFocusOut: true });
    if (!region) return false;
    const credentialAction = await vscode.window.showInformationMessage(
      t("Le solde requiert des identifiants API OVHcloud distincts de la clé AI Endpoints, avec des droits GET uniquement sur usage et credit."),
      { modal: true },
      t("J’ai les identifiants"),
      t("Créer les identifiants"),
    );
    if (credentialAction === t("Créer les identifiants")) {
      const origin = region.baseUrl.replace(/\/1\.0$/, "");
      const scopes = "GET=/cloud/project/*/usage/*&GET=/cloud/project/*/credit&GET=/cloud/project/*/credit/*";
      await vscode.env.openExternal(vscode.Uri.parse(`${origin}/createToken/?${scopes}`));
      vscode.window.showInformationMessage(t("Crée les identifiants en lecture seule, puis reclique sur l’icône du solde dans {0}.", BRAND_NAME));
      return false;
    }
    if (credentialAction !== t("J’ai les identifiants")) return false;
    const project = await vscode.window.showInputBox({
      title: t("Facturation OVHcloud · 1/4"),
      prompt: t("ID du projet Public Cloud qui porte AI Endpoints."),
      placeHolder: t("Identifiant du projet Public Cloud"),
      ignoreFocusOut: true,
      validateInput: (value) => value.trim() ? undefined : t("L’identifiant du projet est requis."),
    });
    if (!project) return false;
    const applicationKey = await vscode.window.showInputBox({
      title: t("Facturation OVHcloud · 2/4"),
      prompt: t("Application Key OVH avec droits GET sur usage et credit."),
      password: true,
      ignoreFocusOut: true,
    });
    if (!applicationKey) return false;
    const applicationSecret = await vscode.window.showInputBox({
      title: t("Facturation OVHcloud · 3/4"),
      prompt: t("Application Secret OVH. Stockage dans les secrets serveur Docker."),
      password: true,
      ignoreFocusOut: true,
    });
    if (!applicationSecret) return false;
    const consumerKey = await vscode.window.showInputBox({
      title: t("Facturation OVHcloud · 4/4"),
      prompt: t("Consumer Key OVH en lecture seule."),
      password: true,
      ignoreFocusOut: true,
    });
    if (!consumerKey) return false;
    const credentials = { project, applicationKey, applicationSecret, consumerKey, baseUrl: region.baseUrl };
    for (const [key, name] of Object.entries(BILLING_SECRETS)) persistServerSecret(name, credentials[key]);
    this.stopAgent(false);
    this.post({ type: "billing_configured" });
    await this.ensureAgent();
    return true;
  }

  cachedBilling(provider) {
    try {
      const cache = this.store.readCached(path.join(this.store.root,
        provider === "deepseek" ? "billing-deepseek.json" : "billing.json"));
      if (!cache || Array.isArray(cache) || !cache.available || !Number.isFinite(cache.credit_available)) return;
      const result = {...cache, provider};
      if (provider === "deepseek") {
        if (cache.provider !== provider || cache.schema_version !== 2 || cache.currency !== "EUR" ||
            cache.credit_currency !== "EUR" || !readServerSecret("DEEPSEEK_API_KEY")) return;
      } else {
        const project = readServerSecret("OVH_CLOUD_PROJECT");
        if (cache.schema_version !== 2 || !project || cache.project !== project ||
            (cache.provider && cache.provider !== provider)) return;
        if (!Object.values(BILLING_SECRETS).filter(name => name !== "OVH_API_BASE_URL").every(readServerSecret)) return;
        delete result.credit_remaining;
        delete result.credit_remaining_display;
        delete result.balance_error;
        result.balance_estimated = false;
        const currency = String(cache.currency || "").toUpperCase();
        const creditCurrency = String(cache.credit_currency || cache.currency || "").toUpperCase();
        if (currency && currency === creditCurrency && Number.isFinite(cache.current_usage)) {
          result.credit_remaining = cache.credit_available - cache.current_usage;
          result.balance_estimated = true;
        }
      }
      const age = Date.now() / 1000 - cache.cached_at;
      if (!Number.isFinite(age) || age < 0) return;
      const ttl = Number(cache.refresh_seconds) || Number(process.env.OVH_AGENT_BILLING_CACHE_SECONDS) || 60;
      result.stale = Boolean(cache.stale) || age >= Math.max(1, ttl);
      return result;
    } catch { /* A missing or invalid cache never prevents a live refresh. */ }
  }

  refreshBilling(configureIfMissing = false) {
    const provider = inferenceProvider();
    if (this.billingPromise && this.billingProvider === provider) return this.billingPromise;
    this.billingProvider = provider;
    const cached = this.cachedBilling(provider);
    this.post({ ...(cached || {configured: true, available: false, pending: true}),
      type: "billing_status", provider, silent: true });
    let pending;
    pending = (async () => {
      // The yield also assigns the request identity before a synchronous failure.
      await Promise.resolve();
      const current = () => this.billingPromise === pending && inferenceProvider() === provider;
      try {
        const agent = this.agent;
        let result;
        if (agent && agent.provider === provider && !agent.killed && agent.stdin.writable) {
          result = await this.billingChannel.request(payload => agent.stdin.write(JSON.stringify(payload)+"\n"), configureIfMissing);
        } else {
          const credentials = provider === "ovhcloud" ? await this.billingCredentials() : undefined;
          const key = provider === "deepseek" ? readServerSecret("DEEPSEEK_API_KEY") : undefined;
          if (provider === "deepseek" ? !key : !credentials) {
            if (current()) {
              this.post({type: "billing_status", provider, configured: false, available: false, silent: true});
              if (configureIfMissing && provider === "ovhcloud") await this.configureBilling();
            }
            return;
          }
          const workspace = this.workspace;
          result = await new Promise((resolve, reject) => {
            execFile(agentPath(), ["--billing-status"], {
              cwd: workspace, timeout: 30000, maxBuffer: 1024 * 1024,
              env: {...process.env, OVH_AGENT_WORKSPACE: workspace, OVH_AGENT_PROVIDER: provider,
                ...(provider === "deepseek" ? {DEEPSEEK_API_KEY: key} : {
                  OVH_APPLICATION_KEY: credentials.applicationKey, OVH_APPLICATION_SECRET: credentials.applicationSecret,
                  OVH_CONSUMER_KEY: credentials.consumerKey, OVH_CLOUD_PROJECT: credentials.project,
                  OVH_API_BASE_URL: credentials.baseUrl,
                })},
            }, (error, stdout) => {
              if (error) return reject(new Error(t("L’API de facturation ne répond pas.")));
              try { resolve(JSON.parse(stdout)); } catch { reject(new Error(t("Réponse de facturation illisible."))); }
            });
          });
        }
        if (!current()) return;
        if (!result || typeof result !== "object" || Array.isArray(result) ||
            (result.provider && result.provider !== provider) || (provider === "deepseek" && result.provider !== provider)) {
          throw new Error(t("Réponse de facturation illisible."));
        }
        this.post({...result, type: "billing_status", provider, silent: !configureIfMissing});
        if (!result.configured && configureIfMissing && provider === "ovhcloud") await this.configureBilling();
      } catch (error) {
        if (current()) this.post({...(cached ? {...cached, stale: true} : {configured: true, available: false}),
          type: "billing_status", provider, error: error.message, silent: true});
      }
    })().finally(() => { if (this.billingPromise === pending) this.billingPromise = undefined; });
    this.billingPromise = pending;
    return pending;
  }

  requestArchivesRefresh() {
    // Backend archive notifications and the UI idle notification often arrive
    // together. Scan once at the end of the burst, without caching directory state.
    if(!this.archiveRefreshTimer) this.archiveRefreshTimer=setTimeout(()=>{
      this.archiveRefreshTimer=undefined;this.showArchives();
    },75);
  }

  showArchives() {
    clearTimeout(this.archiveRefreshTimer);this.archiveRefreshTimer=undefined;
    if (this.disposed) return Promise.resolve();
    this.archiveRefreshRequested = true;
    if (this.archivesPromise) return this.archivesPromise;
    this.archivesPromise = (async () => {
      while (this.archiveRefreshRequested && !this.disposed) {
        this.archiveRefreshRequested = false;
        const request = {workspace:this.workspace, dataRoot:this.store.dataRoot, selected:this.newConversationProject};
        try {
          this.archiveReader ||= new ArchiveReader();
          const result = await this.archiveReader.read(request);
          if (this.disposed) return;
          if (request.workspace !== this.workspace || request.dataRoot !== this.store.dataRoot || request.selected !== this.newConversationProject) {
            this.archiveRefreshRequested = true;
            continue;
          }
          this.post(result);
        } catch(error) {
          if (!this.disposed) this.post({type:"archive_delete_failed",text:t("Historique indisponible : {0}",error.message)});
        }
      }
    })().finally(() => {this.archivesPromise=undefined;});
    return this.archivesPromise;
  }

  async stopArchiveWriter() {
    const agent=this.agent;
    if(!agent)return;
    await new Promise((resolve,reject)=>{
      if(agent.exitCode!==null || agent.signalCode!==null)return resolve();
      const exited=()=>{clearTimeout(timer);resolve();};
      const timer=setTimeout(()=>{agent.removeListener('exit',exited);reject(new Error(t('L’agent ne s’est pas arrêté ; suppression annulée.')));},5000);
      agent.once('exit',exited);agent.kill('SIGTERM');
    });
    if(this.agent===agent)this.agent=undefined;
    this.busy=false;
  }

  async deleteProject(project) {
    if(this.deleting || !this.windowLock || !validMessage({type:'deleteProjectConfirmed',project}))return;
    if(this.switchingProject || this.clearingConversation) {
      this.post({type:'project_delete_failed',project,text:t('Attends la fin du changement de conversation avant de supprimer ce dossier.')});return;
    }
    this.deleting=true;
    this.post({type:'project_delete_pending',project});
    try {
      if(this.startPromise)await this.startPromise;
      this.archiveReader ||= new ArchiveReader();
      const writer=this.agent?{source:this.store.session,id:this.store.current()}:undefined;
      const request={workspace:this.workspace,dataRoot:this.store.dataRoot,project,writer};
      const plan=await this.archiveReader.read({...request,action:'prepareProjectDeletion'});
      if(writer && plan.targets.some(item=>item.source===writer.source && item.id===writer.id))await this.stopArchiveWriter();
      const result=await this.archiveReader.read({...request,action:'deleteProject',expected:plan.targets});
      for(const item of result.deleted) {
        const active=item.source===this.store.session;
        this.post({type:'archive_deleted',...item,active,was_current:active && item.was_current});
      }
      if(result.error)throw new Error(result.error);
      if(this.newConversationProject===project) {
        this.newConversationProject=undefined;
        await this.state?.update('ovhAgent.newConversationProject',undefined);
      }
      if(this.pendingConversationProject===project)this.pendingConversationProject=undefined;
      this.post({type:'project_deleted',project});
    } catch(error) {
      this.post({type:'project_delete_failed',project,text:t('Suppression impossible : {0}',i18n.message(error.message))});
    } finally {this.deleting=false;this.showArchives();}
  }

  async deleteArchive(id, source = this.store.session) {
    if (this.deleting) return;
    this.deleting = true;
    this.post({ type: "archive_delete_pending", id, source });
    try {
      if (this.startPromise) await this.startPromise;
      const active = source === this.store.session;
      let archive = this.store;
      if (!active) {
        const entry = new ProjectStore(this.store.dataRoot).catalog().find(entry => entry.id === source);
        if (!entry) throw new Error(t("Projet introuvable."));
        archive = new ArchiveStore(entry.root, this.store.dataRoot);
        if (archive.session !== source) throw new Error(t("Projet introuvable."));
      }
      if (active && archive.current() === id && this.agent) {
        await this.stopArchiveWriter();
      }
      const wasCurrent = archive.remove(String(id || "")) && active;
      this.post({ type: "archive_deleted", id, source, active, was_current: wasCurrent });
      this.showArchives();
    } catch (error) {
      this.post({ type: "archive_delete_failed", id, source, text: t("Suppression impossible : {0}", i18n.message(error.message)) });
      vscode.window.showErrorMessage(t("Suppression impossible : {0}", i18n.message(error.message)));
    } finally { this.deleting = false; }
  }

  performanceMode() {
    if (this.performanceOverride) return this.performanceOverride;
    const config = vscode.workspace.getConfiguration?.('ovhAgent');
    const settings = config?.inspect?.('performanceMode');
    const mode = settings
      ? settings.workspaceFolderValue ?? settings.workspaceValue ?? settings.globalValue ?? process.env.OVH_AGENT_PERFORMANCE_MODE
      : config?.get?.('performanceMode') ?? process.env.OVH_AGENT_PERFORMANCE_MODE;
    return mode === 'max' ? 'max' : 'auto';
  }

  async setPerformance(mode) {
    if (!['auto', 'max'].includes(mode)) return;
    try {
      await vscode.workspace.getConfiguration('ovhAgent').update('performanceMode', mode, vscode.ConfigurationTarget.Global);
      this.performanceOverride = mode;
      if (this.agent && !this.agent.killed && this.agent.stdin.writable) {
        this.agent.stdin.write(JSON.stringify({type: 'set_performance', mode}) + '\n');
      } else this.post({type: 'performance_status', mode});
    } catch (error) {
      this.post({type: 'performance_error', mode: this.performanceMode(), text: String(error.message)});
    }
  }

  async ensureAgent() {
    if (!this.windowLock) return false;
    if (this.agent?.sessionBusy) await this.stopAgent(false);
    if (this.stopPromise) await this.stopPromise;
    if (!this.windowLock || this.windowClosing || this.deleting) return false;
    if (this.agent && !this.agent.killed) return true;
    if (!this.startPromise) this.startPromise = this.startAgent().finally(() => { this.startPromise = undefined; });
    return this.startPromise;
  }

  async startAgent() {
    if (!this.windowLock) return false;
    if (this.agent && !this.agent.killed) return true;
    if (!fs.existsSync(agentPath())) {
      this.post({ type: "error", text: t("Serveur TRINITY absent : configure ovhAgent.backendPath. Consulte le guide d’installation de l’extension.") });
      return false;
    }
    const provider = inferenceProvider();
    const secretName = provider === "deepseek" ? "DEEPSEEK_API_KEY" : "OVH_AI_API_KEY";
    let apiKey;
    try {
      apiKey = readServerSecret(secretName);
      if (!apiKey) {
        try { apiKey = await this.secrets.get(provider === "deepseek" ? "ovhAgent.deepseekApiKey" : "ovhAgent.apiKey"); } catch {}
        if (apiKey) persistServerSecret(secretName, apiKey);
      }
    } catch {
      this.post({ type: "error", text: t("Impossible de lire ou conserver la clé API sur le serveur.") });
      return false;
    }
    if (!apiKey) {
      const configured = await this.configureKey();
      if (!configured) {
        this.post({ type: "error", text: t("Aucune clé API n’est configurée pour ce fournisseur.") });
        return false;
      }
      apiKey = readServerSecret(secretName);
    }
    const workspace = this.workspace;
    const billing = provider === "deepseek" ? null : await this.billingCredentials();
    if (!this.windowLock || this.windowClosing || inferenceProvider() !== provider) return false;
    this.decoder.reset();
    const agent = spawn(agentPath(), ["--json-lines"], {
      cwd: workspace,
      env: {
        ...process.env,
        OVH_AGENT_WORKSPACE: workspace,
        OVH_AGENT_PERFORMANCE_MODE: this.performanceMode(),
        OVH_AGENT_PROVIDER: provider,
        ...(provider === "deepseek" ? { DEEPSEEK_API_KEY: apiKey } : {
          OVH_AI_API_KEY: apiKey, OVHCLOUD_API_KEY: apiKey,
        }),
        ...(billing ? {
          OVH_APPLICATION_KEY: billing.applicationKey,
          OVH_APPLICATION_SECRET: billing.applicationSecret,
          OVH_CONSUMER_KEY: billing.consumerKey,
          OVH_CLOUD_PROJECT: billing.project,
          OVH_API_BASE_URL: billing.baseUrl,
        } : {}),
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    agent.provider = provider;
    this.agent = agent;
    const pendingModel = this.pendingModel;
    if (pendingModel && pendingModel.provider === inferenceProvider() && pendingModel.model) {
      // Applied before the first message so the switched API runs the chosen model.
      this.pendingModel = undefined;
      void this.state?.update?.("ovhAgent.pendingModel", undefined);
      agent.stdin.write(`${JSON.stringify({ type: "set_model", model: pendingModel.model })}\n`);
    }
    agent.stdin.on?.("error", error => {
      if(this.agent !== agent) return;
      this.billingChannel.cancel(error);
      this.busy=false;
      this.clearingConversation=false;
      if (this.pendingArchiveId) {
        const id = this.pendingArchiveId; this.pendingArchiveId = undefined;
        this.post({type:"archive_open_failed",id,text:`Écriture vers le backend interrompue : ${error.message}`});
        return;
      }
      this.post({type:"error",text:`Écriture vers le backend interrompue : ${error.message}`});
    });
    agent.stdout.setEncoding("utf8");
    agent.stdout.on("data", (chunk) => { if (this.agent === agent) this.consumeOutput(chunk); });
    agent.stderr.setEncoding("utf8");
    agent.stderr.on("data", (chunk) => {
      const text = chunk.trim();
      if (text) this.post({ type: "diagnostic", text });
    });
    agent.on("error", (error) => {
      if (this.agent !== agent) return;
      this.busy = false;
      this.clearingConversation = false;
      this.billingChannel.cancel(error);
      if (this.pendingArchiveId) {
        const id = this.pendingArchiveId; this.pendingArchiveId = undefined;
        this.post({ type: "archive_open_failed", id, text: t("Impossible de démarrer {0} : {1}", BRAND_NAME, error.message) });
        if (this.agent === agent) this.agent = undefined;
        return;
      }
      this.post({ type: "error", text: t("Impossible de démarrer {0} : {1}", BRAND_NAME, error.message) });
      if (this.agent === agent) this.agent = undefined;
    });
    agent.on("exit", (code, signal) => {
      if (this.agent !== agent) return;
      this.busy = false;
      this.clearingConversation = false;
      this.billingChannel.cancel();
      this.flushDelta();
      if (this.pendingArchiveId) {
        const id = this.pendingArchiveId; this.pendingArchiveId = undefined;
        this.post({ type: "archive_open_failed", id, text: t("Impossible de charger la conversation.") });
      }
      if (code && code !== 0) {
        this.post({ type: "error", text: t("{0} s’est arrêté (code {1}{2}).", BRAND_NAME, code, signal ? `, ${signal}` : "") });
      }
      if (this.agent === agent) {
        if (!agent.sessionBusy) this.post({ type: "idle" });
        this.agent = undefined;
      }
    });
    return true;
  }

  flushDelta() {
    clearTimeout(this.deltaTimer);this.deltaTimer=undefined;
    const message=this.pendingDelta;this.pendingDelta=undefined;
    if(message) {message.html=this.renderMarkdown(this.responseText);this.post(message);}
  }

  consumeOutput(chunk) {
    const lines = this.decoder.feed(chunk, message => this.post(message));
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const message = JSON.parse(line);
        if (!message || typeof message !== "object" || Array.isArray(message) || typeof message.type !== "string") continue;
        if (this.pendingArchiveId && ["archive_loaded", "snapshot_loaded"].includes(message.type)) {
          if (message.id !== this.pendingArchiveId) continue;
          this.pendingArchiveId = undefined;
        } else if (this.pendingArchiveId && ["error", "idle", "session_busy"].includes(message.type)) {
          const id = this.pendingArchiveId; this.pendingArchiveId = undefined;
          this.post({type:"archive_open_failed",id,text:message.text});
          continue;
        }
        if(message.type === "billing_status") {
          const received=this.billingChannel.receive(message);
          // Late correlated replies must not overwrite the newer displayed balance.
          if(received || message.request_id) continue;
          message.provider ||= this.agent?.provider || "ovhcloud";
          if (message.provider !== inferenceProvider()) continue;
        }
        // A final response already contains all text: emitting the pending delta
        // first would parse, sanitize and rebuild the same DOM twice.
        if(message.type === "response") {
          clearTimeout(this.deltaTimer);this.deltaTimer=undefined;this.pendingDelta=undefined;
        } else if(message.type !== "delta") this.flushDelta();
        if(message.type === "archives") {this.requestArchivesRefresh();continue;}
        if (message.type === "memory_exported") {
          const expected=path.join(this.store.dataRoot,"memory/AGGREGATE.md");
          assertStoragePath(expected);
          if(path.resolve(message.file || "")===path.resolve(expected))vscode.workspace.openTextDocument(expected).then(doc=>vscode.window.showTextDocument(doc)).catch(error=>vscode.window.showErrorMessage(String(error.message)));
        }
        // Refresh Explorer only after a possible filesystem mutation, not after
        // every conversational/read-only response. Commands are conservative:
        // even an unsuccessful command can have changed files before failing.
        if(message.type === "file_change" || (message.type === "tool_start" &&
          ["write_file","edit_file","run_command","run_host_command","write","edit","command"].includes(message.tool))) this.explorerDirty=true;
        if (message.type === "session_busy" && this.agent) this.agent.sessionBusy = true;
        if (message.type === "status") { this.responseText = ""; this.busy = true; }
        if (["response", "error", "idle", "session_busy"].includes(message.type)) this.busy = false;
        if (message.type === "idle" && this.providerRestartPending) {
          // A provider change requested during a task waits for this idle point.
          this.providerRestartPending = false;
          void this.stopAgent(false);
        }
        if (["cleared", "error", "idle", "session_busy"].includes(message.type)) this.clearingConversation = false;
        if (message.type === "followup_ack" && message.already_started && message.running === false) this.busy = false;
        if (message.type === "delta") {
          this.responseText = (this.responseText + String(message.text || "")).slice(0, 2*1024*1024);
          this.pendingDelta = message;
          if(!this.deltaTimer) this.deltaTimer=setTimeout(()=>this.flushDelta(),50);
          continue;
        }
        if (message.type === "response") {
          this.responseText = String(message.text || "");
          message.html = this.renderMarkdown(this.responseText);
        }
        if (message.type === "error") message.html = this.renderMarkdown(`**Erreur :** ${message.text}`);
        if (message.type === "cleared") {
          message.workspace = this.workspace;
          // The backend has now created the archive directory, so assignment is safe.
          const target = this.pendingConversationProject;
          this.pendingConversationProject = undefined;
          if (target && message.conversation) {
            try { new ProjectStore(this.store.dataRoot).assign(this.store.session,message.conversation,target); }
            catch (error) { this.post({type:"diagnostic",text:`Classement de la conversation impossible : ${error.message}`}); }
          }
        }
        if (message.type === "archive_loaded") {
          message.messages = (message.messages || []).map((item) => ({
            ...item,
            html: ["response", "partial_response", "error"].includes(item.type) ? this.renderMarkdown(item.text || "") : undefined,
          }));
        }
        this.post(message);
        if (["response","error","idle"].includes(message.type) && this.explorerDirty) {
          this.explorerDirty=false;
          vscode.commands.executeCommand("workbench.files.action.refreshFilesExplorer");
        }
      } catch {
        this.post({ type: "diagnostic", text: line.slice(0, 4096) });
      }
    }
  }

  async sendPrompt(text, fullAccess, model, images) {
    if (this.switchingProject || this.clearingConversation) return;
    const prompt = String(text || "").trim();
    const attachedImages = Array.isArray(images) ? images.slice(0, 5) : [];
    if ((!prompt && !attachedImages.length) || !(await this.ensureAgent())) return;
    if (this.switchingProject || this.clearingConversation) return;
    this.responseText = "";
    this.busy = true;
    this.agent.stdin.write(`${JSON.stringify({
      type: "prompt",
      text: prompt,
      full_access: Boolean(fullAccess),
      model: String(model || "auto-balanced"),
      images: attachedImages,
    })}\n`);
  }

  sendApproval(id, allow) {
    if (!this.agent || this.agent.killed || !this.agent.stdin.writable) return;
    this.agent.stdin.write(`${JSON.stringify({ type: "approval", id: String(id), allow: Boolean(allow) })}\n`);
  }

  async sendControl(payload) {
    try {
      if (payload.type === "load_archive") {
        if (this.busy) { this.post({type:"archive_open_failed",id:payload.id}); return; }
        const {provider}=this.store.modelSelection(payload.id);
        if (provider && provider!==inferenceProvider()) {
          this.restoringProvider=true;
          try {
            await vscode.workspace.getConfiguration("ovhAgent").update("provider",provider,vscode.ConfigurationTarget.Global);
          } finally { this.restoringProvider=false; }
          await this.providerChanged();
        }
      }
      if (!(await this.ensureAgent())) {
        if (payload.type === "load_archive") this.post({type:"archive_open_failed",id:payload.id});
        return;
      }
      if (payload.type === "load_archive") this.pendingArchiveId = payload.id;
      if (payload.type === "resume_archive" || (payload.type === "followup" && ["add", "steer", "continue"].includes(payload.action))) this.busy = true;
      this.agent.stdin.write(`${JSON.stringify(payload)}\n`);
    } catch (error) {
      this.busy=false;
      this.clearingConversation=false;
      if (payload.type === "load_archive") this.pendingArchiveId = undefined;
      this.post({type:payload.type==="load_archive" ? "archive_open_failed" : "error",id:payload.type==="load_archive" ? payload.id : undefined,text:String(error.message)});
    }
  }

  async clearConversation() {
    if (this.deleting || this.clearingConversation) return;
    this.clearingConversation = true;
    let sent = false;
    try {
      const dataRoot=this.store.dataRoot;
      // An explicitly selected folder wins; browsing an old archive alone does not.
      const selected=new ProjectStore(dataRoot).list().find(entry=>entry.id===this.newConversationProject);
      if (this.newConversationProject && !selected) {
        this.post({type:"error",text:"Le dossier sélectionné n’existe plus. Sélectionne un dossier avant de créer la conversation."});
        return;
      }
      await this.stopAgent(false);
      this.busy = false;
      this.pendingConversationProject = undefined;
      this.workspace=selected?.root || this.initialWorkspace;
      this.store=new ArchiveStore(this.workspace,dataRoot);
      new ProjectStore(dataRoot).activate(this.initialWorkspace,this.workspace,{register:false});
      await this.state?.update("ovhAgent.projectRoot",this.workspace);
      if (!(await this.ensureAgent())) {
        this.post({ type: "error", text: t("Impossible de démarrer {0} : {1}", BRAND_NAME, "initialization failed") });
        return;
      }
      this.pendingConversationProject = selected?.id;
      this.agent.stdin.write(`${JSON.stringify({ type: "clear" })}\n`);
      sent = true;
    } finally {
      if (!sent) this.clearingConversation = false;
    }
  }

  async openLink(href) {
    const value = String(href || "");
    if (/^(https?:|mailto:)/i.test(value)) {
      try {
        const url = new URL(value);
        if (url.username || url.password || /[\x00-\x1f\x7f]/.test(value)) return;
        await vscode.env.openExternal(vscode.Uri.parse(url.href));
      } catch {}
      return;
    }
    const workspace = this.workspace;
    const target = localLink(value, workspace);
    if (!target) return;
    try {
      const document = await vscode.workspace.openTextDocument(target);
      await vscode.window.showTextDocument(document);
    } catch {
      vscode.window.showWarningMessage(t("Fichier introuvable : {0}", value));
    }
  }

  async providerChanged() {
    if (this.restoringProvider) return;
    const provider = inferenceProvider();
    if (this.busy || this.clearingConversation) {
      this.providerRestartPending = true;
      this.post({ type: "provider_switched", provider, pending: true });
      void this.refreshBilling(false);
      return;
    }
    this.providerRestartPending = false;
    await this.stopAgent(false);
    // The next message starts the backend with the newly selected provider.
    this.post({ type: "provider_switched", provider });
    void this.refreshBilling(false);
  }

  async selectProvider() {
    const picked = await vscode.window.showQuickPick([
      { label: "OVHcloud AI Endpoints", description: "oai.endpoints.kepler.ai.cloud.ovh.net", value: "ovhcloud" },
      { label: "DeepSeek", description: "api.deepseek.com", value: "deepseek" },
    ], { title: t("Fournisseur d’inférence"), ignoreFocusOut: true });
    if (!picked) return false;
    await vscode.workspace.getConfiguration("ovhAgent").update("provider", picked.value, vscode.ConfigurationTarget.Global);
    // The configuration listener applies the change; a same-value update is a no-op.
    return true;
  }

  async selectModel(model, provider) {
    const name = String(model || "").trim();
    if (!name) return;
    const target = provider === "deepseek" || provider === "ovhcloud" ? provider : "";
    if (!target || target === inferenceProvider()) {
      await this.sendControl({ type: "set_model", model: name });
      return;
    }
    // The panel lists both APIs: switching the provider first makes the chosen
    // model valid for the backend that will run it. A running task keeps its
    // provider until idle, then the new backend applies the pending model.
    this.pendingModel = name === "auto-balanced" ? undefined : { model: name, provider: target };
    await this.state?.update?.("ovhAgent.pendingModel", this.pendingModel);
    await vscode.workspace.getConfiguration("ovhAgent").update("provider", target, vscode.ConfigurationTarget.Global);
    // A same-value update fires no configuration event: report the switch here.
    if (this.providerRestartPending || this.busy || this.clearingConversation) {
      this.providerRestartPending = true;
      this.post({ type: "provider_switched", provider: target, pending: true });
    }
  }

  stopAgent(notify = true) {
    if (this.stopPromise) return this.stopPromise;
    const starting = this.startPromise;
    this.stopPromise = (async () => {
      if (starting) await starting;
      const agent = this.agent;
      this.billingChannel.cancel();
      this.agent = undefined;
      if (agent && agent.exitCode === null && agent.signalCode === null) {
        await new Promise(resolve => {
          const timer = setTimeout(() => agent.kill("SIGKILL"), 5000);
          agent.once("close", () => { clearTimeout(timer); resolve(); });
          agent.kill("SIGTERM");
        });
      }
      this.flushDelta();
      this.busy = false;
      if (notify) this.clearingConversation = false;
      if (notify) this.post({ type: "idle" });
    })().finally(() => { this.stopPromise = undefined; });
    return this.stopPromise;
  }

  dispose() {
    this.disposed = true;
    this.archiveReader?.dispose();
    this.view = undefined;
    this.renderMarkdown.clear();
    clearTimeout(this.archiveRefreshTimer);this.archiveRefreshTimer=undefined;
    void this.closeWindow();
  }

  getHtml(webview) {
    return buildChatHtml({extensionUri:this.extensionUri, brand:BRAND_NAME,
      performanceMode:this.performanceMode(), env:{...process.env, OVH_AGENT_PROVIDER:inferenceProvider()}, joinPath:vscode.Uri.joinPath}, webview);
  }
}

async function activate(context) {
  // Remove obsolete browser copies only after server persistence is confirmed.
  const migrated = {
    "ovhAgent.apiKey": "OVH_AI_API_KEY", "ovhAgent.cloudProject": "OVH_CLOUD_PROJECT",
    "ovhAgent.applicationKey": "OVH_APPLICATION_KEY", "ovhAgent.applicationSecret": "OVH_APPLICATION_SECRET",
    "ovhAgent.consumerKey": "OVH_CONSUMER_KEY", "ovhAgent.apiBaseUrl": "OVH_API_BASE_URL",
  };
  for (const [key, name] of Object.entries(migrated)) {
    if (readServerSecret(name)) await context.secrets.delete(key);
  }
  const provider = new OvhAgentViewProvider(context.extensionUri, context.secrets, context.workspaceState);
  const registration = vscode.window.registerWebviewViewProvider(
    "ovhAgent.launch",
    provider,
    { webviewOptions: { retainContextWhenHidden: true } },
  );
  const start = vscode.commands.registerCommand("ovhAgent.start", async () => {
    await vscode.commands.executeCommand("workbench.view.extension.ovhAgent");
  });
  const configure = vscode.commands.registerCommand("ovhAgent.configureKey", () => provider.configureKey());
  const selectProject = vscode.commands.registerCommand("ovhAgent.selectProject", async () => {
    await vscode.commands.executeCommand("workbench.view.extension.ovhAgent");
    await provider.selectProject();
  });
  const selectProvider = vscode.commands.registerCommand("ovhAgent.selectProvider", () => provider.selectProvider());
  const providerChange = vscode.workspace.onDidChangeConfiguration((event) => {
    if (!event.affectsConfiguration("ovhAgent.provider")) return;
    void provider.providerChanged();
  });
  const editProfile = vscode.commands.registerCommand("ovhAgent.editProfile", () => provider.editProfile());
  const configureBilling = vscode.commands.registerCommand("ovhAgent.configureBilling", () => provider.configureBilling());
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 90);
  status.name = BRAND_NAME;
  status.text = BRAND_NAME;
  status.tooltip = `Ouvrir le chat ${BRAND_NAME}`;
  status.command = "ovhAgent.start";
  status.show();
  context.subscriptions.push(registration, start, configure, configureBilling, selectProject, selectProvider, providerChange, editProfile, status, provider);
}

function deactivate() {}

module.exports = { activate, deactivate };
