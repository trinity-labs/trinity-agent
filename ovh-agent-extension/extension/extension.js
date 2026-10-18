const { svg: uiIcon } = require("./media/icons.min.js");
const vscode = require("vscode");
const i18n = require("./media/i18n");
const { t } = i18n;
i18n.setLanguage(i18n.resolveLanguage(process.env.OVH_AGENT_LANG, [vscode.env.language]));
const { ArchiveStore } = require("./archive-store");
const { ProjectStore } = require("./project-store");
const { acquireWindowLock, releaseWindowLock } = require("./window-lock");
const { spawn, execFile } = require("child_process");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { validMessage, localLink } = require("./security");
const MarkdownIt = require("markdown-it");
const sanitizeHtml = require("sanitize-html");

const BRAND_NAME = [process.env.OVH_AGENT_NAME, require("./package.json").displayName, "TRINITY Agent"]
  .map(name => String(name || "").replace(/[\x00-\x1f\x7f]/g, "").trim().slice(0, 80)).find(Boolean);
function escapeBrand(value) {
  return value.replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

function agentPath() {
  return process.env.OVH_AGENT_PATH || vscode.workspace.getConfiguration?.("ovhAgent").get("backendPath") || "/home/coder/TRINITY-DOCKER/var/docker/docker_ide/ovh-agent.sh";
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
function readPersistentKey() { return readServerSecret("OVH_AI_API_KEY"); }
function persistKey(value) { return persistServerSecret("OVH_AI_API_KEY", value); }

const markdown = new MarkdownIt({ html: false, linkify: true, typographer: true, breaks: true });

function renderMarkdown(source) {
  const rendered = markdown.render(String(source || ""));
  return sanitizeHtml(rendered, {
    allowedTags: [
      ...sanitizeHtml.defaults.allowedTags,
      "h1", "h2", "h3", "h4", "h5", "h6", "img", "del",
      "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    ],
    allowedAttributes: {
      a: ["href", "title", "target", "rel"],
      img: ["src", "alt", "title"],
      code: ["class"],
      th: ["align"],
      td: ["align"],
    },
    allowedSchemes: ["http", "https", "mailto"],
    allowedSchemesByTag: { img: ["https"] },
    transformTags: {
      a: sanitizeHtml.simpleTransform("a", { target: "_blank", rel: "noopener noreferrer" }),
    },
  });
}

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
    this.stdoutBuffer = "";
    this.responseText = "";
    this.startPromise = undefined;
    this.deleting = false;
    this.store = new ArchiveStore(this.workspace);
    try {
      const remembered=state?.get("ovhAgent.projectRoot") ?? new ProjectStore(this.store.dataRoot).active(initial)?.root;
      if(remembered && remembered!==initial) {
        const archive=new ArchiveStore(remembered,this.store.dataRoot);
        // Only a saved conversation can restore a project context, never a folder selection alone.
        if(archive.list().some(item=>item.current)) {this.workspace=remembered;this.store=archive;}
      }
    } catch {}
    this.projectArchives = new Map();
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
      if (message.type === "openProject") await this.openProject(message.project, message.conversation);
      if (message.type === "openProjectDocument") await this.openProjectDocument(message);
      if (message.type === "assignProject") await this.assignProject(message);
      if (["moveProject","moveConversation"].includes(message.type)) this.organizeArchives(message);
      if (message.type === "updateProject") this.updateProject(message);
      if (message.type === "configureKey") await this.configureKey();
      if (message.type === "billing") await this.refreshBilling(true);
      if (message.type === "billingRefresh") await this.refreshBilling(false);
      if (message.type === "approval") this.sendApproval(message.id, message.allow);
      if (message.type === "openLink") await this.openLink(message.href);
      if (message.type === "listArchives") this.showArchives();
      if (message.type === "listModels") await this.sendControl({ type: "list_models", refresh: Boolean(message.refresh) });
      if (message.type === "selectModel") await this.sendControl({ type: "set_model", model: String(message.model || "") });
      if (message.type === "deleteArchiveConfirmed") await this.deleteArchive(message.id, message.source);
      if (message.type === "resumeArchive") await this.sendControl({ type: "resume_archive", id: message.id });
      if (message.type === "loadArchive") await this.sendControl({ type: "load_archive", id: message.id });
      if (message.type === "snapshot" && !this.deleting && this.agent && message.conversation && message.conversation === this.store.current()) await this.sendControl({
        type: "snapshot",
        conversation: message.conversation,
        html: String(message.html || ""),
        full_access: Boolean(message.fullAccess),
        model: String(message.model || "auto-balanced"),
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
    return `<!DOCTYPE html><html><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';"><style nonce="${nonce}">body { padding: 20px; color: var(--vscode-foreground); background: var(--vscode-sideBar-background); font: 13px/1.5 var(--vscode-font-family); } button { padding: 6px 12px; border: 0; border-radius: 4px; color: var(--vscode-button-foreground); background: var(--vscode-button-background); cursor: pointer; }</style></head><body><h2>${title}</h2><p>${message}</p><button id="retry">${escapeBrand(t("Réessayer"))}</button><script nonce="${nonce}">const vscode=acquireVsCodeApi(); document.getElementById('retry').addEventListener('click',()=>{document.getElementById('retry').disabled=true;vscode.postMessage({type:'retryWindow'});});</script></body></html>`;
  }

  post(message) {
    if (this.view) this.view.webview.postMessage(message);
  }

  async editProfile() {
    const file = path.join(this.store.dataRoot, "MASTER.md");
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
    if (this.busy || this.switchingProject || this.startPromise) {
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
        fs.mkdirSync(folder,{recursive:true,mode:0o700});
        const file=path.join(folder,"NOTES.md");
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
            try {name=JSON.parse(fs.readFileSync(path.join(this.store.dataRoot,"memory/projects",dir.name,"project.json"),"utf8")).workspace || name;} catch {}
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
      // Adding a folder organizes history without changing the current conversation's context.
      this.showArchives();
    } catch (error) {
      vscode.window.showErrorMessage(String(error.message));
    } finally {
      this.switchingProject = false;
    }
  }

  async activateProject(entry, conversation) {
    await this.stopAgent(false);
    this.busy = false;
    const dataRoot=this.store.dataRoot;
    new ProjectStore(dataRoot).activate(this.initialWorkspace,entry.root,{register:!conversation});
    this.workspace=entry.root;
    this.store=new ArchiveStore(entry.root,dataRoot);
    await this.state?.update("ovhAgent.projectRoot",entry.root);
    // Keep the webview and expanded history folders alive while switching backends.
    await this.ensureAgent();
    this.showArchives();
    if(conversation)await this.sendControl({type:"load_archive",id:conversation});
  }

  async openProject(id, conversation) {
    if(!this.windowLock) {this.post({type:"archive_open_failed"});return;}
    if(this.busy || this.switchingProject || this.startPromise || this.deleting) {
      this.post({type:"archive_open_failed"});
      vscode.window.showInformationMessage(t("Termine ou arrête la tâche avant de changer de projet."));return;
    }
    this.switchingProject=true;
    try {
      const projects=new ProjectStore(this.store.dataRoot);
      const entry=projects.catalog().find(p=>p.id===id);
      if(!entry)throw new Error(t("Projet introuvable."));
      if(conversation && !new ArchiveStore(entry.root,this.store.dataRoot).list().some(item=>item.id===conversation))throw new Error(t("Conversation introuvable."));
      if(conversation)await this.activateProject(entry,conversation);
      else {projects.register(entry.root);this.showArchives();}
    } catch(error) {this.post({type:"archive_open_failed",text:String(error.message)});vscode.window.showErrorMessage(String(error.message));}
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
    if(!this.windowLock)return;
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

  updateProject(message) {
    try {
      const project=new ProjectStore(this.store.dataRoot).update(message.project,message);
      this.post({type:'project_updated',project,previousId:message.project,requestId:message.requestId});
    } catch(error) {
      this.post({type:'project_update_failed',project:message.project,requestId:message.requestId,text:String(error.message)});
    }
    this.showArchives();
  }

  organizeArchives(message) {
    if(!this.windowLock || !validMessage(message))return;
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
      title: t("Clé OVHcloud AI Endpoints"),
      prompt: t("La clé sera conservée sur le serveur dans un fichier privé, même après redémarrage."),
      password: true,
      ignoreFocusOut: true,
    });
    if (!apiKey) return false;
    try {
      persistKey(apiKey);
    } catch {
      this.post({ type: "error", text: t("Impossible d’enregistrer la clé OVH sur le serveur. Vérifie les droits du dossier de secrets serveur.") });
      return false;
    }
    await this.secrets.delete("ovhAgent.apiKey");
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

  refreshBilling(configureIfMissing = false) {
    if (this.billingPromise) return this.billingPromise;
    this.billingPromise = (async () => {
      this.post({ type: "billing_status", configured: true, available: false, pending: true, silent: true });
      try {
        const credentials = await this.billingCredentials();
        if (!credentials) {
          this.post({ type: "billing_status", configured: false, available: false, silent: true });
          if (configureIfMissing) await this.configureBilling();
          return;
        }
        const workspace = this.workspace;
        const result = await new Promise((resolve, reject) => {
          execFile(agentPath(), ["--billing-status"], {
            cwd: workspace, timeout: 30000, maxBuffer: 1024 * 1024,
            env: { ...process.env, OVH_AGENT_WORKSPACE: workspace },
          }, (error, stdout) => {
            if (error) return reject(new Error(t("L’API de facturation ne répond pas.")));
            try { resolve(JSON.parse(stdout)); } catch { reject(new Error(t("Réponse de facturation illisible."))); }
          });
        });
        this.post({ ...result, type: "billing_status", silent: !configureIfMissing });
      } catch (error) {
        this.post({ type: "billing_status", configured: true, available: false, error: error.message, silent: true });
      }
    })().finally(() => { this.billingPromise = undefined; });
    return this.billingPromise;
  }

  showArchives() {
    try {
      const registry=new ProjectStore(this.store.dataRoot);
      const entries=registry.catalog();
      if(!entries.some(p=>p.id===this.store.session))entries.unshift({id:this.store.session,name:path.basename(this.workspace),root:this.workspace});
      const assignments=registry.assignments(),items=[];
      const projects=registry.list().map(entry=>({...entry,active:entry.id===this.store.session,documents:registry.documents(entry),items:[]}));
      const groups=new Map(projects.map(entry=>[entry.id,entry]));
      for(const entry of entries) {
        const active=entry.id===this.store.session;
        let archive=this.store;
        if(!active) {
          archive=this.projectArchives.get(entry.id);
          if(!archive) {
            archive=new ArchiveStore(entry.root,this.store.dataRoot);
            if(this.projectArchives.size>=8)this.projectArchives.delete(this.projectArchives.keys().next().value);
            this.projectArchives.set(entry.id,archive);
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
      this.post({type:"archives",items,projects,archive_root:this.store.root});
    } catch(error) {this.post({type:"archive_delete_failed",text:t("Historique indisponible : {0}",error.message)});}
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
        const agent = this.agent;
        await new Promise((resolve, reject) => {
          if (agent.exitCode !== null || agent.signalCode !== null) return resolve();
          const timer = setTimeout(() => reject(new Error(t("L’agent ne s’est pas arrêté ; suppression annulée."))), 5000);
          agent.once("exit", () => { clearTimeout(timer); resolve(); });
          agent.kill("SIGTERM");
        });
        this.agent = undefined;
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
    let apiKey;
    try {
      apiKey = readPersistentKey();
      if (!apiKey) {
        try { apiKey = await this.secrets.get("ovhAgent.apiKey"); } catch {}
        if (apiKey) persistKey(apiKey);
      }
    } catch {
      this.post({ type: "error", text: t("Impossible de lire ou conserver la clé OVH sur le serveur.") });
      return false;
    }
    if (!apiKey) {
      const configured = await this.configureKey();
      if (!configured) {
        this.post({ type: "error", text: t("Aucune clé OVH n’est configurée.") });
        return false;
      }
      apiKey = readPersistentKey();
    }
    const workspace = this.workspace;
    const billing = await this.billingCredentials();
    if (!this.windowLock || this.windowClosing) return false;
    this.stdoutBuffer = "";
    const agent = spawn(agentPath(), ["--json-lines"], {
      cwd: workspace,
      env: {
        ...process.env,
        OVH_AGENT_WORKSPACE: workspace,
        OVH_AGENT_PERFORMANCE_MODE: this.performanceMode(),
        OVH_AI_API_KEY: apiKey,
        OVHCLOUD_API_KEY: apiKey,
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
    this.agent = agent;
    agent.stdout.setEncoding("utf8");
    agent.stdout.on("data", (chunk) => { if (this.agent === agent) this.consumeOutput(chunk); });
    agent.stderr.setEncoding("utf8");
    agent.stderr.on("data", (chunk) => {
      const text = chunk.trim();
      if (text) this.post({ type: "diagnostic", text });
    });
    agent.on("error", (error) => {
      if (this.agent !== agent) return;
      this.post({ type: "error", text: t("Impossible de démarrer {0} : {1}", BRAND_NAME, error.message) });
      if (this.agent === agent) this.agent = undefined;
    });
    agent.on("exit", (code, signal) => {
      if (this.agent !== agent) return;
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

  consumeOutput(chunk) {
    this.stdoutBuffer += chunk;
    const lines = this.stdoutBuffer.split("\n");
    this.stdoutBuffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const message = JSON.parse(line);
        if(message.type === "archives") {this.showArchives();continue;}
        if (message.type === "memory_exported") {
          const expected=path.join(this.store.dataRoot,"memory/AGGREGATE.md");
          if(path.resolve(message.file || "")===path.resolve(expected))vscode.workspace.openTextDocument(expected).then(doc=>vscode.window.showTextDocument(doc)).catch(error=>vscode.window.showErrorMessage(String(error.message)));
        }
        if (message.type === "session_busy" && this.agent) this.agent.sessionBusy = true;
        if (message.type === "status") { this.responseText = ""; this.busy = true; }
        if (["response", "error", "idle"].includes(message.type)) this.busy = false;
        if (message.type === "delta") {
          this.responseText += String(message.text || "");
          message.html = renderMarkdown(this.responseText);
        }
        if (message.type === "response") {
          this.responseText = String(message.text || "");
          message.html = renderMarkdown(this.responseText);
        }
        if (message.type === "error") message.html = renderMarkdown(`**Erreur :** ${message.text}`);
        if (message.type === "cleared") message.workspace = this.workspace;
        if (message.type === "archive_loaded") {
          message.messages = (message.messages || []).map((item) => ({
            ...item,
            html: item.type === "user" ? undefined : renderMarkdown(item.text || ""),
          }));
        }
        this.post(message);
        if (message.type === "response") {
          vscode.commands.executeCommand("workbench.files.action.refreshFilesExplorer");
        }
      } catch {
        this.post({ type: "diagnostic", text: line });
      }
    }
  }

  async sendPrompt(text, fullAccess, model, images) {
    if (this.switchingProject) return;
    const prompt = String(text || "").trim();
    const attachedImages = Array.isArray(images) ? images.slice(0, 5) : [];
    if ((!prompt && !attachedImages.length) || !(await this.ensureAgent())) return;
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
    if (!(await this.ensureAgent())) return;
    if (payload.type === "resume_archive" || (payload.type === "followup" && ["add", "steer", "continue"].includes(payload.action))) this.busy = true;
    this.agent.stdin.write(`${JSON.stringify(payload)}\n`);
  }

  async clearConversation() {
    await this.stopAgent(false);
    this.busy = false;
    const dataRoot=this.store.dataRoot;
    // A new conversation belongs to the IDE workspace, independent of the last archive opened.
    this.workspace=this.initialWorkspace;
    this.store=new ArchiveStore(this.workspace,dataRoot);
    new ProjectStore(dataRoot).activate(this.initialWorkspace,this.workspace,{register:false});
    await this.state?.update("ovhAgent.projectRoot",this.workspace);
    if (!(await this.ensureAgent())) {
      this.post({ type: "error", text: t("Impossible de démarrer {0} : {1}", BRAND_NAME, "initialization failed") });
      return;
    }
    this.agent.stdin.write(`${JSON.stringify({ type: "clear" })}\n`);
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

  stopAgent(notify = true) {
    if (this.stopPromise) return this.stopPromise;
    const starting = this.startPromise;
    this.stopPromise = (async () => {
      if (starting) await starting;
      const agent = this.agent;
      this.agent = undefined;
      if (agent && agent.exitCode === null && agent.signalCode === null) {
        await new Promise(resolve => {
          const timer = setTimeout(() => agent.kill("SIGKILL"), 5000);
          agent.once("close", () => { clearTimeout(timer); resolve(); });
          agent.kill("SIGTERM");
        });
      }
      this.busy = false;
      if (notify) this.post({ type: "idle" });
    })().finally(() => { this.stopPromise = undefined; });
    return this.stopPromise;
  }

  dispose() {
    this.view = undefined;
    void this.closeWindow();
  }

  getHtml(webview) {
    const brandName = escapeBrand(BRAND_NAME);
    const brandBadge = escapeBrand(Array.from(BRAND_NAME.split(/\s+/)[0]).slice(0, 3).join("").toUpperCase());
    const configuredColor = String(process.env.OVH_AGENT_PRIMARY_COLOR || "").trim();
    const primaryColor = /^#[0-9a-f]{6}$/i.test(configuredColor) ? configuredColor : "#3e3aab";
    const styleUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "chat.css"));
    const codeStyleUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "monokai.min.css"));
    const highlightUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "highlight.min.js"));
    const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "chat.js"));
    const i18nUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "i18n.js"));
    const translationData = JSON.stringify(require("./media/translations.json")).replace(/</g, "\\u003c");
    const localeOverride = escapeBrand(process.env.OVH_AGENT_LANG || "auto");
    const showMountPoint = !["false", "0", "no", "non", "off"].includes(
      String(process.env.OVH_AGENT_SHOW_MOUNT_POINT ?? "true").trim().toLowerCase());
    const archiveBanner = showMountPoint ? `<div id="archive-banner" class="archive-head"><div class="archive-heading"><span class="archive-heading-icon" aria-hidden="true">${uiIcon("archive", "archive-title-icon")}</span><div class="archive-heading-text"><strong class="archive-title">Conversations</strong><div class="archive-mount"><span class="archive-mount-label">Mount Point : </span><span id="archive-path">Chargement du chemin d’archivage…</span></div></div></div><button id="close-history" class="icon" aria-label="Fermer">${uiIcon("close")}</button></div>` : "";
    const purifyUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "purify.min.js"));
    const iconsUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "icons.min.js"));
    const safeHtmlUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "safe-html.js"));
    const nonce = crypto.randomBytes(24).toString("base64");
    return `<!doctype html>
<html lang="fr"><head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'; connect-src 'none'; img-src data:; style-src ${webview.cspSource} 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
  <link rel="stylesheet" href="${codeStyleUri}?build=${nonce}">
  <link rel="stylesheet" href="${styleUri}?build=${nonce}">
  <style nonce="${nonce}">:root { --ovh-primary: ${primaryColor}; }</style>
  <title>${brandName}</title>
</head><body data-emoji-mode="${/^[0-3]$/.test(process.env.OVH_AGENT_EMOJI_MODE || "") ? process.env.OVH_AGENT_EMOJI_MODE : "1"}" data-language="${localeOverride}" data-brand-name="${brandName}" data-brand-badge="${brandBadge}" data-primary-color="${primaryColor}" data-performance-mode="${this.performanceMode()}">
  <header><div class="header-leading"><span id="history-brand" class="history-brand" aria-hidden="true">${uiIcon("nodes")}</span><button id="history" hidden class="icon back-history" title="Retour à l’historique" aria-label="Retour à l’historique">${uiIcon("back")}</button><div class="brand"><strong>${brandName}</strong><button id="model-picker" title="Choisir le modèle"><span id="selected-model">Auto équilibré</span>${uiIcon("chevron")}</button></div></div><div class="header-actions"><button id="project-picker" class="icon" title="Choisir ou créer un projet" aria-label="Choisir ou créer un projet">${uiIcon("project")}</button><button id="billing" class="icon billing" title="Configurer le suivi du solde OVHcloud" aria-label="Solde OVHcloud">${uiIcon("database")}<span class="billing-dot"></span></button><button id="configure" class="icon" title="Configurer la clé OVH" aria-label="Configurer la clé">${uiIcon("key")}</button><button id="clear" class="icon" title="Nouvelle conversation" aria-label="Nouvelle conversation">${uiIcon("plus")}</button></div></header>
  <div class="workspace-content">
  <aside id="archive-panel">${archiveBanner}<p id="archive-notice" role="status"></p><div id="archive-list"><p class="archive-empty">Chargement…</p></div><p class="agent-disclaimer"></p></aside><dialog id="delete-dialog"><h3>${uiIcon("trash", "delete-title-icon")}Supprimer définitivement ?</h3><p id="delete-title"></p><p>Les messages, pièces jointes et la mémoire de cette conversation seront supprimés. Cette action est irréversible.</p><div class="delete-dialog-actions"><button id="delete-cancel" type="button">Annuler</button><button id="delete-confirm" type="button">Supprimer définitivement</button></div></dialog>
  <aside id="model-panel" hidden><div class="model-head"><div><strong>Modèles OVHcloud</strong><span>Catalogue et tarifs en direct</span><span id="model-availability" aria-live="polite"></span></div><button id="close-models" class="icon" aria-label="Fermer">${uiIcon("close")}</button></div><div class="model-tools"><input id="model-search" type="search" placeholder="Rechercher un modèle…"><button id="refresh-models" class="icon" title="Actualiser les tarifs">${uiIcon("reload")}</button></div><div id="model-list"><p class="model-empty">Chargement du catalogue…</p></div><div class="model-foot">Tarifs OVHcloud HT. Seuls les modèles sélectionnables sont affichés. Les modèles non conversationnels ou indisponibles sont exclus. Le mode Auto ne compte pas comme un modèle.</div></aside>
  <main id="messages"><section class="welcome"><h2>${brandName}</h2><p>Je peux analyser des images, lire et modifier les fichiers, rechercher dans le projet et exécuter des commandes.</p></section></main>
  <footer><div id="followups" class="followups" aria-live="polite" hidden></div><div class="composer-meta"><div id="status" aria-live="polite"></div><button type="button" id="live-balance" aria-live="polite" title="Configurer ou actualiser le solde OVHcloud">Solde : chargement…</button></div><div id="composer" class="composer"><div id="attachments" class="attachments" hidden></div><textarea id="prompt" rows="1" placeholder="Demander à ${brandName}…"></textarea><div class="composer-bar"><div class="composer-left"><button id="attach" class="round attach" title="Joindre une image" aria-label="Joindre une image">${uiIcon("image")}</button><input id="image-input" type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple hidden><button id="access" class="access full" title="Basculer le niveau d’accès">${uiIcon("shield")}<span>Accès complet</span></button><select id="performance-mode" aria-label="Performances locales"><option value="auto">Perf. Auto</option><option value="max">Perf. Max</option></select></div><div class="send-column"><div class="send-actions"><button id="stop" class="round stop" title="Arrêter" aria-label="Arrêter">${uiIcon("stop")}</button><button id="send" class="round send" title="Envoyer" aria-label="Envoyer">${uiIcon("send")}</button></div></div></div></div><p class="agent-disclaimer"></p></footer>
  </div>
  <script nonce="${nonce}">window.OVH_TRANSLATIONS = ${translationData};</script>
  <script nonce="${nonce}" src="${i18nUri}?build=${nonce}"></script>
  <script nonce="${nonce}" src="${purifyUri}?build=${nonce}"></script>
  <script nonce="${nonce}" src="${safeHtmlUri}?build=${nonce}"></script>
  <script nonce="${nonce}" src="${iconsUri}?build=${nonce}"></script>
  <script nonce="${nonce}" src="${highlightUri}?build=${nonce}"></script>
  <script nonce="${nonce}" src="${scriptUri}?build=${nonce}"></script>
</body></html>`;
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
  const editProfile = vscode.commands.registerCommand("ovhAgent.editProfile", () => provider.editProfile());
  const configureBilling = vscode.commands.registerCommand("ovhAgent.configureBilling", () => provider.configureBilling());
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 90);
  status.name = BRAND_NAME;
  status.text = BRAND_NAME;
  status.tooltip = `Ouvrir le chat ${BRAND_NAME}`;
  status.command = "ovhAgent.start";
  status.show();
  context.subscriptions.push(registration, start, configure, configureBilling, selectProject, editProfile, status, provider);
}

function deactivate() {}

module.exports = { activate, deactivate };
