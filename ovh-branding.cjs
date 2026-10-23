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
    const role = variable === 'OVH_AGENT_MENU_ICON' ? 'menu' :
      variable === 'OVH_AGENT_DARK_THEME_ICON' ? 'theme-dark' : 'app';
    const filename = `custom-${role}-${digest}.${extension}`;
    fs.writeFileSync(path.join(media, filename), content);
    return 'media/' + filename;
  } catch (error) {
    if (process.env.OVH_AGENT_BRANDING_STRICT === 'true') throw error;
    console.warn(`[branding] ${variable}: ${error.code || error.message}; preserving current icon.`);
    return null;
  }
}
function themedIcon(lightAsset, darkAsset, media, role) {
  if (lightAsset === darkAsset) return lightAsset;
  const readAsset = asset => {
    const match = /^media\/([a-z0-9_.-]+\.(png|svg|jpg|jpeg|webp))$/i.exec(String(asset));
    if (!match) throw new Error('invalid local theme icon');
    const content = fs.readFileSync(path.join(media, match[1]));
    const mime = {png:'image/png',svg:'image/svg+xml',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp'}[match[2].toLowerCase()];
    return `data:${mime};base64,${content.toString('base64')}`;
  };
  const light = readAsset(lightAsset);
  const dark = readAsset(darkAsset);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><style>.dark{display:none}@media(prefers-color-scheme:dark){.light{display:none}.dark{display:inline}}</style><image class="light" width="512" height="512" preserveAspectRatio="xMidYMid meet" href="${light}"/><image class="dark" width="512" height="512" preserveAspectRatio="xMidYMid meet" href="${dark}"/></svg>`;
  const digest = crypto.createHash('sha256').update(svg).digest('hex').slice(0, 20);
  const filename = `theme-${role}-${digest}.svg`;
  fs.writeFileSync(path.join(media, filename), svg);
  return 'media/' + filename;
}
function configuredDarkTheme() {
  const requested = String(process.env.OVH_AGENT_ICON_THEME || 'auto').trim().toLowerCase();
  if (requested === 'dark') return true;
  if (requested === 'light') return false;
  const files = ['/home/coder/.vscode/settings.json', path.join(require('os').homedir(), '.local/share/code-server/User/settings.json')];
  for (const file of files) {
    try {
      const value = JSON.parse(fs.readFileSync(file, 'utf8'))['workbench.colorTheme'];
      if (typeof value === 'string') return /dark/i.test(value);
    } catch {}
  }
  return false;
}
function staticThemeIcon(asset, media, role, dark) {
  const match = /^media\/([a-z0-9_.-]+\.([a-z0-9]+))$/i.exec(String(asset));
  if (!match) throw new Error('invalid local theme icon');
  const content = fs.readFileSync(path.join(media, match[1]));
  const digest = crypto.createHash('sha256').update(content).digest('hex').slice(0, 20);
  const filename = `theme-${role}-${dark ? 'dark' : 'light'}-${digest}.${match[2]}`;
  fs.writeFileSync(path.join(media, filename), content);
  return 'media/' + filename;
}
function applyManifest(file, name, language = process.env.OVH_AGENT_LANG || 'auto') {
  const manifest = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (manifest.name !== 'ovh-agent' || manifest.publisher !== 'trinity') return;
  manifest.displayName = name;
  if (manifest.__metadata) manifest.__metadata.publisherDisplayName = process.env.OVH_AGENT_PUBLISHER_NAME || 'TRINITY';
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
  const defaultIcon = manifest.icon;
  const appIcon = customIcon('OVH_AGENT_APP_ICON', media);
  const lightThemeIcon = appIcon || defaultIcon;
  const menuOverride = customIcon('OVH_AGENT_MENU_ICON', media);
  const menuIcon = menuOverride ||
    (fs.existsSync(path.join(media, 'trinity-activity.png')) ? 'media/trinity-activity.png' : 'media/ovh-activity.svg');
  const darkOverride = customIcon('OVH_AGENT_DARK_THEME_ICON', media);
  const darkThemeIcon = darkOverride ||
    (fs.existsSync(path.join(media, 'trinity-activity.png')) ? 'media/trinity-activity.png' : menuIcon);
  // The published VSIX keeps its purple Marketplace icon. This local branding
  // pass runs only after installation and gives the installed app its dark set.
  const darkTheme = configuredDarkTheme();
  manifest.icon = staticThemeIcon(darkTheme ? darkThemeIcon : lightThemeIcon, media, 'app', darkTheme);
  const branding = manifest.ovhAgentBranding && typeof manifest.ovhAgentBranding === 'object' && !Array.isArray(manifest.ovhAgentBranding)
    ? manifest.ovhAgentBranding : {};
  manifest.ovhAgentBranding = { ...branding, lightThemeIcon, darkThemeIcon };
  const locale = language.toLowerCase().split(/[-_]/)[0];
  const forced = commandTranslations[locale] ? locale : null;
  const ids = ['ovhAgent.start', 'ovhAgent.configureKey', 'ovhAgent.configureBilling'];
  for (const command of manifest.contributes?.commands || []) {
    command.category = name;
    if (ids.includes(command.command)) command.title = `%${command.command}.title%`;
  }
  for (const lang of Object.keys(commandTranslations)) {
    const labels = commandTranslations[forced || lang];
    const updates = Object.fromEntries(ids.map((id, i) => [id + '.title', `${name}: ${labels[i]}`]));
    for (const target of [path.join(path.dirname(file), `package.nls.${lang}.json`), ...(lang === 'en' ? [path.join(path.dirname(file), 'package.nls.json')] : [])]) {
      // Preserve every unrelated localization key; replacing the catalog makes VS Code report missing translations.
      const catalog = fs.existsSync(target) ? JSON.parse(fs.readFileSync(target, 'utf8')) : {};
      fs.writeFileSync(target, JSON.stringify({ ...catalog, ...updates }, null, 2) + '\n');
    }
  }
  const themedMenuIcon = menuOverride || staticThemeIcon(darkTheme ? darkThemeIcon : lightThemeIcon, media, 'menu', darkTheme);
  for (const view of manifest.contributes?.viewsContainers?.activitybar || []) { view.title = name; view.icon = themedMenuIcon; }
  for (const view of manifest.contributes?.views?.ovhAgent || []) view.name = name;
  fs.writeFileSync(file, JSON.stringify(manifest, null, 2) + '\n');
}
if (require.main === module) {
  const extensions = path.join(require('os').homedir(), '.local/share/code-server/extensions');
  const registryFile = path.join(extensions, 'extensions.json');
  const registry = JSON.parse(fs.readFileSync(registryFile, 'utf8'));
  for (const item of registry) {
    if (item.identifier?.id !== 'trinity.ovh-agent') continue;
    const file = path.join(extensions, path.basename(item.relativeLocation), 'package.json');
    const manifest = JSON.parse(fs.readFileSync(file, 'utf8'));
    // Unset branding variables mean bundled TRINITY defaults; only explicit values persist custom branding.
    const name = (process.env.OVH_AGENT_NAME || 'TRINITY Agent').replace(/[\x00-\x1f\x7f]/g, '').trim().slice(0, 80);
    const publisher = process.env.OVH_AGENT_PUBLISHER_NAME || 'TRINITY';
    item.metadata = { ...item.metadata, publisherDisplayName: publisher };
    applyManifest(file, name);
  }
  fs.writeFileSync(registryFile + '.branding.tmp', JSON.stringify(registry));
  fs.renameSync(registryFile + '.branding.tmp', registryFile);
}
module.exports = { applyManifest, configuredDarkTheme };
