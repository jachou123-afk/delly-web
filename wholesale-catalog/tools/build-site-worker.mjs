import assert from 'node:assert/strict';
import { access, copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const file = (name) => new URL(name, root);
const config = JSON.parse(await readFile(file('.openai/hosting.json'), 'utf8'));
assert(config.project_id && config.d1 === 'DB' && config.static === undefined, 'Sites 必須使用 Worker 與 DB 綁定');
await Promise.all(['index.html', 'catalog-data.js', 'owner-quote-tools.js', 'ad-selections.js']
  .map((name) => access(file('dist/client/' + name))));
await mkdir(file('dist/server/'), { recursive: true });
await Promise.all(['index.js', 'ad-selection-zip.js', 'ad-cover-overlap.js']
  .map((name) => copyFile(file('worker/' + name), file('dist/server/' + name))));
await writeFile(file('dist/server/wrangler.json'), JSON.stringify({
  main: 'index.js',
  compatibility_date: '2026-05-15',
  assets: { directory: '../client', binding: 'ASSETS' },
}, null, 2) + '\n');
