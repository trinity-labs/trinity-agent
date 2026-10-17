const { svg: uiIcon } = AgentIcons;
const uiLanguage = OvhI18n.setLanguage(OvhI18n.resolveLanguage(
  document.body.dataset.language, navigator.languages?.length ? navigator.languages : [navigator.language]));
const { t } = OvhI18n;
document.documentElement.lang = uiLanguage;
OvhI18n.translateStatic(document.body);
const vscode = acquireVsCodeApi();
const brandName = document.body.dataset.brandName || document.title || "TRINITY Agent";
for (const notice of document.querySelectorAll(".agent-disclaimer")) {
  notice.textContent = t("{0} est un outil d’assistance. Vous restez responsable des actions exécutées. L’IA peut se tromper : vérifiez ses réponses et les commandes.", brandName);
}
document.getElementById("prompt").placeholder = t("Demander à {0}…", brandName);
document.documentElement.style.setProperty("--ovh-brand-badge", JSON.stringify(document.body.dataset.brandBadge || Array.from(brandName.split(/\s+/)[0]).slice(0, 3).join("").toUpperCase()));
const messages = document.getElementById("messages");
const prompt = document.getElementById("prompt");
const send = document.getElementById("send");
const stop = document.getElementById("stop");
const followupMode = document.getElementById("followup-mode");
const followupsBox = document.getElementById("followups");
let followups = [];
let pendingFollowup = null;
let editingFollowup = null;
function followupCommand(action, extra = {}) {
  if (!activeArchiveId) return;
  vscode.postMessage({type: "followup", conversation: activeArchiveId, action, ...extra});
}
function renderFollowups() {
  followupsBox.replaceChildren();
  followupsBox.hidden = !followups.length;
  for (const item of followups) {
    const row = document.createElement("div");
    row.className = "followup-row";
    const label = document.createElement("span");
    label.textContent = `${t(item.mode === "steer" ? "Réorientation en attente" : "En attente")} · ${item.text}`;
    label.title = item.text;
    row.append(label);
    for (const [action, icon, title] of [["steer", "send", "Réorienter"], ["edit", "edit", "Modifier"], ["remove", "trash", "Retirer"]]) {
      if (action === "steer" && item.mode === "steer") continue;
      const button = document.createElement("button");
      button.type = "button";
      button.title = t(title);
      button.setAttribute("aria-label", t(title));
      button.innerHTML = uiIcon(icon);
      button.onclick = () => {
        if (action === "edit") {
          if (prompt.value.trim() || pendingImages.length) { setStatus(t("Vide le brouillon avant de modifier un message.")); return; }
          editingFollowup = item.id;
          prompt.value = item.text;
          resizePrompt();
          updateSendButton();
          prompt.focus();
        } else followupCommand(action, {id: item.id});
      };
      row.append(button);
    }
    followupsBox.append(row);
  }
  if (!busy && followups.length) {
    const resume = document.createElement("button");
    resume.type = "button";
    resume.textContent = t("Exécuter la file d’attente");
    resume.onclick = () => followupCommand("continue");
    followupsBox.append(resume);
  }
}

const clear = document.getElementById("clear");
const configure = document.getElementById("configure");
const billing = document.getElementById("billing");
const liveBalance = document.getElementById("live-balance");
const access = document.getElementById("access");
const history = document.getElementById("history");
const historyBrand = document.getElementById("history-brand");
const archivePanel = document.getElementById("archive-panel");
const archiveList = document.getElementById("archive-list");
const closeHistory = document.getElementById("close-history");
const modelPicker = document.getElementById("model-picker");
const selectedModelLabel = document.getElementById("selected-model");
const modelPanel = document.getElementById("model-panel");
const modelList = document.getElementById("model-list");
const modelSearch = document.getElementById("model-search");
const closeModels = document.getElementById("close-models");
const refreshModels = document.getElementById("refresh-models");
const status = document.getElementById("status");
const composer = document.getElementById("composer");
const attach = document.getElementById("attach");
const imageInput = document.getElementById("image-input");
const attachments = document.getElementById("attachments");
const saved = vscode.getState();

function setArchiveVisible(visible) {
  archivePanel.hidden = !visible;
  history.hidden = visible;
  historyBrand.hidden = !visible;
}
setArchiveVisible(!archivePanel.hidden);


let promptHistory = Array.isArray(saved?.promptHistory) ? saved.promptHistory.filter(item => typeof item === "string").slice(-200) : [];
let historyIndex = promptHistory.length;
let historyDraft = "";
let busy = false;
let activeArchiveId = "";
const resumableArchives = new Set();
const sendArrow = send.innerHTML;
function updateSendButton() {
  const resumable = !busy && resumableArchives.has(activeArchiveId);
  send.innerHTML = resumable
    ? uiIcon("play")
    : sendArrow;
  send.title = editingFollowup ? t("Enregistrer le message") : resumable ? t("Reprendre la tâche interrompue")
    : busy ? t(followupMode.value === "steer" ? "Réorienter" : "Mettre en attente") : t("Envoyer");
  send.setAttribute("aria-label", send.title);
}
function resumeArchive(id) {
  if (busy) return;
  activeArchiveId = id;
  setArchiveVisible(false);
  setBusy(true, t("Reprise de la tâche interrompue…"));
  vscode.postMessage({ type: "resumeArchive", id });
}
let currentRun = null;
let fullAccess = saved?.fullAccess !== false;
let selectedModel = saved?.model || "auto-balanced";
let lastRoutedModel = saved?.lastRoutedModel || "";
let modelCatalog = [];
let backendReady = false;
let creatingConversation = false;
let selectedArchiveForDeletion = null;
let snapshotTimer;
let archiveNoticeTimer;
let pendingImages = [];
const activityRows = new Map();
const approvalCards = new Map();
const supportedImageTypes = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);
const maxImages = 5;
const maxClientImageBytes = 4 * 1024 * 1024;

function persist() {
  vscode.setState({ html: messages.innerHTML, fullAccess, model: selectedModel, lastRoutedModel, promptHistory });
  if (!backendReady || creatingConversation || !activeArchiveId) return;
  window.clearTimeout(snapshotTimer);
  const snapshotConversation = activeArchiveId;
  snapshotTimer = window.setTimeout(() => {
    if (creatingConversation || snapshotConversation !== activeArchiveId) return;
    vscode.postMessage({
      type: "snapshot",
      conversation: snapshotConversation,
      html: messages.innerHTML,
      fullAccess,
      model: selectedModel,
    });
  }, 350);
}

function updateModelButton() {
  const auto = selectedModel === "auto-balanced";
  selectedModelLabel.textContent = auto
    ? t("Auto équilibré{0}", lastRoutedModel ? ` → ${lastRoutedModel}` : "")
    : selectedModel;
  const vision = modelCatalog.find((item) => item.id === selectedModel)?.vision;
  modelPicker.title = auto
    ? t("Routage automatique selon le prix, l’usage et la taille du contexte")
    : t("Modèle actif : {0}{1}", selectedModel, vision ? " · Vision" : "");
}

function modelKindLabel(kind) {
  return {
    chat: t("Chat / agent"),
    embedding: "Embeddings",
    moderation: t("Modération"),
    speech_to_text: t("Transcription"),
    text_to_speech: t("Synthèse vocale"),
    image: t("Génération d’image"),
    auto: t("Éco · Auto"),
  }[kind] || kind;
}

function compactTokens(value) {
  if (!value) return "";
  return value >= 1024 ? t("{0}k contexte", Math.round(value / 1024)) : t("{0} tokens", value);
}

function priceLabel(model) {
  if (model.id === "auto-balanced") return t("Prix minimal utile · contexte compacté");
  const currency = model.currency || "EUR";
  if (model.hourly != null) return t("{0}/heure", formatModelCost(model.hourly, currency));
  if (model.free) return t("Gratuit");
  const input = Number(model.input_per_million || 0);
  const output = Number(model.output_per_million || 0);
  if (input || output) {
    const inputText = input ? formatModelCost(input, currency) : "—";
    const outputText = output ? formatModelCost(output, currency) : "—";
    return t("{0} entrée · {1} sortie / 1M tokens", inputText, outputText);
  }
  return t("Tarif non communiqué par l’API");
}

function modelAvailabilityLabel() {
  const models = modelCatalog.filter((item) => item.id !== "auto-balanced" && item.kind !== "auto");
  return t("{0} modèles disponibles dans l’agent · {1} au catalogue OVHcloud", models.filter((item) => item.selectable).length, models.length);
}

