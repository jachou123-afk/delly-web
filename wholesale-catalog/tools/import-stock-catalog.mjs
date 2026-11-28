import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

const [input] = process.argv.slice(2);
assert(input, '請指定現貨資料的 JSON 路徑');
const source = JSON.parse((await readFile(input, 'utf8')).replace(/^\uFEFF/, ''));
const { checkedAt, checkedAtLabel, stockThreshold, stockThresholdUnit } = source;
assert(source.version === 1 && Number.isFinite(Date.parse(checkedAt)) &&
  typeof checkedAtLabel === 'string' && checkedAtLabel.trim() &&
  Number.isSafeInteger(stockThreshold) && stockThreshold > 0 && stockThresholdUnit === '件');
assert(Array.isArray(source.products) && source.products.length === 20, '首批現貨需對應 20 款樣板商品');

const cleanText = (text) => typeof text === 'string' && text.trim().length > 0 && !/[\x00-\x1f\x7f]/.test(text);
const digest = (bytes) => createHash('sha256').update(bytes).digest('hex');
const output = new URL('../dist/client/images/stock/', import.meta.url);
const ids = new Set();
const count = { G: 0, K: 0 };
const items = [];

for (const row of source.products) {
  const { sku, name, unit, variant, tiers, image, imageSha256 } = row;
  assert(typeof sku === 'string' && /^[GK]\d+$/.test(sku) && !ids.has(sku), '無效或重複的現貨貨號');
  ids.add(sku);
  count[sku[0]]++;
  assert(cleanText(name) && cleanText(unit) && (variant === undefined || cleanText(variant)), sku);
  assert(Array.isArray(tiers) && tiers.length === 2, sku);
  const prices = tiers.map(({ label, quantity, unitPrice, total, consult }, index) => {
    assert(label === (index === 0 ? '小量' : '大量') && typeof consult === 'boolean', sku);
    if (consult) {
      assert(quantity === null && unitPrice === null && total === null, sku);
    } else {
      assert(Number.isSafeInteger(quantity) && quantity > 0 && Number.isFinite(unitPrice) && unitPrice > 0 &&
        Number.isFinite(total) && total > 0 && Math.abs(quantity * unitPrice - total) < 0.000001, sku);
    }
    return { label, quantity, unitPrice, total, consult };
  });
  assert(image === 'media/' + sku + '.webp' && /^[a-f\d]{64}$/.test(imageSha256), sku);
  const imageBytes = await readFile(path.resolve(path.dirname(input), image));
  assert.equal(digest(imageBytes), imageSha256, sku);
  assert(imageBytes.subarray(0, 4).toString() === 'RIFF' && imageBytes.subarray(8, 12).toString() === 'WEBP', sku);
  items.push({
    imageBytes,
    product: { sku, name, unit, ...(variant ? { variant } : {}), tiers: prices, image: './images/stock/' + sku + '.webp' },
  });
}
assert.deepEqual(count, { G: 10, K: 10 });
await mkdir(output, { recursive: true });
await Promise.all(items.map(({ imageBytes, product }) => writeFile(new URL(product.sku + '.webp', output), imageBytes)));
const stock = { version: 1, checkedAt, checkedAtLabel, stockThreshold, stockThresholdUnit,
  products: items.map(({ product }) => product) };
await writeFile(new URL('../worker/stock-product-index.js', import.meta.url),
  'export const stockProductIndex = ' + JSON.stringify(stock) + ';\n');
console.log(JSON.stringify({ count, products: stock.products.length,
  quoteTiers: items.flatMap(({ product }) => product.tiers).filter(({ consult }) => !consult).length,
  imageBytes: items.reduce((total, { imageBytes }) => total + imageBytes.length, 0) }));
