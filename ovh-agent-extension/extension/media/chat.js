const { svg: faIcon } = AgentIcons;
const uiLanguage = OvhI18n.setLanguage(OvhI18n.resolveLanguage(
  document.body.dataset.language, navigator.languages?.length ? navigator.languages : [navigator.language]));
const { t } = OvhI18n;
document.documentElement.lang = uiLanguage;
OvhI18n.translateStatic(document.body);
const vscode = acquireVsCodeApi();
const brandName = document.body.dataset.brandName || "OVH Agent";
document.getElementById("prompt").placeholder = t("Demander à {0}…", brandName);
document.documentElement.style.setProperty("--ovh-brand-badge", JSON.stringify(document.body.dataset.brandBadge || "OVH"));
const messages = document.getElementById("messages");
const prompt = document.getElementById("prompt");
const send = document.getElementById("send");
const stop = document.getElementById("stop");
const clear = document.getElementById("clear");
const configure = document.getElementById("configure");
const billing = document.getElementById("billing");
const liveBalance = document.getElementById("live-balance");
const access = document.getElementById("access");
const history = document.getElementById("history");
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
    ? faIcon("play")
    : sendArrow;
  send.title = resumable ? t("Reprendre la tâche interrompue") : t("Envoyer");
  send.setAttribute("aria-label", send.title);
}
function resumeArchive(id) {
  if (busy) return;
  activeArchiveId = id;
  archivePanel.hidden = true;
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
  if (model.hourly != null) return t("${0}/heure", Number(model.hourly).toFixed(3));
  if (model.free) return t("Gratuit");
  const input = Number(model.input_per_million || 0);
  const output = Number(model.output_per_million || 0);
  if (input || output) {
    const inputText = input ? `$${input.toFixed(2)}` : "—";
    const outputText = output ? `$${output.toFixed(2)}` : "—";
    return t("{0} entrée · {1} sortie / 1M tokens", inputText, outputText);
  }
  return t("Tarif non communiqué par l’API");
}