function renderModels() {
  document.getElementById("model-availability").textContent = modelAvailabilityLabel();
  const query = modelSearch.value.trim().toLowerCase();
  const visible = modelCatalog.filter((item) =>
    item.selectable && (!query || item.id.toLowerCase().includes(query) || String(item.owner).toLowerCase().includes(query)),
  );
  modelList.innerHTML = "";
  if (!visible.length) {
    const empty = document.createElement("p");
    empty.className = "model-empty";
    empty.textContent = t("Aucun modèle trouvé.");
    modelList.appendChild(empty);
    return;
  }
  for (const model of visible) {
    const row = document.createElement("button");
    row.className = `model-item${model.id === selectedModel ? " selected" : ""}${model.selectable ? "" : " unavailable"}`;
    row.disabled = !model.selectable;
    const top = document.createElement("div");
    top.className = "model-title";
    const name = document.createElement("strong");
    name.textContent = model.id === "auto-balanced" ? t("Auto équilibré") : model.id;
    const badge = document.createElement("span");
    badge.textContent = model.kind === "auto" ? t("Éco · Auto") : model.vision ? (model.supports_tools === false ? "Vision" : "Vision · Agent") : modelKindLabel(model.kind);
    top.append(name, badge);
    const price = document.createElement("div");
    price.className = "model-price";
    price.textContent = priceLabel(model);
    const meta = document.createElement("div");
    meta.className = "model-meta";
    const size = model.parameters_b ? `${model.parameters_b}B` : "";
    const active = model.active_parameters_b ? t("{0}B actifs", model.active_parameters_b) : "";
    const contextLabel = model.id === "auto-balanced"
      ? t("Jusqu’à {0}k selon le modèle", Math.round(model.context_length / 1024))
      : compactTokens(model.context_length);
    meta.textContent = [model.owner, size, active, model.quantization, contextLabel].filter(Boolean).join(" · ");
    if (model.parameters_b) meta.title = t("Taille documentée du modèle ; ne garantit ni sa vitesse ni sa qualité.");
    row.append(top, price, meta);
    if (model.cyber_eval) {
      const evaluation = document.createElement("div");
      evaluation.className = "model-meta";
      const e = model.cyber_eval;
      evaluation.textContent = t("Labo cyber : {0}/{1} cas validés · {2}/{3} refus · {4} erreurs", e.passed, e.samples, e.refusals, e.answered, e.errors);
      evaluation.title = t("Échantillon limité : SQLi, XSS DOM, traversée de chemins. Aucune garantie d’absence de refus sur d’autres demandes.");
      row.appendChild(evaluation);
    }
    if (model.selectable) {
      row.addEventListener("click", () => {
        selectedModel = model.id;
        if (selectedModel !== "auto-balanced") lastRoutedModel = "";
        updateModelButton();
        renderModels();
        modelPanel.hidden = true;
        setStatus(t("Modèle sélectionné : {0}", selectedModel));
        vscode.postMessage({ type: "selectModel", model: selectedModel });
        persist();
      });
    }
    modelList.appendChild(row);
  }
}

function updateAccessButton() {
  access.classList.toggle("full", fullAccess);
  access.classList.toggle("approval", !fullAccess);
  access.querySelector("span").textContent = fullAccess ? t("Accès complet") : t("Demander l’accès");
  access.title = fullAccess
    ? t("Accès complet : les actions s’exécutent sans confirmation")
    : t("Validation demandée avant les écritures et les commandes");
  persist();
}

function scrollToBottom() {
  messages.scrollTop = messages.scrollHeight;
}

function removeWelcome() {
  messages.querySelector(".welcome")?.remove();
}

function updateStopIcon() {
  stop.innerHTML = uiIcon(prompt.value.length || busy ? "stopFilled" : "stop");
}

function stamp(block, timestamp = new Date().toISOString()) {
  let time = block.querySelector(":scope > .message-time");
  if (!time) { time = document.createElement("time"); time.className = "message-time"; block.appendChild(time); }
  const date = timestamp ? new Date(timestamp) : null;
  if (!date || !Number.isFinite(date.getTime())) { time.textContent = "—"; return; }
  time.dateTime = date.toISOString();
  time.textContent = new Intl.DateTimeFormat(uiLanguage, { dateStyle: "short", timeStyle: "short" }).format(date);
  time.title = date.toLocaleString(uiLanguage);
}

function restoreTimestamps(root, timeline = []) {
  const queue = type => timeline.filter(item => item.type === type).map(item => item.timestamp);
  const users = queue("user"), replies = queue("response"), changes = queue("file_change"), tools = queue("tool_start"), approvals = queue("approval");
  for (const block of root.querySelectorAll(":scope > .message")) {
    const recorded = block.classList.contains("user") ? users.shift()
      : block.classList.contains("archived-file-change") ? changes.shift() : replies.shift();
    const existing = block.querySelector(":scope > .message-time");
    stamp(block, existing?.dateTime || recorded || null);
  }
  for (const [selector, dates] of [[".activity-row > div", tools], [".approval-card", approvals], [".file-change > summary", changes]]) {
    for (const block of root.querySelectorAll(selector)) {
      const recorded = dates.shift();
      const existing = block.querySelector(":scope > .message-time");
      stamp(block, existing?.dateTime || recorded || null);
    }
  }
}

