"use strict";
const fs = require("fs");
const path = require("path");
const { fileURLToPath } = require("url");
const text = (value, max) => typeof value === "string" && value.length <= max;
const id = value => text(value, 128) && /^[A-Za-z0-9_-]+$/.test(value);
const model = value => value === undefined || (text(value, 256) && /^[A-Za-z0-9_.-]+$/.test(value));
const boolean = value => value === undefined || typeof value === "boolean";
function validMessage(message) {
  if (!message || typeof message !== "object" || Array.isArray(message)) return false;
  switch (message.type) {
    case "prompt":
      return text(message.text, 1024 * 1024) && boolean(message.fullAccess) && model(message.model)
        && (message.images === undefined || (Array.isArray(message.images) && message.images.length <= 5
          && message.images.every(image => image && text(image.name || "", 1024)
            && text(image.data_url, 6 * 1024 * 1024)
            && /^data:image\/(?:jpeg|png|webp|gif);base64,[A-Za-z0-9+/=]+$/.test(image.data_url))));
    case "followup":
      return id(message.conversation) && ["add", "edit", "remove", "steer", "list", "continue"].includes(message.action)
        && (["list", "continue"].includes(message.action) || id(message.id))
        && (!["add", "edit"].includes(message.action) || (text(message.text, 16000) && !!message.text.trim()))
        && (message.action !== "add" || ["queue", "steer"].includes(message.mode))
        && boolean(message.fullAccess) && model(message.model);
    case "snapshot":
      return id(message.conversation) && text(message.html, 8 * 1024 * 1024)
        && boolean(message.fullAccess) && model(message.model);
    case "loadArchive": case "resumeArchive": case "deleteArchiveConfirmed": return id(message.id);
    case "approval": return id(message.id) && typeof message.allow === "boolean";
    case "openLink": return text(message.href, 8192);
    case "selectModel": return typeof message.model === "string" && model(message.model);
    case "webviewReady": return message.language === undefined || text(message.language, 32);
    case "listModels": return boolean(message.refresh);
    case "clear": case "cancel": case "configureKey": case "billing":
    case "billingRefresh": case "listArchives": case "retryWindow": return true;
    default: return false;
  }
}
function localLink(value, workspace) {
  if (typeof value !== "string" || value.length > 8192 || /[\x00-\x1f\x7f]/.test(value)) return null;
  try {
    if (/^[a-z][a-z0-9+.-]*:/i.test(value) && !value.startsWith("file:")) return null;
    const root = fs.realpathSync(workspace);
    const candidate = fs.realpathSync(value.startsWith("file:") ? fileURLToPath(value) : path.resolve(root, value));
    const relative = path.relative(root, candidate);
    if (relative === ".." || relative.startsWith(".." + path.sep) || path.isAbsolute(relative)) return null;
    return fs.statSync(candidate).isFile() ? candidate : null;
  } catch { return null; }
}
module.exports = { validMessage, localLink };
