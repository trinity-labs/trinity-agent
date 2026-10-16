const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {applyManifest} = require('../../../ovh-branding.cjs');
test('custom icons, safe SVG, cache refresh and default reset', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'branding-'));
  const saved = {...process.env};
  try {
    fs.mkdirSync(path.join(root, 'media'));
    fs.symlinkSync(path.resolve(__dirname, '../node_modules'), path.join(root, 'node_modules'));
    fs.copyFileSync(path.resolve(__dirname, '../media/ovh-agent.svg'), path.join(root, 'media/ovh-agent.svg'));
    const manifest = path.join(root, 'package.json');
    fs.copyFileSync(path.resolve(__dirname, '../package.json'), manifest);
    const svg = path.join(root, 'logo.svg');
    fs.writeFileSync(svg, '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><script>alert(1)</script><path onclick="alert(1)" d="M0 0h24v24z" fill="red"/><image href="https://example.org/x"/></svg>');
    process.env.OVH_AGENT_APP_ICON = svg;
    process.env.OVH_AGENT_MENU_ICON = svg;
    applyManifest(manifest, 'Test');
    let pkg = JSON.parse(fs.readFileSync(manifest));
    const first = pkg.icon;
    assert.match(first, /^media\/custom-app-/);
    assert.match(pkg.contributes.viewsContainers.activitybar[0].icon, /^media\/custom-menu-/);
    const output = fs.readFileSync(path.join(root, first), 'utf8');
    assert.ok(!/script|onclick|https:|<image/.test(output));
    assert.match(output, /viewBox/);
    fs.writeFileSync(svg, '<svg><circle r="10"/></svg>');
    applyManifest(manifest, 'Test');
    assert.notEqual(JSON.parse(fs.readFileSync(manifest)).icon, first);
    process.env.OVH_AGENT_APP_ICON = '';
    process.env.OVH_AGENT_MENU_ICON = '';
    applyManifest(manifest, 'Test');
    pkg = JSON.parse(fs.readFileSync(manifest));
    assert.match(pkg.icon, /^media\/ovh-agent-brand-/);
    assert.equal(pkg.contributes.viewsContainers.activitybar[0].icon, 'media/ovh-activity.svg');
    process.env.OVH_AGENT_APP_ICON = '/nonexistent/icon.png';
    applyManifest(manifest, 'Test');
    assert.equal(JSON.parse(fs.readFileSync(manifest)).icon, pkg.icon);
  } finally {
    for (const key of ['OVH_AGENT_APP_ICON', 'OVH_AGENT_MENU_ICON']) {
      if (saved[key] === undefined) delete process.env[key]; else process.env[key] = saved[key];
    }
    fs.rmSync(root, {recursive:true, force:true});
  }
});
