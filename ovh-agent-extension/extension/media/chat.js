const { svg: uiIcon } = AgentIcons;
const uiLanguage = OvhI18n.setLanguage(OvhI18n.resolveLanguage(
  document.body.dataset.language, navigator.languages?.length ? navigator.languages : [navigator.language]));
const { t } = OvhI18n;
document.documentElement.lang = uiLanguage;
OvhI18n.translateStatic(document.body);
const vscode = acquireVsCodeApi();
let emojiMode = /^[0-3]$/.test(document.body.dataset.emojiMode || "") ? Number(document.body.dataset.emojiMode) : 1;
const brandName = document.body.dataset.brandName || document.title || "TRINITY Agent";
const primaryColor = /^#[a-f0-9]{6}$/i.test(document.body.dataset.primaryColor || '') ? document.body.dataset.primaryColor : '#3e3aab';
for (const notice of document.querySelectorAll(".agent-disclaimer")) {
  notice.textContent = t("{0} est un outil d’assistance. Vous restez responsable des actions exécutées. L’IA peut se tromper : vérifiez ses réponses et les commandes.", brandName);
}
document.getElementById("prompt").placeholder = t("Demander à {0}…", brandName);
document.documentElement.style.setProperty("--ovh-brand-badge", JSON.stringify(document.body.dataset.brandBadge || Array.from(brandName.split(/\s+/)[0]).slice(0, 3).join("").toUpperCase()));
const messages = document.getElementById("messages");
const prompt = document.getElementById("prompt");
const send = document.getElementById("send");
const stop = document.getElementById("stop");
const performancePicker = document.getElementById('performance-mode');
function showPerformance(mode, resources = {}) {
  if (!performancePicker) return;
  performancePicker.value = mode === 'max' ? 'max' : 'auto';
  performancePicker.disabled = false;
  performancePicker.title = t(mode === 'max' ? 'Max : exploiter les ressources locales disponibles.' : 'Auto : allocation modérée, adaptée au matériel.');
  if (Number.isFinite(resources.workers) && Number.isFinite(resources.cache_mb)) {
    performancePicker.title += t(' · {0} lecteurs · cache jusqu’à {1} Mio', resources.workers, resources.cache_mb);
  }
}
showPerformance(document.body.dataset.performanceMode);
performancePicker?.addEventListener('change', () => {
  performancePicker.disabled = true;
  vscode.postMessage({type: 'setPerformance', mode: performancePicker.value});
});
const followupsBox = document.getElementById("followups");
let followups = [];
let pendingFollowup = null;
let retryFollowup = null;
let editingFollowup = null;
const followupDragType = "application/x-trinity-followup";
let followupDrag = null;
let pendingFollowupOrder = null;
let followupFocus = null;
let followupScrollFrame = null;
let followupPointerY = null;
function followupCommand(action, extra = {}) {
  if (!activeArchiveId) return;
  vscode.postMessage({type: "followup", conversation: activeArchiveId, action, ...extra});
}
function clearFollowupDropTargets() {
  followupsBox.querySelectorAll('.followup-drop-before,.followup-drop-after').forEach(row =>
    row.classList.remove('followup-drop-before', 'followup-drop-after'));
}
function endFollowupDrag() {
  followupDrag = null;
  followupPointerY = null;
  if (followupScrollFrame !== null) window.cancelAnimationFrame(followupScrollFrame);
  followupScrollFrame = null;
  renderFollowups();
}
function resetFollowupInteraction() {
  pendingFollowupOrder = null;
  followupFocus = null;
  followupsBox.removeAttribute('aria-busy');
  endFollowupDrag();
}
function interruptPendingFollowup() {
  // The backend may have persisted the item before its acknowledgement was
  // lost. Keep its identity for a retry instead of silently enqueueing a copy.
  if (pendingFollowup) retryFollowup = {...pendingFollowup, conversation: activeArchiveId};
  pendingFollowup = null;
  resetFollowupInteraction();
}
function scrollFollowupDrag() {
  if (!followupDrag) return;
  const bounds = followupsBox.getBoundingClientRect();
  if (followupPointerY !== null && followupPointerY >= bounds.top && followupPointerY <= bounds.bottom) {
    if (followupPointerY < bounds.top + 24) followupsBox.scrollTop -= 8;
    else if (followupPointerY > bounds.bottom - 24) followupsBox.scrollTop += 8;
  }
  followupScrollFrame = window.requestAnimationFrame(scrollFollowupDrag);
}
function reorderFollowup(id, target, position) {
  if (pendingFollowupOrder || !activeArchiveId || id === target) return;
  if (![id, target].every(value => followups.some(item => item.id === value))) {
    setStatus(t("La liste a changé : cette tâche a déjà démarré ou été retirée."), "warning");
    return;
  }
  const ordered = followups.filter(item => item.id !== id);
  ordered.splice(ordered.findIndex(item => item.id === target) + (position === 'after' ? 1 : 0), 0, followups.find(item => item.id === id));
  if (ordered.every((item, index) => item.id === followups[index].id)) return;
  pendingFollowupOrder = {id, conversation: activeArchiveId};
  followupFocus = id;
  followupsBox.setAttribute('aria-busy', 'true');
  followupCommand('reorder', {id, target, position});
}
function followupDropTarget(event) {
  if (!followupDrag || followupDrag.conversation !== activeArchiveId || !Array.from(event.dataTransfer?.types || []).includes(followupDragType)) return null;
  const row = event.target.closest('.followup-row');
  if (!row || !followupsBox.contains(row) || row.dataset.followupId === followupDrag.id) return null;
  const bounds = row.getBoundingClientRect();
  return {row, target: row.dataset.followupId, position: event.clientY < bounds.top + bounds.height / 2 ? 'before' : 'after'};
}
followupsBox.addEventListener('dragstart', event => {
  const row = event.target.closest('.followup-row');
  if (!row || !activeArchiveId || pendingFollowupOrder || !event.dataTransfer || event.target.closest('button:not(.followup-drag-handle)')) {
    event.preventDefault(); return;
  }
  followupDrag = {id: row.dataset.followupId, conversation: activeArchiveId};
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData(followupDragType, JSON.stringify(followupDrag));
  row.classList.add('followup-drag-source');
  followupScrollFrame = window.requestAnimationFrame(scrollFollowupDrag);
});
followupsBox.addEventListener('dragover', event => {
  clearFollowupDropTargets();
  if (followupDrag) followupPointerY = event.clientY;
  const destination = followupDropTarget(event);
  if (!destination) { if (followupDrag && event.dataTransfer) event.dataTransfer.dropEffect = 'none'; return; }
  event.preventDefault();
  event.dataTransfer.dropEffect = 'move';
  destination.row.classList.add('followup-drop-' + destination.position);
});
followupsBox.addEventListener('dragleave', event => {
  if (!followupsBox.contains(event.relatedTarget)) {
    followupPointerY = null;
    clearFollowupDropTargets();
  }
});
followupsBox.addEventListener('drop', event => {
  const destination = followupDropTarget(event), source = followupDrag;
  if (destination) {
    event.preventDefault();
    reorderFollowup(source.id, destination.target, destination.position);
  }
  if (source) endFollowupDrag();
});
window.addEventListener('dragend', () => { if (followupDrag) endFollowupDrag(); });
window.addEventListener('blur', () => { if (followupDrag) endFollowupDrag(); });
window.addEventListener('keydown', event => { if (event.key === 'Escape' && followupDrag) endFollowupDrag(); });
followupsBox.addEventListener('keydown', event => {
  if (!event.altKey || !['ArrowUp', 'ArrowDown'].includes(event.key) || !event.target.closest('.followup-drag-handle')) return;
  event.preventDefault();
  const id = event.target.closest('.followup-row').dataset.followupId;
  const up = event.key === 'ArrowUp', index = followups.findIndex(item => item.id === id);
  const target = followups[index + (up ? -1 : 1)];
  if (target) reorderFollowup(id, target.id, up ? 'before' : 'after');
});
function renderFollowups() {
  if (followupDrag) {
    if (followupDrag.conversation === activeArchiveId && followups.length) return;
    endFollowupDrag(); return;
  }
  if (pendingFollowupOrder && pendingFollowupOrder.conversation !== activeArchiveId) {
    pendingFollowupOrder = null;
    followupFocus = null;
    followupsBox.removeAttribute('aria-busy');
  }
  const scrollTop = followupsBox.scrollTop;
  const focused = document.activeElement?.closest('.followup-drag-handle')?.closest('.followup-row')?.dataset.followupId;
  followupsBox.replaceChildren();
  followupsBox.hidden = !followups.length;
  for (const item of followups) {
    const row = document.createElement("div");
    row.className = "followup-row";
    row.dataset.followupId = item.id;
    row.draggable = true;
    const grip = document.createElement('button');
    grip.type = 'button';
    grip.className = 'followup-drag-handle';
    grip.draggable = true;
    grip.title = t('Glisser pour réordonner · Alt + ↑ / ↓');
    grip.setAttribute('aria-label', t('Déplacer la tâche : {0}', item.text));
    grip.setAttribute('aria-keyshortcuts', 'Alt+ArrowUp Alt+ArrowDown');
    grip.innerHTML = uiIcon('grip');
    row.append(grip);
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
          if (prompt.value.trim() || pendingImages.length) { setStatus(t("Vide le brouillon avant de modifier un message."), "warning"); return; }
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
  followupsBox.scrollTop = scrollTop;
  const focusId = pendingFollowupOrder ? focused : followupFocus || focused;
  const handle = [...followupsBox.querySelectorAll('.followup-row')].find(row => row.dataset.followupId === focusId)?.querySelector('.followup-drag-handle');
  if (handle) {
    handle.focus({preventScroll: true});
    if (followupFocus && !pendingFollowupOrder) handle.scrollIntoView({block: 'nearest'});
  }
  if (!pendingFollowupOrder) followupFocus = null;
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
let stopping = false;
let activeArchiveId = "";
let loadingArchive = null;
let archivePreviewKey = '';
let archiveRevision = '';
let archiveModelRestored = false;
const archivePreviews = new Map();
// Two bounded previews accelerate repeat opens. The archive timestamp is part
// of the key, and the backend always reloads authoritative context/permissions.
function beginArchiveLoading(item, source, title, color) {
  // Resolve the clicked folder now: a previous conversation must not tint this loader.
  if (/^#[a-f0-9]{6}$/i.test(color || "")) messages.style.setProperty("--archive-loading-color", color);
  else messages.style.removeProperty("--archive-loading-color");
  archiveRevision = ""; archiveModelRestored = false;
  archiveLoadingModelRevision = modelSelectionRevision;
  window.clearTimeout(snapshotTimer);
  window.clearTimeout(viewStateTimer);
  if (currentRun) window.clearInterval(currentRun.timer);
  currentRun = null;
  activityRows.clear(); approvalCards.clear();
  archivePreviewKey = `${source || ''}:${item.id}:${item.updated_at || ''}`;
  messages.innerHTML = safeHtml(archivePreviews.get(archivePreviewKey) || '');
  if (!messages.firstChild) {
    const heading = document.createElement('h2'); heading.className = 'archive-loading-title';
    heading.textContent = title; messages.appendChild(heading);
    const skeleton = document.createElement('div'); skeleton.className = 'archive-skeleton';
    skeleton.setAttribute('aria-hidden', 'true'); messages.appendChild(skeleton);
  }
  const overlay = document.createElement('div'); overlay.className = 'archive-loading-overlay';
  overlay.setAttribute('role', 'status'); overlay.setAttribute('aria-live', 'polite');
  const spinner = document.createElement('span'); spinner.className = 'archive-loading-spinner';
  spinner.setAttribute('aria-hidden', 'true'); overlay.appendChild(spinner);
  const label = document.createElement('span'); label.textContent = t('Chargement de la conversation…');
  overlay.appendChild(label); messages.appendChild(overlay);
  messages.classList.add('archive-loading'); messages.setAttribute('aria-busy', 'true');
  messages.scrollTop = 0; send.disabled = true;
  setArchiveVisible(false); modelPanel.hidden = true;
}
function endArchiveLoading(remember = false) {
  messages.querySelector('.archive-loading-overlay')?.remove();
  messages.classList.remove('archive-loading'); messages.removeAttribute('aria-busy');
  messages.style.removeProperty('--archive-loading-color');
  send.disabled = false;
  if (remember && archivePreviewKey) {
    const html = messages.innerHTML;
    if (html.length <= 256 * 1024) {
      archivePreviews.delete(archivePreviewKey); archivePreviews.set(archivePreviewKey, html);
      while (archivePreviews.size > 2) archivePreviews.delete(archivePreviews.keys().next().value);
    }
  }
  archivePreviewKey = '';
}
const resumableArchives = new Set();
const sendArrow = send.innerHTML;
function updateSendButton() {
  const resumable = !busy && resumableArchives.has(activeArchiveId);
  send.innerHTML = resumable
    ? uiIcon("play")
    : sendArrow;
  send.title = editingFollowup ? t("Enregistrer le message") : resumable ? t("Reprendre la tâche interrompue")
    : t("Envoyer");
  send.setAttribute("aria-label", send.title);
}
function resumeArchive(id) {
  if (busy || creatingConversation || loadingArchive) return;
  activeArchiveId = id;
  pendingSubmission = {kind:"resume", model:selectedModel};
  setArchiveVisible(false);
  setBusy(true, t("Reprise de la tâche interrompue…"));
  vscode.postMessage({ type: "resumeArchive", id, fullAccess, model: selectedModel });
}
let currentRun = null;
let pendingQuestionnaire = null;
let fullAccess = saved?.fullAccess !== false;
let activeFullAccess = null;
let pendingSubmission = null;
const normalizeModelSelection = value => value === "auto" ? "auto-balanced" : (value || "auto-balanced");
let selectedModel = normalizeModelSelection(saved?.model);
let lastRoutedModel = saved?.lastRoutedModel || "";
let modelCatalog = [];
let modelPanelActive = false;
let modelSelectionChanged = false;
let modelSelectionRevision = 0;
let archiveLoadingModelRevision = 0;
let backendReady = false;
let creatingConversation = false;
let selectedArchiveForDeletion = null;
let snapshotTimer;
let viewStateTimer;
const noticeTimers = new WeakMap();
let pendingImages = [];
const activityRows = new Map();
const approvalCards = new Map();
const supportedImageTypes = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);
const maxImages = 5;
const maxClientImageBytes = 4 * 1024 * 1024;

function persist(streaming = false) {
  if (loadingArchive) return;
  // Full history serialization is expensive. Coalesce streamed updates only;
  // approvals, navigation and terminal events cancel the delay and save now.
  window.clearTimeout(viewStateTimer);
  const saveView = () => vscode.setState({ html: messages.innerHTML, fullAccess, model: selectedModel, lastRoutedModel, promptHistory });
  if (streaming === true) viewStateTimer = window.setTimeout(saveView, 150);
  else saveView();
  if (!backendReady || creatingConversation || !activeArchiveId) return;
  window.clearTimeout(snapshotTimer);
  const snapshotConversation = activeArchiveId;
  snapshotTimer = window.setTimeout(() => {
    if (creatingConversation || snapshotConversation !== activeArchiveId) return;
    cleanActivityLabels(messages);
    vscode.postMessage({
      type: "snapshot",
      conversation: snapshotConversation,
      html: messages.innerHTML,
      archive_revision: archiveRevision,
      fullAccess,
      model: selectedModel,
    });
  }, 350);
}

// Routing belongs to a conversation, not to the webview instance. Rebuild it
// from archived execution events so a previous conversation cannot label this one.
function restoreArchiveModel(data) {
  archiveModelRestored = true;
  const events = data.timeline || data.messages || [];
  const execution = [...events].reverse().find(event =>
    ['model_active', 'model_routed'].includes(event.type) && event.model);
  const changed = [...events].reverse().find(event => event.type === 'model_changed' && event.to_model);
  const request = [...events].reverse().find(event => event.type === 'user' && !event.steering);
  selectedModel = normalizeModelSelection(execution?.selection || changed?.to_model || request?.selection || data.model || request?.model);
  lastRoutedModel = selectedModel === 'auto-balanced'
    ? (execution?.model || data.active_model || '') : '';
  updateModelButton();
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
    const inputText = input ? formatModelCost(input, currency) : "-";
    const outputText = output ? formatModelCost(output, currency) : "-";
    return t("{0} entrée · {1} sortie / 1M tokens", inputText, outputText);
  }
  return t("Tarif non communiqué par l’API");
}

function modelAvailabilityLabel() {
  const models = modelCatalog.filter((item) => item.id !== "auto-balanced" && item.kind !== "auto");
  if (models.some(item => item.provider === "deepseek")) return `DeepSeek · ${models.filter(item => item.selectable).length} / ${models.length}`;
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
      ? (Number.isFinite(model.context_length) && model.context_length > 0
        ? t("Jusqu’à {0}k selon le modèle", Math.round(model.context_length / 1024))
        : t("Contexte selon le modèle choisi"))
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
        modelSelectionChanged = true;
        modelSelectionRevision += 1;
        if (selectedModel !== "auto-balanced") lastRoutedModel = "";
        updateModelButton();
        renderModels();
        setStatus(t("Modèle sélectionné : {0}", selectedModel));
        vscode.postMessage({ type: "selectModel", model: selectedModel });
        persist();
      });
    }
    modelList.appendChild(row);
  }
}

