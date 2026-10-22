const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {applyManifest} = require('../../../ovh-branding.cjs');
const {buildChatHtml} = require('../webview-html');
test('custom icons, safe SVG, cache refresh and default reset', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'branding-'));
  const saved = {...process.env};
  try {
    fs.mkdirSync(path.join(root, 'media'));
    fs.symlinkSync(path.resolve(__dirname, '../node_modules'), path.join(root, 'node_modules'));
    fs.copyFileSync(path.resolve(__dirname, '../media/ovh-agent.svg'), path.join(root, 'media/ovh-agent.svg'));
    fs.copyFileSync(path.resolve(__dirname, '../media/ovh-activity.svg'), path.join(root, 'media/ovh-activity.svg'));
    const manifest = path.join(root, 'package.json');
    fs.copyFileSync(path.resolve(__dirname, '../package.json'), manifest);
    const svg = path.join(root, 'logo.svg');
    fs.writeFileSync(svg, '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><script>alert(1)</script><path onclick="alert(1)" d="M0 0h24v24z" fill="red"/><image href="https://example.org/x"/></svg>');
    process.env.OVH_AGENT_APP_ICON = svg;
    process.env.OVH_AGENT_MENU_ICON = svg;
    process.env.OVH_AGENT_DARK_THEME_ICON = svg;
    applyManifest(manifest, 'Test');
    let pkg = JSON.parse(fs.readFileSync(manifest));
    const first = pkg.icon;
    assert.match(first, /^media\/ovh-agent-brand-/);
    assert.match(pkg.contributes.viewsContainers.activitybar[0].icon, /^media\/theme-menu-/);
    assert.match(pkg.ovhAgentBranding.lightThemeIcon, /^media\/custom-app-/);
    assert.match(pkg.ovhAgentBranding.darkThemeIcon, /^media\/custom-theme-dark-/);
    const output = fs.readFileSync(path.join(root, pkg.ovhAgentBranding.lightThemeIcon), 'utf8');
    assert.ok(!/script|onclick|https:|<image/.test(output));
    assert.match(output, /viewBox/);
    const firstLight = pkg.ovhAgentBranding.lightThemeIcon;
    fs.writeFileSync(svg, '<svg><circle r="10"/></svg>');
    applyManifest(manifest, 'Test');
    pkg = JSON.parse(fs.readFileSync(manifest));
    assert.equal(pkg.icon, first);
    assert.notEqual(pkg.ovhAgentBranding.lightThemeIcon, firstLight);
    process.env.OVH_AGENT_APP_ICON = '';
    process.env.OVH_AGENT_MENU_ICON = '';
    process.env.OVH_AGENT_DARK_THEME_ICON = '';
    applyManifest(manifest, 'Test');
    pkg = JSON.parse(fs.readFileSync(manifest));
    assert.match(pkg.icon, /^media\/ovh-agent-brand-/);
    assert.match(pkg.ovhAgentBranding.lightThemeIcon, /^media\/ovh-agent-brand-/);
    assert.equal(pkg.contributes.viewsContainers.activitybar[0].icon, 'media/ovh-activity.svg');
    assert.equal(pkg.ovhAgentBranding.darkThemeIcon, 'media/ovh-activity.svg');
    process.env.OVH_AGENT_APP_ICON = '/nonexistent/icon.png';
    applyManifest(manifest, 'Test');
    assert.equal(JSON.parse(fs.readFileSync(manifest)).icon, pkg.icon);
  } finally {
    for (const key of ['OVH_AGENT_APP_ICON', 'OVH_AGENT_MENU_ICON', 'OVH_AGENT_DARK_THEME_ICON']) {
      if (saved[key] === undefined) delete process.env[key]; else process.env[key] = saved[key];
    }
    fs.rmSync(root, {recursive:true, force:true});
  }
});

