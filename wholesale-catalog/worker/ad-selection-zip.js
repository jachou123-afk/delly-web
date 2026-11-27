const maxArchiveBytes = 32 * 1024 * 1024;
const maxProducts = 50;
const utf8Flag = 1 << 11;
const encoder = new TextEncoder();
const codePattern = /^BGD-[A-Z]{1,8}-[0-9]{1,12}$/;
const productIdPattern = /^p-[a-f0-9]{24}$/;
const imagePathPattern = /^\/images\/[a-z0-9][a-z0-9._-]*\.(avif|jpe?g|png|webp)$/i;
const crcTable = Uint32Array.from({ length: 256 }, (_, byte) => {
  let value = byte;
  for (let bit = 0; bit < 8; bit++) {
    value = (value >>> 1) ^ ((value & 1) ? 0xedb88320 : 0);
  }
  return value >>> 0;
});

export class AdSelectionZipError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function archiveTooLarge() {
  return new AdSelectionZipError(413, '這批圖文包超過 32 MB，請分批下載。');
}

function zipTimestamp(value) {
  const date = new Date(value);
  const year = date.getUTCFullYear();
  if (!Number.isInteger(year) || year < 1980 || year > 2107) {
    throw new AdSelectionZipError(409, '無法確認這批選品時間。');
  }
  return {
    date: ((year - 1980) << 9) | ((date.getUTCMonth() + 1) << 5) | date.getUTCDate(),
    time: (date.getUTCHours() << 11) | (date.getUTCMinutes() << 5) | (date.getUTCSeconds() >>> 1),
  };
}

class StoreZip {
  constructor(timestamp) {
    this.buffer = new Uint8Array(64 * 1024);
    this.length = 0;
    this.entries = [];
    this.timestamp = timestamp;
  }

  reserve(size) {
    const start = this.length;
    const end = start + size;
    if (end > maxArchiveBytes) throw archiveTooLarge();
    if (end > this.buffer.length) {
      const expanded = new Uint8Array(Math.min(maxArchiveBytes, Math.max(end, this.buffer.length * 2)));
      expanded.set(this.buffer.subarray(0, start));
      this.buffer = expanded;
    }
    this.length = end;
    return start;
  }

  view() {
    return new DataView(this.buffer.buffer);
  }

  put(bytes) {
    const start = this.reserve(bytes.length);
    this.buffer.set(bytes, start);
  }

  startEntry(filename) {
    const name = encoder.encode(filename);
    const offset = this.reserve(30 + name.length);
    const header = this.view();
    header.setUint32(offset, 0x04034b50, true);
    header.setUint16(offset + 4, 20, true);
    header.setUint16(offset + 6, utf8Flag, true);
    header.setUint16(offset + 10, this.timestamp.time, true);
    header.setUint16(offset + 12, this.timestamp.date, true);
    header.setUint16(offset + 26, name.length, true);
    this.buffer.set(name, offset + 30);
    return { name, offset, dataOffset: this.length, crc: 0xffffffff };
  }

  append(entry, bytes) {
    this.put(bytes);
    let crc = entry.crc;
    for (const byte of bytes) crc = (crc >>> 8) ^ crcTable[(crc ^ byte) & 0xff];
    entry.crc = crc;
  }

  finishEntry(entry) {
    entry.crc = (entry.crc ^ 0xffffffff) >>> 0;
    entry.size = this.length - entry.dataOffset;
    const header = this.view();
    header.setUint32(entry.offset + 14, entry.crc, true);
    header.setUint32(entry.offset + 18, entry.size, true);
    header.setUint32(entry.offset + 22, entry.size, true);
    this.entries.push(entry);
  }

  addText(filename, content) {
    const entry = this.startEntry(filename);
    this.append(entry, encoder.encode(content));
    this.finishEntry(entry);
  }