function formatElapsed(startedAt) {
  const seconds = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
  if (seconds < 60) return `${seconds} s`;
  return `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
}

function setStatus(text, failed = false) {
  status.style.opacity = text === t("Conversation supprimée définitivement") ? "0.33" : "";
  status.textContent = text;
  status.classList.toggle("error-message", failed);
}

function setBusy(value, text = "", failed = false) {
  busy = value;
  send.disabled = !!pendingFollowup;
  followupMode.hidden = !value;
  renderFollowups();
  updateSendButton();
  stop.hidden = false;
  updateStopIcon();
  setStatus(text, failed);
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = String(value || "");
  return node.innerHTML;
}

function appendMessageImages(container, images) {
  if (!images?.length) return;
  const gallery = document.createElement("div");
  gallery.className = "message-images";
  for (const image of images) {
    const thumbnail = document.createElement("img");
    thumbnail.src = image.data_url;
    thumbnail.alt = image.name || t("Image jointe");
    thumbnail.title = image.name || t("Image jointe");
    gallery.appendChild(thumbnail);
  }
  container.appendChild(gallery);
}

function addUserMessage(text, images = [], timestamp = undefined) {
  removeWelcome();
  const article = document.createElement("article");
  article.className = "message user";
  const label = document.createElement("div");
  label.className = "label";
  label.textContent = t("Vous");
  const content = document.createElement("div");
  content.className = "content";
  content.textContent = text;
  article.append(label);
  appendMessageImages(article, images);
  if (text) article.append(content);
  stamp(article, timestamp);
  messages.appendChild(article);
  persist();
  scrollToBottom();
}

function addArchivedAssistant(html, failed = false, timestamp = null) {
  removeWelcome();
  const article = document.createElement("article");
  article.className = `message assistant ${failed ? "failure" : "done"}`;
  const label = document.createElement("div");
  label.className = "label";
  label.textContent = brandName;
  const content = document.createElement("div");
  content.className = "content markdown";
  content.innerHTML = safeHtml(html);
  enhanceMarkdown(content);
  article.append(label, content);
  stamp(article, timestamp);
  messages.appendChild(article);
}

function wireFileChangeCopy(details) {
  const copy = details.querySelector(".copy-diff");
  if (!copy) return;
  copy.innerHTML = uiIcon("copy");
  copy.onclick = async (event) => {
    event.preventDefault();
    event.stopPropagation();
    await navigator.clipboard.writeText(details.dataset.diff || "");
    copy.innerHTML = uiIcon("check");
    window.setTimeout(() => { copy.innerHTML = uiIcon("copy"); }, 1200);
  };
}

function createFileChange(data) {
  const details = document.createElement("details");
  details.className = "file-change";
  details.open = true;
  details.dataset.diff = String(data.diff || "");

  const summary = document.createElement("summary");
  const icon = document.createElement("span");
  icon.className = "file-change-icon";
  icon.innerHTML = uiIcon(data.action === "created" ? "plus" : "edit");
  const title = document.createElement("strong");
  title.textContent = data.action === "created" ? t("Fichier créé") : t("Fichier modifié");
  summary.append(icon, title);
  stamp(summary, data.timestamp);
  summary.insertAdjacentHTML("beforeend", uiIcon("chevron", "summary-chevron"));

  const body = document.createElement("div");
  body.className = "file-change-body";
  const header = document.createElement("div");
  header.className = "file-change-head";
  const path = document.createElement("code");
  path.textContent = data.path || t("fichier");
  const stats = document.createElement("span");
  stats.className = "diff-stats";
  const added = document.createElement("b");
  added.className = "diff-added";
  added.textContent = `+${Number(data.added || 0)}`;
  const removed = document.createElement("b");
  removed.className = "diff-removed";
  removed.textContent = `−${Number(data.removed || 0)}`;
  const copy = document.createElement("button");
  copy.className = "copy-diff";
  copy.type = "button";
  copy.title = t("Copier le diff");
  copy.setAttribute("aria-label", t("Copier le diff"));
  copy.innerHTML = uiIcon("copy");
  stats.append(added, removed, copy);
  header.append(path, stats);

  const scroller = document.createElement("div");
  scroller.className = "diff-scroller";
  let oldLine = 0;
  let newLine = 0;
  for (const rawLine of String(data.diff || "").split("\n")) {
    if (rawLine.startsWith("---") || rawLine.startsWith("+++")) continue;
    const hunk = rawLine.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/);
    if (hunk) {
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[2]);
      const row = document.createElement("div");
      row.className = "diff-line hunk";
      const marker = document.createElement("span");
      marker.textContent = "···";
      const code = document.createElement("code");
      code.textContent = rawLine;
      row.append(marker, code);
      scroller.appendChild(row);
      continue;
    }
    if (!rawLine && oldLine === 0 && newLine === 0) continue;
    const kind = rawLine.startsWith("+") ? "added" : rawLine.startsWith("-") ? "removed" : "context";
    if (rawLine.startsWith("\\ No newline")) continue;
    const row = document.createElement("div");
    row.className = `diff-line ${kind}`;
    const oldNumber = document.createElement("span");
    oldNumber.className = "diff-number";
    const newNumber = document.createElement("span");
    newNumber.className = "diff-number";
    const prefix = document.createElement("span");
    prefix.className = "diff-prefix";
    const code = document.createElement("code");
    if (kind === "added") {
      newNumber.textContent = String(newLine++);
      prefix.textContent = "+";
      code.textContent = rawLine.slice(1);
    } else if (kind === "removed") {
      oldNumber.textContent = String(oldLine++);
      prefix.textContent = "−";
      code.textContent = rawLine.slice(1);
    } else {
      oldNumber.textContent = String(oldLine++);
      newNumber.textContent = String(newLine++);
      prefix.textContent = " ";
      code.textContent = rawLine.startsWith(" ") ? rawLine.slice(1) : rawLine;
    }
    row.append(oldNumber, newNumber, prefix, code);
    scroller.appendChild(row);
  }
  body.append(header, scroller);
  details.append(summary, body);
  wireFileChangeCopy(details);
  return details;
}

function addFileChange(data) {
  const run = ensureRun();
  run.article.insertBefore(createFileChange(data), run.content);
  persist();
  scrollToBottom();
}

function addArchivedFileChange(data) {
  removeWelcome();
  const article = document.createElement("article");
  article.className = "message assistant archived-file-change";
  article.appendChild(createFileChange({ ...data, timestamp: data.timestamp || null }));
  stamp(article, data.timestamp || null);
  messages.appendChild(article);
}

function renderArchiveMessages(items) {
  if (currentRun) window.clearInterval(currentRun.timer);
  currentRun = null;
  activityRows.clear();
  approvalCards.clear();
  messages.innerHTML = "";
  for (const item of items || []) {
    if (item.type === "user") addUserMessage(item.text || "", item.images || [], item.timestamp || null);
    if (item.type === "file_change") addArchivedFileChange(item);
    if (item.type === "response") addArchivedAssistant(item.html || "", false, item.timestamp || null);
    if (item.type === "error") addArchivedAssistant(item.html || "", true, item.timestamp || null);
  }
  if (!messages.children.length) {
    messages.innerHTML = OvhI18n.markup("<section class=\"welcome\"><h2>Conversation vide</h2><p>Cette archive ne contient pas encore de réponse.</p></section>");
  }
  persist();
  scrollToBottom();
}

function applySnapshot(data) {
  if (!data.html) return;
  if (currentRun) window.clearInterval(currentRun.timer);
  currentRun = null;
  activityRows.clear();
  approvalCards.clear();
  messages.innerHTML = safeHtml(data.html);
  upgradeSnapshotIcons(messages);
  restoreTimestamps(messages, data.timeline || []);
  OvhI18n.translateSnapshot(messages);
  messages.querySelectorAll("details.activity").forEach(activity => { activity.open = false; });
  for (const article of messages.querySelectorAll(".message.failure")) article.remove();
  for (const article of messages.querySelectorAll(".message.assistant")) {
    if (article.textContent.includes(t("Aucune clé OVH n’est configurée"))) article.remove();
  }
  for (const article of messages.querySelectorAll(".message.working")) {
    article.classList.remove("working");
    article.classList.add("stopped");
  }
  for (const button of messages.querySelectorAll(".approval-actions button")) button.disabled = true;
  for (const content of messages.querySelectorAll(".markdown")) enhanceMarkdown(content);
  for (const change of messages.querySelectorAll(".file-change")) wireFileChangeCopy(change);
  // Restored content must not change the access mode chosen in this webview.
  if (data.model) selectedModel = data.model;
  updateModelButton();
  updateAccessButton();
  persist();
  scrollToBottom();
}

function renderArchives(items) {
  archiveList.innerHTML = "";
  if (!items?.length) {
    const empty = document.createElement("p");
    empty.className = "archive-empty";
    empty.textContent = t("Aucune conversation archivée.");
    archiveList.appendChild(empty);
    return;
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = `archive-item${item.current ? " current" : ""}`;
    row.dataset.archiveId = item.id;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "archive-open";
    const rawTitle = item.title === "Nouvelle conversation" ? t("Nouvelle conversation") : (item.title || t("Nouvelle conversation"));
    const displayTitle = String(rawTitle).replace(/\p{L}/u, letter => letter.toLocaleUpperCase(uiLanguage));
    const title = document.createElement("strong");
    title.textContent = displayTitle;
    const meta = document.createElement("span");
    const date = item.updated_at ? new Date(item.updated_at) : null;
    const dateText = date && !Number.isNaN(date.valueOf())
      ? date.toLocaleString(uiLanguage, { dateStyle: "short", timeStyle: "short" })
      : item.id;
    meta.textContent = [dateText, item.model].filter(Boolean).join(" · ");
    button.append(title, meta);
    const openArchive = () => {
      activeArchiveId = item.id;
      updateSendButton();
      setArchiveVisible(false);
      setStatus(t("Chargement de la conversation…"));
      vscode.postMessage({ type: "loadArchive", id: item.id });
    };
    button.addEventListener("click", openArchive);
    const view = document.createElement("button");
    view.type = "button";
    view.className = "icon archive-action archive-view";
    view.title = t("Consulter la conversation archivée");
    view.setAttribute("aria-label", `${view.title} : ${displayTitle}`);
    view.innerHTML = uiIcon("archive");
    view.addEventListener("click", openArchive);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon archive-action archive-delete";
    remove.title = t("Supprimer définitivement la conversation");
    remove.setAttribute("aria-label", t("Supprimer définitivement : {0}", displayTitle));
    remove.innerHTML = uiIcon("trash");
    remove.addEventListener("click", () => {
      if (busy) { setStatus(t("Attends la fin de la réponse avant de supprimer une conversation.")); return; }
      selectedArchiveForDeletion = item.id;
      document.getElementById("delete-title").textContent = displayTitle;
      document.getElementById("delete-dialog").showModal();
      document.getElementById("delete-cancel").focus();
    });
    row.append(button, view);
    if (item.resumable) {
      const resume = document.createElement("button");
      resume.type = "button";
      resume.className = "icon archive-action archive-resume";
      resume.setAttribute("aria-label", t("Reprendre la tâche interrompue"));
      resume.innerHTML = uiIcon("reload");
      resume.title = t("Reprendre la tâche interrompue après vérification de son état");
      resume.addEventListener("click", () => {
        if (busy) { setStatus(t("Arrête la tâche en cours avant de reprendre une autre conversation.")); return; }
        resumeArchive(item.id);
      });
      row.appendChild(resume);
    }
    row.appendChild(remove);
    archiveList.appendChild(row);
  }
}

function createAssistantRun() {
  removeWelcome();
  const article = document.createElement("article");
  article.className = "message assistant working";

  const label = document.createElement("div");
  label.className = "label";
  label.textContent = brandName;

  const activity = document.createElement("details");
  activity.className = "activity";
  activity.open = false;
  activity.addEventListener("toggle", persist);
  const summary = document.createElement("summary");
  const spinner = document.createElement("span");
  spinner.className = "run-indicator";
  spinner.innerHTML = uiIcon("terminal");
  const summaryText = document.createElement("span");
  summaryText.className = "activity-summary-text";
  summaryText.textContent = t("Réflexion en cours…");
  summary.append(spinner, summaryText);
  summary.insertAdjacentHTML("beforeend", uiIcon("chevron", "summary-chevron"));
  const list = document.createElement("div");
  list.className = "activity-list";
  activity.append(summary, list);

  const content = document.createElement("div");
  content.className = "content markdown streaming";
  content.innerHTML = "";
  article.append(label, activity, content);
  stamp(article);
  messages.appendChild(article);

  const run = {
    article,
    activity,
    list,
    summaryText,
    content,
    startedAt: Date.now(),
    actions: 0,
    timer: undefined,
  };
  run.timer = window.setInterval(() => updateRunSummary(run), 1000);
  currentRun = run;
  persist();
  scrollToBottom();
  return run;
}

function ensureRun() {
  return currentRun || createAssistantRun();
}

function updateRunSummary(run, finished = false) {
  const count = run.actions;
  const actionLabel = t("{0} action{1}", count, count === 1 ? "" : "s");
  run.summaryText.textContent = finished
    ? t("Terminé en {0} · {1}", formatElapsed(run.startedAt), actionLabel)
    : (run.currentAction || t("Réflexion en cours…"));
  run.summaryText.title = `${run.summaryText.textContent} · ${formatElapsed(run.startedAt)} · ${actionLabel}`;
}

function finishRun(state = "done") {
  if (!currentRun) return;
  const run = currentRun;
  window.clearInterval(run.timer);
  updateRunSummary(run, true);
  run.article.classList.remove("working");
  run.article.classList.add(state);
  run.content.classList.remove("streaming");
  enhanceMarkdown(run.content);
  // Preserve the user’s expanded/collapsed choice through completion.
  persist();
  currentRun = null;
  scrollToBottom();
}

function alignTextTables(source) {
  if (source.length > 100000) return source;
  const lines = source.split("\n");
  for (let start = 0; start < lines.length; start++) {
    const top = /^( *)┌[─┬]+┐ *$/.exec(lines[start]);
    if (!top) continue;
    const indent = top[1];
    let end = start + 1;
    while (end < lines.length && end - start <= 200 && !lines[end].startsWith(indent + "└")) end++;
    if (end >= lines.length || end - start > 200 || !/^└[─┴]+┘ *$/.test(lines[end].slice(indent.length))) continue;
    const rows = lines.slice(start, end + 1).map(line => {
      if (!line.startsWith(indent)) return null;
      const text = line.slice(indent.length).trimEnd();
      // Pad only single-cell-width Latin text and box drawing, never guess emoji/CJK widths.
      if (!/^[\x20-\x7e\u00a0-\u024f\u2500-\u257f]+$/.test(text)) return null;
      if (/^│.*│$/.test(text)) return {cells: text.slice(1, -1).split("│").map(cell => cell.trimEnd())};
      if (/^[┌├└][─┬┼┴]+[┐┤┘]$/.test(text)) return {border: text};
      return null;
    });
    if (rows.some(row => !row)) continue;
    const body = rows.filter(row => row.cells);
    const columns = Math.max(0, ...body.map(row => row.cells.length));
    if (!columns || columns > 16 || body.some(row => row.cells.length !== 1 && row.cells.length !== columns)) continue;
    const widths = Array.from({length: columns}, (_, column) =>
      Math.max(...body.filter(row => row.cells.length === columns).map(row => row.cells[column].length + 1)));
    let innerWidth = widths.reduce((sum, width) => sum + width, 0) + columns - 1;
    const extraWidth = Math.max(0, rows[0].border.length - 2 - innerWidth);
    widths[columns - 1] += extraWidth;
    innerWidth += extraWidth;
    for (const row of body.filter(row => row.cells.length < columns)) {
      const extra = Math.max(0, row.cells[0].trim().length + 2 - innerWidth);
      widths[columns - 1] += extra;
      innerWidth += extra;
    }
    const aligned = rows.map(row => {
      if (row.border) {
        const joints = row.border.slice(1, -1).match(/[┬┼┴]/g) || [];
        if (joints.length && joints.length !== columns - 1) return null;
        const middle = joints.length ? widths.map((width, i) => "─".repeat(width) + (joints[i] || "")).join("") : "─".repeat(innerWidth);
        return indent + row.border[0] + middle + row.border.at(-1);
      }
      if (row.cells.length === columns) return indent + "│" + row.cells.map((cell, i) => cell.padEnd(widths[i])).join("│") + "│";
      const title = row.cells[0].trim();
      return indent + "│" + (" ".repeat(Math.floor((innerWidth - title.length) / 2)) + title).padEnd(innerWidth) + "│";
    });
    if (aligned.some(line => line === null) || aligned.join("\n").length > 100000) continue;
    lines.splice(start, aligned.length, ...aligned);
    start = end;
  }
  return lines.join("\n");
}

function highlightCodeBlock(code, streaming) {
  if (!code || !window.hljs || code.dataset.highlighted === "yes") return;
  let source = code.textContent || "";
  const language = (Array.from(code.classList).find(name => name.startsWith("language-")) || "").slice(9).toLowerCase();
  // Repair complete text tables before colouring; explicit program source stays verbatim.
  if (!language || ["text", "txt", "plaintext", "ascii"].includes(language)) {
    source = alignTextTables(source);
    if (source !== code.textContent) code.textContent = source;
  }
  code.classList.add("hljs");
  // Bound work while streaming; detect unlabelled snippets only when complete.
  if (source.length <= 100000 && !code.classList.contains("no-highlight") && !code.classList.contains("nohighlight")) {
    try {
      let result;
      if (language && window.hljs.getLanguage(language)) {
        result = window.hljs.highlight(source, { language, ignoreIllegals: true });
      } else if (!language && !streaming && source.length <= 8000) {
        result = window.hljs.highlightAuto(source, ["bash", "javascript", "typescript", "python", "json", "yaml", "xml", "css", "sql"]);
      }
      if (result) code.innerHTML = safeHtml(result.value);
    } catch { /* Keep the exact source readable if a grammar fails. */ }
  }
  if (language || !streaming) code.dataset.highlighted = "yes";
}

function enhanceMarkdown(container) {
  for (const table of container.querySelectorAll("table")) {
    if (table.parentElement?.classList.contains("table-scroll")) continue;
    const wrapper = document.createElement("div");
    wrapper.className = "table-scroll";
    table.before(wrapper);
    wrapper.appendChild(table);
  }
  for (const block of container.querySelectorAll("pre")) {
    block.querySelector(":scope > .copy-code")?.remove();
    highlightCodeBlock(block.querySelector(":scope > code"), container.classList.contains("streaming"));
    const button = document.createElement("button");
    button.className = "copy-code";
    button.type = "button";
    button.textContent = t("Copier");
    button.addEventListener("click", async () => {
      const code = block.querySelector("code")?.textContent || block.textContent || "";
      await navigator.clipboard.writeText(code);
      button.textContent = t("Copié");
      window.setTimeout(() => { button.textContent = t("Copier"); }, 1200);
    });
    block.appendChild(button);
  }
}

function setMarkdown(html) {
  const run = ensureRun();
  run.content.innerHTML = safeHtml(html);
  enhanceMarkdown(run.content);
  persist();
  scrollToBottom();
}

function iconForTool(tool) {
  const name = { list: "list", read: "read", search: "search", edit: "edit", write: "edit",
    command: "terminal", route: "reload", context: "context", cost: "cost", model: "model" }[tool];
  return typeof name === "string" ? name : "terminal";
}

// Old saved conversations retain their content while adopting the current icon set.
function upgradeSnapshotIcons(root) {
  for (const indicator of root.querySelectorAll(".run-indicator, .run-spinner")) {
    indicator.classList.remove("run-spinner");
    indicator.classList.add("run-indicator");
    indicator.innerHTML = uiIcon("terminal");
  }
  for (const icon of root.querySelectorAll(".file-change-icon")) {
    if (!icon.querySelector(".agent-icon")) icon.innerHTML = uiIcon(icon.textContent.trim() === "+" ? "plus" : "edit");
  }
  const old = { "≡":"list", "⌕":"search", "✎":"edit", ">_":"terminal", "↻":"reload", "≋":"context", "$":"cost", "◆":"model", "✓":"check", "!":"error" };
  for (const icon of root.querySelectorAll(".activity-icon")) {
    if (!icon.querySelector(".agent-icon")) {
      const name = old[icon.textContent.trim()];
      icon.innerHTML = uiIcon(typeof name === "string" ? name : "terminal");
    }
  }
  for (const summary of root.querySelectorAll(".activity > summary, .file-change > summary")) {
    if (!summary.querySelector(".summary-chevron")) summary.insertAdjacentHTML("beforeend", uiIcon("chevron", "summary-chevron"));
  }
}

function addActivity(data) {
  const run = ensureRun();
  run.actions += 1;
  run.activeId = data.id;
  run.currentAction = [data.title || t("Action en cours"), data.detail || ""].filter(Boolean).join(" · ").replace(/\s+/g, " ");
  updateRunSummary(run);
  const row = document.createElement("div");
  row.className = "activity-row running";
  const icon = document.createElement("span");
  icon.className = "activity-icon";
  icon.innerHTML = uiIcon(iconForTool(data.tool));
  const body = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = data.title || t("Action");
  const detail = document.createElement("code");
  detail.textContent = data.detail || "";
  body.append(title, detail);
  stamp(body, data.timestamp);
  row.append(icon, body);
  run.list.appendChild(row);
  activityRows.set(data.id, { row, icon, detail });
  persist();
  scrollToBottom();
}

function finishActivity(data) {
  const item = activityRows.get(data.id);
  if (!item) return;
  item.row.classList.remove("running");
  item.row.classList.add(data.success ? "success" : "failure");
  item.icon.innerHTML = uiIcon(data.success ? "check" : "error");
  if (currentRun?.activeId === data.id) {
    // Keep the last displayed action until the next activity arrives.
    currentRun.activeId = null;
    updateRunSummary(currentRun);
  }
  item.detail.textContent = data.detail || "";
  persist();
}

function addCompletedActivity(tool, title, detail) {
  const id = `${tool}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  addActivity({ id, tool, title, detail });
  finishActivity({ id, success: true, detail });
}

