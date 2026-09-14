from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image

from dispatch_fakes import FakeSpreadsheet, image_data, product_rows
from dispatch_images import match_image_pack
from dispatch_manager import DispatchError, new_batch
from dispatch_storage import CloudDispatchStore, IMAGE_SHEET, asset_bytes, encode_record, validate_image
from product_images import PRODUCT_IMAGE_SHEET, subject_key, unique_proposals
from quote_images import save_quote_images
from test_dispatch_images import make_zip


def fixture(numbers=(1, 2)):
    sheet = FakeSpreadsheet(product_rows(numbers))
    store = CloudDispatchStore(sheet)
    return sheet, store, store.catalog()


def picture(n=0):
    output = BytesIO()
    Image.new("RGB", (80, 60), (n % 255, n // 255, 120)).save(output, format="PNG")
    return validate_image(output.getvalue(), f"test-{n}.png")


def assignment(source, assets=None, revision=""):
    return {"source": source, "assets": assets or [picture()], "expected_revision": revision}


def test_opening_library_is_read_only_and_empty_is_not_created():
    sheet, store, products = fixture()
    assert store.product_image_refs(products)["images"] == {p["identity"]: [] for p in products}
    assert store.product_image_bindings() == {}
    assert set(sheet.sheets) == {"G正版"}


def test_import_69_is_lossless_idempotent_and_no_batch_or_quote_changes():
    sheet, store, products = fixture(range(1, 70))
    before = deepcopy(sheet.sheets["G正版"].rows)
    batch = new_batch("合成", "測試群・不會發送", products, "測試")
    batch_before = deepcopy(batch)
    result = store.save_product_images([assignment(p) for p in products], actor="測試", origin="test")
    assert len(result) == 69 and {r["結果"] for r in result} == {"已綁定"}
    assert sheet.sheets["G正版"].rows == before and batch == batch_before
    assert len(sheet.sheets[IMAGE_SHEET].rows) == 2  # Identical bytes stored once.
    assert len(sheet.sheets[PRODUCT_IMAGE_SHEET].rows) == 70
    before_internal = {k: deepcopy(ws.rows) for k, ws in sheet.sheets.items()}
    retry = store.save_product_images([assignment(p) for p in products], actor="測試", origin="retry")
    assert {r["結果"] for r in retry} == {"已存在"}
    assert before_internal == {k: ws.rows for k, ws in sheet.sheets.items()}
    reopened = CloudDispatchStore(sheet)
    refs = reopened.product_image_refs(products)
    assert all(len(ref) == 1 for ref in refs["images"].values())
    assert asset_bytes(reopened.get_asset(refs["images"][products[0]["identity"]][0]["stored_id"])) == asset_bytes(picture())
    assert all(not item["review"] and not item["images"] for item in batch["items"])


def test_metadata_read_does_not_download_photo_payloads():
    sheet, store, products = fixture()
    store.save_product_images([assignment(products[0])], actor="測試", origin="test")
    sheet.sheets[IMAGE_SHEET].get = lambda *a, **k: pytest.fail("metadata must not read image payloads")
    refs = CloudDispatchStore(sheet).product_image_refs(products)
    assert refs["images"][products[0]["identity"]]


@pytest.mark.parametrize("field,value", [("name", "其他商品"), ("supplier_code", "OTHER"), ("vendor", "v另一廠商")])
def test_subject_change_quarantines_old_photo(field, value):
    _, store, products = fixture()
    store.save_product_images([assignment(products[0])], actor="測試", origin="test")
    changed = deepcopy(products[0])
    changed[field] = value
    refs = store.product_image_refs([changed])
    assert not refs["images"][changed["identity"]] and refs["warnings"]


def test_cost_date_or_row_move_keeps_same_product_binding_and_vendor_alias_is_stable():
    _, store, products = fixture()
    store.save_product_images([assignment(products[0])], actor="測試", origin="test")
    changed = deepcopy(products[0])
    changed.update(row=13, source_hash="new price", price="999", date="2026-10-01")
    assert store.product_image_refs([changed])["images"][changed["identity"]]
    assert subject_key({**changed, "vendor": "V-非凡"}) == subject_key({**changed, "vendor": "v菲凡"})


def test_duplicate_source_never_auto_loads_or_saves():
    sheet, store, products = fixture()
    store.save_product_images([assignment(products[0])], actor="測試", origin="test")
    refs = store.product_image_refs([products[0], products[0]])
    assert not refs["images"][products[0]["identity"]]
    sheet.sheets["G正版"].rows += deepcopy(sheet.sheets["G正版"].rows[:6])
    result = store.save_product_images([assignment(products[0])], actor="測試", origin="test")
    assert result[0]["結果"] == "待處理" and "重複" in result[0]["原因"]


def test_stale_one_is_reported_while_other_valid_product_saves():
    sheet, store, products = fixture()
    sheet.sheets["G正版"].rows[0][1] = "變動商品"
    result = store.save_product_images([assignment(p) for p in products], actor="測試", origin="test")
    assert [r["結果"] for r in result] == ["待處理", "已綁定"]
    assert set(store.product_image_bindings()) == {products[1]["identity"]}


def test_product_changes_during_image_write_is_rechecked(monkeypatch):
    sheet, store, products = fixture()
    original = store.put_assets
    def change(assets):
        result = original(assets)
        sheet.sheets["G正版"].rows[0][1] = "讀取期間換款"
        return result
    monkeypatch.setattr(store, "put_assets", change)
    result = store.save_product_images([assignment(products[0])], actor="測試", origin="test")
    assert result[0]["結果"] == "待處理" and not store.product_image_bindings()


def test_existing_photo_requires_explicit_replace_and_matching_revision():
    _, store, products = fixture()
    source = products[0]
    store.save_product_images([assignment(source)], actor="測試", origin="test")
    old = store.product_image_bindings()[source["identity"]]
    wrong_revision = store.save_product_images([assignment(source, [picture(1)])], actor="測試", origin="test", replace=True)
    assert wrong_revision[0]["結果"] == "待處理"
    new = assignment(source, [picture(1)], old["_revision"])
    assert store.save_product_images([new], actor="測試", origin="test")[0]["結果"] == "待處理"
    assert store.save_product_images([new], actor="測試", origin="test", replace=True)[0]["結果"] == "已綁定"
    assert asset_bytes(store.get_asset(old["assets"][0])) == asset_bytes(picture())  # Old original remains recoverable.


@pytest.mark.parametrize("target", [IMAGE_SHEET, PRODUCT_IMAGE_SHEET])
def test_write_timeout_is_pending_and_retry_does_not_duplicate_quote_or_records(target):
    sheet, store, products = fixture()
    ws = store._sheet(target, create=True)
    ws.fail_append_after_write = True
    result = store.save_product_images([assignment(products[0])], actor="測試", origin="test")
    assert result[0]["結果"] == "待處理"
    ws.fail_append_after_write = False
    before = len(ws.rows)
    retried = store.save_product_images([assignment(products[0])], actor="測試", origin="retry")
    assert retried[0]["結果"] in {"已存在", "已綁定"} and len(ws.rows) == before
    assert len(sheet.sheets["G正版"].rows) == 12


def test_damaged_asset_and_conflicting_metadata_never_auto_pass():
    sheet, store, products = fixture()
    source = products[0]
    store.save_product_images([assignment(source)], actor="測試", origin="test")
    sheet.sheets[IMAGE_SHEET].rows[1][6] += "bad"
    result = store.save_product_images([assignment(source)], actor="測試", origin="test")
    assert result[0]["結果"] == "待處理"
    old = store.product_image_bindings()[source["identity"]]
    sheet.sheets[PRODUCT_IMAGE_SHEET].rows += encode_record(source["identity"], old, parent="wrong-parent")
    with pytest.raises(DispatchError, match="衝突"):
        store.product_image_refs(products)


def test_only_single_distinct_candidates_are_in_automatic_save_plan():
    _, store, products = fixture((1, 2, 3))
    proposals = {products[0]["identity"]: [picture(), picture()],
                 products[1]["identity"]: [picture(), picture(1)]}
    plan, rows = unique_proposals(products, proposals, {})
    assert len(plan) == 1 and plan[0]["source"] == products[0]
    assert "多張" in rows[1]["配對結果"] and "未找到" in rows[2]["配對結果"]
    store.save_product_images([assignment(products[0], [picture(2)])], actor="測試", origin="test")
    assert not unique_proposals(products, proposals, store.product_image_bindings())[0]


def test_filename_must_be_unique_in_whole_catalog_not_just_current_batch():
    _, _, products = fixture()
    products[1]["supplier_code"] = products[0]["supplier_code"]
    data = make_zip({products[0]["supplier_code"] + ".png": image_data()})
    result = match_image_pack(data, [products[0]], all_products=products)
    assert not any(result["images"].values()) and result["warnings"]
    exact = match_image_pack(make_zip({products[0]["code"] + ".png": image_data()}), [products[0]], all_products=products)
    assert exact["images"][products[0]["identity"]]


def test_multiple_valid_photos_are_supported_when_explicitly_bound():
    _, store, products = fixture()
    result = store.save_product_images([assignment(products[0], [picture(n) for n in range(5)])], actor="測試", origin="manual")
    assert result[0]["結果"] == "已綁定"
    assert len(store.product_image_refs(products)["images"][products[0]["identity"]]) == 5
    invalid = store.save_product_images([assignment(products[1], [picture(n) for n in range(6)])], actor="測試", origin="test")
    assert invalid[0]["結果"] == "待處理"


def test_asset_bulk_requests_are_bounded_and_readback_verified():
    sheet, store, _ = fixture()
    ws = store._sheet(IMAGE_SHEET, create=True)
    calls = []
    original = ws.append_rows
    def recording(rows, **kwargs):
        calls.append(len(rows))
        return original(rows, **kwargs)
    ws.append_rows = recording
    stored = store.put_assets([picture(n) for n in range(100)])
    assert len(stored) == 100 and calls == [80, 20]


def test_quote_hook_binds_only_exact_saved_source_never_rewrites_quote():
    sheet, store, products = fixture()
    source = products[0]
    expected = store.read_cost_source(source)
    before = deepcopy(sheet.sheets["G正版"].rows)
    result = save_quote_images(store, "G正版", 1, expected, [picture()])
    assert result[0]["結果"] == "已綁定" and sheet.sheets["G正版"].rows == before
    changed = deepcopy(expected)
    changed[0][1] = "另一款"
    with pytest.raises(DispatchError, match="商品身分"):
        save_quote_images(store, "G正版", 1, changed, [picture(1)])
    assert len(store.product_image_bindings()) == 1


def test_empty_quote_image_upload_does_not_touch_storage():
    class NoStore:
        def catalog(self):
            pytest.fail("no images should not read cloud")
    assert save_quote_images(NoStore(), "G正版", 1, [], []) == []