  async addImage(filename, response, format, code) {
    if (!response?.ok || !response.body) throw unavailableImage(code);
    const size = Number(response.headers.get('Content-Length'));
    if (size > maxArchiveBytes - this.length) {
      await response.body.cancel().catch(() => {});
      throw archiveTooLarge();
    }
    const entry = this.startEntry(filename);
    const reader = response.body.getReader();
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        this.append(entry, chunk.value);
      }
    } catch (error) {
      await reader.cancel().catch(() => {});
      if (error instanceof AdSelectionZipError) throw error;
      throw unavailableImage(code);
    } finally {
      reader.releaseLock();
    }
    const image = this.buffer.subarray(entry.dataOffset, this.length);
    if (!imageMatchesFormat(image, format)) throw unavailableImage(code);
    this.finishEntry(entry);
  }

  finish() {
    const directory = this.length;
    for (const { name, offset, crc, size } of this.entries) {
      const start = this.reserve(46 + name.length);
      const header = this.view();
      header.setUint32(start, 0x02014b50, true);
      header.setUint16(start + 4, 20, true);
      header.setUint16(start + 6, 20, true);
      header.setUint16(start + 8, utf8Flag, true);
      header.setUint16(start + 12, this.timestamp.time, true);
      header.setUint16(start + 14, this.timestamp.date, true);
      header.setUint32(start + 16, crc, true);
      header.setUint32(start + 20, size, true);
      header.setUint32(start + 24, size, true);
      header.setUint16(start + 28, name.length, true);
      header.setUint32(start + 42, offset, true);
      this.buffer.set(name, start + 46);
    }
    const end = this.reserve(22);
    const header = this.view();
    header.setUint32(end, 0x06054b50, true);
    header.setUint16(end + 8, this.entries.length, true);
    header.setUint16(end + 10, this.entries.length, true);
    header.setUint32(end + 12, end - directory, true);
    header.setUint32(end + 16, directory, true);
    return this.buffer.subarray(0, this.length);
  }
}

function unavailableImage(code) {
  return new AdSelectionZipError(409, `${code} 的圖片無法讀取或檔案格式不符，請確認後再下載。`);
}

function hasPrefix(bytes, prefix) {
  return prefix.every((byte, index) => bytes[index] === byte);
}

function hasTag(bytes, offset, tag) {
  for (let index = 0; index < tag.length; index++) {
    if (bytes[offset + index] !== tag.charCodeAt(index)) return false;
  }
  return true;
}

function imageMatchesFormat(bytes, format) {
  if (format === 'jpg' || format === 'jpeg') return hasPrefix(bytes, [0xff, 0xd8, 0xff]);
  if (format === 'png') return hasPrefix(bytes, [0x89, 0x50, 0x4e, 0x47, 13, 10, 26, 10]);
  if (format === 'webp') return hasTag(bytes, 0, 'RIFF') && hasTag(bytes, 8, 'WEBP');
  if (format !== 'avif' || bytes.length < 16 || !hasTag(bytes, 4, 'ftyp')) return false;
  if (hasTag(bytes, 8, 'avif') || hasTag(bytes, 8, 'avis')) return true;
  const headerSize = new DataView(bytes.buffer, bytes.byteOffset, 4).getUint32(0);
  for (let offset = 16; offset + 4 <= Math.min(headerSize, bytes.length, 512); offset += 4) {
    if (hasTag(bytes, offset, 'avif') || hasTag(bytes, offset, 'avis')) return true;
  }
  return false;
}

function imageReference(source, code) {
  try {
    const url = new URL(source);
    const match = imagePathPattern.exec(url.pathname);
    if (match && /^https?:$/.test(url.protocol) && !url.username && !url.password &&
        !url.search && !url.hash) {
      return { url: url.href, format: match[1].toLowerCase() };
    }
  } catch {
    // Stored image references must be absolute URLs.
  }
  throw unavailableImage(code);
}