function formatModelCost(value, currency = "EUR") {
  return formatMoney(value, currency);
}

function formatMoney(value, currency = "EUR") {
  try {
    return new Intl.NumberFormat(uiLanguage, {
      style: "currency",
      currency: String(currency || "EUR").toUpperCase(),
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(Number(value || 0));
  } catch {
    return `${Number(value || 0).toFixed(2)} ${currency || "EUR"}`;
  }
}

function applyBillingStatus(data, announce = true) {
  if (Number.isFinite(data.refresh_seconds) && data.refresh_seconds > 0) scheduleBillingRefresh(data.refresh_seconds);
  if (data.pending) {
    billing.classList.remove("connected");
    billing.classList.add("failed");
    billing.title = t("Vérification de la connexion OVHcloud…");
    liveBalance.textContent = t("Solde : vérification…");
    return;
  }
  if (!data.configured) liveBalance.textContent = t("Solde : à configurer");
  else if (!data.available) liveBalance.textContent = t("Solde : indisponible");
  else {
    const value = Number(data.balance_estimated ? data.credit_remaining ?? data.credit_remaining_display : data.credit_available || 0);
    const amount = document.createElement("span");
    amount.className = "balance-amount";
    amount.classList.toggle("negative", value < 0);
    amount.textContent = (value < 0 ? "-" : "") + formatMoney(Math.abs(value), data.credit_currency || data.currency);
    const label = data.balance_estimated ? "Solde estimé : {0}{1}" : "Crédit OVH : {0}{1}";
    const [prefix, suffix = ""] = t(label, "\uFFFC", data.stale ? t(" (non actualisé)") : "").split("\uFFFC");
    liveBalance.replaceChildren(document.createTextNode(prefix), amount, document.createTextNode(suffix));
  }
  liveBalance.title = t("Cliquer pour configurer le suivi : identifiants API OVHcloud et projet Public Cloud requis (distincts de la clé AI Endpoints)");
  billing.classList.toggle("connected", Boolean(data.available && !data.stale));
  billing.classList.toggle("failed", Boolean(!data.available || data.stale));
  if (!data.configured) {
    billing.title = t("Configurer le suivi du solde OVHcloud");
    if (announce) setStatus(t("Solde OVHcloud non configuré"));
    return;
  }
  if (!data.available) {
    billing.title = t("Facturation OVHcloud indisponible{0}", data.error ? ` · ${data.error}` : "");
    if (announce) setStatus(t("Impossible de consulter la facturation OVHcloud"), true);
    return;
  }
  const currency = data.currency || "EUR";
  const credit = formatMoney(data.credit_available, data.credit_currency || currency);
  const balance = formatMoney(data.credit_remaining, currency);
  const current = formatMoney(data.current_usage, currency);
  const forecast = formatMoney(data.forecast_usage, currency);
  const calculation = data.balance_estimated
    ? t("Solde estimé {0} = crédit OVH {1} − consommation courante du projet {2}. Montants affichés au centime ; calculs conservés sans arrondi intermédiaire.", balance, credit, current)
    : t("Crédit OVH {0} · consommation {1} · déduction indisponible : {2}", credit, current, t(data.balance_error || "inconnu"));
  const freshness = t("Relevé OVH {0} · vérification toutes les {1} s. La consommation peut être publiée avec retard ; les dépenses de l’agent sont comprises dans le relevé OVH et ne sont pas déduites une seconde fois.", data.last_update || t("inconnu"), data.refresh_seconds || 60);
  liveBalance.title = `${calculation}\n${freshness}${data.stale ? t(" · dernière valeur connue") : ""}`;
  billing.title = `${liveBalance.title}\n${t("Prévision de consommation {0} · cliquer pour actualiser", forecast)}`;
  if (announce) setStatus(calculation);
}

function addApproval(data) {
  const run = ensureRun();
  const card = document.createElement("section");
  card.className = "approval-card";
  const heading = document.createElement("strong");
  heading.textContent = data.title || t("Autorisation requise");
  const detail = document.createElement("code");
  detail.textContent = data.detail || "";
  card.append(heading, detail);

  if (data.diff) {
    const diff = document.createElement("pre");
    diff.className = "diff";
    diff.textContent = data.diff;
    card.appendChild(diff);
  }

  const result = document.createElement("span");
  result.className = "approval-result";
  const actions = document.createElement("div");
  actions.className = "approval-actions";
  const deny = document.createElement("button");
  deny.className = "deny";
  deny.textContent = t("Refuser");
  const allow = document.createElement("button");
  allow.className = "allow";
  allow.textContent = t("Autoriser");
  const answer = (accepted) => {
    deny.disabled = true;
    allow.disabled = true;
    result.textContent = accepted ? t("Autorisation envoyée") : t("Refus envoyé");
    vscode.postMessage({ type: "approval", id: data.id, allow: accepted });
    persist();
  };
  deny.addEventListener("click", () => answer(false));
  allow.addEventListener("click", () => answer(true));
  actions.append(result, deny, allow);
  card.appendChild(actions);
  stamp(card, data.timestamp);
  run.list.appendChild(card);
  approvalCards.set(data.id, { card, deny, allow, result });
  persist();
  scrollToBottom();
}

function resolveApproval(data) {
  const item = approvalCards.get(data.id);
  if (!item) return;
  item.deny.disabled = true;
  item.allow.disabled = true;
  item.card.classList.add(data.allowed ? "approved" : "rejected");
  item.result.textContent = data.allowed ? t("Autorisé") : t("Refusé");
  persist();
}

function dataUrlBytes(dataUrl) {
  const payload = String(dataUrl).split(",", 2)[1] || "";
  return Math.ceil(payload.length * 0.75);
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error(`Impossible de lire ${file.name}.`));
    reader.readAsDataURL(file);
  });
}

