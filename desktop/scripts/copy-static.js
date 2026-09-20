// Copies renderer static assets next to the bundled renderer script.
const fs = require('node:fs');
const path = require('node:path');
const src = path.join(__dirname, '..', 'src', 'renderer');
const dst = path.join(__dirname, '..', 'dist', 'renderer');
fs.mkdirSync(dst, { recursive: true });
for (const name of ['index.html', 'styles.css']) {
  fs.copyFileSync(path.join(src, name), path.join(dst, name));
}