function updateAccessButton() {
  const effective = busy ? activeFullAccess : null;
  access.classList.toggle("full", effective ?? fullAccess);
  access.classList.toggle("approval", !(effective ?? fullAccess));
  access.querySelector("span").textContent = fullAccess ? t("Accès complet") : t("Demander l’accès");
  const preference = fullAccess ? t("Accès complet") : t("Demander l’accès");
  if (busy) {
    const current = effective === null ? t("En attente") : effective ? t("Accès complet") : t("Demander l’accès");
    access.querySelector("span").textContent = `${t("En cours")} : ${current} · ${t("Prochaine demande")} : ${preference}`;
    access.title = t("Le choix d’accès s’applique à la prochaine demande ou reprise. Pour arrêter les actions en cours, utilise Arrêter.");
  } else access.title = fullAccess
    ? t("Accès complet : les actions s’exécutent sans confirmation")
    : t("Validation demandée avant les écritures et les commandes");
  access.setAttribute("aria-label", access.querySelector("span").textContent);
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
  if (!date || !Number.isFinite(date.getTime())) { time.textContent = "-"; return; }
  time.dateTime = date.toISOString();
  time.textContent = new Intl.DateTimeFormat(uiLanguage, { dateStyle: "short", timeStyle: "short" }).format(date);
  time.title = date.toLocaleString(uiLanguage);
}

function restoreTimestamps(root, timeline = []) {
  const queue = type => timeline.filter(item => item.type === type).map(item => item.timestamp);
  const users = queue("user"), replies = queue("response"), changes = queue("file_change"), approvals = queue("approval");
  for (const block of root.querySelectorAll(":scope > .message")) {
    const recorded = block.classList.contains("user") ? users.shift()
      : block.classList.contains("archived-file-change") ? changes.shift() : replies.shift();
    const existing = block.querySelector(":scope > .message-time");
    stamp(block, existing?.dateTime || recorded || null);
  }
  for (const [selector, dates] of [[".approval-card", approvals], [".file-change > summary", changes]]) {
    for (const block of root.querySelectorAll(selector)) {
      const recorded = dates.shift();
      const existing = block.querySelector(":scope > .message-time");
      stamp(block, existing?.dateTime || recorded || null);
    }
  }
}

