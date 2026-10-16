"use strict";
// All restored/server HTML passes this boundary before entering the live DOM.
DOMPurify.addHook("afterSanitizeAttributes", node => {
  if (node.nodeName === "IMG") {
    const src = node.getAttribute("src") || "";
    if (!/^data:image\/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+$/.test(src)) node.removeAttribute("src");
  }
  if (node.nodeName === "A") {
    const href = node.getAttribute("href") || "";
    if (/^[a-z][a-z0-9+.-]*:/i.test(href) && !/^(?:https?:|mailto:|file:)/i.test(href)) node.removeAttribute("href");
    if (href.startsWith("//")) node.removeAttribute("href");
    node.setAttribute("rel", "noopener noreferrer");
  }
});
window.safeHtml = value => DOMPurify.sanitize(typeof value === "string" ? value : "", {
  ALLOWED_TAGS: ["time","section","article","div","span","p","br","hr","h1","h2","h3","h4","h5","h6",
    "strong","b","em","i","s","del","blockquote","ul","ol","li","pre","code","a","img",
    "table","thead","tbody","tfoot","tr","th","td","details","summary","button",
    "svg","path","rect","circle","line","polyline","polygon","ellipse"],
  ALLOWED_ATTR: ["datetime","class","title","href","src","alt","rel","target","align","open","type","disabled",
    "data-diff","viewBox","d","x","y","width","height","rx","ry","cx","cy","r","x1","x2","y1","y2",
    "points","fill","fill-rule","stroke","stroke-width","stroke-linecap","stroke-linejoin"],
  FORBID_ATTR: ["id","name","style","nonce","srcset","formaction"],
  ALLOW_DATA_ATTR: false,
  ALLOW_ARIA_ATTR: true,
  SANITIZE_DOM: true,
});
