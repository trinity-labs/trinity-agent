'use strict';
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const args = Object.fromEntries(process.argv.slice(2).reduce((rows, value, index, all) => {
  if (value.startsWith('--') && all[index + 1] && !all[index + 1].startsWith('--')) rows.push([value.slice(2), all[index + 1]]);
  return rows;
}, []));
const lightPath = path.resolve(args.light || path.join(root, 'media/trinity-icon.png'));
const darkPath = path.resolve(args.dark || path.join(root, 'media/trinity-activity.png'));
const outputPath = path.resolve(args.out || path.join(root, 'media/trinity-theme.svg'));

function dataUri(file) {
  const extension = path.extname(file).slice(1).toLowerCase();
  const mime = {png:'image/png',svg:'image/svg+xml',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp'}[extension];
  if (!mime) throw new Error(`Unsupported theme icon: ${file}`);
  return `data:${mime};base64,${fs.readFileSync(file).toString('base64')}`;
}

const light = dataUri(lightPath);
const dark = dataUri(darkPath);
const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><style>.dark{display:none}@media(prefers-color-scheme:dark){.light{display:none}.dark{display:inline}}</style><image class="light" width="512" height="512" preserveAspectRatio="xMidYMid meet" href="${light}"/><image class="dark" width="512" height="512" preserveAspectRatio="xMidYMid meet" href="${dark}"/></svg>\n`;
fs.writeFileSync(outputPath, svg);