function formatElapsed(startedAt) {
  const seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  if (seconds < 60) return `${seconds} s`;
  return `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
}

function paintNotice(element, text, severity = 'info') {
  window.clearTimeout(noticeTimers.get(element));
  noticeTimers.delete(element);
  const kind = ['success','warning','error','info'].includes(severity) ? severity : 'info';
  element.style.opacity = '';
  element.textContent = text;
  if (text) {
    element.dataset.status = kind;
    noticeTimers.set(element, window.setTimeout(() => paintNotice(element, ''), 7000));
  } else {
    delete element.dataset.status;
    element.classList.remove('error-message');
  }
}
function setStatus(text, failed = false) {
  const severity = failed === true ? 'error' : typeof failed === 'string' ? failed : 'info';
  paintNotice(status, presentationText(text), severity);
  status.classList.toggle('error-message', severity === 'error');
}

function setBusy(value, text = "", failed = false) {
  busy = value;
  if (!value) activeFullAccess = null;
  send.disabled = !!pendingFollowup || !!loadingArchive || creatingConversation || stopping;
  updateAccessButton();
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

function modelDisplayName(model) {
  return normalizeModelSelection(model) === "auto-balanced" ? t("Auto équilibré") : String(model || "");
}

function addModelChange(data) {
  removeWelcome();
  const divider = document.createElement("div");
  divider.className = "model-change";
  divider.setAttribute("role", "status");
  const lineBefore = document.createElement("span");
  lineBefore.className = "model-change-line";
  const content = document.createElement("span");
  content.className = "model-change-content";
  const modelIcon = document.createElement("span");
  modelIcon.className = "model-change-icon";
  modelIcon.innerHTML = uiIcon("model");
  const label = document.createElement("span");
  label.textContent = t("Modèle changé de {0} vers {1}.", modelDisplayName(data.from_model), modelDisplayName(data.to_model));
  const info = document.createElement("span");
  info.className = "model-change-info";
  info.textContent = "i";
  info.title = t("Les prochains messages utiliseront ce modèle. Le contexte de la conversation est conservé.");
  info.setAttribute("aria-label", info.title);
  const lineAfter = document.createElement("span");
  lineAfter.className = "model-change-line";
  content.append(modelIcon, label, info);
  divider.append(lineBefore, content, lineAfter);
  messages.appendChild(divider);
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
  return article;
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
    if (item.type === "user" && !item.steering && currentRun) finishRun("stopped");
    if (item.type === "model_changed") addModelChange(item);
    if (item.type === "model_active") addActivity({id:`archived-model-${item.timestamp}`,type:"model_active",timestamp:item.timestamp,tool:"model",title:item.model,detail:""}, false);
    if (item.type === "tool_start") addActivity(item);
    if (item.type === "tool_end") finishActivity(item);
    if (item.type === "partial_response") {
      setMarkdown(item.html || "");
      finishRun("stopped");
    }
    if (["response", "error"].includes(item.type) && currentRun) finishRun(item.type === "response" ? "done" : "error");
    if (item.type === "user") addUserMessage(item.text || "", item.images || [], item.timestamp || null);
    if (item.type === "file_change") addArchivedFileChange(item);
    if (item.type === "response") {
      addArchivedAssistant(item.html || "", false, item.timestamp || null);
    }
    if (item.type === "error") addArchivedAssistant(item.html || "", true, item.timestamp || null);
    if (item.type === "verification_status" && item.status !== "running") {
      const article = document.createElement("article");
      article.className = "message assistant archived-verification";
      showVerification(item, article);
      messages.appendChild(article);
    }
    if (item.type === "verification_check") {
      const article = document.createElement("article");
      article.className = "message assistant archived-verification";
      const detail = document.createElement("pre");
      detail.textContent = verificationCheckText(item);
      article.appendChild(detail);
      messages.appendChild(article);
    }
  }
  if (currentRun) finishRun("stopped");
  messages.querySelectorAll(".activity-row").forEach(row => { delete row.dataset.durationMs; });
  restoreActivityTimings(messages, items || []);
  // Replaying an archive is not a new execution: do not display its render time.
  messages.querySelectorAll(".activity-elapsed").forEach(el => { el.textContent = ""; el.removeAttribute("title"); });
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
  upgradeSnapshotPresentation(messages);
  restoreTimestamps(messages, data.timeline || []);
  restoreActivityTimings(messages, data.timeline || []);
  OvhI18n.translateSnapshot(messages);
  messages.querySelectorAll("details.activity").forEach(activity => { activity.open = false; });
  for (const article of messages.querySelectorAll(".message.failure")) article.remove();
  for (const article of messages.querySelectorAll(".message.assistant")) {
    if (article.textContent.includes(t("Aucune clé OVH n’est configurée"))) article.remove();
  }
  for (const article of messages.querySelectorAll(".message.working")) {
    article.classList.remove("working");
    article.classList.add("stopped");
    article.querySelectorAll(".streaming").forEach(content => content.classList.remove("streaming"));
  }
  for (const button of messages.querySelectorAll(".approval-actions button")) button.disabled = true;
  for (const content of messages.querySelectorAll(".markdown")) enhanceMarkdown(content);
  for (const change of messages.querySelectorAll(".file-change")) wireFileChangeCopy(change);
  restoreExecutionEvidence(messages, data.timeline || []);
  // Restored content must not change the access mode chosen in this webview.
  restoreArchiveModel(data);
  updateAccessButton();
  persist();
  scrollToBottom();
}

function restorePendingSubmission(submission, restoredText) {
  // A backend launched by Send restores its archive before processing that
  // request. Merge the optimistic prompt after authoritative history, without
  // turning the still-pending request into an idle conversation.
  if (submission) {
    selectedModel = submission.model;
    archiveModelRestored = true;
    updateModelButton();
    if (submission.kind === "prompt") {
      addUserMessage(submission.text, submission.images, submission.timestamp);
      createAssistantRun();
    }
    setBusy(true, t("Demande en cours…"));
  } else setBusy(false, restoredText);
}

const archiveDragType='application/x-trinity-conversation-organizer';
let archiveDrag=null, deferredArchives=null, pendingOrganization=null, organizationFocus=null;
let archiveScrollFrame=null, archivePointerY=null;
const pendingProjectUpdates=new Map();
let projectUpdateSequence=0;

function updateProject(project, patch) {
  const message={type:'updateProject',project,...patch,requestId:`project-${++projectUpdateSequence}`};
  pendingProjectUpdates.set(project,message);
  if(patch.root!==undefined) {
    const group=[...archiveList.querySelectorAll('.archive-project')].find(item=>item.dataset.projectId===project);
    group?.querySelectorAll('.project-editor input,.project-editor button').forEach(control=>{control.disabled=true;});
  }
  archiveOrganizationNotice(t('Enregistrement…'));
  vscode.postMessage(message);
}
function flushDeferredArchives() {
  if(deferredArchives) {const latest=deferredArchives;deferredArchives=null;renderArchives(latest.items,latest.projects);}
}
function closeProjectEditor(group) {
  group.querySelector('.project-editor').hidden=true;
  group.querySelector('input[type=text]').value=group.dataset.projectName;
  group.querySelector('.project-path-input').value=group.dataset.projectRoot;
  group.querySelector('.project-color-hex').value=group.dataset.projectColor.toUpperCase();
  if(!pendingProjectUpdates.has(group.dataset.projectId)) {
    group.querySelector('input[type=color]').value=group.dataset.projectColor;
    group.style.setProperty('--project-color',group.dataset.projectColor);
  }
  flushDeferredArchives();
  [...archiveList.querySelectorAll('.archive-project')].find(item=>item.dataset.projectId===group.dataset.projectId)?.querySelector('.project-edit').focus();
}
function projectUpdateResult(data) {
  const success=data.type==='project_updated',id=success?(data.previousId || data.project.id):data.project;
  const group=[...archiveList.querySelectorAll('.archive-project')].find(item=>item.dataset.projectId===id);
  if(success) {
    if(group) {group.dataset.projectName=data.project.name;group.dataset.projectColor=data.project.color || primaryColor;group.dataset.projectRoot=data.project.root || group.dataset.projectRoot;}
    const deferred=deferredArchives?.projects?.find(item=>item.id===id);
    if(deferred)Object.assign(deferred,data.project);
  }
  const pending=pendingProjectUpdates.get(id);
  if(!pending || pending.requestId!==data.requestId)return;
  pendingProjectUpdates.delete(id);
  group?.querySelectorAll('.project-editor input,.project-editor button').forEach(control=>{control.disabled=false;});
  if(success && group) {
    group.dataset.projectId=data.project.id;
    group.querySelector('.project-folder-name').textContent=data.project.name;
    group.querySelector('.project-folder-path').textContent=group.dataset.projectRoot;
    // A color acknowledgement must not discard a title draft or close the picker.
    if(pending.name!==undefined && group.querySelector('input[type=text]').value.trim()===pending.name && group.querySelector('input[type=color]').value===pending.color && (pending.root===undefined || group.querySelector('.project-path-input').value.trim()===pending.root))closeProjectEditor(group);
  }
  archiveOrganizationNotice(success?t(pending.name===undefined?'Couleur du dossier enregistrée.':'Projet enregistré.'):t('Enregistrement impossible : {0}',data.text || t('Erreur inconnue')),success?'success':'error');
}

function archiveOrganizationNotice(text, severity = 'info') {
  const notice=document.getElementById('archive-notice');
  paintNotice(notice,text,severity);
}
function clearArchiveDropTargets() {
  archiveList.querySelectorAll('.archive-drop-target,.project-drop-before,.project-drop-after').forEach(element=>
    element.classList.remove('archive-drop-target','project-drop-before','project-drop-after'));
}
function endArchiveDrag() {
  archiveDrag=null;archivePointerY=null;
  if(archiveScrollFrame!==null)window.cancelAnimationFrame(archiveScrollFrame);
  archiveScrollFrame=null;
  archiveList.classList.remove('dragging-conversation','dragging-project');
  archiveList.querySelectorAll('.archive-drag-source').forEach(element=>element.classList.remove('archive-drag-source'));
  clearArchiveDropTargets();
  flushDeferredArchives();
}
function scrollArchiveDrag() {
  if(!archiveDrag)return;
  if(archivePointerY!==null) {
    const bounds=archiveList.getBoundingClientRect();
    const edge=40;
    if(archivePointerY>=bounds.top && archivePointerY<=bounds.bottom) {
      const movement=archivePointerY<bounds.top+edge?-12:archivePointerY>bounds.bottom-edge?12:0;
      if(movement)archiveList.scrollTop+=movement;
    }
  }
  archiveScrollFrame=window.requestAnimationFrame(scrollArchiveDrag);
}
function beginArchiveDrag(event, payload, element) {
  if(pendingOrganization || !event.dataTransfer) {event.preventDefault();return;}
  event.stopPropagation();
  archiveDrag=payload;
  event.dataTransfer.effectAllowed='move';
  event.dataTransfer.setData(archiveDragType,JSON.stringify(payload));
  archiveList.classList.add(payload.kind==='project'?'dragging-project':'dragging-conversation');
  element.classList.add('archive-drag-source');
  archiveScrollFrame=window.requestAnimationFrame(scrollArchiveDrag);
}
function archiveDropDestination(event) {
  if(!archiveDrag || !Array.from(event.dataTransfer?.types||[]).includes(archiveDragType))return null;
  const group=event.target.closest('.archive-project');
  if(archiveDrag.kind==='project') {
    if(!group || group.dataset.projectId===archiveDrag.source)return null;
    const bounds=group.querySelector('summary').getBoundingClientRect();
    return {element:group,target:group.dataset.projectId,position:event.clientY<bounds.top+bounds.height/2?'before':'after'};
  }
  const element=group || event.target.closest('.archive-ungrouped');
  if(!element)return null;
  const target=group?.dataset.projectId || null;
  return target===archiveDrag.project?null:{element,target};
}
function organizeArchives(command) {
  if(pendingOrganization)return;
  pendingOrganization=command;
  archiveList.setAttribute('aria-busy','true');
  archiveOrganizationNotice(t('Enregistrement du classement…'));
  vscode.postMessage(command);
}
archiveList.addEventListener('dragover',event=>{
  if(!archiveDrag)return;
  event.preventDefault();
  archivePointerY=event.clientY;
  clearArchiveDropTargets();
  const destination=archiveDropDestination(event);
  if(event.dataTransfer)event.dataTransfer.dropEffect=destination?'move':'none';
  if(destination)destination.element.classList.add(archiveDrag.kind==='project'?'project-drop-'+destination.position:'archive-drop-target');
});
archiveList.addEventListener('dragleave',event=>{
  if(!archiveList.contains(event.relatedTarget)) {clearArchiveDropTargets();archivePointerY=null;}
});
archiveList.addEventListener('drop',event=>{
  if(!archiveDrag)return;
  event.preventDefault();event.stopPropagation();
  const destination=archiveDropDestination(event);
  if(destination)organizeArchives(archiveDrag.kind==='project'
    ?{type:'moveProject',source:archiveDrag.source,target:destination.target,position:destination.position}
    :{type:'moveConversation',source:archiveDrag.source,conversation:archiveDrag.conversation,target:destination.target});
  endArchiveDrag();
});
window.addEventListener('dragend',endArchiveDrag);
// Native dragging can blur an embedded webview; only stop edge scrolling then.
window.addEventListener('blur',()=>{if(archiveDrag){archivePointerY=null;clearArchiveDropTargets();}});
document.addEventListener('visibilitychange',()=>{if(document.hidden && archiveDrag)endArchiveDrag();});
document.addEventListener('pointerdown',()=>{if(archiveDrag)endArchiveDrag();});
window.addEventListener('keydown',event=>{if(event.key==='Escape' && archiveDrag)endArchiveDrag();});

function restoreOrganizationFocus() {
  if(!organizationFocus)return;
  const command=organizationFocus;organizationFocus=null;
  const element=command.type==='moveProject'
    ?[...archiveList.querySelectorAll('.archive-project')].find(group=>group.dataset.projectId===command.source)?.querySelector('.project-drag-handle')
    :[...archiveList.querySelectorAll('.archive-item')].find(row=>row.dataset.archiveId===command.conversation && row.dataset.archiveProject===command.source)?.querySelector('.archive-open');
  element?.focus({preventScroll:true});
  element?.scrollIntoView({block:'nearest'});
}

function renderArchives(items, projects) {
  // Keep native color pickers, title drafts and dragged elements alive during refreshes.
  if(archiveDrag) {deferredArchives={items,projects};return;}
  if(archiveList.querySelector('.project-editor:not([hidden])')) {
    deferredArchives={items,projects};
    // Reorder existing nodes without destroying a focused editor or unsaved drafts.
    const groups=new Map([...archiveList.querySelectorAll('.archive-project')].map(group=>[group.dataset.projectId,group]));
    const ungrouped=archiveList.querySelector('.archive-ungrouped');
    const ordered=(projects || []).map(project=>groups.get(project.id)).filter(Boolean);
    if(ordered.some((group,index)=>[...groups.values()][index]!==group)) {
      for(const group of ordered)archiveList.insertBefore(group,ungrouped);
    }
    restoreOrganizationFocus();
    return;
  }
  const expanded=new Map([...archiveList.querySelectorAll('.archive-project')].map(group=>[group.dataset.projectId,group.open]));
  const expandedDocuments=new Map([...archiveList.querySelectorAll('.archive-project')].map(group=>[group.dataset.projectId,group.querySelector('.project-documents')?.open]));
  archiveList.innerHTML="";
  const ungrouped=document.createElement('section');ungrouped.className='archive-ungrouped';
  ungrouped.setAttribute('aria-label',t('Conversations hors projet'));
  if(!projects?.length) {archiveList.append(ungrouped);renderArchiveRows(items,ungrouped);restoreOrganizationFocus();return;}
  const title=document.createElement('h2');title.className='archive-section-title';title.textContent=t('Conversations hors projet');ungrouped.append(title);
  if(items?.length) {
    renderArchiveRows(items,ungrouped);
  } else {
    ungrouped.classList.add('archive-ungrouped-empty');
    const hint=document.createElement('p');hint.className='archive-drop-hint';hint.textContent=t('Déposer ici pour retirer la conversation de son dossier.');ungrouped.append(hint);
  }
  for(const project of projects) {
    const group=document.createElement('details');
    group.className='archive-project';group.classList.toggle('selected',Boolean(project.selected));group.dataset.projectId=project.id;
    group.open=expanded.get(project.id) ?? false;
    const color=/^#[a-f0-9]{6}$/i.test(project.color||'')?project.color:primaryColor;
    group.dataset.projectName=project.name;group.dataset.projectColor=color;group.dataset.projectRoot=project.root;
    const displayedColor=pendingProjectUpdates.get(project.id)?.color || color;
    group.style.setProperty('--project-color',displayedColor);
    const summary=document.createElement('summary');
    summary.draggable=false;
    summary.addEventListener('click',event=>{
      if(event.target.closest('button'))return;
      vscode.postMessage({type:'openProject',project:project.id});
    });
    const handle=document.createElement('button');handle.type='button';handle.className='icon archive-drag-handle project-drag-handle';handle.draggable=true;handle.innerHTML=uiIcon('grip');
    handle.title=t('Déplacer le dossier : glisser-déposer ou Alt + ↑ / ↓');handle.setAttribute('aria-label',handle.title+' : '+project.name);handle.setAttribute('aria-keyshortcuts','Alt+ArrowUp Alt+ArrowDown');
    handle.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();});
    handle.addEventListener('keydown',event=>{
      if(!event.altKey || !['ArrowUp','ArrowDown'].includes(event.key))return;
      event.preventDefault();event.stopPropagation();
      const groups=[...archiveList.querySelectorAll('.archive-project')],index=groups.indexOf(group),up=event.key==='ArrowUp';
      const target=groups[index+(up?-1:1)];
      if(target)organizeArchives({type:'moveProject',source:group.dataset.projectId,target:target.dataset.projectId,position:up?'before':'after'});
    });
    summary.addEventListener('dragstart',event=>{
      if(!event.target.closest('.project-drag-handle')) {event.preventDefault();return;}
      beginArchiveDrag(event,{kind:'project',source:group.dataset.projectId},group);
    });
    const caret=document.createElement('button');caret.type='button';caret.className='icon project-caret';caret.innerHTML=uiIcon('chevron');
    const updateCaret=()=>{caret.setAttribute('aria-expanded',String(group.open));caret.title=t(group.open?'Replier le projet':'Déplier le projet');caret.setAttribute('aria-label',caret.title+' : '+project.name);};
    caret.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();group.open=!group.open;updateCaret();if(group.open)vscode.postMessage({type:'openProject',project:project.id});});
    group.addEventListener('toggle',updateCaret);updateCaret();
    const icon=document.createElement('span');icon.className='project-folder-icon';icon.innerHTML=uiIcon('folder');
    const name=document.createElement('span');name.className='project-folder-name';name.textContent=project.name;name.title=project.root;
    const total=project.items?.length || 0;
    const count=document.createElement('span');count.className='project-folder-count';count.textContent=total===1?t('1 conversation'):t('{0} conversations',total);
    summary.append(handle,icon,name,count);
    const edit=document.createElement('button');edit.type='button';edit.className='icon project-edit';edit.innerHTML=uiIcon('edit');edit.title=t('Personnaliser le projet');edit.setAttribute('aria-label',edit.title+' : '+project.name);
    summary.append(edit,caret);
    const contents=document.createElement('div');contents.className='project-conversations';
    const editor=document.createElement('form');editor.className='project-editor';editor.hidden=true;
    const titleLabel=document.createElement('label');titleLabel.textContent=t('Titre du projet');
    const titleInput=document.createElement('input');titleInput.type='text';titleInput.value=project.name;titleInput.maxLength=80;titleInput.required=true;titleLabel.append(titleInput);
    const colorLabel=document.createElement('label');colorLabel.textContent=t('Couleur du projet');
    const colorControls=document.createElement('span');colorControls.className='project-color-controls';
    const hexInput=document.createElement('input');hexInput.type='text';hexInput.className='project-color-hex';
    hexInput.value=displayedColor.toUpperCase();hexInput.placeholder='#RRGGBB';hexInput.maxLength=7;
    hexInput.pattern='#?[0-9a-fA-F]{6}';hexInput.required=true;hexInput.spellcheck=false;
    hexInput.setAttribute('aria-label',t('Couleur du projet')+' (HEX)');
    const colorInput=document.createElement('input');colorInput.type='color';colorInput.value=displayedColor;colorInput.title=t('Couleur enregistrée automatiquement.');
    colorInput.setAttribute('aria-label',t('Couleur du projet'));
    colorControls.append(hexInput,colorInput);colorLabel.append(colorControls);
    const hexColor=()=>/^#?[0-9a-f]{6}$/i.test(hexInput.value)?'#'+hexInput.value.replace(/^#/,'').toLowerCase():null;
    hexInput.addEventListener('input',()=>{
      const color=hexColor();if(!color)return;
      colorInput.value=color;group.style.setProperty('--project-color',color);
    });
    hexInput.addEventListener('change',()=>{
      const color=hexColor();if(!color){hexInput.reportValidity();return;}
      hexInput.value=color.toUpperCase();colorInput.value=color;
      group.style.setProperty('--project-color',color);updateProject(project.id,{color});
    });
    const previewColor=()=>{
      hexInput.value=colorInput.value.toUpperCase();
      group.style.setProperty('--project-color',colorInput.value);
    };
    colorInput.addEventListener('input',previewColor);
    colorInput.addEventListener('change',()=>{
      previewColor();updateProject(project.id,{color:colorInput.value});
    });
    const pathLabel=document.createElement('label');pathLabel.className='project-path-label';pathLabel.textContent=t('Chemin du projet');
    const pathInput=document.createElement('input');pathInput.type='text';pathInput.className='project-path-input';pathInput.value=project.root;pathInput.required=true;pathInput.maxLength=4096;pathInput.spellcheck=false;pathLabel.append(pathInput);
    const save=document.createElement('button');save.type='submit';save.textContent=t('Enregistrer');
    const cancel=document.createElement('button');cancel.type='button';cancel.textContent=t('Fermer');cancel.onclick=()=>closeProjectEditor(group);
    editor.append(titleLabel,colorLabel,pathLabel,save,cancel);
    edit.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();group.open=true;updateCaret();if(!editor.hidden)closeProjectEditor(group);else {editor.hidden=false;titleInput.focus();}});
    editor.addEventListener('submit',event=>{event.preventDefault();if(!titleInput.value.trim() || !pathInput.value.trim() || !hexInput.reportValidity())return;updateProject(project.id,{name:titleInput.value.trim(),color:colorInput.value,...(pathInput.value.trim()!==project.root?{root:pathInput.value.trim()}:{})});});
    contents.append(editor);
    const root=document.createElement('p');root.className='project-folder-path';root.textContent=project.root;contents.append(root);
    if(project.documents?.length) {
      const documents=document.createElement('details');documents.className='project-documents';documents.open=expandedDocuments.get(project.id) ?? false;
      const label=document.createElement('summary');label.className='project-documents-label';
      label.innerHTML=uiIcon('chevron','summary-chevron');
      label.append(document.createTextNode(t('Documents Markdown du projet')+' · '+project.documents.length));documents.append(label);
      for(const item of project.documents) {
        const button=document.createElement('button');button.type='button';button.className='project-document';
        const name=document.createElement('strong');name.textContent=item.name;
        const location=document.createElement('span');location.textContent=item.path;
        button.append(name,location);button.title=item.path;
        button.addEventListener('click',()=>vscode.postMessage({type:'openProjectDocument',project:project.id,name:item.name,scope:item.scope}));
        documents.append(button);
      }
      contents.append(documents);
    }
    renderArchiveRows(project.items,contents,project);
    group.append(summary,contents);archiveList.append(group);
  }
  archiveList.append(ungrouped);
  restoreOrganizationFocus();
}

function renderArchiveRows(items, container, project) {
  if (!items?.length) {
    const empty = document.createElement("p");
    empty.className = "archive-empty";
    empty.textContent = t(project ? "Aucune conversation associée à ce projet." : "Aucune conversation archivée.");
    container.appendChild(empty);
    return;
  }
  for (const item of items) {
    const active=item.archive_active ?? (!project || project.active);
    const source=item.archive_project || project?.id;
    const row = document.createElement("div");
    row.className = `archive-item${item.current && active ? " current" : ""}`;
    row.dataset.archiveId = item.id;
    if(source)row.dataset.archiveProject=source;
    row.dataset.archiveActive=String(active);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "archive-open";
    if(source) {
      row.draggable=false;button.draggable=false;
      row.addEventListener('dragstart',event=>{
        if(!event.target.closest('.conversation-drag-handle')) {event.preventDefault();return;}
        beginArchiveDrag(event,{kind:'conversation',source,conversation:item.id,project:project?.id || null},row);
      });
    }
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
      if(busy || loadingArchive) {setStatus(t("Termine ou arrête la tâche avant de changer de projet."), "warning");return;}
      loadingArchive = item.id;
      beginArchiveLoading(item, source, displayTitle, button.closest(".archive-project")?.style.getPropertyValue("--project-color"));
      if(!active && source) {
        setStatus(t("Chargement de la conversation…"));
        vscode.postMessage({type:"openProject",project:source,conversation:item.id});return;
      }
      activeArchiveId = item.id;
      updateSendButton();
      setArchiveVisible(false);
      setStatus(t("Chargement de la conversation…"));
      vscode.postMessage({ type: "loadArchive", id: item.id });
    };
    button.addEventListener("click", openArchive);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon archive-action archive-delete";
    remove.title = t("Supprimer définitivement la conversation");
    remove.setAttribute("aria-label", t("Supprimer définitivement : {0}", displayTitle));
    remove.innerHTML = uiIcon("trash");
    remove.addEventListener("click", () => {
      if (busy) { setStatus(t("Attends la fin de la réponse avant de supprimer une conversation."), "warning"); return; }
      selectedArchiveForDeletion = {id:item.id,source};
      document.getElementById("delete-title").textContent = displayTitle;
      document.getElementById("delete-dialog").showModal();
      document.getElementById("delete-cancel").focus();
    });
    if(source) {
      const handle=document.createElement('button');
      handle.type='button';handle.className='icon archive-drag-handle conversation-drag-handle';
      handle.draggable=true;handle.innerHTML=uiIcon('grip');
      handle.title=t('Glisser la conversation vers un dossier');
      handle.setAttribute('aria-label',handle.title+' : '+displayTitle);
      handle.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();});
      row.append(handle);
    }
    row.append(button);
    if(source) {
      const classify=document.createElement('button');classify.type='button';classify.className='icon archive-action archive-classify';classify.innerHTML=uiIcon('project');
      classify.title=t('Classer cette conversation dans un projet');classify.setAttribute('aria-label',classify.title+' : '+displayTitle);
      classify.addEventListener('click',()=>vscode.postMessage({type:'assignProject',source,conversation:item.id}));row.append(classify);
    }
    if (item.resumable && active) {
      const resume = document.createElement("button");
      resume.type = "button";
      resume.className = "icon archive-action archive-resume";
      resume.setAttribute("aria-label", t("Reprendre la tâche interrompue"));
      resume.innerHTML = uiIcon("reload");
      resume.title = t("Reprendre la tâche interrompue après vérification de son état");
      resume.addEventListener("click", () => {
        if (busy) { setStatus(t("Arrête la tâche en cours avant de reprendre une autre conversation."), "warning"); return; }
        resumeArchive(item.id);
      });
      row.appendChild(resume);
    }
    row.appendChild(remove);
    container.appendChild(row);
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
  const elapsedText = document.createElement("span");
  elapsedText.className = "activity-elapsed";
  summary.append(spinner, summaryText, elapsedText);
  summary.insertAdjacentHTML("beforeend", uiIcon("chevron", "summary-chevron"));
  const list = document.createElement("div");
  list.className = "activity-list";
  activity.append(summary, list);

  const content = document.createElement("div");
  content.className = "content markdown streaming";
  content.innerHTML = "";
  article.append(label, content, activity);
  stamp(article);
  messages.appendChild(article);

  const run = {
    article,
    activity,
    list,
    summaryText,
    indicator: spinner,
    elapsedText,
    content,
    startedAt: Date.now(),
    actions: 0,
    timer: undefined,
  };
  updateRunSummary(run);
  run.timer = window.setInterval(() => {updateRunSummary(run);updateActivityTimers(run);}, 1000);
  currentRun = run;
  persist();
  scrollToBottom();
  return run;
}

function ensureRun() {
  return currentRun || createAssistantRun();
}

function renderQuestionnaire(data) {
  const questions = Array.isArray(data?.questions) ? data.questions.slice(0, 3) : [];
  if (!questions.length) return;
  const article = [...messages.querySelectorAll(".message.assistant")].at(-1);
  const content = article?.querySelector(".content");
  if (!content) return;
  const form = document.createElement("form");
  form.className = "questionnaire";
  questions.forEach((item, questionIndex) => {
    const fieldset = document.createElement("fieldset");
    const legend = document.createElement("legend");
    legend.textContent = String(item.question || "");
    fieldset.appendChild(legend);
    const name = `qcm-${data.id || "questionnaire"}-${questionIndex}`;
    (Array.isArray(item.options) ? item.options.slice(0, 3) : []).forEach((option, optionIndex) => {
      const label = document.createElement("label");
      label.className = "questionnaire-option";
      const radio = document.createElement("input");
      radio.type = "radio"; radio.name = name; radio.value = String(option.label || "");
      if (optionIndex === 0) radio.checked = true;
      const text = document.createElement("span");
      const title = document.createElement("strong"); title.textContent = radio.value;
      text.appendChild(title);
      if (option.description) { const description = document.createElement("small"); description.textContent = String(option.description); text.appendChild(description); }
      label.append(radio, text); fieldset.appendChild(label);
    });
    const customLabel = document.createElement("label");
    customLabel.className = "questionnaire-option questionnaire-custom";
    const customRadio = document.createElement("input"); customRadio.type = "radio"; customRadio.name = name; customRadio.value = "__custom__";
    const customInput = document.createElement("input"); customInput.type = "text"; customInput.maxLength = 1000; customInput.placeholder = t("Réponse personnelle…");
    customInput.addEventListener("focus", () => { customRadio.checked = true; });
    customLabel.append(customRadio, customInput); fieldset.appendChild(customLabel);
    form.appendChild(fieldset);
  });
  const submitButton = document.createElement("button"); submitButton.type = "submit"; submitButton.textContent = t("Envoyer mes réponses"); form.appendChild(submitButton);
  form.addEventListener("submit", event => {
    event.preventDefault();
    const answers = [];
    for (const [index, item] of questions.entries()) {
      const selected = form.querySelector(`input[name="qcm-${data.id || "questionnaire"}-${index}"]:checked`);
      if (!selected) { setStatus(t("Choisis une réponse pour chaque question."), "warning"); return; }
      let value = selected.value;
      if (value === "__custom__") {
        value = selected.closest("label").querySelector('input[type="text"]').value.trim();
        if (!value) { setStatus(t("Renseigne la réponse personnelle sélectionnée."), "warning"); return; }
      }
      answers.push(`- ${String(item.question || "Question")} : ${value}`);
    }
    form.querySelectorAll("input,button").forEach(control => { control.disabled = true; });
    resetFollowupInteraction();
    editingFollowup = null;
    pendingFollowup = null;
    prompt.value = `${t("Réponses au questionnaire :")}\n${answers.join("\n")}`;
    submit();
  });
  content.appendChild(form);
  persist(); scrollToBottom();
}

function updateRunSummary(run, state = "") {
  const count = run.actions;
  const actionLabel = t("{0} action{1}", count, count === 1 ? "" : "s");
  const elapsed = formatElapsed(run.startedAt);
  run.elapsedText.textContent = elapsed;
  run.elapsedText.title = t("Temps écoulé : {0}", elapsed);
  run.elapsedText.setAttribute("aria-label", run.elapsedText.title);
  run.summaryText.textContent = state
    ? t(state === "done" ? "Terminé · {0}" : state === "error" ? "Échec · {0}" : "Interrompu · {0}", actionLabel)
    : (run.currentAction || t("Réflexion en cours…"));
  run.summaryText.textContent = activityLabel(run.summaryText.textContent);
  run.indicator.innerHTML = uiIcon(state === "done" ? "check" : state === "error" ? "error" : iconForTool(run.currentTool || "model"));
  run.summaryText.title = `${run.summaryText.textContent} · ${elapsed}${state ? "" : ` · ${actionLabel}`}`;
}

function finishRun(state = "done") {
  if (!currentRun) return;
  const run = currentRun;
  window.clearInterval(run.timer);
  for (const item of activityRows.values()) {
    if (item.row.closest('.message')!==run.article || !item.row.classList.contains('running')) continue;
    setActivityDuration(item.row, Date.now()-item.startedAt, {partial:true});
    item.row.classList.replace('running','stopped');
  }
  updateRunSummary(run, state);
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
  if (!code || !window.hljs || code.dataset.highlighted === "yes" || streaming) return;
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

const responseEmojiSegments = new Intl.Segmenter(undefined, { granularity: "grapheme" });
const responseEmojiPattern = /\p{Extended_Pictographic}|\p{Regional_Indicator}|\p{Emoji_Modifier}|\u20e3/u;

function presentationText(text, mode = emojiMode) {
  const source = String(text).replace(/\u2014/g, "-");
  if (mode >= 2 || !responseEmojiPattern.test(source)) return source;
  let filtered = "";
  for (const { segment } of responseEmojiSegments.segment(source)) {
    const base = segment.replace(/[\ufe0e\ufe0f]/g, "");
    if (!responseEmojiPattern.test(segment) || /^[©®™]$/.test(segment)) filtered += segment;
    else if (mode >= 2) filtered += segment;
    else if (mode === 1 && base === "➡") filtered += "➡️";
    else if (mode === 1 && base === "✅") filtered += "✅";
  }
  return filtered;
}

function activityLabel(text) {
  // UI action labels use SVG icons; emoji policy applies to assistant prose only.
  return presentationText(text, 0).trim();
}

function cleanActivityLabels(root) {
  for (const label of root.querySelectorAll(".activity-summary-text, .activity-row strong, .verification-status")) {
    const cleaned = activityLabel(label.textContent);
    if (label.textContent !== cleaned) label.textContent = cleaned;
  }
}

function filterResponseEmojis(container) {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    // Literal source and evidence must stay exact, including punctuation.
    if (node.parentElement?.closest("pre,code,blockquote,a")) continue;
    const cleaned = presentationText(node.textContent);
    if (cleaned !== node.textContent) node.textContent = cleaned;
  }
}

function enhanceMarkdown(container) {
  filterResponseEmojis(container);
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
  // The response often repeats the last delta. Preserve its DOM and selection;
  // finishRun still performs final highlighting and immediate persistence.
  if (run.lastHtml === html) return;
  run.lastHtml = html;
  run.content.innerHTML = safeHtml(html);
  enhanceMarkdown(run.content);
  persist(true);
  scrollToBottom();
}

function iconForTool(tool) {
  const name = { list: "list", read: "read", search: "search", edit: "edit", write: "edit",
    web: "search", search_web: "search", read_webpage: "read", edit_file: "edit", write_file: "edit",
    run_command: "terminal", run_host_command: "terminal", verify: "check",
    command: "terminal", route: "reload", context: "context", cost: "model", model: "model" }[tool];
  return typeof name === "string" ? name : "terminal";
}

// Saved conversations retain their content while adopting the current layout/icons.
function upgradeSnapshotPresentation(root) {
  cleanActivityLabels(root);
  for (const article of root.querySelectorAll(".message.assistant")) {
    const content = article.querySelector(":scope > .content");
    const activity = article.querySelector(":scope > .activity");
    if (content && activity) content.after(activity);
  }
  for (const indicator of root.querySelectorAll(".run-indicator, .run-spinner")) {
    indicator.classList.remove("run-spinner");
    indicator.classList.add("run-indicator");
    const article = indicator.closest(".message.assistant");
    const actions = indicator.closest(".activity")?.querySelectorAll(".activity-icon .icon-terminal, .activity-icon .icon-read, .activity-icon .icon-edit, .activity-icon .icon-search, .activity-icon .icon-list");
    const lastAction = actions?.[actions.length - 1];
    indicator.innerHTML = article?.classList.contains("done") ? uiIcon("check")
      : article?.classList.contains("error") ? uiIcon("error") : lastAction?.outerHTML || uiIcon("model");
  }
  for (const icon of root.querySelectorAll(".file-change-icon")) {
    if (!icon.querySelector(".agent-icon")) icon.innerHTML = uiIcon(icon.textContent.trim() === "+" ? "plus" : "edit");
  }
  const old = { "≡":"list", "⌕":"search", "✎":"edit", ">_":"terminal", "↻":"reload", "≋":"context", "$":"model", "◆":"model", "✓":"check", "!":"error" };
  for (const icon of root.querySelectorAll(".activity-icon")) {
    if (icon.querySelector(".icon-cost")) icon.innerHTML = uiIcon("model");
    if (!icon.querySelector(".agent-icon")) {
      const name = old[icon.textContent.trim()];
      icon.innerHTML = uiIcon(typeof name === "string" ? name : "terminal");
    }
  }
  for (const summary of root.querySelectorAll(".activity > summary, .file-change > summary")) {
    if (!summary.querySelector(".summary-chevron")) summary.insertAdjacentHTML("beforeend", uiIcon("chevron", "summary-chevron"));
  }
}

function validDuration(value) { return Number.isFinite(value) && value>=0; }
function actionDuration(data) {
  return validDuration(data?.duration_ms)?data.duration_ms:validDuration(data?.preparation_ms)?data.preparation_ms:null;
}
function setActivityDuration(row, milliseconds, {estimated=false,partial=false}={}) {
  const title=row.querySelector('strong');
  if(!title)return;
  let heading=title.closest('.activity-heading');
  if(!heading) {heading=document.createElement('div');heading.className='activity-heading';title.before(heading);heading.append(title);}
  let elapsed=heading.querySelector('.activity-duration');
  if(!elapsed) {elapsed=document.createElement('span');elapsed.className='activity-duration';heading.append(elapsed);}
  if(!validDuration(milliseconds)) {
    delete row.dataset.durationMs;delete row.dataset.durationEstimated;delete row.dataset.durationPartial;
    elapsed.textContent=t('Durée non enregistrée');elapsed.title=elapsed.textContent;return;
  }
  const ms=Math.max(0,Math.round(milliseconds));
  row.dataset.durationMs=String(ms);row.dataset.durationEstimated=String(estimated);row.dataset.durationPartial=String(partial);
  const duration=ms<1000?(ms?`${ms} ms`:'< 1 ms'):ms<60000?`${new Intl.NumberFormat(uiLanguage,{maximumFractionDigits:2}).format(ms/1000)} s`:`${Math.floor(ms/60000)} min ${Math.floor(ms/1000)%60} s`;
  elapsed.textContent=(partial?'≥ ':estimated?'≈ ':'')+duration;
  elapsed.title=t('Durée : {0}',elapsed.textContent);
}
function updateActivityTimers(run) {
  for(const item of activityRows.values()) {
    if(item.row.closest('.message')===run.article && item.row.classList.contains('running'))setActivityDuration(item.row,Math.max(0,Date.now()-item.startedAt));
  }
}
function restoreActivityTimings(root, timeline=[]) {
  const labels=new Map([
    ['Contexte préparé','request_prepared'],['Réponse depuis la mémoire du projet','request_prepared'],
    ['Modèle exécuté','model_active'],['Routage économique','model_routed'],['Plan de la tâche','plan_updated'],
    ['Réorientation appliquée','steer_applied'],['Point d’avancement','task_checkpoint'],
    ['Budget de contexte','context_compacted'],['Contexte compacté','context_compacted'],
    ['Utilisation du modèle','usage'],['Coût de la compaction','usage'],['Mémoire locale · aucun appel API','usage'],
  ].map(([label,type])=>[activityLabel(t(label)),type]));
  const used=new Set();
  for(const row of root.querySelectorAll('.activity-row')) {
    const title=activityLabel(OvhI18n.message(row.querySelector('strong')?.textContent || ''));
    const type=row.dataset.eventType || labels.get(title);
    const stampValue=row.querySelector('.message-time')?.dateTime;
    let event=row.dataset.activityId && timeline.find(item=>!used.has(item) && item.id===row.dataset.activityId && item.type==='tool_start');
    if(!event)event=timeline.find(item=>!used.has(item) && (type?item.type===type:item.type==='tool_start' && activityLabel(OvhI18n.message(item.title || ''))===title)
      && (!row.dataset.eventType || !stampValue || Date.parse(item.timestamp)===Date.parse(stampValue)));
    if(event)used.add(event);
    let end=event;
    if(event?.type==='tool_start')end=timeline.find(item=>item.type==='tool_end' && item.id===event.id);
    if(event?.type==='model_active') {
      end=undefined;
      for(const item of timeline.slice(timeline.indexOf(event)+1)) {
        if((item.type==='user' && !item.steering) || item.type==='model_active')break;
        if((item.type==='usage' && !item.purpose && item.model===event.model) || ['response','error'].includes(item.type)) {end=item;break;}
      }
    }
    let duration=actionDuration(end),estimated=false,partial=false;
    if(duration===null && row.dataset.durationMs!==undefined && validDuration(Number(row.dataset.durationMs))) {
      duration=Number(row.dataset.durationMs);estimated=row.dataset.durationEstimated==='true';partial=row.dataset.durationPartial==='true' || row.classList.contains('running');
    }
    if(duration===null && event && end && end!==event) {
      const start=Date.parse(event.timestamp),finish=Date.parse(end.timestamp);
      if(Number.isFinite(start) && Number.isFinite(finish) && finish>=start) {duration=finish-start;estimated=true;}
    }
    setActivityDuration(row,duration,{estimated,partial});
    // Older archives have second-resolution timestamps: zero means less than a second, not a measured zero.
    if(estimated && duration===0 && event?.timestamp && !/\.\d+/.test(event.timestamp)) {
      const elapsed=row.querySelector('.activity-duration');elapsed.textContent='< 1 s';elapsed.title=t('Durée : {0}',elapsed.textContent);
    }
    if(row.classList.contains('running'))row.classList.replace('running','stopped');
    const body=row.querySelector(':scope > div');
    if(body)stamp(body,stampValue || event?.timestamp || null);
  }
}

function addActivity(data, updateCurrentAction = true) {
  data={...data,id:data.id || `${data.type || data.tool}-${Date.now()}-${Math.random().toString(36).slice(2)}`};
  const run = ensureRun();
  run.actions += 1;
  if (updateCurrentAction) {
    run.activeId = data.id;
    run.currentTool = data.tool;
    run.currentAction = [data.title || t("Action en cours"), data.detail || ""].filter(Boolean).join(" · ").replace(/\s+/g, " ");
  }
  updateRunSummary(run);
  const row = document.createElement("div");
  row.className = "activity-row running";
  row.dataset.activityId=data.id;
  if(data.type)row.dataset.eventType=data.type;
  const icon = document.createElement("span");
  icon.className = "activity-icon";
  icon.innerHTML = uiIcon(iconForTool(data.tool));
  const body = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = activityLabel(data.title || t("Action"));
  const detail = document.createElement("code");
  detail.textContent = data.detail || "";
  body.append(title, detail);
  stamp(body, data.timestamp);
  row.append(icon, body);
  run.list.appendChild(row);
  activityRows.set(data.id, { row, icon, detail, startedAt:Date.now(), timestamp:data.timestamp });
  setActivityDuration(row,0);
  persist();
  scrollToBottom();
  return data.id;
}

function finishActivity(data) {
  const item = activityRows.get(data.id);
  if (!item) return;
  const wasRunning=item.row.classList.contains('running');
  item.row.classList.remove("running","stopped","success","failure");
  item.row.classList.add(data.success ? "success" : "failure");
  if (!data.success) item.icon.innerHTML = uiIcon("error");
  if (currentRun?.activeId === data.id) {
    // Keep the last displayed action until the next activity arrives.
    currentRun.activeId = null;
    updateRunSummary(currentRun);
  }
  item.detail.textContent = data.detail || "";
  let duration=actionDuration(data);
  if(duration===null && !data.completedOnly)duration=!wasRunning && validDuration(Number(item.row.dataset.durationMs))?Number(item.row.dataset.durationMs):Math.max(0,Date.now()-item.startedAt);
  setActivityDuration(item.row,duration);
  persist();
}

function addCompletedActivity(tool, title, detail, data={}) {
  const id = `${tool}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  // Context/billing telemetry belongs in the journal, not in the current task label.
  addActivity({ ...data, id, tool, title, detail }, false);
  finishActivity({ ...data, id, success: true, detail, completedOnly:true });
}

