const { svg: uiIcon } = require("./media/icons.min.js");
const vscode = require("vscode");
const i18n = require("./media/i18n");
const { t } = i18n;
i18n.setLanguage(i18n.resolveLanguage(process.env.OVH_AGENT_LANG, [vscode.env.language]));
const { ArchiveStore } = require("./archive-store");
const { spawn, execFile } = require("child_process");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { validMessage, localLink } = require("./security");
const MarkdownIt = require("markdown-it");
const sanitizeHtml = require("sanitize-html");

const BRAND_NAME = (process.env.OVH_AGENT_NAME || "TRINITY Agent").replace(/[\x00-\x1f\x7f]/g, "").trim().slice(0, 80) || "OVH Agent";
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
  constructor(extensionUri, secrets) {
    this.extensionUri = extensionUri;
    this.secrets = secrets;
    this.view = undefined;
    this.agent = undefined;
    this.stdoutBuffer = "";
    this.responseText = "";
    this.startPromise = undefined;
    this.deleting = false;
    this.store = new ArchiveStore(vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || require("os").homedir());
  }

  resolveWebviewView(webviewView) {
    this.view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")],
    };
    webviewView.webview.html = this.getHtml(webviewView.webview);
    webviewView.webview.onDidReceiveMessage(async (message) => {
      if (!validMessage(message)) return;
      if (message.type === "prompt") await this.sendPrompt(message.text, message.fullAccess, message.model, message.images);
      if (message.type === "followup") await this.sendControl({
        type: "followup", action: message.action, conversation: message.conversation,
        id: message.id, text: message.text, mode: message.mode,
        full_access: Boolean(message.fullAccess), model: message.model,
      });
      if (message.type === "clear") await this.clearConversation();
      if (message.type === "cancel") this.stopAgent();
      if (message.type === "configureKey") await this.configureKey();
      if (message.type === "billing") await this.refreshBilling(true);
      if (message.type === "billingRefresh") await this.refreshBilling(false);
      if (message.type === "approval") this.sendApproval(message.id, message.allow);
      if (message.type === "openLink") await this.openLink(message.href);
      if (message.type === "listArchives") this.showArchives();
      if (message.type === "listModels") await this.sendControl({ type: "list_models", refresh: Boolean(message.refresh) });
      if (message.type === "selectModel") await this.sendControl({ type: "set_model", model: String(message.model || "") });
      if (message.type === "deleteArchiveConfirmed") await this.deleteArchive(message.id);
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
  }

  post(message) {
    if (this.view) this.view.webview.postMessage(message);
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
      vscode.window.showInformationMessage(t("Crée les identifiants en lecture seule, puis reclique sur l’icône du solde dans OVH Agent."));
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
        const workspace = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || require("os").homedir();
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
    try { this.post({ type: "archives", items: this.store.list(), archive_root: this.store.root }); }
    catch (error) { this.post({ type: "archive_delete_failed", text: t("Historique indisponible : {0}", error.message) }); }
  }

  async deleteArchive(id) {
    if (this.deleting) return;
    this.deleting = true;
    this.post({ type: "archive_delete_pending", id });
    try {
      if (this.startPromise) await this.startPromise;
      if (this.store.current() === id && this.agent) {
        const agent = this.agent;
        await new Promise((resolve, reject) => {
          if (agent.exitCode !== null || agent.signalCode !== null) return resolve();
          const timer = setTimeout(() => reject(new Error(t("L’agent ne s’est pas arrêté ; suppression annulée."))), 5000);
          agent.once("exit", () => { clearTimeout(timer); resolve(); });
          agent.kill("SIGTERM");
        });
        this.agent = undefined;
      }
      const wasCurrent = this.store.remove(String(id || ""));
      this.post({ type: "archive_deleted", id, was_current: wasCurrent });
      this.showArchives();
    } catch (error) {
      this.post({ type: "archive_delete_failed", id, text: t("Suppression impossible : {0}", i18n.message(error.message)) });
      vscode.window.showErrorMessage(t("Suppression impossible : {0}", i18n.message(error.message)));
    } finally { this.deleting = false; }
  }

  async ensureAgent() {
    if (this.deleting) return false;
    if (this.agent && !this.agent.killed) return true;
    if (!this.startPromise) this.startPromise = this.startAgent().finally(() => { this.startPromise = undefined; });
    return this.startPromise;
  }

  async startAgent() {

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
    const workspace = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || require("os").homedir();
    const billing = await this.billingCredentials();
    this.stdoutBuffer = "";
    const agent = spawn(agentPath(), ["--json-lines"], {
      cwd: workspace,
      env: {
        ...process.env,
        OVH_AGENT_WORKSPACE: workspace,
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
      this.post({ type: "error", text: t("Impossible de démarrer OVH Agent : {0}", error.message) });
      if (this.agent === agent) this.agent = undefined;
    });
    agent.on("exit", (code, signal) => {
      if (this.agent !== agent) return;
      if (code && code !== 0) {
        this.post({ type: "error", text: t("OVH Agent s’est arrêté (code {0}{1}).", code, signal ? `, ${signal}` : "") });
      }
      if (this.agent === agent) {
        this.post({ type: "idle" });
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
        if (message.type === "status") this.responseText = "";
        if (message.type === "delta") {
          this.responseText += String(message.text || "");
          message.html = renderMarkdown(this.responseText);
        }
        if (message.type === "response") {
          this.responseText = String(message.text || "");
          message.html = renderMarkdown(this.responseText);
        }
        if (message.type === "error") message.html = renderMarkdown(`**Erreur :** ${message.text}`);
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
    const prompt = String(text || "").trim();
    const attachedImages = Array.isArray(images) ? images.slice(0, 5) : [];
    if ((!prompt && !attachedImages.length) || !(await this.ensureAgent())) return;
    this.responseText = "";
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
    this.agent.stdin.write(`${JSON.stringify(payload)}\n`);
  }

  async clearConversation() {
    this.stopAgent(false);
    if (!(await this.ensureAgent())) {
      this.post({ type: "error", text: t("Impossible de démarrer OVH Agent : {0}", "initialization failed") });
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
    const workspace = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || require("os").homedir();
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
    if (this.agent && !this.agent.killed) this.agent.kill("SIGTERM");
    this.agent = undefined;
    if (notify) this.post({ type: "idle" });
  }

  dispose() {
    this.stopAgent(false);
  }

  getHtml(webview) {
    const brandName = escapeBrand(BRAND_NAME);
    const brandBadge = escapeBrand(Array.from(BRAND_NAME.split(/\s+/)[0]).slice(0, 3).join("").toUpperCase());
    const configuredColor = String(process.env.OVH_AGENT_PRIMARY_COLOR || "").trim();
    const primaryColor = /^#[0-9a-f]{6}$/i.test(configuredColor) ? configuredColor : "#3e3aab";
    const styleUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "chat.css"));
    const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "chat.js"));
    const i18nUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "i18n.js"));
    const translationData = JSON.stringify(require("./media/translations.json")).replace(/</g, "\\u003c");
    const localeOverride = escapeBrand(process.env.OVH_AGENT_LANG || "auto");
    const showMountPoint = !["false", "0", "no", "non", "off"].includes(
      String(process.env.OVH_AGENT_SHOW_MOUNT_POINT ?? "true").trim().toLowerCase());
    const archiveBanner = showMountPoint ? `<div id="archive-banner" class="archive-head"><div><strong class="archive-title">${uiIcon("archive", "archive-title-icon")}Conversations</strong><div class="archive-mount"><span class="archive-mount-label">Mount Point : </span><span id="archive-path">Chargement du chemin d’archivage…</span></div></div><button id="close-history" class="icon" aria-label="Fermer">${uiIcon("close")}</button></div>` : "";
    const purifyUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "purify.min.js"));
    const iconsUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "icons.min.js"));
    const safeHtmlUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "safe-html.js"));
    const nonce = crypto.randomBytes(24).toString("base64");
    return `<!doctype html>
<html lang="fr"><head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'; connect-src 'none'; img-src data:; style-src ${webview.cspSource} 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
  <link rel="stylesheet" href="${styleUri}?build=${nonce}">
  <style nonce="${nonce}">:root { --ovh-primary: ${primaryColor}; }</style>
  <title>${brandName}</title>
</head><body data-language="${localeOverride}" data-brand-name="${brandName}" data-brand-badge="${brandBadge}">
  <header><div class="header-leading"><span id="history-brand" class="history-brand" aria-hidden="true">${uiIcon("nodes")}</span><button id="history" hidden class="icon back-history" title="Retour à l’historique" aria-label="Retour à l’historique">${uiIcon("back")}</button><div class="brand"><strong>${brandName}</strong><button id="model-picker" title="Choisir le modèle"><span id="selected-model">Auto équilibré</span>${uiIcon("chevron")}</button></div></div><div class="header-actions"><button id="billing" class="icon billing" title="Configurer le suivi du solde OVHcloud" aria-label="Solde OVHcloud">${uiIcon("database")}<span class="billing-dot"></span></button><button id="configure" class="icon" title="Configurer la clé OVH" aria-label="Configurer la clé">${uiIcon("key")}</button><button id="clear" class="icon" title="Nouvelle conversation" aria-label="Nouvelle conversation">${uiIcon("plus")}</button></div></header>
  <aside id="archive-panel">${archiveBanner}<p id="archive-notice" role="status"></p><div id="archive-list"><p class="archive-empty">Chargement…</p></div></aside><dialog id="delete-dialog"><h3>${uiIcon("trash", "delete-title-icon")}Supprimer définitivement ?</h3><p id="delete-title"></p><p>Les messages, pièces jointes et la mémoire de cette conversation seront supprimés. Cette action est irréversible.</p><div class="delete-dialog-actions"><button id="delete-cancel" type="button">Annuler</button><button id="delete-confirm" type="button">Supprimer définitivement</button></div></dialog>
  <aside id="model-panel" hidden><div class="model-head"><div><strong>Modèles OVHcloud</strong><span>Catalogue et tarifs en direct</span></div><button id="close-models" class="icon" aria-label="Fermer">${uiIcon("close")}</button></div><div class="model-tools"><input id="model-search" type="search" placeholder="Rechercher un modèle…"><button id="refresh-models" class="icon" title="Actualiser les tarifs">${uiIcon("reload")}</button></div><div id="model-list"><p class="model-empty">Chargement du catalogue…</p></div><div class="model-foot">Tarifs OVHcloud HT. Les modèles non conversationnels sont affichés à titre informatif.</div></aside>
  <main id="messages"><section class="welcome"><h2>${brandName}</h2><p>Je peux analyser des images, lire et modifier les fichiers, rechercher dans le projet et exécuter des commandes.</p></section></main>
  <footer><div id="followups" class="followups" aria-live="polite" hidden></div><div class="composer-meta"><div id="status" aria-live="polite"></div><button type="button" id="live-balance" aria-live="polite" title="Configurer ou actualiser le solde OVHcloud">Solde : chargement…</button></div><div id="composer" class="composer"><div id="attachments" class="attachments" hidden></div><textarea id="prompt" rows="1" placeholder="Demander à ${brandName}…"></textarea><div class="composer-bar"><div class="composer-left"><button id="attach" class="round attach" title="Joindre une image" aria-label="Joindre une image">${uiIcon("image")}</button><input id="image-input" type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple hidden><button id="access" class="access full" title="Basculer le niveau d’accès">${uiIcon("shield")}<span>Accès complet</span></button></div><div class="send-column"><div class="send-actions"><select id="followup-mode" title="Réorienter applique la consigne à la prochaine étape, après l’appel ou l’outil en cours." aria-label="Envoi pendant une tâche" hidden><option value="queue">Mettre en attente</option><option value="steer">Réorienter</option></select><button id="stop" class="round stop" title="Arrêter" aria-label="Arrêter">${uiIcon("stop")}</button><button id="send" class="round send" title="Envoyer" aria-label="Envoyer">${uiIcon("send")}</button></div></div></div></div></footer>
  <script nonce="${nonce}">window.OVH_TRANSLATIONS = ${translationData};</script>
  <script nonce="${nonce}" src="${i18nUri}?build=${nonce}"></script>
  <script nonce="${nonce}" src="${purifyUri}?build=${nonce}"></script>
  <script nonce="${nonce}" src="${safeHtmlUri}?build=${nonce}"></script>
  <script nonce="${nonce}" src="${iconsUri}?build=${nonce}"></script>
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
  const provider = new OvhAgentViewProvider(context.extensionUri, context.secrets);
  const registration = vscode.window.registerWebviewViewProvider(
    "ovhAgent.launch",
    provider,
    { webviewOptions: { retainContextWhenHidden: true } },
  );
  const start = vscode.commands.registerCommand("ovhAgent.start", async () => {
    await vscode.commands.executeCommand("workbench.view.extension.ovhAgent");
  });
  const configure = vscode.commands.registerCommand("ovhAgent.configureKey", () => provider.configureKey());
  const configureBilling = vscode.commands.registerCommand("ovhAgent.configureBilling", () => provider.configureBilling());
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 90);
  status.name = BRAND_NAME;
  status.text = BRAND_NAME;
  status.tooltip = `Ouvrir le chat ${BRAND_NAME}`;
  status.command = "ovhAgent.start";
  status.show();
  context.subscriptions.push(registration, start, configure, configureBilling, status, provider);
}

function deactivate() {}

module.exports = { activate, deactivate };
