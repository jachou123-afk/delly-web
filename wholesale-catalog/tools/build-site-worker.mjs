import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { access, copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import { adCoverOverlaps, verifiedAdAssetHashes } from '../worker/ad-cover-overlap.js';

const root = new URL('../', import.meta.url);
const file = (name) => new URL(name, root);
const config = JSON.parse(await readFile(file('.openai/hosting.json'), 'utf8'));
assert(config.project_id && config.d1 === 'DB' && config.static === undefined, 'Sites 必須使用 Worker 與 DB 綁定');
await Promise.all(['index.html', 'catalog.js', 'styles.css', 'catalog-data.js', 'owner-quote-tools.js', 'ad-selections.js']
  .map((name) => access(file('dist/client/' + name))));
await mkdir(file('dist/server/'), { recursive: true });
await Promise.all(['index.js', 'ad-selection-zip.js', 'internal-product-index.js']
  .map((name) => copyFile(file('worker/' + name), file('dist/server/' + name))));
async function verifiedImage(path) {
  const expected = verifiedAdAssetHashes.get(path);
  if (!expected) return false;
  try {
    const asset = await readFile(file('dist/client' + path));
    return createHash('sha256').update(asset).digest('hex') === expected;
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    return false;
  }
}

const activeOverlaps = [];
for (const [id, { cover, crops }] of adCoverOverlaps) {
  if (!await verifiedImage(cover)) continue;
  const valid = [];
  for (const crop of crops) {
    if (await verifiedImage(crop)) valid.push(crop);
  }
  if (valid.length) activeOverlaps.push([id, { cover, crops: valid }]);
}
await writeFile(file('dist/server/ad-cover-overlap.js'),
  'export const adCoverOverlaps = new Map(' + JSON.stringify(activeOverlaps, null, 2) + ');\n');

await writeFile(file('dist/server/wrangler.json'), JSON.stringify({
  main: 'index.js',
  compatibility_date: '2026-05-15',
  assets: { directory: '../client', binding: 'ASSETS' },
}, null, 2) + '\n');