function verificationCheckText(data) {
  const phase = t(data.phase === "before" ? "Avant modification" : "Après vérification");
  const exit = data.exit_code == null ? t("indisponible") : String(data.exit_code);
  const count = Number.isInteger(data.reported_tests) && data.reported_tests >= 0 ? String(data.reported_tests) : t("indisponible");
  const countLabel = t("Tests décomptés : {0}", count);
  return `${phase} · ${data.check || ""}\n${(data.argv || []).join(" ")}\nexit=${exit} · ${data.duration_ms || 0} ms\n${countLabel}\n${String(data.output || "").slice(-4000)}`;
}

function showVerification(data, article = ensureRun().article) {
  if (data.status === "not_required") return;
  let badge = article.querySelector(":scope > .verification-status");
  if (!badge) {
    badge = document.createElement("div");
    badge.className = "verification-status";
    article.appendChild(badge);
  }
  const labels = {
    pending: "Modifications en attente de vérification",
    running: "Contrôles en cours",
    passed: "Contrôles configurés réussis",
    not_configured: "Observations locales · aucun test fonctionnel exécuté",
    failed: "Validation refusée",
    observed_failure: "Audit : échecs observés dans les contrôles",
    rolled_back: "Modifications non validées restaurées",
    conflict: "Conflit de restauration : changement extérieur préservé",
  };
  // A failed read-only task can trigger rollback with no tracked changes.
  const emptyRollback = data.status === "rolled_back" && Array.isArray(data.restored)
    && data.restored.length === 0 && !(data.conflicts || []).length;
  const partialValidation = data.status === "passed" && (data.scope === "syntax_and_content" ||
    (Array.isArray(data.functional_coverage_missing) && data.functional_coverage_missing.length > 0));
  badge.textContent = t(emptyRollback ? "Aucune modification à restaurer" : partialValidation
    ? "Contrôles limités · couverture fonctionnelle incomplète" : (labels[data.status] || "Validation refusée"));
  badge.className = `verification-status verification-${Object.hasOwn(labels, data.status) ? data.status : "failed"}`;
  const details = [...(data.problems || []), ...(data.conflicts || [])];
  if (data.status === "not_configured") {
    details.push(t("Aucune suite de tests n’est configurée pour cet audit. Les observations locales ne valident pas le comportement fonctionnel."));
  }
  if (partialValidation) {
    details.push(t("Pour les fichiers sans contrôle fonctionnel, seuls le contenu sauvegardé et la syntaxe disponible sont vérifiés. Cela ne valide pas leur comportement."));
    if (Array.isArray(data.functional_coverage_missing) && data.functional_coverage_missing.length) {
      details.push(t("Sans contrôle fonctionnel : {0}", data.functional_coverage_missing.join(", ")));
    }
  }
  if (data.status === "passed") details.push(t("Ces contrôles couvrent leur périmètre, pas l’ensemble des affirmations du modèle."));
  badge.title = details.join("\n");
  persist();
}

