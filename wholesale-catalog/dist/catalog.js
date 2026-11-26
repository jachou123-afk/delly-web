(() => {
  'use strict';

  const products = window.catalogProducts;
  const app = document.querySelector('#app');
  const notice = document.querySelector('#notice');
  const inquiry = document.querySelector('#inquiry');
  const inquiryCartons = document.querySelector('#inquiry-cartons');
  const inquiryMessage = document.querySelector('#inquiry-message');
  const inquiryStatus = document.querySelector('#inquiry-status');
  const productId = new URLSearchParams(location.search).get('p');
  const latestDate = products.map((product) => product.date).sort().at(-1);
  const ordered = [...products].sort((a, b) => b.date.localeCompare(a.date));
  const categories = ['正版帶雷標', '玩具', '生活用品'];
  const quoteLabels = ['批發單價', '箱入數', '整箱估算'];
  const formatAmount = new Intl.NumberFormat('zh-TW');
  let inquiryProduct;
  let noticeTimer;

  function element(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = value;
    return node;
  }

  function catalogUrl() {
    const url = new URL(location.href);
    url.search = '';
    url.hash = '';
    return url;
  }

  function productUrl(product) {
    const url = catalogUrl();
    url.searchParams.set('p', product.id);
    return url.toString();
  }

  document.querySelector('#home-link').href = catalogUrl().toString();

  function cartonText(product) {
    return product.pack + ' ' + product.unit + '／箱';
  }

  function quoteValues(product) {
    const cartonAmount = BigInt(product.price.replaceAll(',', '')) * BigInt(product.pack);
    const values = [
      ['NT$' + product.price, '／' + product.unit],
      [cartonText(product)],
      ['NT$' + formatAmount.format(cartonAmount), '／箱'],
    ];
    const list = element('dl', 'quote-values');
    for (const [index, [amount, unit]] of values.entries()) {
      const item = element('div', 'quote-metric');
      const value = element('dd', '', amount);
      if (unit) value.append(element('small', '', unit));
      item.append(element('dt', '', quoteLabels[index]), value);
      list.append(item);
    }
    return list;
  }

  function picture(product, priority) {
    const media = element('div', 'product-picture');
    const img = element('img');
    img.alt = product.name;
    img.loading = priority ? 'eager' : 'lazy';
    img.decoding = 'async';
    if (priority) img.fetchPriority = 'high';
    img.src = product.image;
    media.append(img);
    return media;
  }

  function productGallery(product) {
    const main = picture(product, true);
    if (!product.galleryImages?.length) return main;

    const gallery = element('div', 'product-gallery');
    const thumbnails = element('div', 'gallery-thumbnails');
    thumbnails.setAttribute('role', 'group');
    thumbnails.setAttribute('aria-label', '切換商品照片');
    const displayedImage = main.querySelector('img');

    for (const [index, src] of [product.image, ...product.galleryImages].entries()) {
      const thumbnail = element('button', 'gallery-thumbnail');
      thumbnail.type = 'button';
      thumbnail.setAttribute('aria-label', `查看第 ${index + 1} 張照片`);
      thumbnail.setAttribute('aria-pressed', String(index === 0));
      const preview = element('img');
      preview.alt = '';
      preview.loading = 'lazy';
      preview.decoding = 'async';
      preview.src = src;
      thumbnail.append(preview);
      thumbnail.addEventListener('click', () => {
        displayedImage.src = src;
        displayedImage.alt = `${product.name}（第 ${index + 1} 張照片）`;
        for (const button of thumbnails.children) {
          button.setAttribute('aria-pressed', String(button === thumbnail));
        }
      });
      thumbnails.append(thumbnail);
    }

    gallery.append(main, thumbnails);
    return gallery;
  }

  function quoteRow(product) {
    const row = element('li', 'quote-row');
    const link = element('a');
    link.href = productUrl(product);
    const item = element('div', 'quote-item');
    const identity = element('div', 'quote-identity');
    const meta = element('p', 'card-meta');
    meta.append(element('span', 'product-code', product.code), element('span', '', product.category));
    identity.append(meta, element('h2', '', product.name));
    const photo = picture(product, false);
    photo.querySelector('img').alt = '';
    item.append(photo, identity);
    link.append(item, quoteValues(product));
    row.append(link);
    return row;
  }

  function searchKey(value) {
    return value.normalize('NFKC').toLowerCase().replace(/[\s-]+/g, '');
  }

  function displayDate(date) {
    const [year, month, day] = date.split('-').map(Number);
    return year + ' 年 ' + month + ' 月 ' + day + ' 日';
  }

  function home() {
    const hero = element('section', 'catalog-heading shell');
    hero.append(element('h1', '', '新品批發目錄'));
    if (latestDate) {
      const updated = element('p', 'catalog-date', '最新商品批次 ');
      const date = element('time', '', displayDate(latestDate));
      date.dateTime = latestDate;
      updated.append(date);
      hero.append(updated);
    }

    const catalog = element('section', 'catalog shell');
    catalog.setAttribute('aria-label', '搜尋和分類');
    const controls = element('div', 'controls');
    const search = element('label', 'search-wrap');
    const searchIcon = element('span', 'search-icon');
    searchIcon.setAttribute('aria-hidden', 'true');
    const input = element('input');
    input.type = 'search';
    input.placeholder = '搜尋品名或商品編號';
    input.setAttribute('aria-label', '搜尋品名或商品編號');
    search.append(searchIcon, input);
    const filters = element('div', 'filters');
    filters.setAttribute('aria-label', '商品分類');
    controls.append(search, filters);
    const summary = element('p', 'results-label');
    summary.setAttribute('role', 'status');
    const listInfo = element('div', 'list-info');
    listInfo.append(summary, element('p', 'list-hint', '點選商品查看規格、照片與詢價'));
    const sheet = element('div', 'quote-sheet');
    const columns = element('div', 'quote-column-header');
    columns.setAttribute('aria-hidden', 'true');
    const columnMetrics = element('div', 'quote-values');
    columnMetrics.append(...quoteLabels.map(label => element('span', '', label)));
    columns.append(element('span', '', '商品／編號'), columnMetrics);
    const list = element('ul', 'quote-list');
    list.setAttribute('role', 'list');
    sheet.append(columns, list);
    const empty = element('p', 'empty');
    empty.hidden = true;
    catalog.append(controls, listInfo, sheet, empty);
    if (latestDate) catalog.append(element('p', 'quote-note', '整箱估算＝批發單價 × 箱入數。實際報價請用 LINE 確認。'));

    let selectedCategory = '';
    function renderResults() {
      const query = searchKey(input.value);
      const matching = ordered.filter((product) =>
        (!selectedCategory || product.category === selectedCategory) &&
        (!query || searchKey(product.code + product.name).includes(query))
      );
      list.replaceChildren(...matching.map(quoteRow));
      summary.textContent = '共 ' + matching.length + ' 款商品';
      sheet.hidden = matching.length === 0;
      empty.hidden = matching.length > 0;
      empty.textContent = ordered.length ? '找不到符合的商品，試試其他關鍵字或分類。' : '目前沒有新品。';
    }

    for (const category of ['', ...categories]) {
      const button = element('button', 'filter', category || '全部');
      button.type = 'button';
      button.setAttribute('aria-pressed', String(!category));
      button.addEventListener('click', () => {
        selectedCategory = category;
        for (const item of filters.children) item.setAttribute('aria-pressed', String(item === button));
        renderResults();
      });
      filters.append(button);
    }
    input.addEventListener('input', renderResults);
    app.replaceChildren(hero, catalog);
    renderResults();
  }

  function showNotice(message) {
    notice.textContent = message;
    notice.hidden = false;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { notice.hidden = true; }, 3200);
  }

  async function copy(value) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      const field = inquiry.open ? inquiryMessage : element('textarea');
      if (!inquiry.open) {
        field.value = value;
        field.className = 'notice';
        document.body.append(field);
      }
      field.focus();
      field.select();
      field.setSelectionRange(0, value.length);
      const copied = document.execCommand('copy');
      if (field !== inquiryMessage) field.remove();
      return copied;
    }
  }

  function inquiryText(product, cartons) {
    const lines = [
      '您好，我想詢問「' + product.name + '」（' + product.code + '）的供貨與批發報價，謝謝。',
      '箱入數 ' + cartonText(product),
    ];
    if (cartons) lines.push('預計採購 ' + cartons + ' 箱');
    if (location.protocol !== 'file:') lines.push('商品連結 ' + productUrl(product));
    return lines.join('\n');
  }

  function refreshInquiry() {
    const cartons = /^[1-9]\d*$/.test(inquiryCartons.value) ? inquiryCartons.value : '';
    inquiryMessage.value = inquiryText(inquiryProduct, cartons);
    inquiryStatus.hidden = true;
  }

  function handleInquiry(product) {
    inquiryProduct = product;
    inquiryCartons.value = '';
    refreshInquiry();
    inquiry.showModal();
  }

  async function handleShare(product, button) {
    const url = productUrl(product);
    if (location.protocol === 'file:') {
      const copied = await copy(product.code);
      showNotice(copied ? '已複製商品編號 ' + product.code : '商品編號 ' + product.code);
      button.focus();
      return;
    }
    if (navigator.share) {
      try {
        await navigator.share({ title: product.name, url });
        return;
      } catch (error) {
        if (error.name === 'AbortError') return;
      }
    }
    const copied = await copy(url);
    if (copied) {
      showNotice('已複製商品連結');
    } else {
      window.prompt('複製這個商品連結', url);
    }
    button.focus();
  }

  inquiryCartons.addEventListener('input', refreshInquiry);
  document.querySelector('#copy-inquiry').addEventListener('click', async () => {
    if (!inquiryCartons.reportValidity()) return;
    const copied = await copy(inquiryMessage.value);
    inquiryStatus.textContent = copied ? '已複製。' : '已選取文字，可長按複製。';
    inquiryStatus.hidden = false;
  });

  function factList(entries) {
    const list = element('dl', 'facts');
    for (const [label, value] of entries) {
      const item = element('div');
      item.append(element('dt', '', label), element('dd', '', value));
      list.append(item);
    }
    return list;
  }

  function detail(product) {
    const section = element('section', 'detail shell');
    const back = element('a', 'back', '返回批發目錄');
    back.href = catalogUrl().toString();
    section.append(back);
    if (!product) {
      section.append(element('h1', '', '找不到這項商品'));
      app.replaceChildren(section);
      return;
    }
    document.title = product.name + '｜大陸工廠直發';
    const layout = element('article', 'detail-card');
    const info = element('div', 'detail-info');
    const quote = element('div', 'detail-quote');
    quote.append(quoteValues(product), element('p', 'quote-note', '整箱估算＝批發單價 × 箱入數。'));
    info.append(element('p', 'card-meta', product.category), element('h1', '', product.name), quote);
    const entries = (product.specs ?? []).map(({ label, value }) => [label, value]);
    entries.push(
      ['計價單位', product.unit],
      ['商品編號', product.code],
    );
    info.append(element('h2', '', '商品資訊'), factList(entries));
    layout.append(productGallery(product), info);
    section.append(layout);
    const actions = element('div', 'actions detail-actions');
    const line = element('button', 'button primary', 'LINE 詢價');
    line.type = 'button';
    line.addEventListener('click', () => handleInquiry(product));
    const share = element('button', 'button', '分享商品');
    share.type = 'button';
    share.addEventListener('click', () => handleShare(product, share));
    actions.append(line, share);
    section.append(actions);
    app.replaceChildren(section);
  }

  if (productId) {
    detail(products.find((product) => product.id === productId));
  } else {
    home();
  }
})();
