const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {applyManifest, applyExtensionListStyle} = require('../../branding/trinity-branding.cjs');
const {buildChatHtml} = require('../webview-html');
test('existing browsers get a new stylesheet URL even when the branding CSS is already current', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'branding-cache-'));
  try {
    const css = path.join(root, 'workbench.css');
    const html = path.join(root, 'workbench.html');
    fs.writeFileSync(css, '.existing { color: red; }\n');
    applyExtensionListStyle(css);
    const initialCss = fs.readFileSync(css, 'utf8');
    const template = '<link rel="stylesheet" href="{{WORKBENCH_WEB_BASE_URL}}/out/vs/code/browser/workbench/workbench.css?existing=keep">';
    fs.writeFileSync(html, template);
    assert.equal(applyExtensionListStyle(css), true);
    assert.equal(fs.readFileSync(css, 'utf8'), initialCss);
    const first = fs.readFileSync(html, 'utf8');
    assert.match(first, /workbench\.css\?existing=keep&trinity-branding=[a-f0-9]{16}/);
    assert.equal(fs.readFileSync(html + '.trinity.bak', 'utf8'), template);
    assert.equal(applyExtensionListStyle(css), false);
    assert.equal(fs.readFileSync(html, 'utf8'), first);
    fs.writeFileSync(css, initialCss.replace('color: red', 'color: blue'));
    assert.equal(applyExtensionListStyle(css), true);
    assert.notEqual(fs.readFileSync(html, 'utf8'), first);
    assert.equal((fs.readFileSync(html, 'utf8').match(/trinity-branding=/g) || []).length, 1);
    assert.equal(fs.readFileSync(html + '.trinity.bak', 'utf8'), template);
  } finally {
    fs.rmSync(root, {recursive:true, force:true});
  }
});
test('packaged local icon matches the theme before installation; Marketplace stays purple', () => {
  const filename = path.resolve(__dirname, '../scripts/package-vsix.cjs');
  const originalRequire = require('node:module').createRequire(filename);
  const code = fs.readFileSync(filename, 'utf8');
  const savedTheme = process.env.TRINITY_AGENT_ICON_THEME;
  const stopped = new Error('Stage inspected before npm installation');
  try {
    for (const [publisher, theme, expected] of [
      ['trinity', 'dark', 'media/trinity-activity.png'],
      ['trinity', 'light', 'media/trinity-icon.png'],
      ['stopyz', 'dark', 'media/trinity-icon.png'],
    ]) {
      process.env.TRINITY_AGENT_ICON_THEME = theme;
      let inspected = false;
      const wrappedRequire = name => name === 'node:child_process' ? {
        execFileSync(command, args, options) {
          if (command !== 'npm') return; // Frontend compilation does not select icons.
          const stage = options.cwd;
          const manifest = JSON.parse(fs.readFileSync(path.join(stage, 'package.json')));
          assert.equal(manifest.publisher, publisher);
          assert.equal(manifest.icon, expected);
          assert.deepEqual(fs.readFileSync(path.join(stage, manifest.icon)),
            fs.readFileSync(path.resolve(__dirname, '..', expected)));
          assert.equal(manifest.trinityAgentBranding.darkThemeIcon, 'media/trinity-activity.png');
          assert.equal(manifest.contributes.viewsContainers.activitybar[0].icon, 'media/trinity-menu.svg');
          const start = manifest.contributes.commands.find(item => item.command === 'trinityAgent.start');
          assert.deepEqual(start.icon, {light:'media/trinity-title-black.png', dark:'media/trinity-header-white.png'});
          assert.deepEqual(manifest.contributes.menus['editor/title'], [
            {command:'trinityAgent.start', group:'navigation'},
          ]);
          inspected = true;
          throw stopped;
        },
      } : originalRequire(name);
      wrappedRequire.resolve = originalRequire.resolve;
      assert.throws(() => require('node:vm').runInNewContext(code, {
        require: wrappedRequire, __dirname: path.dirname(filename), console,
        process: {execPath: process.execPath, env: {MARKETPLACE_PUBLISHER: publisher}},
      }, {filename}), error => error === stopped);
      assert.equal(inspected, true);
    }
  } finally {
    if (savedTheme === undefined) delete process.env.TRINITY_AGENT_ICON_THEME;
    else process.env.TRINITY_AGENT_ICON_THEME = savedTheme;
  }
});