function showExecutionEvidence(data, article) {
  // Execution evidence remains in the journal for the controller and archive,
  // but it is no longer exposed as a decorative badge in the conversation.
  article?.querySelectorAll(":scope > .execution-evidence").forEach(badge => badge.remove());
}

function restoreExecutionEvidence(root, timeline) {
  root.querySelectorAll(".execution-evidence").forEach(badge => badge.remove());
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
  if (data.provider === "deepseek") {
    liveBalance.textContent = data.available ? `DeepSeek · ${formatMoney(data.credit_available, data.currency)}` : "DeepSeek · —";
    billing.title = liveBalance.title = data.available ? `DeepSeek · ${data.last_update}` : (data.error || "DeepSeek");
    billing.classList.toggle("connected", Boolean(data.available));
    billing.classList.toggle("failed", !data.available);
    if (announce && data.error) setStatus(data.error, true);
    return;
  }
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
  if (file.size > 32 * 1024 * 1024) throw new Error(t("Image trop volumineuse (32 Mio maximum avant optimisation)."));
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
  // Le lecteur Vision extrait les pixels ; le modèle sélectionné exécute la tâche.
  // La préférence de l’utilisateur reste inchangée.
  setStatus(t("Un modèle Vision sera utilisé pour cette image ; ton modèle sélectionné est conservé."));
}

