import { AdSelectionZipError, makeAdSelectionZip } from './ad-selection-zip.js';

const queuePath = '/api/ad-selections';
const productIdPattern = /^p-[a-f0-9]{24}$/;
const batchIdPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
const imagePathPattern = /^\.\/images\/[a-z0-9][a-z0-9._-]*\.(?:avif|jpe?g|png|webp)$/i;
const queueStatuses = new Set(['pending', 'reviewed']);
const maxProducts = 50;
const maxRequestBytes = 16384;

class RequestError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function json(body, status = 200, headers = {}) {
  return Response.json(body, {
    status,
    headers: { 'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff', ...headers },
  });
}

function record(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

async function readJson(request) {
  if (request.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'application/json') {
    throw new RequestError(415, '請使用 JSON 提交選品。');
  }
  if (!request.body || Number(request.headers.get('Content-Length')) > maxRequestBytes) {
    throw new RequestError(413, '選品資料太大。');
  }
  const stream = request.body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let text = '';
  let bytes = 0;
  try {
    while (true) {
      const { value, done } = await stream.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > maxRequestBytes) {
        await stream.cancel();
        throw new RequestError(413, '選品資料太大。');
      }
      text += decoder.decode(value, { stream: true });
    }
    return JSON.parse(text + decoder.decode());
  } catch (error) {
    if (error instanceof RequestError) throw error;
    throw new RequestError(400, '選品資料格式有誤。');
  } finally {
    stream.releaseLock();
  }
}

function ownerSignedIn(request, env) {
  const owner = env.CATALOG_OWNER_EMAIL?.trim().toLowerCase();
  const email = request.headers.get('oai-authenticated-user-email')?.trim().toLowerCase();
  return Boolean(owner && email === owner && request.headers.get('oai-authenticated-user-id'));
}

function requireSameOrigin(request, url) {
  if (request.headers.get('Origin') !== url.origin || request.headers.get('Sec-Fetch-Site') === 'cross-site') {
    throw new RequestError(403, '請在私人批發目錄操作選品。');
  }
}

function submittedProducts(input) {
  if (!Array.isArray(input) || input.length === 0 || input.length > maxProducts) {
    throw new RequestError(400, '每批可送出 1 到 50 款商品。');
  }
  const seen = new Set();
  return input.map((product) => {
    if (!record(product) || Object.keys(product).length !== 2 ||
        typeof product.id !== 'string' || !productIdPattern.test(product.id) || seen.has(product.id) ||
        !Number.isInteger(product.margin) || product.margin < 10 || product.margin > 20) {
      throw new RequestError(400, '商品重複、編號錯誤或毛利率超出 10% 至 20%。');
    }
    seen.add(product.id);
    return { id: product.id, margin: product.margin };
  });
}

async function assetJson(env, origin, pathname, assignment) {
  const response = await env.ASSETS.fetch(new Request(new URL(pathname, origin)));
  if (!response.ok) throw new Error('Catalog asset unavailable');
  const source = (await response.text()).trim();
  const prefix = `window.${assignment} = `;
  if (!source.startsWith(prefix) || !source.endsWith(';')) throw new Error('Invalid catalog asset');
  return JSON.parse(source.slice(prefix.length, -1));
}

async function loadAssetCatalog(env, origin) {
  const [catalog, tools] = await Promise.all([
    assetJson(env, origin, '/catalog-data.js', 'catalogProducts'),
    assetJson(env, origin, '/owner-quote-tools.js', 'catalogQuoteTools'),
  ]);
  if (!Array.isArray(catalog) || !record(tools)) throw new Error('Invalid catalog data');
  return { catalog, tools };
}

