"""Synthetic product data and in-memory Sheets double; never connects to Google."""
from copy import deepcopy
from io import BytesIO
import re

import gspread
from PIL import Image

from dispatch_manager import catalog, edit_item, new_batch
from dispatch_storage import CloudDispatchStore


def product_rows(numbers=(1126, 1127, 1128, 1129, 1130), vendor="測試供應商", category="G正版"):
    rows = []
    for n in numbers:
        rows.extend([
            [f"no{n}", f"測試收納商品 {n}", "10%報價", "13%報價", "15%報價", "20%報價", "", "", "", "", "", vendor],
            ["2026/9/12", "計價單位：個\n包裝:彩盒\n彩盒尺寸 10*8*3cm", "52.9", "", "", "", "9.3", "71.4", "0", "0.61", "47.6"],
            ["", "裝箱 300個/箱"], ["", "單個重量 68g"], ["", f"貨號 TEST-{n}"], [""],
        ])
    return {category: rows}


def image_data():
    output = BytesIO()
    Image.new("RGB", (80, 60), (62, 136, 134)).save(output, format="PNG")
    return output.getvalue()


class FakeWorksheet:
    def __init__(self, title, rows=None):
        self.title = title
        self.rows = deepcopy(rows or [])
        self.fail_append_after_write = False
        self.before_append = None
        self.read_ranges = []

    def row_values(self, number):
        return list(self.rows[number - 1]) if len(self.rows) >= number else []

    def get_all_values(self):
        return deepcopy(self.rows)

    def update(self, *, values, range_name, value_input_option):
        assert range_name == "A1:H1" and value_input_option == "RAW"
        self.rows = deepcopy(values)

    def append_rows(self, rows, *, value_input_option, table_range):
        assert value_input_option == "RAW" and table_range == "A:H"
        if self.before_append:
            hook, self.before_append = self.before_append, None
            hook()
        self.rows.extend(deepcopy(rows))
        if self.fail_append_after_write:
            raise TimeoutError("synthetic timeout after server write")

    def get(self, range_name):
        self.read_ranges.append(range_name)
        assert range_name == "A2:B"
        return [row[:2] for row in self.rows[1:]]

    def batch_get(self, ranges):
        self.read_ranges.extend(ranges)
        return [deepcopy(self.rows[int(a) - 1:int(b)]) for name in ranges
                for a, b in [re.fullmatch(r"A(\d+):H(\d+)", name).groups()]]


class FakeSpreadsheet:
    def __init__(self, sheets=None):
        self.sheets = {title: FakeWorksheet(title, rows) for title, rows in (sheets or product_rows()).items()}

    def worksheet(self, title):
        if title not in self.sheets:
            raise gspread.exceptions.WorksheetNotFound(title)
        return self.sheets[title]

    def add_worksheet(self, title, rows, cols):
        assert title not in self.sheets
        self.sheets[title] = FakeWorksheet(title)
        return self.sheets[title]

    def worksheets(self):
        return list(self.sheets.values())


def ready_batch(store=None, numbers=(1126, 1127, 1128, 1129, 1130)):
    store = store or CloudDispatchStore(FakeSpreadsheet(product_rows(numbers)))
    batch = new_batch("測試廣告批次", "測試群組", store.catalog(), "測試核對人")
    image = store.put_asset(image_data(), "測試圖片.png")
    for item in list(batch["items"]):
        batch = edit_item(batch, item["id"], text=item["copy"], images=[image], excluded=False,
                          reason="", reviewed=True, actor="測試核對人")
    return batch
