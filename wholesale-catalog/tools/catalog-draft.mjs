import { createHash } from 'node:crypto';

const productIdPattern = /^p-[0-9a-f]{24}$/;
const imagePathPattern = /^\.\/images\/[a-z0-9][a-z0-9._-]*\.(?:avif|jpe?g|png|webp)$/i;
const pricePattern = /^(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?$/;
const sha256Pattern = /^[0-9a-f]{64}$/i;

function sourceProductId({ sheetId, no }) {
  const tab = String(sheetId).trim();
  const noMatch = /^(?:no)?(\d+)$/i.exec(String(no).trim());
  if (!/^(?:0|[1-9]\d*)$/.test(tab) || !noMatch) {
    throw new Error('商品需要實際分頁 ID 與 NO');
  }
  const productNumber = BigInt(noMatch[1]).toString();
  const fingerprint = createHash('sha256').update(`${tab}:${productNumber}`).digest('hex');
  return `p-${fingerprint.slice(0, 24)}`;
}

function displayText(value, key) {
  if (typeof value !== 'string' || !value.trim() || /[\x00-\x1f]/.test(value)) {
    throw new Error(`缺少或無法呈現的商品欄位 ${key}`);
  }
  return value.trim();
}

function customerProduct(row, id) {
  const price = displayText(row.price, 'price');
  const numericPrice = Number(price.replaceAll(',', ''));
  const pack = /^\d+$/.test(String(row.pack).trim()) ? Number(row.pack) : NaN;
  const image = displayText(row.image, 'image');
  const date = displayText(row.date, 'date');
  const localDate = new Date(`${date}T00:00:00Z`);

  if (!productIdPattern.test(id)) throw new Error('商品連結識別碼無效');
  if (!pricePattern.test(price) || !Number.isFinite(numericPrice) || numericPrice <= 0) {
    throw new Error(`商品 ${id} 缺少可用的台幣售價`);
  }
  if (!Number.isSafeInteger(pack) || pack < 1) throw new Error(`商品 ${id} 箱入數無效`);
  if (!imagePathPattern.test(image)) throw new Error(`商品 ${id} 需要目錄內的驗收主圖`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(localDate.valueOf()) || !localDate.toISOString().startsWith(date)) {
    throw new Error(`商品 ${id} 需要有效的入表日期`);
  }

  return {
    id,
    code: displayText(row.code, 'code'),
    name: displayText(row.name, 'name'),
    category: displayText(row.category, 'category'),
    price,
    unit: displayText(row.unit, 'unit'),
    pack,
    image,
    date,
  };
}

function draftEntry(product, mainImageSha256) {
  if (typeof mainImageSha256 !== 'string' || !sha256Pattern.test(mainImageSha256)) {
    throw new Error(`商品 ${product.id} 需要驗收主圖的 SHA-256`);
  }
  const imageFingerprint = mainImageSha256.toLowerCase();
  const contentFingerprint = createHash('sha256')
    .update(JSON.stringify([product, imageFingerprint]))
    .digest('hex');
  return {
    product,
    mainImageSha256: imageFingerprint,
    reviewId: `${product.id}:${contentFingerprint}`,
  };
}

/**
 * 每次只傳入已讀取並整理的 no2 列。price 傳 no2 的 15% 報價欄顯示值，
 * 保留小數位，只去掉幣別與單位。date 傳台灣實際入表日期 YYYY-MM-DD。
 * 單列必須明確通過報價、主圖、規格確認且未暫緩。若商品暫緩或任一條件
 * 變為未確認，仍應將該列與最新狀態傳入，讓本次草稿移除同一分頁和 NO。
 * mainImageSha256 必須取自實際要放進目錄的已驗收圖片位元組。
 * 回傳值委由內部審核流程保存。previousDraft 可傳入上次輸出以更新同一商品。
 */
export function buildCatalogDraft(sourceRows, previousDraft = []) {
  const draft = new Map(previousDraft.map((entry) => [entry.product.id, entry]));
  const seen = new Set();

  for (const row of sourceRows) {
    const id = sourceProductId(row);
    if (seen.has(id)) throw new Error(`商品 ${id} 的分頁 ID 與 NO 重複`);
    seen.add(id);

    if (row.quoteVerified !== true || row.mainImageVerified !== true ||
        row.specsConfirmed !== true || row.onHold !== false) {
      draft.delete(id);
      continue;
    }

    draft.set(id, draftEntry(customerProduct(row, id), row.mainImageSha256));
  }

  return [...draft.values()];
}

/**
 * 只匯出本次人工選取的 reviewId。沿用舊的 reviewId 時，內容或主圖異動
 * 會讓核准失效。輸出可直接存成公開目錄的 JSON。
 */
export function exportReviewedCatalog(draft, reviewedIds = []) {
  const approvals = new Set(reviewedIds);
  const products = [];

  for (const entry of draft) {
    const product = customerProduct(entry.product, entry.product.id);
    const { reviewId } = draftEntry(product, entry.mainImageSha256);
    if (approvals.delete(reviewId)) products.push(product);
  }

  if (approvals.size) throw new Error('部分核准項目已異動或移除，請重新確認本次草稿');
  return products;
}
