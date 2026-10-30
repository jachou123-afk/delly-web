"""Fresh batched source guards for native Google existing-only NAS batches.

One call is one fresh guard: metadata, hinted six-row FORMULA, then open-ended
formatted catalog/category/binding. The fresh actual row must match the hint.
No values survive into another guard and
no network error changes to a legacy or non-batched path.
"""
from copy import deepcopy
import math
import re

from gspread.spreadsheet import Spreadsheet
from gspread.worksheet import Worksheet

from category_codes import CATEGORY_SHEET
from category_storage import CategoryCodeStoreMixin
from cost_audit import block
from dispatch_manager import DispatchError, catalog, digest
from dispatch_storage import HEADER
from nas_dispatch_storage import NasDispatchStore
from product_image_storage import ProductImageStoreMixin
from product_images import PRODUCT_IMAGE_SHEET
from quote_image_repair import _snapshot_digest


MAX_RANGES = 100
BAD_RESPONSE = "批次補圖來源回應範圍或格式不一致；未使用部分資料"
_RANGE = re.compile(r"(?:'((?:[^']|'')*)'|([^'!]+))!([A-Z]+)([1-9][0-9]*):([A-Z]+)([1-9][0-9]*)")


def supports_batched_existing_snapshot(store):
    # Native paced stores keep native Spreadsheet/Worksheet and the exact auth
    # HTTP client. Other adapters retain the established explicit caller path.
    return type(store) is NasDispatchStore and type(store.spreadsheet) is Spreadsheet


def _fail():
    raise DispatchError(BAD_RESPONSE)


def _a1(title, start, end, rows):
    return "'" + title.replace("'", "''") + "'!" + start + "1:" + end + str(rows)


def _range_key(value):
    if not isinstance(value, str):
        _fail()
    match = _RANGE.fullmatch(value)
    if not match:
        _fail()
    quoted, plain, start_col, start_row, end_col, end_row = match.groups()
    return (quoted.replace("''", "'") if quoted is not None else plain,
            start_col, int(start_row), end_col, int(end_row))


def _column(number):
    result = ""
    while number:
        number, remainder = divmod(number-1, 26)
        result = chr(65+remainder)+result
    return result


def _column_number(value):
    result = 0
    for char in value:
        result = result*26+ord(char)-64
    return result


def _read_ranges(spreadsheet, ranges, *, render, open_grids=None):
    """Match exact returned ranges, not positional order; malformed is fatal."""
    expected = ({title: spec["range"] for title, spec in open_grids.items()}
                if open_grids is not None else {_range_key(area): area for area in ranges})
    if len(expected) != len(ranges) or not 1 <= len(ranges) <= MAX_RANGES:
        _fail()
    response = spreadsheet.values_batch_get(ranges, params={
        "valueRenderOption": render, "majorDimension": "ROWS",
        **({"dateTimeRenderOption": "SERIAL_NUMBER"} if render == "FORMULA" else {}),
    })
    if (not isinstance(response, dict) or set(response) != {"spreadsheetId", "valueRanges"}
            or response["spreadsheetId"] != spreadsheet.id
            or not isinstance(response["valueRanges"], list)
            or len(response["valueRanges"]) != len(ranges)):
        _fail()
    result = {}
    for value in response["valueRanges"]:
        if (not isinstance(value, dict) or not {"range", "majorDimension"} <= set(value)
                or set(value) - {"range", "majorDimension", "values"}
                or value["majorDimension"] != "ROWS"):
            _fail()
        key = _range_key(value["range"])
        if open_grids is not None:
            spec = open_grids.get(key[0])
            if (spec is None or key[1:3] != ("A", 1)
                    or key[4] < spec["rows"]
                    or (spec["end_col"] is not None and key[3] != spec["end_col"])
                    or (spec["end_col"] is None and _column_number(key[3]) < spec["cols"])):
                _fail()
            identifier = key[0]
        else:
            identifier = key
        if identifier not in expected or identifier in result:
            _fail()
        rows = value.get("values", [])
        width = _column_number(key[3]) - _column_number(key[1]) + 1
        if (not isinstance(rows, list) or len(rows) > key[4] - key[2] + 1
                or any(not isinstance(row, list) or len(row) > width for row in rows)):
            _fail()
        for row in rows:
            if any(type(cell) not in (str, int, float, bool)
                   or (type(cell) is float and not math.isfinite(cell)) for cell in row):
                _fail()
        result[identifier] = deepcopy(rows)
    if set(result) != set(expected):
        _fail()
    return {area: result[key] for key, area in expected.items()}


