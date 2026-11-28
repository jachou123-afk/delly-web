import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

const [configurationFile, catalogFile, outputFile] = process.argv.slice(2);
if (!configurationFile || !catalogFile || !outputFile) {
  throw new Error('用法  node build-product-search.mjs <兩表來源設定 JSON> <catalog-data.js> <Worker 商品搜尋模組>');
}

const parse = (bytes) => JSON.parse(bytes.replace(/^\uFEFF/, ''));
const read = async (file) => parse(await readFile(file, 'utf8'));
const { checkedAt, sources: input } = await read(configurationFile);
assert(typeof checkedAt === 'string' && Number.isFinite(Date.parse(checkedAt)), '缺少資料的實際查核時間');

const catalog = /^window\.catalogProducts\s*=\s*(\[[\s\S]*\]);\s*$/.exec(await readFile(catalogFile, 'utf8'));
assert(catalog, '目錄格式有誤');
const toyPage = JSON.parse(catalog[1]).filter(({ category }) => category === '玩具')
  .sort((a, b) => b.date.localeCompare(a.date)).slice(0, 10);
assert.equal(toyPage.length, 10, '玩具試用需要 10 款現有商品');
const trialProducts = new Map(toyPage.map(({ id, code }, position) => [id, { code, position }]));
const sources = {};
const products = [];
const linked = new Set();
const entries = new Set();
const counts = {};

function singleLine(value, maxLength, label) {
  assert(typeof value === 'string' && value.trim() && value.trim().length <= maxLength &&
    !/[\x00-\x1f\x7f]/.test(value), label);
  return value.trim();
}

function dateOnly(value) {
  assert(typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    new Date(value).toISOString().slice(0, 10) === value, '無效的商品日期');
  return value;
}

for (const source of ['legacy', 'no2']) {
  const { label, spreadsheetId, file } = input[source];
  assert(typeof spreadsheetId === 'string' && /^[A-Za-z0-9_-]{20,80}$/.test(spreadsheetId), '來源試算表 ID 有誤');
  sources[source] = {
    label: singleLine(label, 35, '來源名稱有誤'),
    url: `https://docs.google.com/spreadsheets/d/${spreadsheetId}/edit`,
  };
  const rows = await read(path.resolve(path.dirname(configurationFile), file));
  assert(Array.isArray(rows) && rows.length > 0, '商品來源沒有有效資料');
  counts[source] = 0;
  for (const item of rows) {
    const { no, sheetId, row } = item;
    assert(typeof no === 'string' && /^\d{1,8}$/.test(no) && Number(no) >= 1 &&
      Number.isSafeInteger(sheetId) && sheetId >= 0 && Number.isSafeInteger(row) && row > 0, '商品來源定位有誤');
    const entity = `${sheetId}:${Number(no)}`;
    const key = `${spreadsheetId}:${entity}`;
    assert(!entries.has(key), `同一來源的商品 NO 重複 ${source} ${entity}`);
    entries.add(key);
    const catalogKey = source === 'legacy' ? key : entity;
    const catalogId = 'p-' + createHash('sha256').update(catalogKey).digest('hex').slice(0, 24);
    const trialProduct = trialProducts.get(catalogId);
    if (!trialProduct) continue;
    const { code } = trialProduct;
    assert((!item.code || item.code === code) && /^BGD-[A-Z]{1,8}-\d+$/.test(code), '商品編號與目錄識別不一致');
    linked.add(catalogId);
    counts[source]++;
    products.push({
      source,
      no,
      name: singleLine(item.name?.replace(/[\t\r\n]+/g, ' '), 300, '商品品名有誤'),
      sheetId,
      tab: singleLine(item.tab, 100, '試算表分頁名稱有誤'),
      row,
      code,
      ...(item.supplierSku ? { supplierSku: singleLine(item.supplierSku, 100, '供應商貨號有誤') } : {}),
      ...(item.date ? { date: dateOnly(item.date) } : {}),
      catalogId,
    });
  }
}
assert.notEqual(sources.legacy.url, sources.no2.url, '兩個來源指向同一份試算表');
assert.equal(linked.size, toyPage.length, '玩具試用品項缺少已核對的原表列');
assert.equal(products.length, toyPage.length, '玩具試用品項的來源筆數不一致');
products.sort((a, b) => trialProducts.get(a.catalogId).position - trialProducts.get(b.catalogId).position);
for (const source of Object.keys(sources)) if (!counts[source]) delete sources[source];
const data = { version: 1, checkedAt, sources, products };
const contents = 'export const internalProductSearch = ' + JSON.stringify(data) + ';\n';
assert(!path.resolve(outputFile).replaceAll('\\', '/').includes('/dist/client/'), '商品索引必須留在 Worker');
await writeFile(outputFile, contents, 'utf8');
console.log(JSON.stringify({ ...counts, total: products.length, linked: linked.size, codes: products.map(({ code }) => code),
  supplierSkus: products.filter(({ supplierSku }) => supplierSku).length, bytes: Buffer.byteLength(contents) }));
