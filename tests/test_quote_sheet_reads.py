from copy import deepcopy
from types import SimpleNamespace

import pytest

from quote_sheet_reads import read_public_sheet_values


class FakeSpreadsheet:
    def __init__(self, titles, response=None, error=None):
        self.titles = titles
        self.response = response
        self.error = error
        self.metadata_calls = 0
        self.batch_calls = []

    def worksheets(self):
        self.metadata_calls += 1
        return [SimpleNamespace(title=title) for title in self.titles]

    def values_batch_get(self, ranges, params=None):
        self.batch_calls.append((ranges, params))
        if self.error:
            raise self.error
        return deepcopy(self.response)


def test_five_public_sheets_use_one_batch_and_skip_internal_sheets():
    titles = ["G正版", "T玩具", "生活用品", "娃娃", "扭蛋"]
    sheet = FakeSpreadsheet(
        ["_設定", *titles, "_報價依據"],
        {"valueRanges": [
            {"range": f"'{title}'!A1:B2", "values": [["no1", title]]}
            for title in titles
        ]},
    )
    assert read_public_sheet_values(sheet) == {title: [["no1", title]] for title in titles}
    assert sheet.metadata_calls == 1
    assert sheet.batch_calls == [(
        [f"'{title}'" for title in titles],
        {"majorDimension": "ROWS", "valueRenderOption": "FORMATTED_VALUE"},
    )]


def test_whole_sheet_ranges_quote_chinese_spaces_commas_and_apostrophes():
    titles = ["中文 分頁", "商品,玩具", "客戶's清單"]
    sheet = FakeSpreadsheet(titles, {"valueRanges": [
        {"range": "'中文 分頁'!A1:B2", "values": [["1"]]},
        {"range": "'商品,玩具'!A1:B2", "values": [["2"]]},
        {"range": "'客戶''s清單'!A1:B2", "values": [["3"]]},
    ]})
    result = read_public_sheet_values(sheet)
    assert list(result) == titles
    assert sheet.batch_calls[0][0] == ["'中文 分頁'", "'商品,玩具'", "'客戶''s清單'"]


@pytest.mark.parametrize("empty_values", [None, [], [[]]])
def test_empty_sheet_matches_get_all_values_empty_representation(empty_values):
    value_range = {"range": "'空分頁'!A1:Z1000", "majorDimension": "ROWS"}
    if empty_values is not None:
        value_range["values"] = empty_values
    sheet = FakeSpreadsheet(["空分頁"], {"valueRanges": [value_range]})
    assert read_public_sheet_values(sheet) == {"空分頁": [[]]}


def test_trailing_columns_and_internal_blank_rows_are_preserved_and_padded():
    wide_row = ["no1", "品名"] + [""] * 17 + ["T欄原始資料"]
    rows = [wide_row, [], ["2026/9/29", "NT$1,234.50"], [""] * 12 + ["M欄資料"]]
    sheet = FakeSpreadsheet(["商品"], {"valueRanges": [
        {"range": "'商品'!A1:T4", "values": rows},
    ]})
    result = read_public_sheet_values(sheet)["商品"]
    assert len(result) == 4
    assert all(len(row) == 20 for row in result)
    assert result[0] == wide_row
    assert result[1] == [""] * 20
    assert result[2][:2] == ["2026/9/29", "NT$1,234.50"]
    assert result[3][12] == "M欄資料"
    assert sheet.batch_calls[0][0] == ["'商品'"]
    assert rows[1] == []


def test_no_public_sheets_requires_no_values_request():
    sheet = FakeSpreadsheet(["_設定", "_報價依據"])
    assert read_public_sheet_values(sheet) == {}
    assert not sheet.batch_calls


def test_request_failure_is_not_returned_as_empty_data():
    error = RuntimeError("Google request failed")
    sheet = FakeSpreadsheet(["商品"], error=error)
    with pytest.raises(RuntimeError, match="Google request failed") as raised:
        read_public_sheet_values(sheet)
    assert raised.value is error


@pytest.mark.parametrize("response", [
    None, {}, {"valueRanges": []}, {"valueRanges": None},
    {"valueRanges": [{"range": "'商品'!A1"}, {"range": "'額外分頁'!A1"}]},
    {"valueRanges": [{}]},
    {"valueRanges": [{"range": "'商品'!A1", "values": None}]},
    {"valueRanges": [{"range": "'商品'!A1", "values": ["bad row"]}]},
    {"valueRanges": [{"range": "'商品'!A1", "majorDimension": "COLUMNS"}]},
    {"valueRanges": [{"range": "'商品'!A1", "error": "no access"}]},
])
def test_incomplete_or_malformed_response_is_not_treated_as_empty(response):
    sheet = FakeSpreadsheet(["商品"], response)
    with pytest.raises(ValueError):
        read_public_sheet_values(sheet)