class _Rows:
    def __init__(self, rows):
        header = list(rows[0]) if rows else []
        while header and header[-1] == "":
            header.pop()
        if header != HEADER:
            raise DispatchError("批次補圖記錄分頁欄位不符；未使用部分資料")
        # Match get_all_values' rectangular padding. Reading the full record
        # grid also preserves its rejection of extra nonempty columns, instead
        # of hiding an invalid I+ field by clipping the request to A:H.
        width = max(len(row) for row in rows)
        self.rows = [deepcopy(row)+[""]*(width-len(row)) for row in rows]

    def get_all_values(self):
        return deepcopy(self.rows)


class _RecordSnapshot(CategoryCodeStoreMixin, ProductImageStoreMixin):
    """Use the existing record validators over this guard's fresh row values."""
    def __init__(self, rows):
        self.rows = {name: _Rows(values) for name, values in rows.items()}

    def _sheet(self, title, create=False):
        if create or title not in self.rows:
            _fail()
        return self.rows[title]


def prepare_batched_existing_image_repair(store, identity, *, source_hint):
    """Read-only native counterpart of prepare_image_repair, 3 GET per guard."""
    if not supports_batched_existing_snapshot(store):
        raise DispatchError("批次來源快照需要原生 NAS 圖庫與 Google 試算表連線")
    if (not isinstance(source_hint, dict) or source_hint.get("identity") != identity
            or not isinstance(identity, str) or ":" not in identity
            or source_hint.get("category") != identity.rsplit(":", 1)[0]
            or type(source_hint.get("row")) is not int or source_hint["row"] < 1):
        raise DispatchError("批次補圖來源位置提示不完整；未讀取其他商品")
    spreadsheet = store.spreadsheet
    # Always fresh. This catches new/moved/duplicate product sheets and binds
    # the same exact sheet IDs into the snapshot across subsequent boundaries.
    try:
        metadata = spreadsheet.fetch_sheet_metadata(params={"fields": "spreadsheetId,sheets.properties"})
    except (KeyError, TypeError, ValueError, AttributeError):
        _fail()
    if (not isinstance(metadata, dict) or set(metadata) != {"spreadsheetId", "sheets"}
            or metadata["spreadsheetId"] != spreadsheet.id
            or not isinstance(metadata["sheets"], list) or not metadata["sheets"]):
        _fail()
    worksheets = []
    for item in metadata["sheets"]:
        if (not isinstance(item, dict) or set(item) != {"properties"}
                or not isinstance(item["properties"], dict)):
            _fail()
        # Same native Worksheet construction used by Spreadsheet.worksheets;
        # preserve the original client/auth while validating book identity too.
        worksheets.append(Worksheet(spreadsheet, deepcopy(item["properties"]), spreadsheet.id, spreadsheet.client))
    by_title, seen_ids = {}, set()
    for ws in worksheets:
        try:
            title, sheet_id = ws.title, ws.id
        except (KeyError, TypeError, ValueError, AttributeError):
            _fail()
        if (type(ws) is not Worksheet or ws.spreadsheet_id != spreadsheet.id
                or not isinstance(title, str) or not title or title != title.strip()
                or title in by_title or type(sheet_id) is not int or sheet_id < 0
                or sheet_id in seen_ids):
            _fail()
        by_title[title] = ws
        seen_ids.add(sheet_id)
    if CATEGORY_SHEET not in by_title or PRODUCT_IMAGE_SHEET not in by_title:
        raise DispatchError("批次補圖缺少分類或圖片對應分頁；未使用預設或部分資料")
    products = [ws for ws in worksheets if not ws.title.startswith("_")]
    selected = [*products, by_title[CATEGORY_SHEET], by_title[PRODUCT_IMAGE_SHEET]]
    if not products or len(selected) > MAX_RANGES:
        _fail()
    ranges, columns, grids = {}, {}, {}
    for ws in selected:
        try:
            rows, cols = ws.row_count, ws.col_count
        except (KeyError, TypeError, ValueError, AttributeError):
            _fail()
        width = 12 if ws in products else 8
        if type(rows) is not int or rows < 1 or type(cols) is not int or cols < width:
            _fail()
        columns[ws.title] = rows
        quoted = "'"+ws.title.replace("'", "''")+"'"
        ranges[ws.title] = quoted+"!A:L" if width == 12 else quoted
        grids[ws.title] = {"range": ranges[ws.title], "rows": rows,
                          "cols": cols, "end_col": "L" if width == 12 else None}
    hinted_title, hinted_row = source_hint["category"], source_hint["row"]
    if hinted_title not in {ws.title for ws in products} or hinted_row+5 > columns[hinted_title]:
        raise DispatchError("商品來源位置已移動或不完整；未讀取其他商品")
    area = "'"+hinted_title.replace("'", "''")+f"'!A{hinted_row}:L{hinted_row+5}"
    formulas = block(_read_ranges(spreadsheet, [area], render="FORMULA")[area])
    # Binding must be read AFTER FORMULA, as in prepare_image_repair. Open
    # product rows/whole record sheets also cover metadata-to-read grid growth.
    formatted = _read_ranges(spreadsheet, list(ranges.values()), render="FORMATTED_VALUE", open_grids=grids)
    records = _RecordSnapshot({name: formatted[ranges[name]] for name in (CATEGORY_SHEET, PRODUCT_IMAGE_SHEET)})
    codes = records.category_settings()["codes"]
    bindings = records.product_image_bindings()
    sources = catalog({ws.title: formatted[ranges[ws.title]] for ws in products}, codes)
    for source in sources:
        source["cost_audit_required"] = True
        source["source_url"] = (f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}/edit"
            f"#gid={by_title[source['category']].id}&range=A{source['row']}:T{source['row']+5}")
        # IDs only, not row counts: a valid append may grow an index grid. The
        # record sheets cannot be silently replaced during one repair.
        source["_repair_record_sheet_ids"] = {name: by_title[name].id for name in (CATEGORY_SHEET, PRODUCT_IMAGE_SHEET)}
    matches = [s for s in sources if s["identity"] == identity]
    if len(matches) != 1:
        raise DispatchError("商品不存在或身分重複，無法單獨補圖；請重新載入")
    source = deepcopy(matches[0])
    row, title = source["row"], source["category"]
    if (type(row) is not int or row < 1 or (title, row) != (hinted_title, hinted_row)
            or sum(s["category"] == title and s["row"] == row for s in sources) != 1):
        raise DispatchError("商品位置無法唯一識別，未補存圖片")
    values = block(formatted[ranges[title]][row-1:row+5])
    if values != block(source["block"]) or digest(values) != source["source_hash"]:
        raise DispatchError("商品來源在讀取期間已變更；請重新載入並核對圖片")
    # A nonempty displayed cell cannot have an empty original/formula value.
    # Do not equate other literals: number/date formatting may legitimately differ.
    if any(value and not formula for vr, fr in zip(values, formulas) for value, formula in zip(vr, fr)):
        _fail()
    return {"schema": 1, "source": source, "values": values, "formulas": formulas,
        "snapshot_digest": _snapshot_digest(source, values, formulas),
        "binding": deepcopy(bindings.get(identity))}
