(() => {
  'use strict';

  const endpoint = './api/ad-selections';
  const pickedKey = 'catalog-ad-picks-v1';
  const requestKey = 'catalog-ad-request-v1';
  const limit = 50;

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function read(key) {
    try { return JSON.parse(sessionStorage.getItem(key)); }
    catch { return null; }
  }

  function save(key, value) {
    try { sessionStorage.setItem(key, JSON.stringify(value)); }
    catch { /* The current view still holds the selection. */ }
  }

  const dateLabel = new Intl.DateTimeFormat('zh-TW', {
    timeZone: 'Asia/Taipei', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });

  window.createCatalogSelections = (products, marginFor) => {
    const byId = new Map(products.map((product) => [product.id, product]));
    const stored = read(pickedKey);
    const picked = new Set(Array.isArray(stored) ? stored.filter((id) => byId.has(id)).slice(0, limit) : []);
    let request = read(requestKey);
    let sending = false;

    const controls = node('section', 'ad-selection-controls');
    controls.setAttribute('aria-label', 'LINE 廣告選品');
    const count = node('p', 'ad-selection-count');
    count.id = 'ad-selection-count';
    count.setAttribute('role', 'status');
    const actions = node('div', 'ad-selection-actions');
    const submit = node('button', 'button primary', '送出待審');
    const showQueue = node('button', 'button', '查看待審批次');
    submit.type = showQueue.type = 'button';
    const status = node('p', 'ad-selection-status');
    status.setAttribute('role', 'status');
    status.hidden = true;
    actions.append(submit, showQueue);
    controls.append(count, actions, status);

    function setStatus(text) {
      status.textContent = text;
      status.hidden = !text;
    }

    function refresh() {
      count.textContent = '已選 ' + picked.size + ' 款';
      submit.disabled = sending || !picked.size;
      submit.textContent = sending ? '送出中…' : '送出待審';
      for (const input of document.querySelectorAll('.ad-pick input')) {
        input.checked = picked.has(input.value);
        input.disabled = sending;
      }
    }

    function checkbox(product) {
      const label = node('label', 'ad-pick');
      const input = node('input');
      input.type = 'checkbox';
      input.value = product.id;
      input.checked = picked.has(product.id);
      input.disabled = sending;
      input.setAttribute('aria-label', '勾選 ' + product.code);
      input.addEventListener('change', () => {
        if (input.checked && picked.size >= limit) {
          input.checked = false;
          setStatus('單批最多勾選 ' + limit + ' 款。');
          return;
        }
        if (input.checked) picked.add(product.id);
        else picked.delete(product.id);
        save(pickedKey, [...picked]);
        setStatus('');
        refresh();
      });
      label.append(input);
      return label;
    }

    submit.addEventListener('click', async () => {
      if (sending || !picked.size) return;
      const products = Array.from(picked, (id) => ({ id, margin: marginFor(byId.get(id)) }));
      const signature = JSON.stringify(products);
      if (!request || request.signature !== signature || typeof request.id !== 'string') {
        request = { id: crypto.randomUUID(), signature };
        save(requestKey, request);
      }
      sending = true;
      refresh();
      setStatus('');
      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ requestId: request.id, products }),
        });
        if (!response.ok) throw new Error('送出失敗');
        const { batch } = await response.json();
        if (!batch?.id || batch.products?.length !== products.length) throw new Error('無法確認批次');
        picked.clear();
        request = null;
        save(pickedKey, []);
        save(requestKey, null);
        setStatus('已送出 ' + products.length + ' 款，等待筆電 Agent 判斷。可在「查看待審批次」確認。');
      } catch {
        setStatus('送出時發生問題，勾選已保留，請再試一次。');
      } finally {
        sending = false;
        refresh();
      }
    });

    showQueue.addEventListener('click', async () => {
      const dialog = document.querySelector('#ad-selection-queue');
      const content = document.querySelector('#ad-selection-queue-items');
      content.textContent = '載入中…';
      dialog.showModal();
      try {
        const response = await fetch(endpoint + '?status=pending', { cache: 'no-store' });
        if (!response.ok) throw new Error('無法讀取批次');
        const { batches } = await response.json();
        if (!batches.length) {
          content.textContent = '目前沒有待審批次。';
          return;
        }
        const entries = batches.map((batch) => {
          const article = node('article', 'ad-selection-batch');
          const date = dateLabel.format(new Date(batch.createdAt));
          article.append(node('h3', '', date + ' · ' + batch.products.length + ' 款'));
          const list = node('ul');
          for (const product of batch.products) list.append(node('li', '', product.code + ' ' + product.name));
          article.append(list);
          return article;
        });
        content.replaceChildren(...entries);
      } catch {
        content.textContent = '暫時無法讀取待審批次，請重新開啟。';
      }
    });

    refresh();
    return { controls, checkbox };
  };
})();
