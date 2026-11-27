import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';

const [catalogPath, pricesPath, templatesPath, outputPath] = process.argv.slice(2);
if (!catalogPath || !pricesPath || !templatesPath || !outputPath) {
  throw new Error('用法  node build-owner-quote-tools.mjs <目錄 JS> <台幣售價 JSON> <文案模板 JSON> <擁有者測試 JS>');
}

const [catalog, prices, templates] = await Promise.all([catalogPath, pricesPath, templatesPath]
  .map((pathname) => readFile(pathname, 'utf8')));
const visible = /^window\.catalogProducts\s*=\s*(\[[\s\S]*\]);\s*$/.exec(catalog);
assert(visible, '目錄資料格式有誤');
const products = JSON.parse(visible[1]);
const priceById = JSON.parse(prices);
const templateById = JSON.parse(templates);
const ids = new Set(products.map((item) => item.id));
assert.equal(ids.size, products.length, '目錄有重複商品');
assert(Object.keys(priceById).every((id) => ids.has(id)), '售價檔有目錄外商品');
assert(Object.keys(templateById).every((id) => ids.has(id)), '文案檔有目錄外商品');

const data = {};
const noPrices = [];
const noTemplate = [];
for (const product of products) {
  const frames = priceById[product.id];
  const template = templateById[product.id];
  if (frames !== undefined) {
    assert(Array.isArray(frames) && frames.length === 11 &&
      frames.every((value, position) => Number.isSafeInteger(value) && value > 0 &&
        (position === 0 || value >= frames[position - 1])), `售價檔格式有誤 ${product.code}`);
    assert.equal(BigInt(frames[5]), BigInt(product.price.replaceAll(',', '')), `15% 報價不一致 ${product.code}`);
  } else {
    noPrices.push(product.code);
  }
  if (template !== undefined) {
    const { before, after } = template;
    assert.deepEqual(Object.keys(template).sort(), ['after', 'before']);
    assert(Array.isArray(before) && before.includes(product.code) &&
      before.at(-1) === `裝箱 ${product.pack}${product.unit}/箱` &&
      before.every((line) => typeof line === 'string' && line.trim() && !/[\r\n]/.test(line)), `文案格式有誤 ${product.code}`);
    assert.deepEqual(after, ['交貨2-3週'], `交期格式有誤 ${product.code}`);
  } else {
    noTemplate.push(product.code);
  }
  data[product.id] = { ...(frames ? { prices: frames } : {}), ...(template ? { template } : {}) };
}

await writeFile(outputPath, 'window.catalogQuoteTools = ' + JSON.stringify(data) + ';\n');
console.log(JSON.stringify({
  products: products.length,
  prices: products.length - noPrices.length,
  templates: products.length - noTemplate.length,
  noPrices,
  noTemplate,
}));
