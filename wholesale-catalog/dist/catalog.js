(() => {
  'use strict';

  const products = window.catalogProducts;
  const app = document.querySelector('#app');
  const notice = document.querySelector('#notice');
  const inquiry = document.querySelector('#inquiry');
  const inquiryMessage = document.querySelector('#inquiry-message');
  const inquiryStatus = document.querySelector('#inquiry-status');
  const productId = new URLSearchParams(location.search).get('p');
  const latestDate = products.map((product) => product.date).sort().at(-1);
  const ordered = [...products].sort((a, b) => b.date.localeCompare(a.date));
  const categories = [...new Set(ordered.map((product) => product.category))];
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

  function price(product) {
    const priceNode = element('p', 'price', 'NT$' + product.price + ' ');
    priceNode.append(element('small', '', '／' + product.unit));
    return priceNode;
  }

  function cartonText(product) {
    return product.pack + ' ' + product.unit + '／箱';
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

  function card(product) {
    const article = element('article', 'product-card');
    const link = element('a');
    link.href = productUrl(product);
    const body = element('div', 'card-body');
    const meta = element('p', 'card-meta');
    meta.append(element('span', 'product-code', product.code), element('span', '', product.category));
    const values = element('div', 'card-bottom');
    values.append(price(product), element('p', 'pack-line', cartonText(product)));
    body.append(meta, element('h2', '', product.name), values);
    link.append(picture(product, false), body);
    article.append(link);
    return article;
  }

  function searchKey(value) {
    return value.normalize('NFKC').toLowerCase().replace(/[\s-]+/g, '');
  }

  function displayDate(date) {
    const [year, month, day] = date.split('-').map(Number);
    return year + ' 年 ' + month + ' 月 ' + day + ' 日';
  }

  function home() {
    const hero = element('section', 'hero');
    const heading = element('div');
    if (latestDate) {
      const date = element('time', 'hero-date', displayDate(latestDate));
      date.dateTime = latestDate;
      heading.append(date);
    }
    heading.append(element('h1', '', '今日新品'));
    if (latestDate) heading.append(element('p', '', '查看最新批發售價與箱入數'));
    const heroInner = element('div', 'hero-inner shell');
    heroInner.append(heading);
    hero.append(heroInner);

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
    const grid = element('div', 'product-grid');
    const empty = element('p', 'empty');
    empty.hidden = true;
    catalog.append(controls, summary, grid, empty);

    let selectedCategory = '';
    function renderResults() {
      const query = searchKey(input.value);
      const matching = ordered.filter((product) =>
        (!selectedCategory || product.category === selectedCategory) &&
        (!query || searchKey(product.code + product.name).includes(query))
      );
      grid.replaceChildren(...matching.map(card));
      summary.textContent = matching.length + ' 件商品';
      empty.hidden = matching.length > 0;
      empty.textContent = ordered.length ? '找不到符合的商品，試試其他關鍵字或分類。' : '目前沒有可試看的新品。';
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

  function inquiryText(product) {
    const lines = ['您好，我想詢問「' + product.name + '」（' + product.code + '）的供貨狀況，謝謝。'];
    if (location.protocol !== 'file:') lines.push('商品連結 ' + productUrl(product));
    return lines.join('\n');
  }

  function handleInquiry(product) {
    inquiryMessage.value = inquiryText(product);
    inquiryStatus.hidden = true;
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

  document.querySelector('#copy-inquiry').addEventListener('click', async () => {
    const copied = await copy(inquiryMessage.value);
    inquiryStatus.textContent = copied ? '已複製。' : '已選取文字，可長按複製。';
    inquiryStatus.hidden = false;
  });

  function detail(product) {
    const section = element('section', 'detail shell');
    const back = element('a', 'back', '返回今日新品');
    back.href = catalogUrl().toString();
    section.append(back);
    if (!product) {
      section.append(element('h1', '', '找不到這項商品'));
      app.replaceChildren(section);
      return;
    }
    document.title = product.name + '｜玩具批發新品';
    const layout = element('article', 'detail-card');
    const info = element('div', 'detail-info');
    info.append(element('p', 'card-meta', product.category), element('h1', '', product.name), price(product));
    const facts = element('dl', 'facts');
    for (const [label, value] of [
      ['商品編號', product.code],
      ['計價單位', product.unit],
      ['箱入數', cartonText(product)],
      ['供貨狀態', '供貨請詢問'],
    ]) {
      const item = element('div');
      item.append(element('dt', '', label), element('dd', '', value));
      facts.append(item);
    }
    const actions = element('div', 'actions');
    const line = element('button', 'button primary', 'LINE 詢價');
    line.type = 'button';
    line.addEventListener('click', () => handleInquiry(product));
    const share = element('button', 'button', '分享商品');
    share.type = 'button';
    share.addEventListener('click', () => handleShare(product, share));
    actions.append(line, share);
    info.append(facts, actions);
    layout.append(picture(product, true), info);
    section.append(layout);
    app.replaceChildren(section);
  }

  if (productId) {
    detail(products.find((product) => product.id === productId));
  } else {
    home();
  }
})();
