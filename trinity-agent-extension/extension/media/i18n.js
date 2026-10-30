(function (root) {
  "use strict";
  const catalog = typeof module === "object" && module.exports
    ? require("./translations.json") : root.OVH_TRANSLATIONS;
  const supported = ["fr", "en", "de", "es"];
  const normalize = value => String(value || "").trim().toLowerCase().split(/[-_]/)[0];
  function resolveLanguage(override, preferences = []) {
    const forced = normalize(override);
    if (supported.includes(forced)) return forced;
    for (const preference of preferences) {
      const lang = normalize(preference);
      if (supported.includes(lang)) return lang;
    }
    return "en";
  }
  let language = "fr";
  function setLanguage(value) { language = resolveLanguage(value); return language; }
  function t(key, ...args) {
    const template = catalog[key]?.[language] ?? key;
    return template.replace(/\{(\d+)\}/g, (match, index) => index < args.length ? String(args[index]) : match);
  }
  // Translate only recognized application messages, never arbitrary conversation text.
  const patterns = Object.keys(catalog).filter(key => /\{\d+\}/.test(key)).map(key => {
    const indices = [];
    const pieces = key.split(/(\{\d+\})/).map(piece => {
      if (/^\{\d+\}$/.test(piece)) { indices.push(Number(piece.slice(1, -1))); return "(.*?)"; }
      return piece.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    });
    return {key, indices, regex: new RegExp("^" + pieces.join("") + "$", "s")};
  });
  function message(text) {
    if (typeof text !== "string") return text;
    if (catalog[text]) return t(text);
    for (const item of patterns) {
      const match = item.regex.exec(text);
      if (!match) continue;
      const args = [];
      item.indices.forEach((index, position) => { args[index] = match[position + 1]; });
      return t(item.key, ...args);
    }
    return text;
  }
  const snapshotKeys = new Map();
  for (const key of Object.keys(catalog)) for (const value of Object.values(catalog[key])) {
    if (!snapshotKeys.has(value)) snapshotKeys.set(value, key);
  }
  function translateSnapshot(node) {
    // Only application-owned labels; never rewrite user/model prose or code.
    const selectors = ".label,.activity-summary-text,.activity-row strong,.approval-card > strong,.approval-actions button,.file-change summary strong,.welcome p,.execution-evidence";
    for (const element of node.querySelectorAll(selectors)) {
      if (element.children.length) continue;
      const value = element.textContent;
      const key = snapshotKeys.get(value);
      if (key) element.textContent = t(key);
      else element.textContent = message(value);
    }
  }
  function translateStatic(node) {
    const doc = node.ownerDocument || node;
    const walker = doc.createTreeWalker(node, 4);
    let text;
    while ((text = walker.nextNode())) {
      if (text.parentElement?.closest("script,style,code,pre")) continue;
      const value = text.textContent.trim();
      if (catalog[value]) text.textContent = text.textContent.replace(value, t(value));
    }
    for (const element of node.querySelectorAll("[title],[aria-label],[placeholder]")) {
      for (const attribute of ["title","aria-label","placeholder"]) {
        const value = element.getAttribute(attribute);
        if (value && catalog[value]) element.setAttribute(attribute, t(value));
      }
    }
  }
  function markup(html) {
    const template = document.createElement("template");
    template.innerHTML = html;
    translateStatic(template.content);
    return template.innerHTML;
  }
  const api = {t, message, markup, translateStatic, translateSnapshot, resolveLanguage, setLanguage, get language() {return language;}};
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.OvhI18n = api;
})(typeof window !== "undefined" ? window : globalThis);