test('custom icons, safe SVG, cache refresh and default reset', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'branding-'));
  const saved = {...process.env};
  try {
    fs.mkdirSync(path.join(root, 'media'));
    fs.symlinkSync(path.resolve(__dirname, '../node_modules'), path.join(root, 'node_modules'));
    fs.copyFileSync(path.resolve(__dirname, '../media/trinity-agent.svg'), path.join(root, 'media/trinity-agent.svg'));
    fs.copyFileSync(path.resolve(__dirname, '../media/trinity-activity-legacy.svg'), path.join(root, 'media/trinity-activity-legacy.svg'));
    const manifest = path.join(root, 'package.json');
    fs.copyFileSync(path.resolve(__dirname, '../package.json'), manifest);
    const svg = path.join(root, 'logo.svg');
    fs.writeFileSync(svg, '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><script>alert(1)</script><path onclick="alert(1)" d="M0 0h24v24z" fill="red"/><image href="https://example.org/x"/></svg>');
    process.env.TRINITY_AGENT_APP_ICON = svg;
    process.env.TRINITY_AGENT_MENU_ICON = svg;
    process.env.TRINITY_AGENT_DARK_THEME_ICON = svg;
    process.env.TRINITY_AGENT_ICON_THEME = 'dark';
    applyManifest(manifest, 'Test');
    let pkg = JSON.parse(fs.readFileSync(manifest));
    const first = pkg.icon;
    assert.match(first, /^media\/theme-app-dark-/);
    assert.match(pkg.contributes.viewsContainers.activitybar[0].icon, /^media\/custom-menu-/);
    assert.match(pkg.trinityAgentBranding.lightThemeIcon, /^media\/custom-app-/);
    assert.match(pkg.trinityAgentBranding.darkThemeIcon, /^media\/custom-theme-dark-/);
    const output = fs.readFileSync(path.join(root, pkg.trinityAgentBranding.lightThemeIcon), 'utf8');
    assert.ok(!/script|onclick|https:|<image/.test(output));
    assert.match(output, /viewBox/);
    assert.match(fs.readFileSync(path.join(root, first), 'utf8'), /viewBox/);
    fs.writeFileSync(svg, '<svg><circle r="10"/></svg>');
    applyManifest(manifest, 'Test');
    pkg = JSON.parse(fs.readFileSync(manifest));
    assert.notEqual(pkg.icon, first);
    process.env.TRINITY_AGENT_APP_ICON = '';
    process.env.TRINITY_AGENT_MENU_ICON = '';
    process.env.TRINITY_AGENT_DARK_THEME_ICON = '';
    applyManifest(manifest, 'Test');
    pkg = JSON.parse(fs.readFileSync(manifest));
    assert.match(pkg.icon, /^media\/theme-app-dark-/);
    assert.match(pkg.trinityAgentBranding.lightThemeIcon, /^media\/trinity-agent-brand-/);
    assert.match(pkg.contributes.viewsContainers.activitybar[0].icon, /^media\/theme-menu-dark-/);
    assert.equal(pkg.trinityAgentBranding.darkThemeIcon, 'media/trinity-activity-legacy.svg');
    process.env.TRINITY_AGENT_APP_ICON = '/nonexistent/icon.png';
    applyManifest(manifest, 'Test');
    assert.equal(JSON.parse(fs.readFileSync(manifest)).icon, pkg.icon);
  } finally {
    for (const key of ['TRINITY_AGENT_APP_ICON', 'TRINITY_AGENT_MENU_ICON', 'TRINITY_AGENT_DARK_THEME_ICON', 'TRINITY_AGENT_ICON_THEME']) {
      if (saved[key] === undefined) delete process.env[key]; else process.env[key] = saved[key];
    }
    fs.rmSync(root, {recursive:true, force:true});
  }
});