function loadImage(dataUrl) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error(t("Cette image est illisible.")));
    image.src = dataUrl;
  });
}

async function prepareImage(file) {
  if (!supportedImageTypes.has(file.type)) {
    throw new Error(t("{0} : format non pris en charge. Utilise JPEG, PNG, WebP ou GIF.", file.name));
  }
  const originalDataUrl = await readFileAsDataUrl(file);
  const image = await loadImage(originalDataUrl);
  const maxDimension = 2560;
  const needsResize = image.naturalWidth > maxDimension || image.naturalHeight > maxDimension;
  if (!needsResize && file.size <= maxClientImageBytes) {
    return { name: file.name, media_type: file.type, data_url: originalDataUrl };
  }

  const scale = Math.min(1, maxDimension / Math.max(image.naturalWidth, image.naturalHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
  canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
  const context = canvas.getContext("2d", { alpha: true });
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  let dataUrl = canvas.toDataURL("image/webp", 0.92);
  if (dataUrlBytes(dataUrl) > maxClientImageBytes) dataUrl = canvas.toDataURL("image/webp", 0.76);
  if (dataUrlBytes(dataUrl) > maxClientImageBytes) dataUrl = canvas.toDataURL("image/webp", 0.58);
  if (dataUrlBytes(dataUrl) > maxClientImageBytes) {
    throw new Error(t("{0} reste trop volumineuse après optimisation.", file.name));
  }
  const stem = file.name.replace(/\.[^.]+$/, "") || "image";
  return { name: `${stem}.webp`, media_type: "image/webp", data_url: dataUrl };
}

function renderPendingImages() {
  attachments.innerHTML = "";
  attachments.hidden = pendingImages.length === 0;
  pendingImages.forEach((image, index) => {
    const card = document.createElement("div");
    card.className = "attachment-preview";
    const thumbnail = document.createElement("img");
    thumbnail.src = image.data_url;
    thumbnail.alt = image.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.title = t("Retirer {0}", image.name);
    remove.setAttribute("aria-label", t("Retirer {0}", image.name));
    remove.innerHTML = uiIcon("close");
    remove.addEventListener("click", () => {
      pendingImages.splice(index, 1);
      renderPendingImages();
      prompt.focus();
    });
    card.append(thumbnail, remove);
    attachments.appendChild(card);
  });
}

function ensureVisionModel() {
  const current = modelCatalog.find((item) => item.id === selectedModel);
  if (selectedModel === "auto-balanced" || !modelCatalog.length || current?.vision) return;
  // Le serveur route uniquement cette demande vers un modèle Vision.
  // La préférence de l’utilisateur reste inchangée.
  setStatus(t("Un modèle Vision sera utilisé pour cette image ; ton modèle sélectionné est conservé."));
}

async function addImageFiles(files) {
  const candidates = Array.from(files || []).filter((file) => file.type.startsWith("image/"));
  if (!candidates.length) return;
  const available = maxImages - pendingImages.length;
  if (available <= 0) {
    setStatus(t("Maximum {0} images par message.", maxImages));
    return;
  }
  if (candidates.length > available) setStatus(t("Seules les {0} premières images ont été ajoutées.", available));
  for (const file of candidates.slice(0, available)) {
    try {
      setStatus(t("Préparation de {0}…", file.name));
      pendingImages.push(await prepareImage(file));
      renderPendingImages();
    } catch (error) {
      setStatus(error.message || t("Impossible d’ajouter cette image."), true);
    }
  }
  ensureVisionModel();
  if (pendingImages.length) setStatus(t("{0} image{1} prête{2}", pendingImages.length, pendingImages.length > 1 ? "s" : "", pendingImages.length > 1 ? "s" : ""));
  prompt.focus();
}

function submit() {
  const text = prompt.value.trim();
  if ((!text && !pendingImages.length) || pendingFollowup) return;
  if (busy || editingFollowup) {
    if (!activeArchiveId) { setStatus(t("Connexion en cours…")); return; }
    if (pendingImages.length) { setStatus(t("Les messages en attente acceptent du texte uniquement.")); return; }
    if (text.length > 16000) { setStatus(t("Message limité à 16 000 caractères.")); return; }
    const id = editingFollowup || crypto.randomUUID();
    pendingFollowup = {id, text};
    send.disabled = true;
    followupCommand(editingFollowup ? "edit" : "add", {id, text, mode: followupMode.value, fullAccess, model: selectedModel});
    return;
  }
  ensureVisionModel();
  const images = pendingImages.slice();
  const displayText = text || t("Analyse cette image.");
  if (promptHistory.at(-1) !== displayText) promptHistory.push(displayText);
  promptHistory = promptHistory.slice(-200);
  historyIndex = promptHistory.length;
  historyDraft = "";
  addUserMessage(displayText, images);
  createAssistantRun();
  prompt.value = "";
  updateStopIcon();
  prompt.style.height = "auto";
  pendingImages = [];
  renderPendingImages();
  setBusy(true, t("Connexion à OVHcloud…"));
  vscode.postMessage({ type: "prompt", text: displayText, fullAccess, model: selectedModel, images });
}

if (saved?.html) {
  messages.innerHTML = safeHtml(saved.html);
  upgradeSnapshotIcons(messages);
  restoreTimestamps(messages);
  OvhI18n.translateSnapshot(messages);
  for (const button of messages.querySelectorAll(".approval-actions button")) button.disabled = true;
  for (const content of messages.querySelectorAll(".markdown")) enhanceMarkdown(content);
  for (const change of messages.querySelectorAll(".file-change")) wireFileChangeCopy(change);
}

if (!promptHistory.length) {
  promptHistory = Array.from(messages.querySelectorAll(".message.user .content"), item => item.textContent).filter(Boolean).slice(-200);
  historyIndex = promptHistory.length;
}
send.addEventListener("click", () => {
  if (!busy && !editingFollowup && !prompt.value.trim() && resumableArchives.has(activeArchiveId)) resumeArchive(activeArchiveId);
  else submit();
});
followupMode.addEventListener("change", updateSendButton);
attach.addEventListener("click", () => imageInput.click());
imageInput.addEventListener("change", async () => {
  await addImageFiles(imageInput.files);
  imageInput.value = "";
});
stop.addEventListener("click", () => vscode.postMessage({ type: "cancel" }));
clear.addEventListener("click", () => {
  if (creatingConversation) return;
  creatingConversation = true;
  window.clearTimeout(snapshotTimer);
  clear.disabled = true;
  activeArchiveId = "";
  followups = [];
  editingFollowup = null;
  pendingFollowup = null;
  renderFollowups();
  updateSendButton();
  setArchiveVisible(false);
  if (currentRun) window.clearInterval(currentRun.timer);
  currentRun = null;
  activityRows.clear();
  approvalCards.clear();
  messages.innerHTML = OvhI18n.markup("<section class=\"welcome\"><h2></h2><p>Je peux analyser des images, lire et modifier les fichiers, rechercher dans le projet et exécuter des commandes.</p></section>");
  messages.querySelector(".welcome h2").textContent = brandName;
  pendingImages = [];
  renderPendingImages();
  persist();
  vscode.postMessage({ type: "clear" });
  setStatus(t("Nouvelle conversation"));
  prompt.focus();
});
configure.addEventListener("click", () => vscode.postMessage({ type: "configureKey" }));
billing.addEventListener("click", () => {
  setStatus(t("Actualisation du solde OVHcloud…"));
  vscode.postMessage({ type: "billing" });
});
history.addEventListener("click", () => {
  modelPanel.hidden = true;
  setArchiveVisible(true);
  hideSelectionQuote();
  vscode.postMessage({ type: "listArchives" });
});
closeHistory?.addEventListener("click", () => {
  document.getElementById("archive-banner").hidden = true;
});
modelPicker.addEventListener("click", () => {
  setArchiveVisible(false);
  modelPanel.hidden = !modelPanel.hidden;
  if (!modelPanel.hidden) {
    modelSearch.focus();
    vscode.postMessage({ type: "listModels" });
  }
});
closeModels.addEventListener("click", () => { modelPanel.hidden = true; });
refreshModels.addEventListener("click", () => {
  setStatus(t("Actualisation du catalogue OVHcloud…"));
  vscode.postMessage({ type: "listModels", refresh: true });
});
modelSearch.addEventListener("input", renderModels);
access.addEventListener("click", () => {
  fullAccess = !fullAccess;
  updateAccessButton();
  setStatus(fullAccess
    ? t("Accès complet activé")
    : t("Validation activée pour les actions sensibles"));
});
prompt.addEventListener("keydown", (event) => {
  if (event.isComposing) return;
  if (event.key === "Escape" && editingFollowup) {
    editingFollowup = null;
    updateSendButton();
    setStatus(t("Modification annulée ; brouillon conservé."));
    event.preventDefault();
    return;
  }
  if ((event.key === "ArrowUp" || event.key === "ArrowDown") && !event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey && prompt.selectionStart === prompt.selectionEnd) {
    const up = event.key === "ArrowUp";
    const atBoundary = up ? !prompt.value.slice(0, prompt.selectionStart).includes("\n") : !prompt.value.slice(prompt.selectionEnd).includes("\n");
    if (atBoundary && promptHistory.length && (up || historyIndex < promptHistory.length)) {
      event.preventDefault();
      if (historyIndex === promptHistory.length) historyDraft = prompt.value;
      historyIndex = Math.max(0, Math.min(promptHistory.length, historyIndex + (up ? -1 : 1)));
      prompt.value = historyIndex === promptHistory.length ? historyDraft : promptHistory[historyIndex];
      prompt.setSelectionRange(up ? 0 : prompt.value.length, up ? 0 : prompt.value.length);
      resizePrompt();
      return;
    }
  }
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    submit();
  }
});
function resizePrompt() {
  updateStopIcon();
  prompt.style.height = "auto";
  prompt.style.height = `${Math.min(prompt.scrollHeight, 180)}px`;
}
prompt.addEventListener("input", () => {
  historyIndex = promptHistory.length;
  historyDraft = prompt.value;
  resizePrompt();
});
prompt.addEventListener("paste", async (event) => {
  const imageFiles = Array.from(event.clipboardData?.files || []).filter((file) => file.type.startsWith("image/"));
  if (!imageFiles.length) return;
  event.preventDefault();
  await addImageFiles(imageFiles);
});
composer.addEventListener("dragover", (event) => {
  event.preventDefault();
  composer.classList.add("dragging");
});
composer.addEventListener("dragleave", (event) => {
  if (!composer.contains(event.relatedTarget)) composer.classList.remove("dragging");
});
composer.addEventListener("drop", async (event) => {
  event.preventDefault();
  composer.classList.remove("dragging");
  await addImageFiles(event.dataTransfer?.files);
});
messages.addEventListener("click", (event) => {
  const anchor = event.target.closest("a");
  if (!anchor) return;
  event.preventDefault();
  vscode.postMessage({ type: "openLink", href: anchor.getAttribute("href") });
});

window.addEventListener("message", ({ data }) => {
  if (creatingConversation) {
    if (data.type === "ready") { backendReady = true; return; }
    if (data.type === "cleared") {
      creatingConversation = false;
      clear.disabled = false;
    } else if (data.type === "error") {
      creatingConversation = false;
      clear.disabled = false;
    } else if (!["billing_status", "billing_configured", "budget_status", "configured"].includes(data.type)) return;
  }
  if (["status", "error", "archive_delete_failed"].includes(data.type)) data = {...data, text: OvhI18n.message(data.text)};
  if (["tool_start", "approval"].includes(data.type)) data = {...data, title: OvhI18n.message(data.title)};
  if (data.type === "tool_end") data = {...data, detail: OvhI18n.message(data.detail)};
  if (data.type === "model_routed") data = {...data, reason: OvhI18n.message(data.reason)};
  if (data.type === "ready") {
    activeArchiveId = data.conversation || activeArchiveId;
    updateSendButton();
    const archivePath = document.getElementById("archive-path");
    if (archivePath) {
      archivePath.textContent = data.archive_root || t("Chemin d’archivage indisponible");
      archivePath.title = data.archive_root || "";
    }
    backendReady = true;
    followupCommand("list");
    if (data.model) selectedModel = data.model;
    updateModelButton();
    setBusy(busy, busy ? status.textContent : t("Connecté · {0}", data.model));
    persist();
    vscode.postMessage({ type: "listModels" });
  }
  if (data.type === "followups" && data.conversation === activeArchiveId) {
    followups = data.items || [];
    if (editingFollowup && !followups.some(item => item.id === editingFollowup)) editingFollowup = null;
    updateSendButton();
    renderFollowups();
  }
  if (data.type === "followup_ack" && pendingFollowup?.id === data.id) {
    if (prompt.value.trim() === pendingFollowup.text) prompt.value = "";
    pendingFollowup = null;
    editingFollowup = null;
    send.disabled = false;
    resizePrompt();
    updateSendButton();
  }
  if (data.type === "followup_error") {
    pendingFollowup = null;
    send.disabled = false;
    setStatus(t("Message non envoyé : {0}", OvhI18n.message(data.text)), true);
  }
  if (data.type === "followup_started") {
    addUserMessage(data.text, [], data.timestamp);
    createAssistantRun();
    setBusy(true);
  }
  if (data.type === "steer_applied") {
    addUserMessage(data.text, [], data.timestamp);
    if (currentRun?.article) messages.append(currentRun.article);
    addCompletedActivity("context", t("Réorientation appliquée"), data.text);
  }
  if (data.type === "status") {
    activeArchiveId = data.conversation || activeArchiveId;
    followupCommand("list");
    ensureRun();
    setBusy(true, data.text);
  }
  if (data.type === "session_busy") {
    if (currentRun) finishRun("stopped");
    setBusy(false, OvhI18n.message(data.text), true);
  }
  if (data.type === "tool_start") addActivity(data);
  if (data.type === "tool_end") finishActivity(data);
  if (data.type === "file_change") addFileChange(data);
  if (data.type === "model_active") {
    const mode = data.automatic ? t("sélection automatique") : t("choix manuel verrouillé");
    addCompletedActivity("model", t("Modèle exécuté"), `${data.model} · ${mode}`);
  }
  if (data.type === "model_routed") {
    lastRoutedModel = data.model || "";
    updateModelButton();
    const projectedCost = data.projected_cost_eur ?? data.projected_cost_usd;
    const cost = projectedCost == null ? "" : t(" · estimation {0}", formatModelCost(projectedCost, data.projected_cost_eur != null ? "EUR" : "USD"));
    const pool = data.eligible_models ? t(" · {0} modèles économiques compatibles", data.eligible_models) : "";
    const history = data.previous_requests
      ? t(" · historique {0} requête{1} / {2}", data.previous_requests, data.previous_requests > 1 ? "s" : "", formatModelCost(data.previous_cost_eur ?? data.previous_cost_usd, data.previous_cost_eur != null ? "EUR" : "USD"))
      : "";
    const account = data.billing?.available
      ? t(data.billing.balance_estimated ? " · solde estimé {0} · prévision {1}" : " · crédits {0} · prévision {1}", formatMoney(data.billing.balance_estimated ? data.billing.credit_remaining ?? data.billing.credit_remaining_display : data.billing.credit_available, data.billing.credit_currency || data.billing.currency), formatMoney(data.billing.forecast_usage, data.billing.currency))
      : "";
    addCompletedActivity("route", t("Routage économique"), `${data.model} · ${data.reason || t("modèle compatible")}${cost}${pool}${history}${account}`);
  }
  if (data.type === "plan_updated") {
    const steps = Array.isArray(data.steps) ? data.steps : [];
    const current = Number(data.current_step) || 0;
    addCompletedActivity("context", t("Plan de la tâche"), steps.map((step, i) =>
      `${i < current ? "✓" : i === current ? "→" : "○"} ${i + 1}. ${step}`).join("\n"));
  }
  if (data.type === "request_prepared") {
    const sources = Array.isArray(data.sources) ? data.sources : [];
    const files = new Set(sources.map(source => source.path)).size;
    const detail = t("{0} fichiers ciblés · préparation locale : {1} ms", files, data.preparation_ms || 0);
    addCompletedActivity("context", t("Contexte préparé"), detail +
      (data.partial ? t(" · recherche partielle, complétée au besoin par les outils") : ""));
  }
  if (data.type === "context_compacted") {
    const usage = Number.isFinite(data.estimated_tokens)
      ? t("≈ {0} / {1} tokens de contexte", data.estimated_tokens, data.budget_tokens) : "";
    const mode = data.summary_mode === "semantic" ? t(" · résumé généré")
      : data.summary_mode === "extractive" ? t(" · extraits archivés") : "";
    const saved = data.saved_percent ? t(" · {0}% de contexte libéré", data.saved_percent) : "";
    addCompletedActivity("context", data.compacted ? t("Contexte compacté") : t("Budget de contexte"), usage + mode + saved);
  }
  if (data.type === "usage") {
    const timing = Number.isFinite(data.first_text_ms)
      ? t(" · premier texte : {0} s", (data.first_text_ms / 1000).toFixed(2)) : "";
    addCompletedActivity("cost", data.purpose === "compaction" ? t("Coût de la compaction") : t("Utilisation du modèle"), t("{0} · {1} tokens entrée · {2} sortie · {3}", data.model, data.input_tokens, data.output_tokens, formatModelCost(data.cost_eur ?? data.cost_usd, data.cost_eur != null ? "EUR" : "USD")) + timing);
  }
  if (data.type === "approval") addApproval(data);
  if (data.type === "approval_result") resolveApproval(data);
  if (data.type === "delta") setMarkdown(data.html);
  if (data.type === "response") {
    resumableArchives.delete(activeArchiveId);
    setMarkdown(data.html);
    finishRun("done");
    setBusy(false, "");
  }
  if (data.type === "error") {
    const detail = String(data.text || t("Erreur inconnue"));
    const run = ensureRun();
    const errorMessage = document.createElement("p");
    errorMessage.className = "error-message";
    errorMessage.textContent = t("Erreur : {0}", detail);
    run.content.appendChild(errorMessage);
    finishRun("error");
    status.title = detail;
    setBusy(false, t("La requête a échoué : {0}", detail), true);
  }
  if (data.type === "idle") {
    vscode.postMessage({ type: "listArchives" });
    if (currentRun) finishRun("stopped");
    setBusy(false, "");
  }
  if (data.type === "cleared") { activeArchiveId = data.conversation || ""; setBusy(false, t("Nouvelle conversation")); }
  if (data.type === "configured") setBusy(false, t("Clé OVH enregistrée de façon sécurisée"));
  if (data.type === "billing_configured") setBusy(false, t("Accès lecture seule à la facturation enregistré"));
  if (data.type === "billing_status") applyBillingStatus(data, !data.silent && !busy);
  if (data.type === "access_mode") {
    fullAccess = Boolean(data.full_access);
    updateAccessButton();
  }
  if (data.type === "task_interrupted") {
    activeArchiveId = data.id || activeArchiveId;
    resumableArchives.add(activeArchiveId);
    setBusy(false, t("Tâche interrompue — clique sur ▶ pour la reprendre."));
    vscode.postMessage({type: "listArchives"});
  }
  if (data.type === "archive_delete_pending") {
    window.clearTimeout(archiveNoticeTimer);
    document.getElementById("archive-notice").style.opacity = "";
    document.getElementById("archive-notice").textContent = t("Suppression définitive en cours…");
    const row = Array.from(archiveList.children).find(item => item.dataset.archiveId === data.id);
    if (row) { row.classList.add("deleting"); row.querySelectorAll("button").forEach(button => { button.disabled = true; }); }
    setStatus(t("Suppression définitive en cours…"));
  }
  if (data.type === "archive_delete_failed") {
    window.clearTimeout(archiveNoticeTimer);
    document.getElementById("archive-notice").style.opacity = "";
    document.getElementById("archive-notice").textContent = data.text || t("La suppression a échoué.");
    const row = Array.from(archiveList.children).find(item => item.dataset.archiveId === data.id);
    if (row) { row.classList.remove("deleting"); row.querySelectorAll("button").forEach(button => { button.disabled = false; }); }
    setStatus(data.text || t("La suppression a échoué."));
  }
  if (data.type === "archive_deleted") {
    resumableArchives.delete(data.id);
    if (activeArchiveId === data.id) activeArchiveId = "";
    updateSendButton();
    const notice = document.getElementById("archive-notice");
    window.clearTimeout(archiveNoticeTimer);
    notice.textContent = t("Conversation supprimée définitivement.");
    notice.style.opacity = "0.33";
    archiveNoticeTimer = window.setTimeout(() => {
      notice.textContent = "";
      notice.style.opacity = "";
      if (status.textContent === t("Conversation supprimée définitivement")) setStatus("");
    }, 5000);
    window.clearTimeout(snapshotTimer);
    const row = Array.from(archiveList.children).find(item => item.dataset.archiveId === data.id);
    if (row) row.remove();
    if (data.was_current) {
      backendReady = false;
      renderArchiveMessages([]);
      persist();
    }
    setStatus(t("Conversation supprimée définitivement"));
  }
  if (data.type === "archives") {
    resumableArchives.clear();
    for (const item of data.items || []) {
      if (item.resumable) resumableArchives.add(item.id);
      if (!activeArchiveId && item.current) activeArchiveId = item.id;
    }
    updateSendButton();
    renderArchives(data.items);
    const archivePath = document.getElementById("archive-path");
    if (data.archive_root && archivePath) {
      archivePath.textContent = data.archive_root;
      archivePath.title = data.archive_root;
    }
  }
  if (data.type === "models") {
    modelCatalog = data.items || [];
    if (data.selected) selectedModel = data.selected;
    updateModelButton();
    renderModels();
    if (pendingImages.length) ensureVisionModel();
    setStatus(modelAvailabilityLabel());
  }
  if (data.type === "model_selected") {
    selectedModel = data.model;
    if (selectedModel !== "auto-balanced") lastRoutedModel = "";
    updateModelButton();
  }
  if (data.type === "archive_loaded") {
    activeArchiveId = data.id || activeArchiveId;
    followups = [];
    followupCommand("list");
    renderArchiveMessages(data.messages);
    setBusy(false, data.has_context ? t("Conversation restaurée avec son contexte") : t("Archive restaurée"));
  }
  if (data.type === "snapshot_loaded") {
    activeArchiveId = data.id || activeArchiveId;
    if (data.resumable) resumableArchives.add(activeArchiveId);
    followups = [];
    followupCommand("list");
    applySnapshot(data);
    setBusy(false, t("Conversation restaurée"));
  }
  if (data.type === "diagnostic") status.title = data.text;
});

updateAccessButton();
updateModelButton();
stop.hidden = false;
updateStopIcon();
prompt.focus();
vscode.postMessage({ type: "webviewReady", language: uiLanguage });


// A single contextual action, outside persisted conversation HTML.
const selectionQuote = document.createElement("button");
selectionQuote.type = "button";
selectionQuote.className = "selection-quote";
selectionQuote.hidden = true;
selectionQuote.textContent = t("Ajouter à la réponse");
selectionQuote.setAttribute("aria-label", t("Citer le texte sélectionné dans votre message"));
document.body.appendChild(selectionQuote);
let selectedQuoteText = "";
function hideSelectionQuote() {
  selectionQuote.hidden = true;
  selectedQuoteText = "";
}
function updateSelectionQuote() {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return hideSelectionQuote();
  const element = selection.anchorNode?.nodeType === Node.ELEMENT_NODE ? selection.anchorNode : selection.anchorNode?.parentElement;
  const content = element?.closest(".message.assistant .content.markdown");
  if (!content || !messages.contains(content) || !content.contains(selection.focusNode) || !selection.toString().trim()) return hideSelectionQuote();
  const rect = selection.getRangeAt(0).getBoundingClientRect();
  const viewport = messages.getBoundingClientRect();
  if (rect.bottom < viewport.top || rect.top > viewport.bottom) return hideSelectionQuote();
  selectedQuoteText = selection.toString();
  selectionQuote.hidden = false;
  const width = selectionQuote.offsetWidth;
  const height = selectionQuote.offsetHeight;
  selectionQuote.style.left = `${Math.max(8, Math.min(window.innerWidth - width - 8, rect.left + (rect.width - width) / 2))}px`;
  const above = rect.top - height - 8;
  selectionQuote.style.top = `${Math.max(8, Math.min(window.innerHeight - height - 8, above >= viewport.top ? above : rect.bottom + 8))}px`;
}
selectionQuote.addEventListener("mousedown", event => event.preventDefault());
selectionQuote.addEventListener("click", () => {
  if (!selectedQuoteText.trim()) return;
  const quote = selectedQuoteText.trim().split("\n").map(line => `> ${line}`).join("\n");
  prompt.value = `${prompt.value}${prompt.value ? "\n\n" : ""}${quote}\n\n`;
  historyIndex = promptHistory.length;
  historyDraft = prompt.value;
  hideSelectionQuote();
  window.getSelection()?.removeAllRanges();
  prompt.focus();
  prompt.setSelectionRange(prompt.value.length, prompt.value.length);
  resizePrompt();
});
document.addEventListener("selectionchange", updateSelectionQuote);
messages.addEventListener("mouseup", updateSelectionQuote);
messages.addEventListener("keyup", updateSelectionQuote);
messages.addEventListener("scroll", hideSelectionQuote);
window.addEventListener("resize", hideSelectionQuote);
document.addEventListener("keydown", event => {
  if (event.key === "Escape") hideSelectionQuote();
});
document.addEventListener("pointerdown", event => {
  if (!selectionQuote.contains(event.target)) hideSelectionQuote();
});
function removeLegacyQuoteButtons() {
  for (const button of messages.querySelectorAll(".quote-response")) button.remove();
  if (!messages.querySelector(".message.assistant .content.markdown")) hideSelectionQuote();
}
new MutationObserver(removeLegacyQuoteButtons).observe(messages, {childList: true, subtree: true});
removeLegacyQuoteButtons();

function refreshLiveBalance() {
  if (!document.hidden) vscode.postMessage({type: "billingRefresh"});
}
let billingRefreshTimer;
let billingRefreshSeconds;
function scheduleBillingRefresh(seconds) {
  const next = Math.max(1, Math.min(2147483, Math.floor(seconds)));
  if (next === billingRefreshSeconds) return;
  window.clearInterval(billingRefreshTimer);
  billingRefreshSeconds = next;
  billingRefreshTimer = window.setInterval(refreshLiveBalance, next * 1000);
}
scheduleBillingRefresh(60);
document.addEventListener("visibilitychange", refreshLiveBalance);

liveBalance.addEventListener("click", () => vscode.postMessage({type: "billing"}));

// Explicit confirmation in this webview, independent of native dialog return values.
document.getElementById("delete-cancel").addEventListener("click", () => {
  selectedArchiveForDeletion = null;
  document.getElementById("delete-dialog").close();
});
document.getElementById("delete-dialog").addEventListener("cancel", () => { selectedArchiveForDeletion = null; });
document.getElementById("delete-confirm").addEventListener("click", () => {
  const id = selectedArchiveForDeletion;
  selectedArchiveForDeletion = null;
  document.getElementById("delete-dialog").close();
  if (id) vscode.postMessage({type: "deleteArchiveConfirmed", id});
});
