import { createHash } from 'node:crypto';

const productIdPattern = /^p-[0-9a-f]{24}$/;
const imagePathPattern = /^\.\/images\/[a-z0-9][a-z0-9._-]*\.(?:avif|jpe?g|png|webp)$/i;
const pricePattern = /^(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?$/;
const sha256Pattern = /^[0-9a-f]{64}$/i;
const unpublishedSpecText = /待確認|待核對|未提供/;
const sourceLabelPrefixPattern = /^(?:【(?:正版・帶雷標|正版|帶雷標)】\s*)+/;

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

function nameWithSourceLabels(name, { licensedLabel, laserLabel }) {
  if (licensedLabel === undefined && laserLabel === undefined) return name;

  const labels = [];
  if (licensedLabel === true) labels.push('正版');
  if (laserLabel === true) labels.push('帶雷標');

  const rawName = displayText(name.replace(sourceLabelPrefixPattern, ''), 'name');
  return labels.length ? `【${labels.join('・')}】${rawName}` : rawName;
}

function customerSpecs(specs) {
  if (specs === undefined) return [];
  if (!Array.isArray(specs)) throw new Error('商品規格需要標籤與內容清單');

  const publicSpecs = [];
  const labels = new Set();
  for (const spec of specs) {
    if (!spec || typeof spec !== 'object' || Array.isArray(spec)) {
      throw new Error('商品規格需要標籤與內容');
    }
    const label = displayText(spec.label, '規格標籤');
    if (label === '整箱毛重' || label === '外箱毛重' || unpublishedSpecText.test(label)) continue;
    const value = displayText(spec.value, '規格內容');
    if (unpublishedSpecText.test(value)) continue;
    if (labels.has(label)) throw new Error(`商品規格標籤「${label}」重複`);
    labels.add(label);
    publicSpecs.push({ label, value });
  }
  return publicSpecs;
}

function customerGallery(images = [], mainImage, id) {
  if (!Array.isArray(images)) throw new Error(`商品 ${id} 的附圖需要圖片路徑清單`);

  const usedPaths = new Set([mainImage.toLowerCase()]);
  return Array.from(images, (value) => {
    const path = displayText(value, '附圖路徑');
    if (!imagePathPattern.test(path)) throw new Error(`商品 ${id} 需要目錄內的驗收附圖`);
    const pathKey = path.toLowerCase();
    if (usedPaths.has(pathKey)) throw new Error(`商品 ${id} 的主圖與附圖路徑須各自唯一`);
    usedPaths.add(pathKey);
    return path;
  });
}

function customerProduct(row, id) {
  const sourcePrice = displayText(row.price, 'price');
  const pack = /^\d+$/.test(String(row.pack).trim()) ? Number(row.pack) : NaN;
  const image = displayText(row.image, 'image');
  const date = displayText(row.date, 'date');
  const localDate = new Date(`${date}T00:00:00Z`);

  if (!productIdPattern.test(id)) throw new Error('商品連結識別碼無效');
  if (!pricePattern.test(sourcePrice) || !/[1-9]/.test(sourcePrice)) {
    throw new Error(`商品 ${id} 缺少可用的台幣售價`);
  }
  if (!Number.isSafeInteger(pack) || pack < 1) throw new Error(`商品 ${id} 箱入數無效`);
  if (!imagePathPattern.test(image)) throw new Error(`商品 ${id} 需要目錄內的驗收主圖`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(localDate.valueOf()) || !localDate.toISOString().startsWith(date)) {
    throw new Error(`商品 ${id} 需要有效的入表日期`);
  }

  const [whole, fraction = ''] = sourcePrice.replaceAll(',', '').split('.');
  const roundedPrice = BigInt(whole) + (/[1-9]/.test(fraction) ? 1n : 0n);
  const specs = customerSpecs(row.specs);
  const galleryImages = customerGallery(row.galleryImages, image, id);

  return {
    id,
    code: displayText(row.code, 'code'),
    name: displayText(row.name, 'name'),
    category: displayText(row.category, 'category'),
    price: roundedPrice.toLocaleString('en-US'),
    unit: displayText(row.unit, 'unit'),
    pack,
    image,
    date,
    ...(specs.length ? { specs } : {}),
    ...(galleryImages.length ? { galleryImages } : {}),
  };
}

function draftEntry(product, mainImageSha256, galleryImageSha256s = []) {
  if (typeof mainImageSha256 !== 'string' || !sha256Pattern.test(mainImageSha256)) {
    throw new Error(`商品 ${product.id} 需要驗收主圖的 SHA-256`);
  }
  if (!Array.isArray(galleryImageSha256s) || galleryImageSha256s.length !== (product.galleryImages?.length ?? 0)) {
    throw new Error(`商品 ${product.id} 的附圖與 SHA-256 數量須相同`);
  }

  const imageFingerprint = mainImageSha256.toLowerCase();
  const galleryFingerprints = Array.from(galleryImageSha256s, (hash) => {
    if (typeof hash !== 'string' || !sha256Pattern.test(hash)) {
      throw new Error(`商品 ${product.id} 需要每張驗收附圖的 SHA-256`);
    }
    return hash.toLowerCase();
  });
  const reviewedContent = [product, imageFingerprint];
  if (galleryFingerprints.length) reviewedContent.push(galleryFingerprints);

  const contentFingerprint = createHash('sha256')
    .update(JSON.stringify(reviewedContent))
    .digest('hex');
  return {
    product,
    mainImageSha256: imageFingerprint,
    ...(galleryFingerprints.length ? { galleryImageSha256s: galleryFingerprints } : {}),
    reviewId: `${product.id}:${contentFingerprint}`,
  };
}

/**
 * 每次只傳入已讀取並整理的 no2 列。price 傳 no2 的 15% 報價欄顯示值，
 * 保留來源小數位，只去掉幣別與單位。網站草稿遇到小數就進到下一個整元。
 * date 傳台灣實際入表日期 YYYY-MM-DD。
 * specs 可依展示順序傳已查證的客用 { label, value } 規格清單。
 * name 傳原品名。licensedLabel 與 laserLabel 分別以來源明載的正版與雷標為準。
 * 單列必須明確通過報價、主圖、規格確認且未暫緩。若商品暫緩或任一條件
 * 變為未確認，仍應將該列與最新狀態傳入，讓本次草稿移除同一分頁和 NO。
 * mainImageSha256 必須取自實際要放進目錄的已驗收圖片位元組。
 * galleryImages 可依展示順序傳已驗收附圖路徑。galleryImageSha256s 傳同順序的圖片雜湊。
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

    const product = customerProduct(row, id);
    product.name = nameWithSourceLabels(product.name, row);
    draft.set(id, draftEntry(product, row.mainImageSha256, row.galleryImageSha256s));
  }

  return [...draft.values()];
}

/**
 * 只匯出本次人工選取的 reviewId。沿用舊的 reviewId 時，內容或圖片異動
 * 會讓核准失效。輸出可直接存成公開目錄的 JSON。
 */
export function exportReviewedCatalog(draft, reviewedIds = []) {
  const approvals = new Set(reviewedIds);
  const products = [];

  for (const entry of draft) {
    const product = customerProduct(entry.product, entry.product.id);
    const { reviewId } = draftEntry(product, entry.mainImageSha256, entry.galleryImageSha256s);
    if (approvals.delete(reviewId)) products.push(product);
  }

  if (approvals.size) throw new Error('部分核准項目已異動或移除，請重新確認本次草稿');
  return products;
}