function snapshotProducts(selection, catalog, tools, origin) {
  const products = new Map(catalog.map((product) => [product.id, product]));
  return selection.map(({ id, margin }) => {
    const product = products.get(id);
    const current = tools[id];
    const price = current?.prices?.[margin - 10];
    const template = current?.template;
    if (!product || !imagePathPattern.test(product.image) || !template ||
        !Number.isSafeInteger(price) || price <= 0) {
      throw new RequestError(409, '有商品或售價已更新。請重新整理頁面再確認。');
    }
    const imagePaths = [...new Set([product.image, ...(product.galleryImages ?? [])])];
    if (imagePaths.some((image) => !imagePathPattern.test(image))) {
      throw new RequestError(409, '有商品圖片待確認。請重新整理頁面再試。');
    }
    const images = imagePaths.map((image) => new URL(image, origin).href);
    const productUrl = new URL('/', origin);
    productUrl.searchParams.set('p', id);
    return {
      id,
      code: product.code,
      name: product.name,
      margin,
      price,
      unit: product.unit,
      pack: product.pack,
      image: images[0],
      images,
      productUrl: productUrl.href,
      draft: [...template.before, `售價${price}元/${product.unit}`, ...template.after].join('\n'),
    };
  });
}

function publicBatch(row) {
  return {
    id: row.id,
    createdAt: row.created_at,
    status: row.status,
    products: JSON.parse(row.products_json),
    reviewedAt: row.reviewed_at,
    recommendedProductIds: row.recommended_product_ids_json
      ? JSON.parse(row.recommended_product_ids_json)
      : null,
  };
}

function readBatch(db, id) {
  return db.prepare('SELECT * FROM ad_selection_batches WHERE id = ?').bind(id).first();
}

async function downloadBatch(env, id, origin) {
  const row = await readBatch(env.DB, id);
  if (!row) throw new RequestError(404, '找不到這批選品。');
  if (!env.ASSETS) throw new RequestError(503, '圖文包暫時無法產生。');
  const bytes = await makeAdSelectionZip(publicBatch(row), (image) => {
    let url;
    try { url = new URL(image); }
    catch { throw new RequestError(409, '有圖片網址待確認。'); }
    if (url.origin !== origin || url.username || url.password || url.search || url.hash ||
        !imagePathPattern.test('.' + url.pathname)) {
      throw new RequestError(409, '圖文包包含無法核對的圖片，請確認批次素材。');
    }
    return env.ASSETS.fetch(new Request(url));
  });
  return new Response(bytes, {
    headers: {
      'Content-Type': 'application/zip',
      'Content-Disposition': `attachment; filename="LINE-ads-${id}.zip"`,
      'Content-Length': String(bytes.byteLength),
      'Cache-Control': 'private, no-store',
      'X-Content-Type-Options': 'nosniff',
    },
  });
}

function sameSubmission(row, requestJson) {
  if (row.request_json !== requestJson) {
    throw new RequestError(409, '這批的識別碼已用於另一份選品。');
  }
  return publicBatch(row);
}

async function createBatch(request, env, origin, loadCatalog) {
  const input = await readJson(request);
  if (!record(input) || Object.keys(input).length !== 2 ||
      typeof input.requestId !== 'string' || !batchIdPattern.test(input.requestId)) {
    throw new RequestError(400, '選品批次識別碼無效。');
  }
  const id = input.requestId.toLowerCase();
  const selection = submittedProducts(input.products);
  const requestJson = JSON.stringify(selection);
  const existing = await readBatch(env.DB, id);
  if (existing) return json({ batch: sameSubmission(existing, requestJson) });

  const { catalog, tools } = await loadCatalog(env, origin);
  const snapshot = snapshotProducts(selection, catalog, tools, origin);
  const result = await env.DB.prepare(
    'INSERT INTO ad_selection_batches (id, created_at, request_json, products_json) VALUES (?, ?, ?, ?) ON CONFLICT(id) DO NOTHING',
  ).bind(id, new Date().toISOString(), requestJson, JSON.stringify(snapshot)).run();
  const saved = await readBatch(env.DB, id);
  return json({ batch: sameSubmission(saved, requestJson) }, result.meta?.changes ? 201 : 200);
}