let imagePreparationQueue = Promise.resolve();
let pendingImagePreparations = 0;

function addImageFiles(files) {
  // Serialize paste/drop/file-picker events: concurrent decodes must share the limit.
  const selected = Array.from(files || []);
  pendingImagePreparations += 1;
  const work = imagePreparationQueue.then(() => prepareImageFiles(selected));
  imagePreparationQueue = work.catch(() => {});
  return work.finally(() => { pendingImagePreparations -= 1; });
}

async function prepareImageFiles(files) {
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
  if (pendingImagePreparations) {
    setStatus(t("Préparation des images en cours…"));
    return;
  }
  if (loadingArchive || creatingConversation || stopping) return;
  const text = prompt.value.trim();
  if ((!text && !pendingImages.length) || pendingFollowup) return;
  const retry = retryFollowup?.conversation === activeArchiveId && retryFollowup.text === text ? retryFollowup : null;
  if (busy || editingFollowup || retry) {
    if (!activeArchiveId) { setStatus(t("Connexion en cours…")); return; }
    if (pendingImages.length) { setStatus(t("Les messages en attente acceptent du texte uniquement."), "warning"); return; }
    if (text.length > 16000) { setStatus(t("Message limité à 16 000 caractères."), "warning"); return; }
    const id = editingFollowup || retry?.id || crypto.randomUUID();
    const action = editingFollowup ? "edit" : retry?.action || "add";
    pendingFollowup = {id, text, action};
    send.disabled = true;
    followupCommand(action, {id, text, mode: "queue", fullAccess, model: selectedModel});
    return;
  }
  ensureVisionModel();
  const images = pendingImages.slice();
  const displayText = text || t("Analyse cette image.");
  retryFollowup = null;
  pendingSubmission = {kind:"prompt", text:displayText, images, timestamp:new Date().toISOString(), model:selectedModel};
  if (promptHistory.at(-1) !== displayText) promptHistory.push(displayText);
  promptHistory = promptHistory.slice(-200);
  historyIndex = promptHistory.length;
  historyDraft = "";
  addUserMessage(displayText, images, pendingSubmission.timestamp);
  createAssistantRun();
  prompt.value = "";
  updateStopIcon();
  prompt.style.height = "auto";
  pendingImages = [];
  renderPendingImages();
  setBusy(true, t("Connexion au fournisseur…"));
  vscode.postMessage({ type: "prompt", text: displayText, fullAccess, model: selectedModel, images });
}

