(() => {
  'use strict';

  const pageSize = 30;
  const all = 'all';
  const sourceKeys = ['legacy', 'no2'];
  const sourceNames = { legacy: '原採購表', no2: 'no2 採購表' };
  const tabPrefixes = new Map([
    ['W玩具', 'W'], ['W娃娃', 'WA'], ['G正版', 'G'], ['S生活用品', 'S'],
    ['F方小姐', 'F'], ['D吊飾', 'D'], ['富酷', 'FK'],
  ]);
  const numberFormat = new Intl.NumberFormat('zh-TW');
  const tabOrder = new Intl.Collator('zh-TW', { numeric: true });
  const activeMounts = new WeakMap();

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function normalize(value) {
    return typeof value === 'string' ? value.normalize('NFKC').trim().toLowerCase() : '';
  }

  function compact(value) {
    return value.replace(/[\s\p{P}\p{S}]/gu, '');
  }

  function indexProduct(product, sources, catalog) {
    const names = [product.name, catalog.get(product.catalogId)?.name].map((text) => compact(normalize(text))).filter(Boolean);
    const no = compact(normalize(product.no));
    const code = compact(normalize(product.code));
    const sku = compact(normalize(product.supplierSku));
    const codeParts = /^bgd([a-z]+)(\d+)$/.exec(code);
    const noParts = /^(?:no)?(\d+)$/.exec(no);
    const number = codeParts?.[2] || noParts?.[1];
    const prefix = codeParts?.[1] || tabPrefixes.get(product.tab)?.toLowerCase();
    const identifiers = [code, sku, no];
    if (number) {
      identifiers.push(number, 'no' + number);
      if (prefix) identifiers.push(prefix + number, 'bgd' + prefix + number);
    }
    const aliases = [...new Set(identifiers.filter(Boolean))];
    const context = [product.tab, product.source, sources[product.source]?.label, product.date]
      .map((value) => compact(normalize(value))).filter(Boolean);
    const parsedDate = Date.parse(product.date);
    return {
      product, names, code, sku, aliases, context,
      date: Number.isFinite(parsedDate) ? parsedDate : 0,
    };
  }

  function validProduct(product) {
    return product && sourceKeys.includes(product.source) &&
      normalize(product.no) && normalize(product.name) && normalize(product.tab) &&
      Number.isSafeInteger(product.sheetId) && product.sheetId >= 0 &&
      Number.isSafeInteger(product.row) && product.row > 0;
  }

  function matchScore(item, terms, phrase) {
    if (phrase) {
      if (item.code === phrase) return 550;
      if (item.sku === phrase) return 510;
      if (item.aliases.includes(phrase)) return 470;
      if (item.names.includes(phrase)) return 430;
    }
    let score = 0;
    for (const term of terms) {
      if (item.aliases.includes(term)) score += 50;
      else if (item.names.some((name) => name.startsWith(term))) score += 32;
      else if (item.names.some((name) => name.includes(term))) score += 20;
      else if (item.aliases.some((alias) => alias.includes(term))) score += 12;
      else if (item.context.some((value) => value.includes(term))) score += 5;
      else return -1;
    }
    if (!phrase) return score;
    if (item.names.some((name) => name.startsWith(phrase))) return score + 30;
    if (item.names.some((name) => name.includes(phrase))) return score + 15;
    return score;
  }

  function compareRecent(a, b) {
    return b.date - a.date ||
      b.product.source.localeCompare(a.product.source) ||
      b.product.row - a.product.row || tabOrder.compare(a.product.tab, b.product.tab);
  }

  function sheetUrl(source, product) {
    if (typeof source?.url !== 'string') return null;
    try {
      const url = new URL(source.url);
      if (url.origin !== 'https://docs.google.com' ||
          !/^\/spreadsheets\/d\/[\w-]+(?:\/|$)/u.test(url.pathname)) return null;
      url.hash = `gid=${product.sheetId}&range=B${product.row}`;
      return url.href;
    } catch {
      return null;
    }
  }

  function catalogUrl(id) {
    const url = new URL('/', location.origin);
    url.searchParams.set('p', id);
    return url.href;
  }

  function thumbnail(product) {
    if (typeof product?.image !== 'string') return null;
    try {
      const url = new URL(product.image, new URL('/', location.origin));
      if (url.origin !== location.origin || !url.pathname.startsWith('/images/')) return null;
      const picture = element('div', 'is-picture');
      const image = element('img');
      image.src = url.href;
      image.alt = '';
      image.loading = 'lazy';
      image.decoding = 'async';
      image.addEventListener('error', () => picture.remove(), { once: true });
      picture.append(image);
      return picture;
    } catch {
      return null;
    }
  }

  function addOption(select, value, label) {
    const option = element('option', '', label);
    option.value = value;
    select.append(option);
  }

  function field(label, input, className) {
    const wrap = element('label', className);
    input.setAttribute('aria-label', label);
    wrap.append(element('span', '', label), input);
    return wrap;
  }

  function actionLink(label, href, className) {
    const link = element('a', className, label);
    link.href = href;
    return link;
  }

  function resultCard(product, sources, catalog) {
    const entry = element('li', 'is-result');
    const summary = element('div', 'is-result-summary');
    const knownProduct = catalog.get(product.catalogId);
    const picture = thumbnail(knownProduct);
    if (picture) summary.append(picture);

    const info = element('div', 'is-result-info');
    const source = sources[product.source];
    const meta = element('p', 'is-result-meta');
    meta.append(
      element('span', 'is-source', source?.label || sourceNames[product.source]),
      element('span', 'is-tab', product.tab),
    );
    const title = element('h2', 'is-result-title', knownProduct?.name ?? product.name);
    title.tabIndex = -1;
    const facts = element('dl', 'is-result-facts');
    const values = [['NO', product.no], ['目錄編號', product.code], ['供應商貨號', product.supplierSku]];
    for (const [label, value] of values) {
      if (!normalize(value)) continue;
      const fact = element('div');
      fact.append(element('dt', '', label), element('dd', '', value));
      facts.append(fact);
    }
    info.append(meta, title, facts);
    summary.append(info);
    entry.append(summary);

    const actions = element('div', 'is-result-actions');
    const href = sheetUrl(source, product);
    if (href) {
      const link = actionLink('開啟試算表', href, 'is-action is-sheet-link');
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.setAttribute('aria-label', `在新分頁開啟 ${product.tab} ${product.no}`);
      actions.append(link);
    }
    if (knownProduct) {
      actions.append(actionLink('看商品頁', catalogUrl(knownProduct.id), 'is-action'));
    }
    if (actions.childElementCount) entry.append(actions);
    return entry;
  }

  window.mountInternalProductSearch = function mountInternalProductSearch(app, catalogProducts, { backHref } = {}) {
    if (!app) throw new TypeError('Internal search requires a mounting element');
    activeMounts.get(app)?.();

    const knownProducts = Array.isArray(catalogProducts) ? catalogProducts : [];
    const catalog = new Map(knownProducts.map((product) => [product.id, product]));
    const root = element('section', 'internal-search shell');
    root.setAttribute('aria-labelledby', 'internal-search-heading');
    const heading = element('h1', '', '玩具商品搜尋試用');
    heading.id = 'internal-search-heading';
    const updated = element('p', 'is-updated');
    updated.textContent = '玩具分類由新到舊前 10 款';
    const header = element('header', 'is-heading');
    header.append(heading, updated);

    const form = element('form', 'is-controls');
    form.setAttribute('role', 'search');
    form.setAttribute('aria-label', '搜尋採購表');
    const query = element('input', 'is-input');
    query.type = 'search';
    query.placeholder = '例如 W2267、遙控車或品名';
    query.autocomplete = 'off';
    query.spellcheck = false;
    query.maxLength = 160;
    const source = element('select', 'is-select');
    const tab = element('select', 'is-select');
    const controls = element('div', 'is-filters');
    const reset = element('button', 'is-action is-reset', '清除');
    reset.type = 'button';
    const sourceField = field('資料來源', source);
    sourceField.hidden = true;
    controls.append(sourceField, field('分頁', tab), reset);
    form.append(field('品名、編號或供應商貨號', query, 'is-query'), controls);
    const status = element('p', 'is-status');
    status.setAttribute('role', 'status');
    const results = element('ul', 'is-results');
    results.setAttribute('aria-label', '商品搜尋結果');
    const footer = element('div', 'is-footer');
    const more = element('button', 'is-action is-more', '載入更多');
    more.type = 'button';
    const retry = element('button', 'is-action is-retry', '重新載入');
    retry.type = 'button';
    footer.append(more, retry);
    if (backHref) root.append(actionLink('返回玩具分類', backHref, 'back'));
    root.append(header, form, status, results, footer);
    app.replaceChildren(root);

    let sources = {};
    let index = [];
    let matches = [];
    let shown = 0;
    let phase = 'loading';
    let composing = false;
    let loading;
    let disposed = false;

    function refreshTabs() {
      const selected = tab.value;
      const names = [...new Set(index.filter(({ product }) =>
        source.value === all || source.value === product.source).map(({ product }) => product.tab))];
      tab.replaceChildren();
      addOption(tab, all, '全部分頁');
      for (const name of names.sort(tabOrder.compare)) addOption(tab, name, name);
      tab.value = names.includes(selected) ? selected : all;
    }

    function refreshStatus() {
      more.hidden = phase !== 'ready' || shown >= matches.length;
      retry.hidden = phase === 'ready' || phase === 'loading';
      reset.disabled = !query.value && source.value === all && tab.value === all;
      if (phase === 'loading') status.textContent = '正在讀取採購表索引…';
      else if (phase === 'restricted') status.textContent = '請以有權限的帳號登入後再試。';
      else if (phase === 'error') status.textContent = '暫時無法讀取商品索引。';
      else if (!matches.length) status.textContent = '沒有符合的商品。';
      else status.textContent = `找到 ${numberFormat.format(matches.length)} 筆，顯示 ${numberFormat.format(shown)} 筆。`;
    }

    function showMore() {
      const items = matches.slice(shown, shown + pageSize);
      const cards = items.map(({ item }) => resultCard(item.product, sources, catalog));
      results.append(...cards);
      shown += cards.length;
      refreshStatus();
      return cards[0]?.querySelector('.is-result-title');
    }

    function search() {
      if (phase !== 'ready') {
        refreshStatus();
        return;
      }
      const normalized = normalize(query.value);
      const terms = [...new Set(normalized.split(/[\s,，、]+/u).map(compact).filter(Boolean))];
      const phrase = compact(normalized);
      matches = [];
      for (const item of index) {
        if (source.value !== all && item.product.source !== source.value) continue;
        if (tab.value !== all && item.product.tab !== tab.value) continue;
        const score = matchScore(item, terms, phrase);
        if (score >= 0) matches.push({ item, score });
      }
      if (terms.length) matches.sort((a, b) => b.score - a.score || compareRecent(a.item, b.item));
      shown = 0;
      results.replaceChildren();
      showMore();
    }

    async function load() {
      loading?.abort();
      const controller = new AbortController();
      loading = controller;
      phase = 'loading';
      source.disabled = true;
      tab.disabled = true;
      results.setAttribute('aria-busy', 'true');
      refreshStatus();

      try {
        const response = await fetch('/api/product-search', {
          credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        });
        if (disposed || controller.signal.aborted || app.firstElementChild !== root) return;
        if (response.status === 401 || response.status === 403) {
          phase = 'restricted';
          return;
        }
        if (!response.ok) throw new Error('Search index unavailable');
        const payload = await response.json();
        if (disposed || controller.signal.aborted || app.firstElementChild !== root) return;
        if (payload?.version !== 1 || !payload.sources || !Array.isArray(payload.products)) {
          throw new Error('Unexpected search index');
        }
        sources = payload.sources;
        index = payload.products.filter(validProduct)
          .map((product) => indexProduct(product, sources, catalog));
        if (payload.products.length && !index.length) throw new Error('Invalid search records');

        const usedSources = sourceKeys.filter((key) => index.some(({ product }) => product.source === key));
        source.replaceChildren();
        addOption(source, all, '全部來源');
        for (const key of usedSources) addOption(source, key, sources[key]?.label || sourceNames[key]);
        sourceField.hidden = usedSources.length < 2;
        controls.classList.toggle('is-single-source', sourceField.hidden);
        refreshTabs();
        source.disabled = false;
        tab.disabled = false;
        phase = 'ready';
        search();
      } catch {
        if (!controller.signal.aborted) phase = 'error';
      } finally {
        if (!disposed && !controller.signal.aborted && app.firstElementChild === root) {
          results.removeAttribute('aria-busy');
          refreshStatus();
        }
      }
    }

    addOption(source, all, '全部來源');
    addOption(tab, all, '全部分頁');
    query.addEventListener('compositionstart', () => { composing = true; });
    query.addEventListener('compositionend', () => { composing = false; search(); });
    query.addEventListener('input', () => { if (!composing) search(); });
    source.addEventListener('change', () => { refreshTabs(); search(); });
    tab.addEventListener('change', search);
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (!composing) results.querySelector('.is-result-title')?.focus();
    });
    reset.addEventListener('click', () => {
      query.value = '';
      source.value = all;
      tab.value = all;
      refreshTabs();
      search();
      query.focus();
    });
    more.addEventListener('click', () => showMore()?.focus());
    retry.addEventListener('click', load);

    const dispose = () => {
      if (disposed) return;
      disposed = true;
      loading?.abort();
      if (activeMounts.get(app) === dispose) activeMounts.delete(app);
      root.remove();
    };
    activeMounts.set(app, dispose);
    load();
    return dispose;
  };
})();
