from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image

from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_manager import DispatchError
from dispatch_storage import CloudDispatchStore, IMAGE_SHEET, asset_bytes, validate_image
from product_images import PRODUCT_IMAGE_SHEET
from quote_image_repair import prepare_image_repair, save_image_repair


def fixture():
    sheet = FakeSpreadsheet(product_rows((1171, 1173)))
    store = CloudDispatchStore(sheet)
    return sheet, store, store.catalog()[0]["identity"]


def picture(number=0):
    data = BytesIO()
    Image.new("RGB", (40, 30), (number, 110, 130)).save(data, format="PNG")
    return validate_image(data.getvalue(), f"synthetic-{number}.png")


def rows(sheet):
    return {title: deepcopy(ws.rows) for title, ws in sheet.sheets.items()}


def test_prepare_is_read_only_and_captures_unique_source_values_and_formulas():
    sheet, store, identity = fixture()
    before = rows(sheet)
    plan = prepare_image_repair(store, identity)
    assert plan["source"]["identity"] == identity
    assert plan["source"]["no"] == "no1171"
    assert plan["source"]["supplier_code"] == "TEST-1171"
    assert plan["values"][1][2] == "52.9"
    assert plan["formulas"][1][2] == "=ROUND(K2/0.9,1)"
    assert plan["binding"] is None and plan["snapshot_digest"]
    assert rows(sheet) == before


def test_success_changes_only_image_storage_and_verifies_original_bytes(monkeypatch):
    sheet, store, identity = fixture()
    for name in ("_報價依據", "_發送批次", "_人工核對", "_LINE紀錄"):
        sheet.add_worksheet(name, rows=5, cols=8).rows = [["preserve existing content"]]
    before = rows(sheet)
    plan = prepare_image_repair(store, identity)
    readbacks, original = [], store.get_assets

    def record(asset_ids):
        readbacks.append(list(asset_ids))
        return original(asset_ids)

    monkeypatch.setattr(store, "get_assets", record)
    result = save_image_repair(store, plan, [picture()], actor="合成測試")
    assert result[0]["結果"] == "已綁定"
    assert {title: ws.rows for title, ws in sheet.sheets.items() if title in before} == before
    assert set(sheet.sheets) - before.keys() == {IMAGE_SHEET, PRODUCT_IMAGE_SHEET}
    reopened = CloudDispatchStore(sheet)
    binding = reopened.product_image_bindings()[identity]
    assert binding["origin"] == "quote_image_repair"
    assert readbacks[-1] == binding["assets"]
    assert asset_bytes(reopened.get_asset(binding["assets"][0])) == asset_bytes(picture())


@pytest.mark.parametrize("identity", ["G正版:no404", "G正版:no1171"])
def test_prepare_rejects_missing_or_duplicate_identity_without_writes(identity):
    sheet, store, _ = fixture()
    if identity.endswith("no1171"):
        sheet.sheets["G正版"].rows += deepcopy(sheet.sheets["G正版"].rows[:6])
    before = rows(sheet)
    with pytest.raises(DispatchError, match="不存在或身分重複"):
        prepare_image_repair(store, identity)
    assert rows(sheet) == before


def test_prepare_rejects_source_change_between_catalog_and_snapshot(monkeypatch):
    sheet, store, identity = fixture()
    ws = sheet.sheets["G正版"]
    original = ws.get

    def changing(area, value_render_option=None):
        ws.rows[0][1] = "另一款商品"
        return original(area, value_render_option=value_render_option)

    monkeypatch.setattr(ws, "get", changing)
    with pytest.raises(DispatchError, match="讀取期間已變更"):
        prepare_image_repair(store, identity)
    assert set(sheet.sheets) == {"G正版"}


@pytest.mark.parametrize("change", ["name", "price", "formula", "row", "identity", "duplicate"])
def test_changed_product_or_formula_is_rejected_before_image_writes(change):
    sheet, store, identity = fixture()
    plan = prepare_image_repair(store, identity)
    ws = sheet.sheets["G正版"]
    if change == "name":
        ws.rows[0][1] = "不同商品"
    elif change == "price":
        ws.rows[1][6] = "99.9"
    elif change == "formula":
        ws.formula_override = deepcopy(plan["formulas"])
        ws.formula_override[1][10] += "+0"  # Same displayed cost, different formula.
    elif change == "row":
        ws.rows.insert(0, [])
    elif change == "identity":
        ws.rows[0][0] = "no9999"
    else:
        ws.rows += deepcopy(ws.rows[:6])
    before = rows(sheet)
    with pytest.raises(DispatchError):
        save_image_repair(store, plan, [picture()], actor="合成測試")
    assert rows(sheet) == before


def test_snapshot_mutation_is_rejected():
    sheet, store, identity = fixture()
    plan = prepare_image_repair(store, identity)
    plan["formulas"][1][10] += "+0"
    with pytest.raises(DispatchError, match="補圖快照"):
        save_image_repair(store, plan, [picture()], actor="合成測試")
    assert set(sheet.sheets) == {"G正版"}


def test_same_sha_set_retry_is_read_only_and_preserves_saved_order():
    sheet, store, identity = fixture()
    plan = prepare_image_repair(store, identity)
    images = [picture(), picture(1)]
    save_image_repair(store, plan, images, actor="合成測試")
    before = rows(sheet)
    reopened = CloudDispatchStore(sheet)
    result = save_image_repair(reopened, plan, list(reversed(images)), actor="合成測試")
    assert result[0]["結果"] == "已存在"
    assert rows(sheet) == before
    assert reopened.product_image_bindings()[identity]["assets"] == [a["sha256"] for a in images]