test('missing overrides restore bundled branding and preserve localization keys', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'branding-preserve-'));
  const saved = {...process.env};
  try {
    for (const key of ['OVH_AGENT_APP_ICON', 'OVH_AGENT_MENU_ICON', 'OVH_AGENT_DARK_THEME_ICON', 'OVH_AGENT_PUBLISHER_NAME']) delete process.env[key];
    fs.mkdirSync(path.join(root, 'media'));
    fs.copyFileSync(path.resolve(__dirname, '../media/ovh-agent.svg'), path.join(root, 'media/ovh-agent.svg'));
    fs.copyFileSync(path.resolve(__dirname, '../media/ovh-activity.svg'), path.join(root, 'media/ovh-activity.svg'));
    fs.copyFileSync(path.resolve(__dirname, '../media/trinity-icon.png'), path.join(root, 'media/trinity-icon.png'));
    fs.copyFileSync(path.resolve(__dirname, '../media/trinity-activity.png'), path.join(root, 'media/trinity-activity.png'));
    const pkg = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../package.json')));
    pkg.icon = 'media/customer.svg';
    pkg.__metadata = {publisherDisplayName: 'Customer'};
    pkg.contributes.viewsContainers.activitybar[0].icon = 'media/customer-menu.svg';
    const file = path.join(root, 'package.json');
    fs.writeFileSync(file, JSON.stringify(pkg));
    fs.writeFileSync(path.join(root, 'package.nls.fr.json'), JSON.stringify({'ovhAgent.performanceMode.auto': 'Auto'}));
    applyManifest(file, 'Customer Agent');
    const result = JSON.parse(fs.readFileSync(file));
    assert.equal(result.displayName, 'Customer Agent');
    assert.equal(result.icon, 'media/trinity-icon.png');
    assert.equal(result.ovhAgentBranding.lightThemeIcon, 'media/trinity-icon.png');
    assert.equal(result.__metadata.publisherDisplayName, 'TRINITY');
    assert.match(result.contributes.viewsContainers.activitybar[0].icon, /^media\/theme-menu-/);
    assert.equal(result.ovhAgentBranding.darkThemeIcon, 'media/trinity-activity.png');
    assert.equal(JSON.parse(fs.readFileSync(path.join(root, 'package.nls.fr.json')))['ovhAgent.performanceMode.auto'], 'Auto');
    process.env.OVH_AGENT_APP_ICON = '/missing/logo.svg';
    process.env.OVH_AGENT_BRANDING_STRICT = 'true';
    assert.throws(() => applyManifest(file, 'Customer Agent'));
  } finally {
    for (const key of ['OVH_AGENT_APP_ICON', 'OVH_AGENT_MENU_ICON', 'OVH_AGENT_DARK_THEME_ICON', 'OVH_AGENT_PUBLISHER_NAME', 'OVH_AGENT_BRANDING_STRICT']) {
      if (saved[key] === undefined) delete process.env[key]; else process.env[key] = saved[key];
    }
    fs.rmSync(root, {recursive:true, force:true});
  }
});

test('webview header keeps application and dark-theme logos independent', () => {
  const root = path.resolve(__dirname, '..');
  const extensionUri = {fsPath: root};
  const joinPath = (base, ...parts) => ({fsPath: path.join(base.fsPath, ...parts)});
  const webview = {cspSource: 'webview-resource:', asWebviewUri: uri => `webview:${uri.fsPath}`};
  const html = buildChatHtml({extensionUri, brand:'TRINITY Agent', performanceMode:'max', env:{}, joinPath}, webview);
  assert.match(html, /header-app-logo header-app-logo-light[^>]+trinity-icon\.png/);
  assert.match(html, /header-app-logo header-app-logo-dark[^>]+trinity-activity\.png/);
  const themed = fs.readFileSync(path.join(root, 'media/trinity-theme.svg'), 'utf8');
  assert.equal((themed.match(/data:image\/png;base64/g) || []).length, 2);
  assert.match(themed, /prefers-color-scheme:dark/);
});
