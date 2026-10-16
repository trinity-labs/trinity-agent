const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const commandTranslations = {
  fr: ['Démarrer', 'Configurer la clé OVH', 'Configurer le solde et la facturation'],
  en: ['Start', 'Configure OVH key', 'Configure balance and billing'],
  de: ['Starten', 'OVH-Schlüssel konfigurieren', 'Guthaben und Abrechnung konfigurieren'],
  es: ['Iniciar', 'Configurar la clave OVH', 'Configurar saldo y facturación'],
};
// Branding assets are administrator-supplied local files, never remote URLs.
function customIcon(variable, media) {
  const input = String(process.env[variable] || '').trim();
  if (!input) return null;
  try {
    if (!path.isAbsolute(input)) throw new Error('absolute local path required');
    const stat = fs.statSync(input);
    if (!stat.isFile() || stat.size > 1024 * 1024) throw new Error('image must be a file smaller than 1 MiB');
    let content = fs.readFileSync(input);
    let extension;
    if (content.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) {
      if (variable === 'OVH_AGENT_MENU_ICON') throw new Error('menu icon requires SVG');
      extension = 'png';
    } else {
      const svg = content.toString('utf8');
      if (!/<svg[\s>]/i.test(svg) || /<!DOCTYPE|<!ENTITY/i.test(svg)) throw new Error('expected SVG or PNG');
      const sanitize = require(require.resolve('sanitize-html', { paths: [path.dirname(media)] }));
      const clean = sanitize(svg, {
        allowedTags: ['svg', 'g', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'defs', 'linearGradient', 'radialGradient', 'stop', 'clipPath', 'title', 'desc'],
        allowedAttributes: { '*': ['xmlns', 'viewBox', 'width', 'height', 'x', 'y', 'x1', 'x2', 'y1', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'd', 'points', 'fill', 'fill-rule', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-opacity', 'opacity', 'transform', 'id', 'offset', 'stop-color', 'stop-opacity', 'gradientUnits', 'gradientTransform', 'clip-path'] },
        parser: { lowerCaseTags: false, lowerCaseAttributeNames: false },
        transformTags: { '*': (tagName, attribs) => {
          for (const [key, value] of Object.entries(attribs)) {
            if (/url\s*\(/i.test(value) && !/^url\(#[A-Za-z0-9_-]+\)$/.test(value)) delete attribs[key];
          }
          return { tagName, attribs };
        } },
      }).trim();
      if (!clean.startsWith('<svg') || !clean.endsWith('</svg>')) throw new Error('invalid SVG root');
      content = Buffer.from(clean);
      extension = 'svg';
    }
    const digest = crypto.createHash('sha256').update(content).digest('hex').slice(0, 20);
    const filename = `custom-${variable === 'OVH_AGENT_MENU_ICON' ? 'menu' : 'app'}-${digest}.${extension}`;
    fs.writeFileSync(path.join(media, filename), content);
    return 'media/' + filename;
  } catch (error) {
    console.warn(`[branding] ${variable}: ${error.code || error.message}; using default icon.`);
    return null;
  }
}
function applyManifest(file, name, language = process.env.OVH_AGENT_LANG || 'auto') {
  const manifest = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (manifest.name !== 'ovh-agent' || manifest.publisher !== 'trinity') return;
  manifest.displayName = name;
  if (manifest.__metadata) manifest.__metadata.publisherDisplayName = 'TRINITY';
  const configuredColor = String(process.env.OVH_AGENT_PRIMARY_COLOR || "").trim();
  const primary = /^#[0-9a-f]{6}$/i.test(configuredColor) ? configuredColor.toLowerCase() : "#3e3aab";
  const media = path.join(path.dirname(file), "media");
  const iconTemplate = path.join(media, "ovh-agent.svg");
  if (fs.existsSync(path.join(media, "trinity-icon.png"))) {
    manifest.icon = "media/trinity-icon.png";
  } else if (fs.existsSync(iconTemplate)) {
    const svg = fs.readFileSync(iconTemplate, "utf8").replace(/(<rect\b[^>]*\bfill=")[^"]*(")/, (_, start, end) => start + primary + end);
    // A color-specific filename prevents VS Code from reusing the old cached icon.
    const iconName = "ovh-agent-brand-" + primary.slice(1) + ".svg";
    fs.writeFileSync(path.join(media, iconName), svg);
    manifest.icon = "media/" + iconName;
  }
  const appIcon = customIcon('OVH_AGENT_APP_ICON', media);
  if (appIcon) manifest.icon = appIcon;
  const menuIcon = customIcon('OVH_AGENT_MENU_ICON', media) || (fs.existsSync(path.join(media, 'trinity-activity.png')) ? 'media/trinity-activity.png' : 'media/ovh-activity.svg');
  const locale = language.toLowerCase().split(/[-_]/)[0];
  const forced = commandTranslations[locale] ? locale : null;
  const ids = ['ovhAgent.start', 'ovhAgent.configureKey', 'ovhAgent.configureBilling'];
  for (const command of manifest.contributes?.commands || []) {
    command.category = name;
    if (ids.includes(command.command)) command.title = `%${command.command}.title%`;
  }
  for (const lang of Object.keys(commandTranslations)) {
    const labels = commandTranslations[forced || lang];
    const catalog = Object.fromEntries(ids.map((id, i) => [id + '.title', `${name}: ${labels[i]}`]));
    fs.writeFileSync(path.join(path.dirname(file), `package.nls.${lang}.json`), JSON.stringify(catalog, null, 2) + '\n');
    if (lang === 'en') fs.writeFileSync(path.join(path.dirname(file), 'package.nls.json'), JSON.stringify(catalog, null, 2) + '\n');
  }
  for (const view of manifest.contributes?.viewsContainers?.activitybar || []) { view.title = name; view.icon = menuIcon; }
  for (const view of manifest.contributes?.views?.ovhAgent || []) view.name = name;
  fs.writeFileSync(file, JSON.stringify(manifest, null, 2) + '\n');
}
if (require.main === module) {
  const name = (process.env.OVH_AGENT_NAME || 'OVH Agent').replace(/[\x00-\x1f\x7f]/g, '').trim().slice(0, 80) || 'OVH Agent';
  const extensions = '/root/.local/share/code-server/extensions';
  const registry = JSON.parse(fs.readFileSync(path.join(extensions, 'extensions.json'), 'utf8'));
  for (const item of registry) {
    if (item.identifier?.id === 'trinity.ovh-agent') {
      item.metadata = { ...item.metadata, publisherDisplayName: 'TRINITY' };
      applyManifest(path.join(extensions, path.basename(item.relativeLocation), 'package.json'), name);
    }
  }
  fs.writeFileSync(path.join(extensions, 'extensions.json'), JSON.stringify(registry));
  applyManifest('/home/coder/TRINITY-DOCKER/var/docker/docker_ide/ovh-agent-extension/extension/package.json', name);
}
module.exports = { applyManifest };
