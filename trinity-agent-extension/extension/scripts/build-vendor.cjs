'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');

// Refresh the bundled browser libraries from the same dependencies used in tests.
for (const script of ['build-icons.cjs', 'build-highlighting.cjs']) {
  execFileSync(process.execPath, [path.join(__dirname, script)], {cwd: root, stdio: 'inherit'});
}
const purify = path.dirname(require.resolve('dompurify'));
const browserCode = fs.readFileSync(path.join(purify, 'purify.min.js'), 'utf8').replace(/^\/\/# sourceMappingURL=.*$/gm, '').trimEnd();
fs.writeFileSync(path.join(root, 'media/purify.min.js'), browserCode + '\n');
fs.copyFileSync(path.join(purify, '../LICENSE'), path.join(root, 'media/DOMPurify.LICENSE'));
