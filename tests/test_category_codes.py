from copy import deepcopy

import pytest

from category_codes import CATEGORY_SHEET, DEFAULT_CATEGORY_CODES, add_code, validate_codes
from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_manager import DispatchError, catalog, new_batch
from dispatch_review import hydrate_draft
from dispatch_storage import CloudDispatchStore, encode_record
from line_ad_copy import build_bgd_code, build_line_ad_copy_from_sheet_block


def test_existing_codes_are_unchanged_and_unknown_is_not_guessed():
    assert build_bgd_code("G正版", "no123") == "BGD-G-123"
    assert build_bgd_code("S生活用品", "no123") == "BGD-S-123"
    with pytest.raises(ValueError, match="尚未設定"):
        build_bgd_code("新的分類", "no123")


@pytest.mark.parametrize("mapping", [
    {"G正版": "G"}, {**DEFAULT_CATEGORY_CODES, "其他": "G"},
    {**DEFAULT_CATEGORY_CODES, "其他": "v-1"}, {**DEFAULT_CATEGORY_CODES, "_內部": "X"},
    {**DEFAULT_CATEGORY_CODES, " G正版": "X"}, {**DEFAULT_CATEGORY_CODES, "其他": ""},
])
def test_duplicate_invalid_or_modified_historical_codes_are_rejected(mapping):
    with pytest.raises(ValueError):
        validate_codes(mapping)


def test_two_w_categories_can_be_explicitly_distinct():
    codes = add_code(DEFAULT_CATEGORY_CODES, "W玩具", "w")
    with pytest.raises(ValueError, match="重複"):
        add_code(codes, "W娃娃", "W")
    codes = add_code(codes, "W娃娃", "WA")
    assert build_bgd_code("W玩具", "no1", codes) != build_bgd_code("W娃娃", "no1", codes)


def test_settings_are_additive_versioned_and_leave_products_and_batch_unchanged():
    sheet = FakeSpreadsheet(product_rows((1,), category="D吊飾"))
    store = CloudDispatchStore(sheet)
    before = deepcopy(sheet.sheets["D吊飾"].rows)
    initial = store.catalog()
    batch = new_batch("測試", "不發送", initial, "測試")
    batch_before = deepcopy(batch)
    assert not initial[0]["code"]
    saved = store.save_category_code("D吊飾", "d", actor="測試", expected_revision="")
    assert saved["codes"]["D吊飾"] == "D"
    reopened = CloudDispatchStore(sheet)
    fresh = reopened.catalog()
    assert fresh[0]["code"] == "BGD-D-1" and fresh[0]["price"] == "53"
    assert "BGD-D-1" in build_line_ad_copy_from_sheet_block("D吊飾", before, saved["codes"])
    hydrated = hydrate_draft(batch, fresh)
    assert hydrated["items"][0]["source"]["code"] == "BGD-D-1" and not hydrated["items"][0]["review"]
    assert before == sheet.sheets["D吊飾"].rows and batch == batch_before
    with pytest.raises(DispatchError, match="已更新"):
        store.save_category_code("D吊飾", "D", actor="測試", expected_revision="")
    with pytest.raises(ValueError, match="不可直接更改"):
        store.save_category_code("D吊飾", "DD", actor="測試", expected_revision=saved["_revision"])


def test_setting_read_and_missing_sheet_do_not_create_cloud_data():
    sheet = FakeSpreadsheet()
    store = CloudDispatchStore(sheet)
    assert store.category_settings()["codes"] == DEFAULT_CATEGORY_CODES
    with pytest.raises(DispatchError, match="找不到"):
        store.save_category_code("不存在", "X", actor="測試", expected_revision="")
    assert set(sheet.sheets) == {"G正版"}


def test_setting_conflict_or_partial_read_stops_instead_of_using_defaults():
    sheet = FakeSpreadsheet(product_rows((1,), category="D吊飾"))
    store = CloudDispatchStore(sheet)
    saved = store.save_category_code("D吊飾", "D", actor="測試", expected_revision="")
    sheet.sheets[CATEGORY_SHEET].rows += encode_record("category_codes", {"schema": 1, "codes": saved["codes"]}, parent="wrong")
    with pytest.raises(DispatchError, match="版本衝突"):
        store.catalog()
