"""Batch quote-sheet reads without changing get_all_values snapshot semantics."""

from gspread.utils import absolute_range_name, fill_gaps


def read_public_sheet_values(spreadsheet):
    """Read every non-internal sheet in one values batch request.

    Whole-sheet ranges include columns beyond the quote's A:L block. As with
    gspread 6.2.1's get_all_values(), rows are formatted values, padded to the
    returned maximum width, and a genuinely empty sheet is represented by [[]].
    API errors and incomplete/malformed responses propagate instead of looking
    like an empty catalog.
    """
    titles = [ws.title for ws in spreadsheet.worksheets() if not ws.title.startswith("_")]
    if not titles:
        return {}
    if any(not isinstance(title, str) or not title for title in titles) or len(set(titles)) != len(titles):
        raise ValueError("商品分頁清單格式不完整或名稱重複")
    response = spreadsheet.values_batch_get(
        [absolute_range_name(title) for title in titles],
        params={"majorDimension": "ROWS", "valueRenderOption": "FORMATTED_VALUE"},
    )
    value_ranges = response.get("valueRanges") if isinstance(response, dict) else None
    if not isinstance(value_ranges, list) or len(value_ranges) != len(titles):
        raise ValueError("雲表批次讀取回應不完整，停止使用商品清單")

    result = {}
    # Sheets guarantees valueRanges are in the same order as requested ranges.
    for title, value_range in zip(titles, value_ranges):
        if (
            not isinstance(value_range, dict)
            or not isinstance(value_range.get("range"), str)
            or not value_range["range"]
            or value_range.get("majorDimension", "ROWS") != "ROWS"
            or "error" in value_range
        ):
            raise ValueError(f"分頁「{title}」批次讀取格式不完整")
        values = value_range.get("values", [[]])
        if not isinstance(values, list) or any(not isinstance(row, list) for row in values):
            raise ValueError(f"分頁「{title}」儲存格資料格式不正確")
        result[title] = fill_gaps(values)
    return result