async function listBatches(db, parameters) {
  const status = parameters.get('status');
  const cursor = parameters.get('cursor');
  const cursorMatch = cursor && /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z)~(.+)$/.exec(cursor);
  if ((status && !queueStatuses.has(status)) || (cursor && (!cursorMatch || !batchIdPattern.test(cursorMatch[2])))) {
    throw new RequestError(400, '批次查詢條件有誤。');
  }
  const conditions = [];
  const values = [];
  if (status) {
    conditions.push('status = ?');
    values.push(status);
  }
  if (cursorMatch) {
    conditions.push('(created_at, id) < (?, ?)');
    values.push(cursorMatch[1], cursorMatch[2]);
  }
  const where = conditions.length ? ` WHERE ${conditions.join(' AND ')}` : '';
  const statement = db.prepare(
    `SELECT * FROM ad_selection_batches${where} ORDER BY created_at DESC, id DESC LIMIT 26`,
  );
  const { results } = await (values.length ? statement.bind(...values) : statement).all();
  const rows = results.slice(0, 25);
  const last = rows.at(-1);
  const nextCursor = results.length > rows.length ? `${last.created_at}~${last.id}` : null;
  return json({ batches: rows.map(publicBatch), nextCursor });
}

async function reviewBatch(request, db, id) {
  const input = await readJson(request);
  if (!record(input) || Object.keys(input).length !== 2 ||
      input.status !== 'reviewed' || !Array.isArray(input.recommendedProductIds) ||
      input.recommendedProductIds.length > maxProducts) {
    throw new RequestError(400, '請提供這批的審查結果和建議商品。');
  }
  const row = await readBatch(db, id);
  if (!row) throw new RequestError(404, '找不到這批選品。');
  const allIds = new Set(JSON.parse(row.products_json).map((product) => product.id));
  const uniqueIds = new Set(input.recommendedProductIds);
  if (uniqueIds.size !== input.recommendedProductIds.length || [...uniqueIds].some((id) => !allIds.has(id))) {
    throw new RequestError(400, '建議清單只能包含這批已選的商品。');
  }
  const approved = JSON.stringify([...uniqueIds].sort());
  await db.prepare(
    "UPDATE ad_selection_batches SET status = 'reviewed', reviewed_at = ?, recommended_product_ids_json = ? WHERE id = ? AND status = 'pending'",
  ).bind(new Date().toISOString(), approved, id).run();
  const saved = await readBatch(db, id);
  if (saved.recommended_product_ids_json !== approved) {
    throw new RequestError(409, '這批已有不同的審查結果。');
  }
  return json({ batch: publicBatch(saved) });
}

export function createWorker(loadCatalog = loadAssetCatalog) {
  return {
    async fetch(request, env) {
      const url = new URL(request.url);
      if (url.pathname !== queuePath && !url.pathname.startsWith(queuePath + '/')) {
        if (!env.ASSETS) return new Response('Catalog assets unavailable', { status: 503 });
        return env.ASSETS.fetch(request);
      }
      if (!ownerSignedIn(request, env)) return json({ error: '請以網站擁有者帳號登入。' }, 401);
      if (!env.DB) return json({ error: '選品清單暫時無法使用。' }, 503);
      try {
        if (url.pathname === queuePath) {
          if (request.method === 'GET') return await listBatches(env.DB, url.searchParams);
          if (request.method === 'POST') {
            requireSameOrigin(request, url);
            return await createBatch(request, env, url.origin, loadCatalog);
          }
          return json({ error: '不支援的操作。' }, 405, { Allow: 'GET, POST' });
        }
        const [rawId, action, ...rest] = url.pathname.slice(queuePath.length + 1).split('/');
        const id = rawId.toLowerCase();
        if (!batchIdPattern.test(id) || rest.length || (action !== undefined && action !== 'download')) {
          throw new RequestError(404, '找不到這批選品。');
        }
        if (action === 'download') {
          if (request.method !== 'GET') return json({ error: '不支援的操作。' }, 405, { Allow: 'GET' });
          return await downloadBatch(env, id, url.origin);
        }
        if (request.method !== 'PATCH') return json({ error: '不支援的操作。' }, 405, { Allow: 'PATCH' });
        requireSameOrigin(request, url);
        return await reviewBatch(request, env.DB, id);
      } catch (error) {
        if (error instanceof RequestError || error instanceof AdSelectionZipError) {
          return json({ error: error.message }, error.status);
        }
        console.error('catalog_selection_request_failed', error?.name);
        return json({ error: '選品清單暫時無法使用。請稍後再試。' }, 503);
      }
    },
  };
}

export default createWorker();
