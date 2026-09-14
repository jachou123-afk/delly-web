"""Append-only cloud records in dedicated worksheets, separate from quotes."""
import base64
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import uuid

import gspread
from PIL import Image

from dispatch_manager import DispatchError, now

BATCH_SHEET = "_發送批次"
IMAGE_SHEET = "_發送圖片"
EVIDENCE_SHEET = "_報價依據"
INTERNAL_SHEETS = {BATCH_SHEET, IMAGE_SHEET, EVIDENCE_SHEET}
HEADER = ["record_id", "entity_id", "parent", "part", "total", "sha256", "payload", "created_at"]
CHUNK_SIZE = 20000  # Also below 50k UTF-16 units for all-emoji content.
MAX_IMAGE_BYTES = 2 * 1024 * 1024


def serialize(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def encode_record(entity, value, parent="", record_id=None):
    text = serialize(value)
    checksum = hashlib.sha256(text.encode()).hexdigest()
    parts = [text[i:i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]
    record_id = record_id or uuid.uuid4().hex
    stamp = now()
    return [[record_id, entity, parent, str(i + 1), str(len(parts)), checksum, part, stamp]
            for i, part in enumerate(parts)]


def decode_records(rows):
    grouped = {}
    for row in rows:
        if not row or not any(row):
            continue
        if len(row) != 8:
            raise DispatchError("發送紀錄欄位不完整，請先檢查紀錄表")
        record, entity, parent, part, total, checksum, payload, stamp = row
        try:
            part, total = int(part), int(total)
        except (ValueError, TypeError) as exc:
            raise DispatchError("發送紀錄分段格式錯誤") from exc
        if not 1 <= part <= total <= 1000:
            raise DispatchError("發送紀錄分段數不合法")
        entry = grouped.setdefault(record, {"entity": entity, "parent": parent, "total": total,
                                            "checksum": checksum, "parts": {}, "at": stamp})
        if (entity, parent, total, checksum) != (entry["entity"], entry["parent"], entry["total"], entry["checksum"]):
            raise DispatchError("同一紀錄的分段互相矛盾")
        if part in entry["parts"] and payload != entry["parts"][part]:
            raise DispatchError("同一紀錄的分段內容衝突")
        entry["parts"][part] = payload
    records = []
    for record_id, entry in grouped.items():
        if len(entry["parts"]) != entry["total"]:
            raise DispatchError("雲端紀錄尚未寫入完整；請重新載入，不可直接重試送出")
        text = "".join(entry["parts"][i] for i in range(1, entry["total"] + 1))
        if hashlib.sha256(text.encode()).hexdigest() != entry["checksum"]:
            raise DispatchError("發送紀錄校驗失敗，請先核對雲端紀錄")
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise DispatchError("發送紀錄內容無法辨識") from exc
        records.append({"id": record_id, **{k: entry[k] for k in ("entity", "parent", "at")}, "value": value})
    return records


def validate_image(data, name):
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise DispatchError("每張圖片需為非空檔案且不超過 2 MB；保留原檔，不自動壓縮")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in ("JPEG", "PNG", "WEBP") or image.width * image.height > 25000000:
                raise DispatchError("請使用 JPG、PNG、WebP 靜態商品圖，且不超過 2500 萬像素")
            if getattr(image, "is_animated", False):
                raise DispatchError("請使用靜態商品圖")
            mime = Image.MIME[image.format]
            image.verify()
    except DispatchError:
        raise
    except Exception as exc:
        raise DispatchError("圖片損壞或格式不支援") from exc
    return {"sha256": hashlib.sha256(data).hexdigest(), "name": str(name), "mime": mime,
            "data": base64.b64encode(data).decode("ascii")}


def asset_bytes(asset):
    try:
        data = base64.b64decode(asset["data"], validate=True)
    except Exception as exc:
        raise DispatchError("雲端圖片內容損壞") from exc
    if len(data) > MAX_IMAGE_BYTES or hashlib.sha256(data).hexdigest() != asset["sha256"]:
        raise DispatchError("圖片校驗失敗，請重新核對")
    return data


class CloudDispatchStore:
    def __init__(self, spreadsheet):
        self.spreadsheet = spreadsheet
        self._worksheets = {}
        self._image_index = None
        self._evidence_index = None
        self._evidence_row_index = None

    def _sheet(self, title, create=False):
        if title in self._worksheets:
            return self._worksheets[title]
        try:
            ws = self.spreadsheet.worksheet(title)
        except gspread.exceptions.WorksheetNotFound:
            if not create:
                return None
            # Creation is lazy: browsing the page never creates a worksheet.
            ws = self.spreadsheet.add_worksheet(title=title, rows=100, cols=8)
            ws.update(values=[HEADER], range_name="A1:H1", value_input_option="RAW")
        if ws.row_values(1) != HEADER:
            raise DispatchError(f"{title} 欄位不符，停止寫入以保留原資料")
        self._worksheets[title] = ws
        return ws

    def list_batches(self):
        ws = self._sheet(BATCH_SHEET)
        if ws is None:
            return []
        records = decode_records(ws.get_all_values()[1:])
        latest = {}
        for record in records:
            entity = record["entity"]
            previous = latest.get(entity)
            if record["parent"] != (previous["_revision"] if previous else ""):
                raise DispatchError("同一批次有同時修改的衝突，請先核對雲端紀錄；不自動覆蓋")
            value = record["value"]
            if value.get("schema") != 1 or value.get("id") != entity:
                raise DispatchError("批次版本或識別碼不合法")
            latest[entity] = {**value, "_revision": record["id"]}
        return sorted(latest.values(), key=lambda b: b["created_at"], reverse=True)

    def save_batch(self, batch, expected_revision="", record_id=None):
        clean = {k: deepcopy(v) for k, v in batch.items() if not k.startswith("_")}
        record_id = record_id or uuid.uuid4().hex
        latest = {b["id"]: b for b in self.list_batches()}
        old = latest.get(batch["id"])
        if old and old["_revision"] == record_id:
            if serialize({k: v for k, v in old.items() if not k.startswith("_")}) != serialize(clean):
                raise DispatchError("儲存識別碼已被另一份內容使用")
            return old
        if (old["_revision"] if old else "") != expected_revision:
            raise DispatchError("批次已在其他視窗更新，請重新載入後再操作")
        ws = self._sheet(BATCH_SHEET, create=True)
        rows = encode_record(batch["id"], clean, expected_revision, record_id)
        try:
            ws.append_rows(rows, value_input_option="RAW", table_range="A:H")
        except Exception as exc:
            raise DispatchError("儲存結果待確認：請重新載入雲端紀錄，不可直接重複操作") from exc
        try:
            saved = next(b for b in self.list_batches() if b["id"] == batch["id"])
            if saved["_revision"] != record_id:
                raise DispatchError("寫後核對發現其他更新")
        except Exception as exc:
            raise DispatchError("紀錄可能已儲存，但寫後核對未完成；請重新載入") from exc
        return saved

    def _asset_rows(self, ws, asset_ids):
        # Read only the short index; never download all image payloads.
        if self._image_index is None:
            self._image_index = ws.get("A2:B")
        index = self._image_index
        ranges = []
        for number, row in enumerate(index, 2):
            if len(row) > 1 and row[1] in asset_ids:
                if ranges and ranges[-1][1] == number - 1:
                    ranges[-1][1] = number
                else:
                    ranges.append([number, number])
        if not ranges:
            return []
        result = []
        for offset in range(0, len(ranges), 50):
            result.extend(row for group in ws.batch_get([f"A{a}:H{b}" for a, b in ranges[offset:offset + 50]]) for row in group)
        return result

    def get_asset(self, asset_id):
        return self.get_assets([asset_id])[asset_id]

    def get_assets(self, asset_ids):
        wanted = set(asset_ids)
        if not wanted:
            return {}
        ws = self._sheet(IMAGE_SHEET)
        records = decode_records(self._asset_rows(ws, wanted)) if ws else []
        assets = {}
        for record in records:
            asset_id, asset = record["entity"], record["value"]
            if asset.get("sha256") != asset_id or (asset_id in assets and assets[asset_id]["data"] != asset["data"]):
                raise DispatchError("商品圖片內容衝突")
            asset_bytes(asset)
            assets[asset_id] = asset
        if set(assets) != wanted:
            raise DispatchError("找不到已保存的商品圖片")
        return assets

    def put_asset(self, data, name):
        asset = validate_image(data, name)
        ws = self._sheet(IMAGE_SHEET, create=True)
        if self._asset_rows(ws, {asset["sha256"]}):
            self.get_asset(asset["sha256"])
            return asset["sha256"]
        try:
            ws.append_rows(encode_record(asset["sha256"], asset), value_input_option="RAW", table_range="A:H")
        except Exception as exc:
            raise DispatchError("圖片儲存結果待確認，請重新載入後確認") from exc
        self._image_index = None
        self.get_asset(asset["sha256"])
        return asset["sha256"]

    def catalog(self):
        from dispatch_manager import catalog
        sheets = [ws for ws in self.spreadsheet.worksheets() if not ws.title.startswith("_")]
        products = catalog({ws.title: ws.get_all_values() for ws in sheets})
        sheet_ids = {ws.title: getattr(ws, "id", "") for ws in sheets}
        spreadsheet_id = getattr(self.spreadsheet, "id", "")
        for product in products:
            product["cost_audit_required"] = True
            product["source_url"] = (f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
                                     f"#gid={sheet_ids[product['category']]}&range=A{product['row']}:T{product['row'] + 5}"
                                     if spreadsheet_id else "")
        return products

    def _evidence(self, identity):
        self._load_evidence([identity])
        return self._evidence_index.get(identity)

    def _load_evidence(self, identities):
        if self._evidence_index is None:
            self._evidence_index = {}
        wanted = set(identities) - self._evidence_index.keys()
        if not wanted:
            return
        ws = self._sheet(EVIDENCE_SHEET)
        rows, ranges = [], []
        if ws:
            if self._evidence_row_index is None:
                self._evidence_row_index = ws.get("A2:B")
            for row_number, row in enumerate(self._evidence_row_index, 2):
                if len(row) > 1 and row[1] in wanted:
                    if ranges and ranges[-1][1] == row_number - 1:
                        ranges[-1][1] = row_number
                    else:
                        ranges.append([row_number, row_number])
            for offset in range(0, len(ranges), 20):
                rows.extend(row for group in ws.batch_get([f"A{a}:H{b}" for a, b in ranges[offset:offset + 20]]) for row in group)
        found = dict.fromkeys(wanted)
        for record in decode_records(rows):
            value = record["value"]
            if value.get("schema") != 1 or value.get("identity") != record["entity"]:
                raise DispatchError("報價依據識別碼或版本不符，停止驗算")
            found[record["entity"]] = value
        self._evidence_index.update(found)

    def read_cost_source(self, source):
        from cost_audit import block, fingerprint
        ws = self.spreadsheet.worksheet(source["category"])
        area = f"A{source['row']}:L{source['row'] + 5}"
        values = block(ws.get(area, value_render_option="FORMATTED_VALUE"))
        if fingerprint(values) != source["source_hash"]:
            raise DispatchError("原表已變動，請重新載入雲端再驗算")
        return block(ws.get(area, value_render_option="FORMULA"))

    def cost_audit(self, source):
        from cost_audit import audit
        formulas = self.read_cost_source(source)
        return audit(source, formulas, self._evidence(source["identity"])), formulas

    def put_quote_evidence(self, source, formulas, raw_source, inputs, *, notes, origin, parsed=None):
        from cost_audit import block, fingerprint, make_evidence
        evidence = make_evidence(source, formulas, raw_source, inputs, notes=notes, origin=origin, parsed=parsed)
        # The source and its formulas must still be exactly the inspected version.
        if fingerprint(self.read_cost_source(source)) != fingerprint(block(formulas)):
            raise DispatchError("原公式已變更，本次依據未保存，請重新載入")
        if self._evidence(source["identity"]) == evidence:
            return evidence
        ws = self._sheet(EVIDENCE_SHEET, create=True)
        try:
            ws.append_rows(encode_record(source["identity"], evidence), value_input_option="RAW", table_range="A:H")
        except Exception as exc:
            self._evidence_index = None
            self._evidence_row_index = None
            raise DispatchError("報價依據保存結果待確認；請重新載入，不要重複新增商品") from exc
        self._evidence_index = None
        self._evidence_row_index = None
        if self._evidence(source["identity"]) != evidence:
            raise DispatchError("報價依據寫後核對未完成；請重新載入，不要重複新增商品")
        return evidence

    def verify_cost_checks(self, batch):
        """Re-read all active formula blocks in bounded requests before approval."""
        from collections import defaultdict
        from cost_audit import audit, blockers, fingerprint
        groups = defaultdict(list)
        self._evidence_index = None
        self._evidence_row_index = None
        for item in batch["items"]:
            if not item["excluded"] and item["source"].get("cost_audit_required"):
                groups[item["source"]["category"]].append(item)
        self._load_evidence([i["id"] for items in groups.values() for i in items])
        for category, items in groups.items():
            ws = self.spreadsheet.worksheet(category)
            for offset in range(0, len(items), 20):
                chosen = items[offset:offset + 20]
                ranges = [f"A{i['source']['row']}:L{i['source']['row'] + 5}" for i in chosen]
                blocks = ws.batch_get(ranges, value_render_option="FORMULA")
                if len(blocks) != len(chosen):
                    raise DispatchError("成本公式讀取不完整，停止確認")
                for item, formulas in zip(chosen, blocks):
                    report = audit(item["source"], formulas, self._evidence(item["id"]))
                    if blockers(item["source"], report) or fingerprint(report) != fingerprint(item.get("cost_audit")):
                        raise DispatchError(f"{item['source']['code']}：成本公式或原始依據已變更／尚未核對，請重新驗算")

    def source_images(self, products):
        from dispatch_images import extract_sheet_images
        from gspread.utils import ExportFormat
        if hasattr(self.spreadsheet, "client"):
            self.spreadsheet.client.set_timeout((10, 45))
        return extract_sheet_images(self.spreadsheet.export(ExportFormat.EXCEL), products)
