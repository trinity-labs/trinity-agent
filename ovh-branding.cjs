const fs = require('fs');
const path = require('path');
const commandTranslations = {
  fr: ['Démarrer', 'Configurer la clé OVH', 'Configurer le solde et la facturation'],
  en: ['Start', 'Configure OVH key', 'Configure balance and billing'],
  de: ['Starten', 'OVH-Schlüssel konfigurieren', 'Guthaben und Abrechnung konfigurieren'],
  es: ['Iniciar', 'Configurar la clave OVH', 'Configurar saldo y facturación'],
};
function applyManifest(file, name, language = process.env.OVH_AGENT_LANG || 'auto') {
  const manifest = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (manifest.name !== 'ovh-agent' || manifest.publisher !== 'trinity') return;
  manifest.displayName = name;
  const configuredColor = String(process.env.OVH_AGENT_PRIMARY_COLOR || "").trim();
  const primary = /^#[0-9a-f]{6}$/i.test(configuredColor) ? configuredColor.toLowerCase() : "#3e3aab";
  const media = path.join(path.dirname(file), "media");
  const iconTemplate = path.join(media, "ovh-agent.svg");
  if (fs.existsSync(iconTemplate)) {
    const svg = fs.readFileSync(iconTemplate, "utf8").replace(/(<rect\b[^>]*\bfill=")[^"]*(")/, (_, start, end) => start + primary + end);
    // A color-specific filename prevents VS Code from reusing the old cached icon.
    const iconName = "ovh-agent-brand-" + primary.slice(1) + ".svg";
    fs.writeFileSync(path.join(media, iconName), svg);
    manifest.icon = "media/" + iconName;
  }
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
  for (const view of manifest.contributes?.viewsContainers?.activitybar || []) view.title = name;
  for (const view of manifest.contributes?.views?.ovhAgent || []) view.name = name;
  fs.writeFileSync(file, JSON.stringify(manifest, null, 2) + '\n');
}
if (require.main === module) {
  const name = (process.env.OVH_AGENT_NAME || 'OVH Agent').replace(/[\x00-\x1f\x7f]/g, '').trim().slice(0, 80) || 'OVH Agent';
  const extensions = '/root/.local/share/code-server/extensions';
  const registry = JSON.parse(fs.readFileSync(path.join(extensions, 'extensions.json'), 'utf8'));
  for (const item of registry) {
    if (item.identifier?.id === 'trinity.ovh-agent') applyManifest(path.join(extensions, path.basename(item.relativeLocation), 'package.json'), name);
  }
  applyManifest('/home/coder/TRINITY-DOCKER/var/docker/docker_ide/ovh-agent-extension/extension/package.json', name);
}
module.exports = { applyManifest };