function renderModels() {
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
    badge.textContent = model.kind === "auto" ? t("Éco · Auto") : model.vision ? "Vision · Agent" : modelKindLabel(model.kind);
    top.append(name, badge);
    const price = document.createElement("div");
    price.className = "model-price";
    price.textContent = priceLabel(model);
    const meta = document.createElement("div");
    meta.className = "model-meta";
    meta.textContent = [model.owner, compactTokens(model.context_length)].filter(Boolean).join(" · ");
    row.append(top, price, meta);
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
  send.disabled = value;
  updateSendButton();
  stop.hidden = !value;
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

function addUserMessage(text, images = []) {
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
  messages.appendChild(article);
  persist();
  scrollToBottom();
}

function addArchivedAssistant(html, failed = false) {
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
  messages.appendChild(article);
}

function wireFileChangeCopy(details) {
  const copy = details.querySelector(".copy-diff");
  if (!copy) return;
  copy.innerHTML = faIcon("copy");
  copy.onclick = async (event) => {
    event.preventDefault();
    event.stopPropagation();
    await navigator.clipboard.writeText(details.dataset.diff || "");
    copy.innerHTML = faIcon("check");
    window.setTimeout(() => { copy.innerHTML = faIcon("copy"); }, 1200);
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
  icon.innerHTML = faIcon(data.action === "created" ? "plus" : "edit");
  const title = document.createElement("strong");
  title.textContent = data.action === "created" ? t("Fichier créé") : t("Fichier modifié");
  summary.append(icon, title);
  summary.insertAdjacentHTML("beforeend", faIcon("chevron", "summary-chevron"));

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
  copy.innerHTML = faIcon("copy");
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
  article.appendChild(createFileChange(data));
  messages.appendChild(article);
}

function renderArchiveMessages(items) {
  if (currentRun) window.clearInterval(currentRun.timer);
  currentRun = null;
  activityRows.clear();
  approvalCards.clear();
  messages.innerHTML = "";
  for (const item of items || []) {
    if (item.type === "user") addUserMessage(item.text || "", item.images || []);
    if (item.type === "file_change") addArchivedFileChange(item);
    if (item.type === "response") addArchivedAssistant(item.html || "");
    if (item.type === "error") addArchivedAssistant(item.html || "", true);
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
    button.addEventListener("click", () => {
      activeArchiveId = item.id;
      updateSendButton();
      archivePanel.hidden = true;
      setStatus(t("Chargement de la conversation…"));
      vscode.postMessage({ type: "loadArchive", id: item.id });
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon archive-delete";
    remove.title = t("Supprimer définitivement la conversation");
    remove.setAttribute("aria-label", t("Supprimer définitivement : {0}", displayTitle));
    remove.innerHTML = faIcon("trash");
    remove.addEventListener("click", () => {
      if (busy) { setStatus(t("Attends la fin de la réponse avant de supprimer une conversation.")); return; }
      selectedArchiveForDeletion = item.id;
      document.getElementById("delete-title").textContent = displayTitle;
      document.getElementById("delete-dialog").showModal();
      document.getElementById("delete-cancel").focus();
    });
    row.appendChild(button);
    if (item.resumable) {
      const resume = document.createElement("button");
      resume.type = "button";
      resume.className = "icon archive-resume";
      resume.setAttribute("aria-label", t("Reprendre la tâche interrompue"));
      resume.innerHTML = faIcon("reload");
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
  spinner.innerHTML = faIcon("terminal");
  const summaryText = document.createElement("span");
  summaryText.className = "activity-summary-text";
  summaryText.textContent = t("Réflexion en cours…");
  summary.append(spinner, summaryText);
  summary.insertAdjacentHTML("beforeend", faIcon("chevron", "summary-chevron"));
  const list = document.createElement("div");
  list.className = "activity-list";
  activity.append(summary, list);

  const content = document.createElement("div");
  content.className = "content markdown streaming";
  content.innerHTML = "";
  article.append(label, activity, content);
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
  // Preserve the user’s expanded/collapsed choice through completion.
  persist();
  currentRun = null;
  scrollToBottom();
}

function enhanceMarkdown(container) {
  for (const block of container.querySelectorAll("pre")) {
    block.querySelector(":scope > .copy-code")?.remove();
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
    indicator.innerHTML = faIcon("terminal");
  }
  for (const icon of root.querySelectorAll(".file-change-icon")) {
    if (!icon.querySelector(".fa-icon")) icon.innerHTML = faIcon(icon.textContent.trim() === "+" ? "plus" : "edit");
  }
  const old = { "≡":"list", "⌕":"search", "✎":"edit", ">_":"terminal", "↻":"reload", "≋":"context", "$":"cost", "◆":"model", "✓":"check", "!":"error" };
  for (const icon of root.querySelectorAll(".activity-icon")) {
    if (!icon.querySelector(".fa-icon")) {
      const name = old[icon.textContent.trim()];
      icon.innerHTML = faIcon(typeof name === "string" ? name : "terminal");
    }
  }
  for (const summary of root.querySelectorAll(".activity > summary, .file-change > summary")) {
    if (!summary.querySelector(".summary-chevron")) summary.insertAdjacentHTML("beforeend", faIcon("chevron", "summary-chevron"));
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
  icon.innerHTML = faIcon(iconForTool(data.tool));
  const body = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = data.title || t("Action");
  const detail = document.createElement("code");
  detail.textContent = data.detail || "";
  body.append(title, detail);
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
  item.icon.innerHTML = faIcon(data.success ? "check" : "error");
  if (currentRun?.activeId === data.id) {
    currentRun.currentAction = "";
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

function formatUsd(value) {
  const amount = Number(value || 0);
  if (amount === 0) return t("gratuit");
  const options = {style: "currency", currency: "USD", minimumFractionDigits: 0,
    maximumFractionDigits: amount < 0.01 ? 5 : 3};
  return new Intl.NumberFormat(uiLanguage, options).format(amount);
}

function formatMoney(value, currency = "EUR") {
  try {
    return new Intl.NumberFormat(uiLanguage, {
      style: "currency",
      currency: String(currency || "EUR").toUpperCase(),
      maximumFractionDigits: 2,
    }).format(Number(value || 0));
  } catch {
    return `${Number(value || 0).toFixed(2)} ${currency || "EUR"}`;
  }
}

function applyBillingStatus(data, announce = true) {
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
    const value = Number(data.credit_available || 0);
    const amount = document.createElement("span");
    amount.className = "balance-amount";
    amount.classList.toggle("negative", value < 0);
    amount.textContent = (value < 0 ? "-" : "") + formatMoney(Math.abs(value), data.currency);
    const [prefix, suffix = ""] = t("Solde : {0}{1}", "\uFFFC", data.stale ? t(" (non actualisé)") : "").split("\uFFFC");
    liveBalance.replaceChildren(document.createTextNode(prefix), amount, document.createTextNode(suffix));
  }
  liveBalance.title = data.available
    ? t("Crédits disponibles OVH · relevé {0} · vérification toutes les 60 s{1}", data.last_update || t("inconnu"), data.stale ? t(" · dernière valeur connue") : "")
    : t("Cliquer pour configurer le suivi : identifiants API OVHcloud et projet Public Cloud requis (distincts de la clé AI Endpoints)");
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
  const credit = formatMoney(data.credit_available, currency);
  const current = formatMoney(data.current_usage, currency);
  const forecast = formatMoney(data.forecast_usage, currency);
  billing.title = t("Crédits disponibles {0} · consommation {1} · prévision {2} · cliquer pour actualiser", credit, current, forecast);
  if (announce) setStatus(t("OVHcloud · crédits {0} · consommation {1} · prévision {2}", credit, current, forecast));
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
    remove.innerHTML = faIcon("close");
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
  if ((!text && !pendingImages.length) || busy) return;
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
  prompt.style.height = "auto";
  pendingImages = [];
  renderPendingImages();
  setBusy(true, t("Connexion à OVHcloud…"));
  vscode.postMessage({ type: "prompt", text: displayText, fullAccess, model: selectedModel, images });
}

if (saved?.html) {
  messages.innerHTML = safeHtml(saved.html);
  upgradeSnapshotIcons(messages);
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
  if (resumableArchives.has(activeArchiveId)) resumeArchive(activeArchiveId);
  else submit();
});
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
  updateSendButton();
  archivePanel.hidden = true;
  if (currentRun) window.clearInterval(currentRun.timer);
  currentRun = null;
  activityRows.clear();
  approvalCards.clear();
  messages.innerHTML = OvhI18n.markup("<section class=\"welcome\"><h2>Agent OVHcloud</h2><p>Je peux analyser des images, lire et modifier les fichiers, rechercher dans le projet et exécuter des commandes.</p></section>");
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
  archivePanel.hidden = false;
  hideSelectionQuote();
  vscode.postMessage({ type: "listArchives" });
});
closeHistory?.addEventListener("click", () => {
  document.getElementById("archive-banner").hidden = true;
});
modelPicker.addEventListener("click", () => {
  archivePanel.hidden = true;
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
    } else if (!["billing_status", "billing_configured", "configured"].includes(data.type)) return;
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
    if (data.model) selectedModel = data.model;
    updateModelButton();
    setBusy(busy, busy ? status.textContent : t("Connecté · {0}", data.model));
    persist();
    vscode.postMessage({ type: "listModels" });
  }
  if (data.type === "status") {
    ensureRun();
    setBusy(true, data.text);
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
    const cost = data.projected_cost_usd == null ? "" : t(" · estimation {0}", formatUsd(data.projected_cost_usd));
    const pool = data.eligible_models ? t(" · {0} modèles économiques compatibles", data.eligible_models) : "";
    const history = data.previous_requests
      ? t(" · historique {0} requête{1} / {2}", data.previous_requests, data.previous_requests > 1 ? "s" : "", formatUsd(data.previous_cost_usd))
      : "";
    const account = data.billing?.available
      ? t(" · crédits {0} · prévision {1}", formatMoney(data.billing.credit_available, data.billing.currency), formatMoney(data.billing.forecast_usage, data.billing.currency))
      : "";
    addCompletedActivity("route", t("Routage économique"), `${data.model} · ${data.reason || t("modèle compatible")}${cost}${pool}${history}${account}`);
  }
  if (data.type === "context_compacted") {
    const saving = data.saved_percent ? t(" · {0}% économisé", data.saved_percent) : "";
    const dropped = data.dropped_turns ? t(" · {0} ancien{1} en mémoire condensée", data.dropped_turns, data.dropped_turns > 1 ? "s" : "") : "";
    addCompletedActivity("context", t("Contexte optimisé"), t("{0} tour{1} actif{2}{3}{4}", data.kept_turns, data.kept_turns > 1 ? "s" : "", data.kept_turns > 1 ? "s" : "", saving, dropped));
  }
  if (data.type === "usage") {
    addCompletedActivity("cost", t("Utilisation du modèle"), t("{0} · {1} tokens entrée · {2} sortie · {3}", data.model, data.input_tokens, data.output_tokens, formatUsd(data.cost_usd)));
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
  if (data.type === "cleared") { activeArchiveId = data.conversation || ""; setBusy(false, t("Conversation effacée")); }
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
    setStatus(t("{0} modèles OVHcloud chargés", modelCatalog.filter((item) => item.id !== "auto-balanced").length));
  }
  if (data.type === "model_selected") {
    selectedModel = data.model;
    if (selectedModel !== "auto-balanced") lastRoutedModel = "";
    updateModelButton();
  }
  if (data.type === "archive_loaded") {
    activeArchiveId = data.id || activeArchiveId;
    renderArchiveMessages(data.messages);
    setBusy(false, data.has_context ? t("Conversation restaurée avec son contexte") : t("Archive restaurée"));
  }
  if (data.type === "snapshot_loaded") {
    activeArchiveId = data.id || activeArchiveId;
    if (data.resumable) resumableArchives.add(activeArchiveId);
    applySnapshot(data);
    setBusy(false, t("Conversation restaurée"));
  }
  if (data.type === "diagnostic") status.title = data.text;
});

updateAccessButton();
updateModelButton();
stop.hidden = true;
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
window.setInterval(refreshLiveBalance, 60000);
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