test('missing overrides restore bundled branding and preserve localization keys', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'branding-preserve-'));
  const saved = {...process.env};
  try {
    for (const key of ['TRINITY_AGENT_APP_ICON', 'TRINITY_AGENT_MENU_ICON', 'TRINITY_AGENT_DARK_THEME_ICON', 'TRINITY_AGENT_PUBLISHER_NAME']) delete process.env[key];
    process.env.TRINITY_AGENT_ICON_THEME = 'dark';
    fs.mkdirSync(path.join(root, 'media'));
    fs.copyFileSync(path.resolve(__dirname, '../media/trinity-agent.svg'), path.join(root, 'media/trinity-agent.svg'));
    fs.copyFileSync(path.resolve(__dirname, '../media/trinity-activity-legacy.svg'), path.join(root, 'media/trinity-activity-legacy.svg'));
    fs.copyFileSync(path.resolve(__dirname, '../media/trinity-icon.png'), path.join(root, 'media/trinity-icon.png'));
    fs.copyFileSync(path.resolve(__dirname, '../media/trinity-activity.png'), path.join(root, 'media/trinity-activity.png'));
    fs.copyFileSync(path.resolve(__dirname, '../media/trinity-menu.png'), path.join(root, 'media/trinity-menu.png'));
    const pkg = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../package.json')));
    pkg.icon = 'media/customer.svg';
    pkg.__metadata = {publisherDisplayName: 'Customer'};
    pkg.contributes.viewsContainers.activitybar[0].icon = 'media/customer-menu.svg';
    const file = path.join(root, 'package.json');
    fs.writeFileSync(file, JSON.stringify(pkg));
    fs.writeFileSync(path.join(root, 'package.nls.fr.json'), JSON.stringify({'trinityAgent.performanceMode.auto': 'Auto'}));
    applyManifest(file, 'Customer Agent');
    const result = JSON.parse(fs.readFileSync(file));
    assert.equal(result.displayName, 'Customer Agent');
    assert.match(result.icon, /^media\/theme-app-dark-/);
    assert.equal(result.trinityAgentBranding.lightThemeIcon, 'media/trinity-icon.png');
    assert.equal(result.__metadata.publisherDisplayName, 'TRINITY');
    assert.match(result.contributes.viewsContainers.activitybar[0].icon, /^media\/theme-menu-dark-/);
    assert.deepEqual(
      fs.readFileSync(path.join(root, result.contributes.viewsContainers.activitybar[0].icon)),
      fs.readFileSync(path.resolve(__dirname, '../media/trinity-menu.png')),
    );
    assert.equal(result.trinityAgentBranding.darkThemeIcon, 'media/trinity-activity.png');
    assert.equal(JSON.parse(fs.readFileSync(path.join(root, 'package.nls.fr.json')))['trinityAgent.performanceMode.auto'], 'Auto');
    process.env.TRINITY_AGENT_APP_ICON = '/missing/logo.svg';
    process.env.TRINITY_AGENT_BRANDING_STRICT = 'true';
    assert.throws(() => applyManifest(file, 'Customer Agent'));
  } finally {
    for (const key of ['TRINITY_AGENT_APP_ICON', 'TRINITY_AGENT_MENU_ICON', 'TRINITY_AGENT_DARK_THEME_ICON', 'TRINITY_AGENT_PUBLISHER_NAME', 'TRINITY_AGENT_BRANDING_STRICT', 'TRINITY_AGENT_ICON_THEME']) {
      if (saved[key] === undefined) delete process.env[key]; else process.env[key] = saved[key];
    }
    fs.rmSync(root, {recursive:true, force:true});
  }
});

test('webview header uses fully opaque dedicated light and dark logos', () => {
  const root = path.resolve(__dirname, '..');
  const extensionUri = {fsPath: root};
  const joinPath = (base, ...parts) => ({fsPath: path.join(base.fsPath, ...parts)});
  const webview = {cspSource: 'webview-resource:', asWebviewUri: uri => `webview:${uri.fsPath}`};
  const html = buildChatHtml({extensionUri, brand:'TRINITY Agent', performanceMode:'max', env:{}, joinPath}, webview);
  assert.match(html, /header-app-logo header-app-logo-light[^>]+trinity-header-primary\.png/);
  assert.match(html, /header-app-logo header-app-logo-dark[^>]+trinity-header-white\.png/);
  const css = fs.readFileSync(path.join(root, 'media/chat.css'), 'utf8');
  assert.doesNotMatch(css, /\.history-brand\s*\{[^}]*opacity\s*:/);
  assert.equal(JSON.parse(fs.readFileSync(path.join(root, 'package.json')))
    .contributes.viewsContainers.activitybar[0].icon, 'media/trinity-menu.svg');
  const menu = fs.readFileSync(path.join(root, 'media/trinity-menu.svg'), 'utf8');
  assert.match(menu, /^<svg[^>]+viewBox="0 0 1254 1254"/);
  assert.match(menu, /prefers-color-scheme:dark/);
  assert.equal((menu.match(/href="data:image\/png;base64,/g) || []).length, 2);
  assert.match(menu, /class="light"/);
  assert.match(menu, /class="dark"/);
});

test('mount point setting controls both the archive path and its storage usage', () => {
  const root = path.resolve(__dirname, '..');
  const extensionUri = {fsPath: root};
  const joinPath = (base, ...parts) => ({fsPath: path.join(base.fsPath, ...parts)});
  const webview = {cspSource: 'webview-resource:', asWebviewUri: uri => `webview:${uri.fsPath}`};
  const render = value => buildChatHtml({extensionUri, brand:'TRINITY Agent', performanceMode:'max',
    env:{TRINITY_AGENT_SHOW_MOUNT_POINT:value}, joinPath}, webview);
  const shown = render('true');
  assert.match(shown, /id="archive-path"/);
  assert.match(shown, /id="archive-used"/);
  const hidden = render('false');
  assert.match(hidden, /id="archive-banner"/);
  assert.match(hidden, /class="archive-title">Conversations/);
  assert.doesNotMatch(hidden, /id="archive-path"/);
  assert.doesNotMatch(hidden, /id="archive-mount-separator"/);
  assert.doesNotMatch(hidden, /id="archive-used"/);
});

test('popup icons use the subdued shared opacity', () => {
  const css = fs.readFileSync(path.resolve(__dirname, '../media/chat.css'), 'utf8');
  assert.match(css, /dialog\s+\.agent-icon\s*\{\s*opacity:\s*\.33;\s*\}/);
  assert.doesNotMatch(css, /\.delete-title-icon\s*\{[^}]*opacity\s*:/);
});