if (saved?.html) {
  messages.innerHTML = safeHtml(saved.html);
  upgradeSnapshotPresentation(messages);
  restoreTimestamps(messages);
  restoreActivityTimings(messages);
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
attach.addEventListener("click", () => imageInput.click());
imageInput.addEventListener("change", async () => {
  await addImageFiles(imageInput.files);
  imageInput.value = "";
});
stop.addEventListener("click", () => {
  interruptPendingFollowup();
  pendingSubmission = null;
  stopping = true;
  // Prevent a new request from racing SIGTERM; idle/error releases the composer.
  send.disabled = true;
  vscode.postMessage({ type: "cancel" });
});
clear.addEventListener("click", () => {
  if (creatingConversation) return;
  creatingConversation = true;
  stopping = false;
  archiveRevision = ""; archiveModelRestored = false;
  endArchiveLoading();
  loadingArchive = null;
  window.clearTimeout(snapshotTimer);
  clear.disabled = true;
  activeArchiveId = "";
  followups = [];
  editingFollowup = null;
  pendingFollowup = null;
  retryFollowup = null;
  pendingSubmission = null;
  activeFullAccess = null;
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
  send.disabled = true;
  setStatus(t("Nouvelle conversation"));
  prompt.focus();
});
document.getElementById("project-picker")?.addEventListener("click", () => vscode.postMessage({type:"selectProject"}));
configure.addEventListener("click", () => vscode.postMessage({ type: "configureKey" }));
billing.addEventListener("click", () => {
  setStatus(t("Actualisation du solde…"));
  vscode.postMessage({ type: "billing" });
});
history.addEventListener("click", () => {
  modelPanelActive = false;
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
  modelPanelActive = !modelPanel.hidden;
  if (!modelPanel.hidden) {
    modelSearch.focus();
    vscode.postMessage({ type: "listModels" });
  }
});
closeModels.addEventListener("click", () => { modelPanelActive = false; modelPanel.hidden = true; });
refreshModels.addEventListener("click", () => {
  setStatus(t("Actualisation du catalogue…"));
  vscode.postMessage({ type: "listModels", refresh: true });
});
modelSearch.addEventListener("input", renderModels);
access.addEventListener("click", () => {
  fullAccess = !fullAccess;
  updateAccessButton();
  setStatus(fullAccess
    ? t("Accès complet pour la prochaine demande ou reprise")
    : t("Validation pour la prochaine demande ou reprise"));
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
  const clipboard = event.clipboardData;
  let imageFiles = Array.from(clipboard?.files || []).filter((file) => file.type.startsWith("image/"));
  // Chromium/VS Code can expose screenshots as DataTransferItems only.
  if (!imageFiles.length) {
    imageFiles = Array.from(clipboard?.items || [])
      .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
      .map((item) => item.getAsFile()).filter(Boolean);
  }
  if (!imageFiles.length) return;
  event.preventDefault();
  const text = clipboard?.getData?.("text/plain") || "";
  if (text) {
    prompt.setRangeText(text, prompt.selectionStart, prompt.selectionEnd, "end");
    prompt.dispatchEvent(new Event("input", { bubbles: true }));
  }
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
  // Only accept revision tokens after conversation-ID filtering below.
  if (data.type === "archive_open_failed" || (loadingArchive && data.type === "error")) {
    loadingArchive = null;
    endArchiveLoading();
    setArchiveVisible(true);
    setStatus(data.text || t("Termine ou arrête la tâche avant de changer de projet."), "warning");
    return;
  }
  if (loadingArchive && ["archive_loaded", "snapshot_loaded"].includes(data.type) && data.id !== loadingArchive) return;
  if (typeof data.archive_revision === 'string' && ['archive_loaded','snapshot_loaded','response','error'].includes(data.type)) {
    archiveRevision = data.archive_revision;
  }
  if (creatingConversation) {
    if (data.type === "ready") { backendReady = true; return; }
    if (data.type === "cleared") {
      creatingConversation = false;
      clear.disabled = false;
    } else if (data.type === "error" || data.type === "idle") {
      creatingConversation = false;
      clear.disabled = false;
      if (data.type === "idle") data = {type:"error",text:t("Création de conversation interrompue. Réessaie Nouvelle conversation.")};
    } else if (!["billing_status", "billing_configured", "budget_status", "configured"].includes(data.type)) return;
  }
  if (["status", "error", "archive_delete_failed"].includes(data.type)) data = {...data, text: OvhI18n.message(data.text)};
  if (["tool_start", "approval"].includes(data.type)) data = {...data, title: OvhI18n.message(data.title)};
  if (data.type === "tool_end") data = {...data, detail: OvhI18n.message(data.detail)};
  if (data.type === "model_routed") data = {...data, reason: OvhI18n.message(data.reason)};
  if (data.type === "ready") {
    if (data.resources) showPerformance(data.resources.mode, data.resources);
    if (Number.isInteger(data.emoji_mode) && data.emoji_mode >= 0 && data.emoji_mode <= 3) emojiMode = data.emoji_mode;
    for (const content of messages.querySelectorAll(".assistant .markdown")) filterResponseEmojis(content);
    if (!loadingArchive) activeArchiveId = data.conversation || activeArchiveId;
    updateSendButton();
    const archivePath = document.getElementById("archive-path");
    if (archivePath) {
      archivePath.textContent = data.archive_root || t("Chemin d’archivage indisponible");
      archivePath.title = data.archive_root || "";
    }
    backendReady = true;
    followupCommand("list");
    if (data.model && !loadingArchive && !archiveModelRestored && !pendingSubmission && !modelSelectionChanged) selectedModel = data.model;
    updateModelButton();
    setBusy(busy, busy ? status.textContent : t("Connecté · {0}", data.model));
    persist();
    vscode.postMessage({ type: "listModels" });
  }
  if (data.type === "followups" && data.conversation === activeArchiveId) {
    followups = data.items || [];
    if (retryFollowup?.conversation === activeArchiveId &&
        followups.some(item => item.id === retryFollowup.id && item.text === retryFollowup.text)) {
      if (prompt.value.trim() === retryFollowup.text) prompt.value = "";
      if (pendingFollowup?.id === retryFollowup.id) pendingFollowup = null;
      retryFollowup = null;
      editingFollowup = null;
      resizePrompt();
      send.disabled = !!loadingArchive || creatingConversation || stopping;
    }
    if (editingFollowup && !followups.some(item => item.id === editingFollowup)) editingFollowup = null;
    updateSendButton();
    renderFollowups();
  }
  if (["followup_ack", "followup_error"].includes(data.type) && data.conversation && data.conversation !== activeArchiveId) return;
  if (["followup_ack", "followup_error"].includes(data.type) && data.action === "reorder") {
    if (pendingFollowupOrder && pendingFollowupOrder.id === data.id) {
      pendingFollowupOrder = null;
      followupsBox.removeAttribute('aria-busy');
      renderFollowups();
      if (data.type === 'followup_error') {
        setStatus(t("Ordre non enregistré : {0}", OvhI18n.message(data.text)), true);
        followupCommand('list');
      } else setStatus(t("Ordre des tâches enregistré."), "success");
    }
    return;
  }
  const acknowledgedFollowup = pendingFollowup || retryFollowup;
  if (data.type === "followup_ack" && acknowledgedFollowup?.id === data.id && data.action === acknowledgedFollowup.action) {
    if (prompt.value.trim() === acknowledgedFollowup.text) prompt.value = "";
    pendingFollowup = null;
    retryFollowup = null;
    editingFollowup = null;
    send.disabled = !!loadingArchive || creatingConversation || stopping;
    resizePrompt();
    updateSendButton();
    if (data.already_started) {
      if (data.resumable) resumableArchives.add(activeArchiveId);
      const notice = data.resumable
        ? t("Ce message a déjà démarré. Reprends la tâche interrompue pour continuer.")
        : t("Ce message a déjà été traité.");
      if (data.running === false) setBusy(false, notice);
      else setStatus(notice);
    }
  }
  if (data.type === "followup_error") {
    if (pendingFollowup && pendingFollowup.id === data.id && (!data.action || data.action === pendingFollowup.action)) {
      pendingFollowup = null;
      retryFollowup = null;
      send.disabled = !!loadingArchive || creatingConversation || stopping;
    }
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
    addCompletedActivity("context", t("Réorientation appliquée"), data.text, data);
  }
  if (data.type === "status") {
    pendingSubmission = null;
    if (!loadingArchive) activeArchiveId = data.conversation || activeArchiveId;
    followupCommand("list");
    ensureRun();
    setBusy(true, data.text);
  }
  if (data.type === "provider_switched") {
    // The backend restarts with the new provider: its model and balance replace
    // the previous provider's selection instead of being ignored as a manual pick.
    if (!data.pending) modelSelectionChanged = false;
    setStatus(data.pending ? t("Fournisseur {0} · appliqué à la fin de la tâche", data.provider)
      : t("Fournisseur : {0}", data.provider));
  }
  if (data.type === "session_busy") {
    stopping = false;
    pendingSubmission = null;
    interruptPendingFollowup();
    if (performancePicker) performancePicker.disabled = false;
    resetFollowupInteraction();
    if (currentRun) finishRun("stopped");
    setBusy(false, OvhI18n.message(data.text), true);
  }
  if (data.type === "tool_start") addActivity(data);
  if (data.type === "tool_end") finishActivity(data);
  if (data.type === "task_checkpoint") addCompletedActivity("context", t("Point d’avancement"), t("{0} s écoulées · {1} appels restants", data.elapsed_seconds, data.remaining_requests), data);
  if (data.type === "verification_status") showVerification(data);
  if (data.type === "execution_evidence") {
    const last = messages.lastElementChild;
    const article = currentRun?.article || (last?.classList.contains("assistant") ? last : ensureRun().article);
    showExecutionEvidence(data, article);
    persist();
  }
  if (data.type === "verification_check") {
    const detail = verificationCheckText(data);
    addActivity({...data, tool: "command", title: data.check, detail}, false);
    finishActivity({...data, success: data.passed === true, detail, completedOnly:true});
  }
  if (data.type === "file_change") addFileChange(data);
  if (data.type === "model_changed") {
    selectedModel = normalizeModelSelection(data.to_model);
    lastRoutedModel = "";
    updateModelButton();
    addModelChange(data);
  }
  if (data.type === "model_active") {
    if (data.selection) selectedModel = data.selection;
    lastRoutedModel = selectedModel === "auto-balanced" ? (data.model || "") : "";
    updateModelButton();
    const mode = data.automatic ? t("sélection automatique") : t("choix manuel verrouillé");
    const id=addActivity({...data, tool:"model", title:t("Modèle exécuté"), detail:`${data.model} · ${mode}`},false);
    currentRun.modelActivityId=id;
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
    addCompletedActivity("route", t("Routage économique"), `${data.model} · ${data.reason || t("modèle compatible")}${cost}${pool}${history}${account}`, data);
  }
  if (data.type === "plan_updated") {
    const steps = Array.isArray(data.steps) ? data.steps : [];
    const current = Number(data.current_step) || 0;
    addCompletedActivity("context", t("Plan de la tâche"), steps.map((step, i) =>
      `${i < current ? "✓" : i === current ? "→" : "○"} ${i + 1}. ${step}`).join("\n"), data);
    if (typeof steps[current] === "string" && !currentRun.activeId) {
      currentRun.currentAction = steps[current].replace(/\s+/g, " ");
      updateRunSummary(currentRun);
    }
  }
  if (data.type === "request_prepared") {
    const sources = Array.isArray(data.sources) ? data.sources : [];
    const files = new Set(sources.map(source => source.path)).size;
    const detail = t("{0} fichiers ciblés", files);
    addCompletedActivity("context", t(data.local_answer ? "Réponse depuis la mémoire du projet" : "Contexte préparé"), detail +
      (data.partial ? t(" · recherche partielle, complétée au besoin par les outils") : ""), data);
  }
  if (data.type === "context_compacted") {
    const usage = Number.isFinite(data.estimated_tokens)
      ? t("≈ {0} / {1} tokens de contexte", data.estimated_tokens, data.budget_tokens) : "";
    const mode = data.summary_mode === "semantic" ? t(" · résumé généré")
      : data.summary_mode === "extractive" ? t(" · extraits archivés") : "";
    const saved = data.saved_percent ? t(" · {0}% de contexte libéré", data.saved_percent) : "";
    addCompletedActivity("context", data.compacted ? t("Contexte compacté") : t("Budget de contexte"), usage + mode + saved, data);
  }
  if (data.type === "usage") {
    if(currentRun?.modelActivityId && !data.purpose && !data.local_answer) {
      const item=activityRows.get(currentRun.modelActivityId);
      if(item)finishActivity({...data,id:currentRun.modelActivityId,success:true,detail:item.detail.textContent});
      currentRun.modelActivityId=null;
    }
    const timing = Number.isFinite(data.first_text_ms)
      ? t(" · premier texte : {0} s", (data.first_text_ms / 1000).toFixed(2)) : "";
    addCompletedActivity("cost", data.local_answer ? t("Mémoire locale · aucun appel API") : data.purpose === "compaction" ? t("Coût de la compaction") : t("Utilisation du modèle"), t("{0} · {1} tokens entrée · {2} sortie · {3}", data.model, data.input_tokens, data.output_tokens, formatModelCost(data.cost_eur ?? data.cost_usd, data.cost_eur != null ? "EUR" : "USD")) + timing, data);
  }
  if (data.type === "approval") addApproval(data);
  if (data.type === "approval_result") resolveApproval(data);
  if (data.type === "questionnaire") pendingQuestionnaire = data;
  if (data.type === "delta") setMarkdown(data.html);
  if (data.type === "response") {
    pendingSubmission = null;
    resumableArchives.delete(activeArchiveId);
    setMarkdown(data.html);
    if (data.verification) showVerification(data.verification);
    finishRun("done");
    setBusy(false, "");
    if (pendingQuestionnaire) { const questionnaire = pendingQuestionnaire; pendingQuestionnaire = null; renderQuestionnaire(questionnaire); }
  }
  if (data.type === "error") {
    stopping = false;
    pendingSubmission = null;
    if (performancePicker) performancePicker.disabled = false;
    interruptPendingFollowup();
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
    stopping = false;
    pendingSubmission = null;
    interruptPendingFollowup();
    if (performancePicker) performancePicker.disabled = false;
    vscode.postMessage({ type: "listArchives" });
    if (currentRun) finishRun("stopped");
    setBusy(false, "");
  }
  if (data.type === "cleared") { lastRoutedModel = ""; updateModelButton(); activeArchiveId = data.conversation || ""; setBusy(false, t("Nouvelle conversation")); }
  if (data.type === "configured") setBusy(false, t("Clé API enregistrée de façon sécurisée"));
  if (data.type === 'performance_status') {
    showPerformance(data.mode, data);
    setStatus(t('Performances locales : {0}', data.mode === 'max' ? 'Max' : 'Auto'));
  }
  if (data.type === 'performance_error') {
    showPerformance(data.mode);
    setStatus(t('Impossible de changer les performances : {0}', OvhI18n.message(data.text)), true);
  }
  if (data.type === "billing_configured") setBusy(false, t("Accès lecture seule à la facturation enregistré"));
  if (data.type === "billing_status") applyBillingStatus(data, !data.silent && !busy);
  if (data.type === "access_mode") {
    activeFullAccess = Boolean(data.full_access);
    updateAccessButton();
  }
  if (data.type === "task_interrupted") {
    activeArchiveId = data.id || activeArchiveId;
    resumableArchives.add(activeArchiveId);
    if (!pendingSubmission) setBusy(false, t("Tâche interrompue - clique sur ▶ pour la reprendre."));
    vscode.postMessage({type: "listArchives"});
  }
  if (data.type === "archive_delete_pending") {
    paintNotice(document.getElementById("archive-notice"),t("Suppression définitive en cours…"),"warning");
    const row = Array.from(archiveList.querySelectorAll('.archive-item')).find(item => item.dataset.archiveId === data.id && (data.source ? item.dataset.archiveProject === data.source : item.dataset.archiveActive !== 'false'));
    if (row) { row.classList.add("deleting"); row.querySelectorAll("button").forEach(button => { button.disabled = true; }); }
    setStatus(t("Suppression définitive en cours…"), "warning");
  }
  if (data.type === "archive_delete_failed") {
    paintNotice(document.getElementById("archive-notice"),data.text || t("La suppression a échoué."),"error");
    const row = Array.from(archiveList.querySelectorAll('.archive-item')).find(item => item.dataset.archiveId === data.id && (data.source ? item.dataset.archiveProject === data.source : item.dataset.archiveActive !== 'false'));
    if (row) { row.classList.remove("deleting"); row.querySelectorAll("button").forEach(button => { button.disabled = false; }); }
    setStatus(data.text || t("La suppression a échoué."), true);
  }
  if (data.type === "archive_deleted") {
    if (data.active !== false) {
      resumableArchives.delete(data.id);
      if (activeArchiveId === data.id) activeArchiveId = "";
    }
    updateSendButton();
    const notice = document.getElementById("archive-notice");
    paintNotice(notice,t("Conversation supprimée définitivement."),"success");
    if (data.active !== false) window.clearTimeout(snapshotTimer);
    const row = Array.from(archiveList.querySelectorAll('.archive-item')).find(item => item.dataset.archiveId === data.id && (data.source ? item.dataset.archiveProject === data.source : item.dataset.archiveActive !== 'false'));
    if (row) row.remove();
    if (data.was_current) {
      backendReady = false;
      renderArchiveMessages([]);
      persist();
    }
    setStatus(t("Conversation supprimée définitivement"), "success");
  }
  if (data.type === 'archives_organized' || data.type === 'archive_organization_failed') {
    const command=pendingOrganization;
    pendingOrganization=null;organizationFocus=command;
    archiveList.removeAttribute('aria-busy');
    const success=data.type==='archives_organized';
    if(success && command?.type==='moveConversation' && command.target) {
      const group=[...archiveList.querySelectorAll('.archive-project')].find(item=>item.dataset.projectId===command.target);
      if(group)group.open=true;
    }
    archiveOrganizationNotice(success?t(command?.type==='moveProject'?'Ordre des dossiers enregistré.':'Conversation déplacée.'):(data.text || t('Le classement a échoué.')),success?'success':'error');
  }
  if (data.type === 'project_updated' || data.type === 'project_update_failed') projectUpdateResult(data);
  if (data.type === "archives") {
    resumableArchives.clear();
    for (const item of [...(data.items || []), ...(data.projects || []).flatMap(project=>(project.items || []).map(item=>({...item,archive_active:item.archive_active ?? project.active})))]) {
      if(item.archive_active===false)continue;
      if (item.resumable) resumableArchives.add(item.id);
      if (!activeArchiveId && item.current) activeArchiveId = item.id;
    }
    updateSendButton();
    renderArchives(data.items, data.projects);
    const archivePath = document.getElementById("archive-path");
    if (data.archive_root && archivePath) {
      archivePath.textContent = data.archive_root;
      archivePath.title = data.archive_root;
    }
  }
  if (data.type === "models") {
    modelCatalog = data.items || [];
    // A delayed catalogue reply describes global preference, not the opened archive.
    if (data.selected && !loadingArchive && !archiveModelRestored && !modelSelectionChanged) selectedModel = normalizeModelSelection(data.selected);
    updateModelButton();
    renderModels();
    if (pendingImages.length) ensureVisionModel();
    setStatus(modelAvailabilityLabel());
  }
  if (data.type === "model_selected") {
    selectedModel = normalizeModelSelection(data.model);
    modelSelectionChanged = true;
    if (selectedModel !== "auto-balanced") lastRoutedModel = "";
    updateModelButton();
  }
  if (data.type === "project_selected") {
    archiveList.querySelectorAll('.archive-project').forEach(group=>group.classList.toggle('selected',group.dataset.projectId===data.project));
  }
  if (data.type === "archive_loaded") {
    const submission = (data.automatic || pendingSubmission?.kind === "resume") ? pendingSubmission : null;
    loadingArchive = null;
    setArchiveVisible(false);
    modelPanel.hidden = !modelPanelActive;
    activeArchiveId = data.id || activeArchiveId;
    followups = [];
    followupCommand("list");
    if (modelSelectionRevision === archiveLoadingModelRevision) restoreArchiveModel(data);
    else { archiveModelRestored = true; updateModelButton(); }
    renderArchiveMessages(data.messages);
    if (data.resumable) resumableArchives.add(activeArchiveId);
    endArchiveLoading(true);
    restorePendingSubmission(submission, data.resumable ? t("Tâche interrompue - clique sur ▶ pour la reprendre.") : data.has_context ? t("Conversation restaurée avec son contexte") : t("Archive restaurée"));
  }
  if (data.type === "snapshot_loaded") {
    const submission = (data.automatic || pendingSubmission?.kind === "resume") ? pendingSubmission : null;
    loadingArchive = null;
    setArchiveVisible(false);
    modelPanel.hidden = !modelPanelActive;
    activeArchiveId = data.id || activeArchiveId;
    if (data.resumable) resumableArchives.add(activeArchiveId);
    followups = [];
    followupCommand("list");
    const preserveModel = modelSelectionRevision !== archiveLoadingModelRevision
      ? { selectedModel, lastRoutedModel } : null;
    applySnapshot(data);
    if (preserveModel) {
      selectedModel = preserveModel.selectedModel;
      lastRoutedModel = preserveModel.lastRoutedModel;
      archiveModelRestored = true;
      updateModelButton();
    }
    endArchiveLoading(true);
    restorePendingSubmission(submission, t("Conversation restaurée"));
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
  const selection = selectedArchiveForDeletion;
  selectedArchiveForDeletion = null;
  document.getElementById("delete-dialog").close();
  if (selection) vscode.postMessage({type: "deleteArchiveConfirmed", ...selection});
});
