'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const stage = fs.mkdtempSync(path.join(os.tmpdir(), 'trinity-vsix-'));
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'package.json')));
const publisher = process.env.MARKETPLACE_PUBLISHER || 'stopyz';
if (!/^[a-zA-Z0-9][a-zA-Z0-9-]*$/.test(publisher)) throw Error('Invalid Marketplace publisher ID');
if (process.env.RELEASE_TAG && process.env.RELEASE_TAG !== `v${manifest.version}`) throw Error('Release tag must match package.json version');
try {
  // Explicit allowlist: never copy local backups, credentials, archives or custom branding.
  const files = ['extension.js','archive-store.js','security.js','package.json','package-lock.json','README.md','CHANGELOG.md',
    'package.nls.json','package.nls.fr.json','package.nls.en.json','package.nls.de.json','package.nls.es.json',
    'media/chat.js','media/chat.css','media/i18n.js','media/translations.json','media/safe-html.js','media/purify.min.js','media/icons.min.js',
    'media/history-demo.png','media/trinity-icon.png','media/trinity-activity.png','media/DOMPurify.LICENSE','media/BootstrapIcons.LICENSE.txt','media/FontAwesome.LICENSE.txt'];
  for (const name of ['key','robot','wallet']) for (const theme of ['light','dark']) files.push(`media/bi-${name}-${theme}.svg`);
  for (const relative of files) {
    const source = path.join(root, relative);
    if (fs.lstatSync(source).isSymbolicLink()) throw Error(`Refusing symlink: ${relative}`);
    const destination = path.join(stage, relative);
    fs.mkdirSync(path.dirname(destination), {recursive:true});
    fs.copyFileSync(source, destination);
  }
  fs.copyFileSync(path.resolve(root, '../../LICENSE'), path.join(stage, 'LICENSE'));
  if (publisher === 'trinity') {
    const crypto = require('node:crypto');
    const backend = {};
    for (const name of ['ovh-agent.py', 'ovh_memory.py', 'ovh-agent.sh']) {
      backend[name] = crypto.createHash('sha256').update(fs.readFileSync(path.resolve(root, '../..', name))).digest('hex');
    }
    fs.writeFileSync(path.join(stage, 'local-update.json'), JSON.stringify({backend}));
  }
  delete manifest.__metadata;
  manifest.publisher = publisher;
  manifest.displayName = 'TRINITY Agent';
  manifest.icon = 'media/trinity-icon.png';
  manifest.contributes.viewsContainers.activitybar[0].icon = 'media/trinity-activity.png';
  delete manifest.scripts;
  delete manifest.devDependencies;
  fs.writeFileSync(path.join(stage,'package.json'), JSON.stringify(manifest,null,2)+'\n');
  fs.writeFileSync(path.join(stage,'.vscodeignore'), 'package-lock.json\n.vscodeignore\nnode_modules/**/test/**\nnode_modules/**/tests/**\nnode_modules/**/benchmark/**\nnode_modules/**/benchmarks/**\nnode_modules/**/.github/**\n');
  execFileSync('npm', ['ci','--omit=dev','--ignore-scripts','--no-audit','--no-fund'], {cwd:stage,stdio:'inherit'});
  const output = path.join(root,'dist',`trinity-agent-${publisher === 'trinity' ? 'local-' : ''}${manifest.version}.vsix`);
  fs.mkdirSync(path.dirname(output), {recursive:true});
  execFileSync(process.execPath, [require.resolve('@vscode/vsce/vsce'),'package','--out',output,
    '--baseContentUrl','https://github.com/trinity-labs/trinity-agent/blob/main/ovh-agent-extension/extension',
    '--baseImagesUrl','https://raw.githubusercontent.com/trinity-labs/trinity-agent/main/ovh-agent-extension/extension'], {cwd:stage,stdio:'inherit'});
  console.log(`Packaged ${publisher}.${manifest.name} ${manifest.version}: ${output}`);
} finally { fs.rmSync(stage,{recursive:true,force:true}); }
