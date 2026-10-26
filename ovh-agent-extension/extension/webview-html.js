"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { svg: uiIcon } = require("./media/icons.min.js");
const { escapeHtml: escapeBrand } = require("./security");

// Keep document construction independent from process/message lifecycle. Asset
// revisions allow browser reuse, while every document receives a fresh CSP nonce.
function buildChatHtml({extensionUri, brand, performanceMode, env, joinPath}, webview) {
  const brandName = escapeBrand(brand);
  const brandBadge = escapeBrand(Array.from(brand.split(/\s+/)[0]).slice(0, 3).join("").toUpperCase());
  const configuredColor = String(env.OVH_AGENT_PRIMARY_COLOR || "").trim();
  const primaryColor = /^#[0-9a-f]{6}$/i.test(configuredColor) ? configuredColor : "#3e3aab";
  const mediaRoot = path.join(extensionUri.fsPath || extensionUri, "media");
  // Branding resolves/sanitizes local assets into the installed manifest. Keep
  // the application icon for light themes and a distinct high-contrast mark for
  // dark headers so changing one no longer changes the other.
  let logoFile = "trinity-icon.png";
  let darkLogoFile = "trinity-activity.png";
  try {
    const manifest = JSON.parse(fs.readFileSync(path.join(extensionUri.fsPath || extensionUri, "package.json"), "utf8"));
    const candidate = String(manifest.ovhAgentBranding?.lightThemeIcon || manifest.icon || "");
    if (/^media\/[a-z0-9_.-]+\.(?:png|svg|jpg|jpeg|webp)$/i.test(candidate) &&
        fs.existsSync(path.join(mediaRoot, candidate.slice(6)))) logoFile = candidate.slice(6);
    const darkCandidate = String(manifest.ovhAgentBranding?.darkThemeIcon || "");
    if (/^media\/[a-z0-9_.-]+\.(?:png|svg|jpg|jpeg|webp)$/i.test(darkCandidate) &&
        fs.existsSync(path.join(mediaRoot, darkCandidate.slice(6)))) darkLogoFile = darkCandidate.slice(6);
  } catch { /* The packaged original remains available when metadata cannot be read. */ }
  const logoUri = webview.asWebviewUri(joinPath(extensionUri, "media", logoFile));
  const darkLogoUri = webview.asWebviewUri(joinPath(extensionUri, "media", darkLogoFile));
  // Packaging generates minified assets; source checkouts work without a build.
  const asset = name => fs.existsSync(path.join(mediaRoot, name+".min.js")) ? name+".min.js" : name+".js";
  const styleUri = webview.asWebviewUri(joinPath(extensionUri, "media", "chat.css"));
  const codeStyleUri = webview.asWebviewUri(joinPath(extensionUri, "media", "monokai.min.css"));
  const highlightUri = webview.asWebviewUri(joinPath(extensionUri, "media", "highlight.min.js"));
  const scriptUri = webview.asWebviewUri(joinPath(extensionUri, "media", asset("chat")));
  const i18nUri = webview.asWebviewUri(joinPath(extensionUri, "media", asset("i18n")));
  const translationData = JSON.stringify(require("./media/translations.json")).replace(/</g, "\\u003c");
  const localeOverride = escapeBrand(env.OVH_AGENT_LANG || "auto");
  const showMountPoint = !["false", "0", "no", "non", "off"].includes(
    String(env.OVH_AGENT_SHOW_MOUNT_POINT ?? "true").trim().toLowerCase());
  const archiveBanner = showMountPoint ? `<div id="archive-banner" class="archive-head"><div class="archive-heading"><span class="archive-heading-icon" aria-hidden="true">${uiIcon("archive", "archive-title-icon")}</span><div class="archive-heading-text"><strong class="archive-title">Conversations</strong><div class="archive-mount"><span class="archive-mount-label">Mount Point : </span><span id="archive-path">Chargement du chemin d’archivage…</span></div></div></div><button id="close-history" class="icon" aria-label="Fermer">${uiIcon("close")}</button></div>` : "";
  const purifyUri = webview.asWebviewUri(joinPath(extensionUri, "media", "purify.min.js"));
  const iconsUri = webview.asWebviewUri(joinPath(extensionUri, "media", "icons.min.js"));
  const safeHtmlUri = webview.asWebviewUri(joinPath(extensionUri, "media", asset("safe-html")));
  const nonce = crypto.randomBytes(24).toString("base64");
  const revision = crypto.createHash("sha256").update([logoFile, darkLogoFile, "chat.css", "monokai.min.css", "highlight.min.js", asset("chat"), asset("i18n"), "purify.min.js", "icons.min.js", asset("safe-html")].map(name => {
    const stat = fs.statSync(path.join(extensionUri.fsPath || extensionUri, "media", name));
    return `${name}:${stat.size}:${stat.mtimeMs}:${stat.ctimeMs}`;
  }).join("|")).digest("hex").slice(0, 20);
  return `<!doctype html>
<html lang="fr"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'; connect-src 'none'; img-src ${webview.cspSource} data:; style-src ${webview.cspSource} 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
<link rel="stylesheet" href="${codeStyleUri}?build=${revision}">
<link rel="stylesheet" href="${styleUri}?build=${revision}">
<style nonce="${nonce}">:root { --ovh-primary: ${primaryColor}; }</style>
<title>${brandName}</title>
</head><body data-emoji-mode="${/^[0-3]$/.test(env.OVH_AGENT_EMOJI_MODE || "") ? env.OVH_AGENT_EMOJI_MODE : "1"}" data-language="${localeOverride}" data-brand-name="${brandName}" data-brand-badge="${brandBadge}" data-primary-color="${primaryColor}" data-performance-mode="${performanceMode}">
<header><div class="header-leading"><span id="history-brand" class="history-brand" aria-hidden="true"><img class="header-app-logo header-app-logo-light" src="${escapeBrand(String(logoUri))}?build=${revision}" alt=""><img class="header-app-logo header-app-logo-dark" src="${escapeBrand(String(darkLogoUri))}?build=${revision}" alt=""></span><button id="history" hidden class="icon back-history" title="Retour à l’historique" aria-label="Retour à l’historique">${uiIcon("back")}</button><div class="brand"><strong>${brandName}</strong><button id="model-picker" title="Choisir le modèle"><span id="selected-model">Auto équilibré</span>${uiIcon("chevron")}</button></div></div><div class="header-actions"><button id="project-picker" class="icon" title="Choisir ou créer un projet" aria-label="Choisir ou créer un projet">${uiIcon("project")}</button><button id="billing" class="icon billing" title="Configurer le suivi du solde OVHcloud" aria-label="Solde OVHcloud">${uiIcon("database")}<span class="billing-dot"></span></button><button id="configure" class="icon" title="Configurer la clé OVH" aria-label="Configurer la clé">${uiIcon("key")}</button><button id="clear" class="icon" title="Nouvelle conversation" aria-label="Nouvelle conversation">${uiIcon("plus")}</button></div></header>
<div class="workspace-content">
<aside id="archive-panel">${archiveBanner}<p id="archive-notice" role="status"></p><div id="archive-list"><p class="archive-empty">Chargement…</p></div><p class="agent-disclaimer"></p></aside><dialog id="delete-dialog" aria-labelledby="delete-heading delete-title" aria-describedby="delete-description"><h3 id="delete-heading">${uiIcon("trash", "delete-title-icon")}Supprimer définitivement ?</h3><p id="delete-title"></p><p id="delete-description">Les messages, pièces jointes et la mémoire de cette conversation seront supprimés. Cette action est irréversible.</p><div class="delete-dialog-actions"><button id="delete-cancel" type="button">Annuler</button><button id="delete-confirm" type="button">Supprimer définitivement</button></div></dialog>
<aside id="model-panel" hidden><div class="model-head"><div><strong id="model-panel-title">Modèles</strong><span id="model-panel-source">Catalogue et tarifs du fournisseur</span><span id="model-availability" aria-live="polite"></span></div><button id="close-models" class="icon" aria-label="Fermer">${uiIcon("close")}</button></div><div class="model-tools"><input id="model-search" type="search" placeholder="Rechercher un modèle…"><button id="refresh-models" class="icon" title="Actualiser les tarifs">${uiIcon("reload")}</button></div><div id="model-list"><p class="model-empty">Chargement du catalogue…</p></div><div class="model-foot">Tarifs du fournisseur (estimations). Seuls les modèles sélectionnables sont affichés. Le mode Auto ne compte pas comme un modèle.</div></aside>
<main id="messages"><section class="welcome"><h2>${brandName}</h2><p>Je peux analyser des images, lire et modifier les fichiers, rechercher dans le projet et exécuter des commandes.</p></section></main>
<footer><div id="followups" class="followups" aria-live="polite" hidden></div><div class="composer-meta"><div id="status" aria-live="polite"></div><button type="button" id="live-balance" aria-live="polite" title="Configurer ou actualiser le solde OVHcloud">Solde : chargement…</button></div><div id="composer" class="composer"><div id="attachments" class="attachments" hidden></div><textarea id="prompt" rows="1" placeholder="Demander à ${brandName}…"></textarea><div class="composer-bar"><div class="composer-left"><button id="attach" class="round attach" title="Joindre une image" aria-label="Joindre une image">${uiIcon("image")}</button><input id="image-input" type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple hidden><button id="access" class="access full" title="Basculer le niveau d’accès">${uiIcon("shield")}<span>Accès complet</span></button><select id="performance-mode" aria-label="Performances locales"><option value="auto">Perf. Auto</option><option value="max">Perf. Max</option></select></div><div class="send-column"><div class="send-actions"><button id="stop" class="round stop" title="Arrêter" aria-label="Arrêter">${uiIcon("stop")}</button><button id="send" class="round send" title="Envoyer" aria-label="Envoyer">${uiIcon("send")}</button></div></div></div></div><p class="agent-disclaimer"></p></footer>
</div>
<script nonce="${nonce}">window.OVH_TRANSLATIONS = ${translationData};</script>
<script nonce="${nonce}" src="${i18nUri}?build=${revision}"></script>
<script nonce="${nonce}" src="${purifyUri}?build=${revision}"></script>
<script nonce="${nonce}" src="${safeHtmlUri}?build=${revision}"></script>
<script nonce="${nonce}" src="${iconsUri}?build=${revision}"></script>
<script nonce="${nonce}" src="${highlightUri}?build=${revision}"></script>
<script nonce="${nonce}" src="${scriptUri}?build=${revision}"></script>
</body></html>`;
}
module.exports = {buildChatHtml};
