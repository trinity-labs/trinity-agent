'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const stage = fs.mkdtempSync(path.join(os.tmpdir(), 'trinity-vsix-'));
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'package.json')));
const publisher = process.env.MARKETPLACE_PUBLISHER || 'stopyz';
const {configuredDarkTheme} = require(path.resolve(__dirname, '../../branding/trinity-branding.cjs'));
const previewAssetType = 'Microsoft.VisualStudio.Services.Content.Images.HistoryDemo';
if (!/^[a-zA-Z0-9][a-zA-Z0-9-]*$/.test(publisher)) throw Error('Invalid Marketplace publisher ID');
if (process.env.RELEASE_TAG && process.env.RELEASE_TAG !== `v${manifest.version}`) throw Error('Release tag must match package.json version');
try {
  // Explicit allowlist: never copy local backups, credentials, archives or custom branding.
  const files = ['extension.js','project-store.js','archive-store.js','window-lock.js','security.js','markdown-renderer.js','backend-protocol.js','billing-channel.js','webview-html.js','ARCHITECTURE.md','package.json','package-lock.json','README.md','CHANGELOG.md',
    'package.nls.json','package.nls.fr.json','package.nls.en.json','package.nls.de.json','package.nls.es.json',
    'media/chat.js','media/chat.css','media/i18n.js','media/translations.json','media/safe-html.js','media/purify.min.js','media/icons.min.js',
    'media/highlight.min.js','media/monokai.min.css','media/HighlightJS.LICENSE',
    'media/history-demo.png','media/trinity_agent_screenshot.png','media/trinity-icon.png','media/trinity-activity.png','media/trinity-menu.png','media/trinity-header-primary.png','media/trinity-header-white.png','media/trinity-title-black.png','media/DOMPurify.LICENSE','media/BootstrapIcons.LICENSE.txt','media/FontAwesome.LICENSE.txt'];
  for (const name of ['key','robot','wallet']) for (const theme of ['light','dark']) files.push(`media/bi-${name}-${theme}.svg`);
  for (const relative of files) {
    const source = path.join(root, relative);
    if (fs.lstatSync(source).isSymbolicLink()) throw Error(`Refusing symlink: ${relative}`);
    const destination = path.join(stage, relative);
    fs.mkdirSync(path.dirname(destination), {recursive:true});
    fs.copyFileSync(source, destination);
  }
  if (publisher !== 'trinity') {
    const readmePath = path.join(stage, 'README.md');
    const readme = fs.readFileSync(readmePath, 'utf8');
    const previewLink = '](media/trinity_agent_screenshot.png)';
    if (!readme.includes(previewLink)) throw Error('README must reference the bundled preview screenshot');
    const previewUrl = `https://${publisher}.gallery.vsassets.io/_apis/public/gallery/publisher/${publisher}/extension/${manifest.name}/${manifest.version}/assetbyname/${previewAssetType}`;
    fs.writeFileSync(readmePath, readme.replace(previewLink, `](${previewUrl})`));
  }
  // Generate from readable sources in the isolated stage: never trust stale build output.
  execFileSync(process.execPath,[path.join(root,'scripts/build-frontend.cjs'),'--out',path.join(stage,'media')],{cwd:root,stdio:'inherit'});
  fs.copyFileSync(path.resolve(root, '../LICENSE'), path.join(stage, 'LICENSE'));
  if (publisher === 'trinity') {
    const crypto = require('node:crypto');
    const backend = {};
    for (const name of ['trinity_agent/__main__.py', 'trinity_agent/agent.py', 'trinity_agent/memory.py', 'trinity_agent/workspace.py', 'trinity_agent/project_memory.py', 'trinity_agent/context.py', 'trinity_agent/vision.py', 'trinity_agent/cache.py', 'trinity_agent/resources.py', 'trinity_agent/limits.py', 'trinity_agent/request.py', 'trinity_agent/budget.py', 'trinity_agent/budget_model.py', 'trinity_agent/evaluation.py', 'trinity_agent/verification.py', 'trinity_agent/instructions.py', 'trinity_agent/task_engine.py', 'trinity_agent/acceptance.py', 'trinity_agent/repository.py', 'trinity_agent/execution.py', 'trinity_agent/operations.py', 'trinity_agent/tools.py', 'trinity_agent/process.py', 'scripts/trinity-agent.sh']) {
      backend[name] = crypto.createHash('sha256').update(fs.readFileSync(path.resolve(root, '..', name))).digest('hex');
    }
    fs.writeFileSync(path.join(stage, 'local-update.json'), JSON.stringify({backend}));
  }
  delete manifest.__metadata;
  manifest.publisher = publisher;
  manifest.displayName = 'TRINITY Agent';
  // Choose the local icon before code-server caches the installed manifest.
  const darkLocalIcon = publisher === 'trinity' && configuredDarkTheme();
  manifest.icon = darkLocalIcon ? 'media/trinity-activity.png' : 'media/trinity-icon.png';
  manifest.contributes.viewsContainers.activitybar[0].icon = 'media/trinity-menu.png';
  delete manifest.scripts;
  delete manifest.devDependencies;
  fs.writeFileSync(path.join(stage,'package.json'), JSON.stringify(manifest,null,2)+'\n');
  fs.writeFileSync(path.join(stage,'.vscodeignore'), 'package-lock.json\n.vscodeignore\nnode_modules/**/test/**\nnode_modules/**/tests/**\nnode_modules/**/benchmark/**\nnode_modules/**/benchmarks/**\nnode_modules/**/.github/**\n');
  execFileSync('npm', ['ci','--omit=dev','--ignore-scripts','--no-audit','--no-fund'], {cwd:stage,stdio:'inherit'});
  const output = path.join(root,'dist',`trinity-agent-${publisher === 'trinity' ? 'local-' : ''}${manifest.version}.vsix`);
  fs.mkdirSync(path.dirname(output), {recursive:true});
  execFileSync(process.execPath, [require.resolve('@vscode/vsce/vsce'),'package','--out',output,
    '--baseContentUrl','https://github.com/trinity-labs/trinity-agent/blob/main/extension',
    '--baseImagesUrl','https://raw.githubusercontent.com/trinity-labs/trinity-agent/main/extension'], {cwd:stage,stdio:'inherit'});
  if (publisher !== 'trinity') {
    const unpacked = fs.mkdtempSync(path.join(os.tmpdir(), 'trinity-vsix-finalize-'));
    try {
      execFileSync('unzip', ['-q', output, '-d', unpacked]);
      const vsixManifest = path.join(unpacked, 'extension.vsixmanifest');
      const xml = fs.readFileSync(vsixManifest, 'utf8');
      const asset = `    <Asset Type="${previewAssetType}" Path="extension/media/trinity_agent_screenshot.png" Addressable="true" />\n`;
      if (!xml.includes(previewAssetType)) fs.writeFileSync(vsixManifest, xml.replace(/([\t ]*)<\/Assets>/, `${asset}$&`));
      fs.rmSync(output);
      execFileSync('zip', ['-qr', output, '.'], {cwd:unpacked});
    } finally { fs.rmSync(unpacked,{recursive:true,force:true}); }
  }
  console.log(`Packaged ${publisher}.${manifest.name} ${manifest.version}: ${output}`);
} finally { fs.rmSync(stage,{recursive:true,force:true}); }
