"""Explicit continuation workbook routing; historical rows are never copied."""
import re

LEGACY_ID = "1lkgnEN4nfRbArfUD8klt82XeNmee3bJAvNrP_mdb1zw"
ACTIVE_ID = "1ZaEQsoe_4MShBO1GCF7aHiEEL18OOsQZPI0p_Ingrco"
ACTIVE_TITLE = "no2 半自動 - 採購報價彙整表BGD"
HISTORY_PREFIX = "舊表 · "


def active_id(configured_id=""):
    configured_id = str(configured_id or "").strip()
    return ACTIVE_ID if configured_id in ("", LEGACY_ID) else configured_id


def is_continuation(spreadsheet):
    return spreadsheet.id == ACTIVE_ID


def combined_catalog(current, history):
    result = dict(current)
    for title, rows in history.items():
        key = HISTORY_PREFIX + title
        if key in result:
            raise ValueError("新舊表分類標籤衝突，停止查重")
        result[key] = rows
    return result


def next_product_no(category, current, history):
    if category not in current or category not in history:
        raise ValueError("新舊表缺少對應分類，停止分配 NO")
    seen = set()
    maximum = 0
    for rows in (history[category], current[category]):
        for row in rows:
            value = str(row[0]).strip() if row else ""
            match = re.fullmatch(r"no(\d+)", value, re.I)
            if not match:
                continue
            number = int(match[1])
            if number in seen:
                raise ValueError(f"新舊表已有重複 NO：{category} no{number}，停止存檔")
            seen.add(number)
            maximum = max(maximum, number)
    return f"no{maximum + 1}"


def validate_next_numbers(category, current, history, incoming):
    expected = int(next_product_no(category, current, history)[2:])
    actual = [str(item.get("no", "")).lower() for item in incoming]
    planned = [f"no{expected + offset}" for offset in range(len(incoming))]
    if not actual or actual != planned:
        raise ValueError("新舊表編號已變動，請重新載入後再新增")


def first_product_layout(sheet_id, start_row, block_size=6):
    """Initialize only an empty destination's quote layout, never old sheets."""
    requests = []
    for start, end, size in ((0, 1, 105), (1, 2, 340), (2, 12, 110), (12, 20, 70)):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": start, "endIndex": end},
            "properties": {"pixelSize": size}, "fields": "pixelSize"}})
    base = start_row - 1
    for offset, size in enumerate((26, 130, 24, 24, 24, 24)):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": base + offset, "endIndex": base + offset + 1},
            "properties": {"pixelSize": size}, "fields": "pixelSize"}})
    requests.append({"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": base, "endRowIndex": base + block_size, "startColumnIndex": 0, "endColumnIndex": 12},
        "cell": {"userEnteredFormat": {"verticalAlignment": "BOTTOM"}},
        "fields": "userEnteredFormat.verticalAlignment"}})
    requests.append({"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": base, "endRowIndex": base + block_size, "startColumnIndex": 1, "endColumnIndex": 2},
        "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}},
        "fields": "userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment"}})
    return requests