def test_different_existing_binding_is_never_replaced_or_written():
    sheet, store, identity = fixture()
    plan = prepare_image_repair(store, identity)
    save_image_repair(store, plan, [picture()], actor="合成測試")
    before = rows(sheet)
    with pytest.raises(DispatchError, match="不會替換"):
        save_image_repair(CloudDispatchStore(sheet), plan, [picture(1)], actor="合成測試")
    assert rows(sheet) == before


def test_same_image_with_stale_subject_is_not_adopted():
    sheet, store, identity = fixture()
    save_image_repair(store, prepare_image_repair(store, identity), [picture()], actor="合成測試")
    sheet.sheets["G正版"].rows[0][1] = "商品已換款"
    reopened = CloudDispatchStore(sheet)
    plan = prepare_image_repair(reopened, identity)
    before = rows(sheet)
    with pytest.raises(DispatchError, match="圖片身分不符"):
        save_image_repair(reopened, plan, [picture()], actor="合成測試")
    assert rows(sheet) == before


@pytest.mark.parametrize("target", [IMAGE_SHEET, PRODUCT_IMAGE_SHEET])
def test_unknown_write_result_is_pending_and_explicit_retry_is_idempotent(target):
    sheet, store, identity = fixture()
    plan = prepare_image_repair(store, identity)
    quote_before = deepcopy(sheet.sheets["G正版"].rows)
    ws = store._sheet(target, create=True)
    ws.fail_append_after_write = True
    first = save_image_repair(store, plan, [picture()], actor="合成測試")
    assert first[0]["結果"] == "待處理"
    count_after_unknown = len(ws.rows)
    ws.fail_append_after_write = False
    retry = save_image_repair(CloudDispatchStore(sheet), plan, [picture()], actor="合成測試")
    assert retry[0]["結果"] in {"已存在", "已綁定"}
    assert len(ws.rows) == count_after_unknown
    assert sheet.sheets["G正版"].rows == quote_before


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_existing_binding_does_not_prove_original_integrity(damage):
    sheet, store, identity = fixture()
    plan = prepare_image_repair(store, identity)
    save_image_repair(store, plan, [picture()], actor="合成測試")
    if damage == "missing":
        sheet.sheets[IMAGE_SHEET].rows = sheet.sheets[IMAGE_SHEET].rows[:1]
    else:
        sheet.sheets[IMAGE_SHEET].rows[1][6] += "broken"
    before = rows(sheet)
    with pytest.raises(DispatchError):
        save_image_repair(CloudDispatchStore(sheet), plan, [picture()], actor="合成測試")
    assert rows(sheet) == before


def test_source_change_during_image_write_prevents_binding(monkeypatch):
    sheet, store, identity = fixture()
    plan = prepare_image_repair(store, identity)
    original = store.put_assets

    def change(assets):
        result = original(assets)
        sheet.sheets["G正版"].rows[0][1] = "上傳期間換款"
        return result

    monkeypatch.setattr(store, "put_assets", change)
    result = save_image_repair(store, plan, [picture()], actor="合成測試")
    assert result[0]["結果"] == "待處理"
    assert not store.product_image_bindings()


def test_formula_change_during_image_write_cannot_report_success(monkeypatch):
    sheet, store, identity = fixture()
    plan = prepare_image_repair(store, identity)
    original = store.put_assets

    def change(assets):
        result = original(assets)
        ws = sheet.sheets["G正版"]
        ws.formula_override = deepcopy(plan["formulas"])
        ws.formula_override[1][10] += "+0"
        return result

    monkeypatch.setattr(store, "put_assets", change)
    with pytest.raises(DispatchError, match="保存結果待核對"):
        save_image_repair(store, plan, [picture()], actor="合成測試")
    # Sheets has no transaction: an in-flight binding can exist, but must not be
    # reported as verified until the changed source is explicitly reviewed again.
    assert identity in store.product_image_bindings()


def test_same_content_in_a_different_cloud_sheet_is_rejected(monkeypatch):
    sheet, store, identity = fixture()
    original = store.catalog

    def catalog(url):
        sources = original()
        for source in sources:
            source["source_url"] = url
        return sources

    monkeypatch.setattr(store, "catalog", lambda: catalog("https://example.test/sheet-a"))
    plan = prepare_image_repair(store, identity)
    before = rows(sheet)
    monkeypatch.setattr(store, "catalog", lambda: catalog("https://example.test/sheet-b"))
    with pytest.raises(DispatchError, match="雲表來源已變更"):
        save_image_repair(store, plan, [picture()], actor="合成測試")
    assert rows(sheet) == before


def test_claimed_success_requires_final_binding_and_original_readback(monkeypatch):
    sheet, store, identity = fixture()
    plan = prepare_image_repair(store, identity)
    monkeypatch.setattr(store, "save_product_images", lambda *args, **kwargs: [{"結果": "已綁定"}])
    with pytest.raises(DispatchError, match="綁定讀回未完成"):
        save_image_repair(store, plan, [picture()], actor="合成測試")
    assert set(sheet.sheets) == {"G正版"}


def test_invalid_or_empty_images_and_blank_actor_never_write():
    sheet, store, identity = fixture()
    plan = prepare_image_repair(store, identity)
    with pytest.raises(DispatchError):
        save_image_repair(store, plan, [], actor="合成測試")
    with pytest.raises(DispatchError, match="保存人"):
        save_image_repair(store, plan, [picture()], actor=" ")
    bad = picture()
    bad["data"] += "broken"
    with pytest.raises(DispatchError):
        save_image_repair(store, plan, [bad], actor="合成測試")
    assert set(sheet.sheets) == {"G正版"}