function prepareEntries(batch) {
  if (!Array.isArray(batch.products) || !batch.products.length || batch.products.length > maxProducts) {
    throw new AdSelectionZipError(409, '這批選品沒有可下載的商品。');
  }
  const seen = new Set();
  return batch.products.map((product, index) => {
    const { id, code, name, margin, price, unit, pack, draft, image, images, omittedCoverCrops = 0 } = product ?? {};
    if (typeof code !== 'string' || !codePattern.test(code) || seen.has(code) ||
        typeof id !== 'string' || !productIdPattern.test(id) ||
        !Number.isInteger(margin) || margin < 10 || margin > 20 ||
        typeof name !== 'string' || !name.trim() || /[\r\n\0]/.test(name) ||
        !Number.isSafeInteger(price) || price <= 0 || !Number.isSafeInteger(pack) || pack <= 0 ||
        !Number.isSafeInteger(omittedCoverCrops) || omittedCoverCrops < 0 ||
        typeof unit !== 'string' || !/^[\p{L}\p{N}]{1,12}$/u.test(unit) ||
        typeof draft !== 'string' || !draft.trim() || draft.includes('\0') ||
        (images !== undefined && !Array.isArray(images))) {
      throw new AdSelectionZipError(409, '這批選品的商品或文案格式有誤。');
    }
    const lines = draft.split(/\r?\n/);
    if (!lines.includes(`售價${price}元/${unit}`) || !lines.includes(`裝箱 ${pack}${unit}/箱`)) {
      throw new AdSelectionZipError(409, `${code} 的文案與批次售價或箱入數不一致。`);
    }
    seen.add(code);
    const order = String(index + 1).padStart(3, '0');
    const directory = `${order}_${code}`;
    const references = [...new Set([image, ...(images ?? [])])];
    if (references.length > 20 || references.some((value) => typeof value !== 'string')) {
      throw unavailableImage(code);
    }
    return {
      id, code, name, margin, price, unit, pack, draft, directory, omittedCoverCrops,
      images: references.map((source, position) => {
        const { url, format } = imageReference(source, code);
        return { url, format, file: `圖片${String(position + 1).padStart(2, '0')}.${format}` };
      }),
    };
  });
}

/**
 * Build a UTF-8 ZIP32 archive from a stored batch. The caller authorizes each
 * URL and reads it through the Site asset binding.
 */
export async function makeAdSelectionZip(batch, readImage) {
  const products = prepareEntries(batch);
  const zip = new StoreZip(zipTimestamp(batch.createdAt));
  const omittedCoverCrops = products.reduce((total, product) => total + product.omittedCoverCrops, 0);
  const sequence = [
    `批次 ${batch.id}`,
    `商品 ${products.length} 款`,
    '供筆電 Agent 查重與挑款。實際發送依核准名單與指定聊天室。',
    '每款使用同一資料夾的圖片和文案。確認圖文都已出現在聊天室再接下一款。',
    ...(omittedCoverCrops ? [`自動省略首圖已呈現的裁切附圖 ${omittedCoverCrops} 張。`] : []),
    '',
    ...products.map(({ directory, name, price, unit, images }) =>
      `${directory} ${name}｜NT$${price}/${unit}｜圖片 ${images.length} 張`),
    '',
  ].join('\n');
  zip.addText('發送順序與說明.txt', sequence);
  zip.addText('manifest.json', JSON.stringify({
    batchId: batch.id,
    createdAt: batch.createdAt,
    status: batch.status,
    omittedCoverCrops,
    recommendedProductIds: batch.status === 'reviewed' ? (batch.recommendedProductIds ?? null) : null,
    products: products.map(({ id, code, name, margin, price, unit, pack, directory, images }) => ({
      id, code, name, margin, price, unit, pack, directory, images: images.map(({ file }) => file), draft: '文案.txt',
    })),
  }, null, 2) + '\n');
  for (const product of products) {
    zip.addText(`${product.directory}/文案.txt`, product.draft);
    for (const { url, format, file } of product.images) {
      let response;
      try {
        response = await readImage(url);
      } catch {
        throw unavailableImage(product.code);
      }
      await zip.addImage(`${product.directory}/${file}`, response, format, product.code);
    }
  }
  return zip.finish();
}
