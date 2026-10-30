"use strict";
const MarkdownIt = require("markdown-it");
const sanitizeHtml = require("sanitize-html");
const {escapeHtml: escapeText} = require("./security");
// Rendering is pure for a fixed sanitizer policy. Keep the security boundary here.
const markdown = new MarkdownIt({ html: false, linkify: true, typographer: true, breaks: true });

function renderMarkdown(source) {
  const text = String(source || "");
  const limit = 512 * 1024;
  if (text.length > limit) return '<p>Affichage abrégé : réponse complète conservée dans l’archive.</p><pre>' + escapeText(text.slice(0, limit)) + '</pre>';
  const rendered = markdown.render(text);
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

// A one-entry cache avoids re-parsing an identical final response. It is scoped
// to one provider, bounded by source AND HTML size, and never persisted to disk.
function createMarkdownRenderer(render = renderMarkdown) {
  let previous, html;
  function cached(source) {
    const text = String(source || "");
    if (text === previous) return html;
    const result = render(text);
    if (text.length <= 128 * 1024 && result.length <= 512 * 1024) {
      previous = text;
      html = result;
    } else {
      previous = undefined;
      html = undefined;
    }
    return result;
  }
  cached.clear = () => { previous = undefined; html = undefined; };
  return cached;
}
module.exports = {renderMarkdown, createMarkdownRenderer};
