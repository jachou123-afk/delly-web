from copy import deepcopy

import pytest

from dispatch_manager import DispatchError, new_batch
from dispatch_storage import (
    BATCH_SHEET, IMAGE_SHEET, CHUNK_SIZE, CloudDispatchStore, HEADER,
    asset_bytes, decode_records, encode_record, validate_image,
)
from dispatch_fakes import FakeSpreadsheet, image_data, ready_batch


def test_opening_management_and_reading_catalog_do_not_create_cloud_sheets():
    spreadsheet = FakeSpreadsheet()
    store = CloudDispatchStore(spreadsheet)
    assert store.list_batches() == []
    assert len(store.catalog()) == 5
    assert set(spreadsheet.sheets) == {"G正版"}


def test_batch_survives_new_store_session_without_modifying_quote_rows():
    spreadsheet = FakeSpreadsheet()
    before = deepcopy(spreadsheet.sheets["G正版"].rows)
    store = CloudDispatchStore(spreadsheet)
    batch = new_batch("測試", "測試群組", store.catalog(), "測試核對人")
    saved = store.save_batch(batch)
    reopened = CloudDispatchStore(spreadsheet)
    assert reopened.list_batches() == [saved]
    assert spreadsheet.sheets["G正版"].rows == before
    assert len(store.catalog()) == 5


def test_image_is_stored_losslessly_and_reused_by_digest():
    spreadsheet = FakeSpreadsheet()
    store = CloudDispatchStore(spreadsheet)
    data = image_data()
    asset_id = store.put_asset(data, "測試圖片.png")
    rows = deepcopy(spreadsheet.sheets[IMAGE_SHEET].rows)
    assert store.put_asset(data, "同圖不同名.png") == asset_id
    assert spreadsheet.sheets[IMAGE_SHEET].rows == rows
    asset = CloudDispatchStore(spreadsheet).get_asset(asset_id)
    assert asset_bytes(asset) == data
    assert "A2:B" in spreadsheet.sheets[IMAGE_SHEET].read_ranges


def test_chunked_records_detect_partial_and_modified_payloads():
    value = {"text": "測試" * CHUNK_SIZE}
    rows = encode_record("entity", value)
    assert len(rows) > 1
    assert decode_records(rows)[0]["value"] == value
    with pytest.raises(DispatchError, match="尚未寫入完整"):
        decode_records(rows[:-1])
    modified = deepcopy(rows)
    modified[0][6] += "改動"
    with pytest.raises(DispatchError, match="校驗失敗"):
        decode_records(modified)


def test_stale_revision_cannot_overwrite_other_operators_changes():
    store = CloudDispatchStore(FakeSpreadsheet())
    batch = store.save_batch(ready_batch())
    newer = deepcopy(batch)
    newer["name"] = "另一位修改"
    store.save_batch(newer, batch["_revision"])
    batch["name"] = "舊畫面修改"
    with pytest.raises(DispatchError, match="其他視窗更新"):
        store.save_batch(batch, batch["_revision"])
    assert store.list_batches()[0]["name"] == "另一位修改"


def test_same_revision_concurrent_writes_are_detected_and_not_silently_merged():
    spreadsheet = FakeSpreadsheet()
    store = CloudDispatchStore(spreadsheet)
    saved = store.save_batch(ready_batch())
    ws = spreadsheet.sheets[BATCH_SHEET]
    other = deepcopy(saved)
    other["name"] = "同時修改"
    ws.before_append = lambda: ws.rows.extend(encode_record(saved["id"], other, saved["_revision"]))
    with pytest.raises(DispatchError, match="核對未完成"):
        store.save_batch(saved, saved["_revision"])
    with pytest.raises(DispatchError, match="同時修改"):
        store.list_batches()


def test_timeout_after_write_is_not_treated_as_failure_and_retried_blindly():
    spreadsheet = FakeSpreadsheet()
    store = CloudDispatchStore(spreadsheet)
    saved = store.save_batch(ready_batch())
    ws = spreadsheet.sheets[BATCH_SHEET]
    ws.fail_append_after_write = True
    updated = deepcopy(saved)
    updated["name"] = "伺服器實際已保存"
    with pytest.raises(DispatchError, match="結果待確認"):
        store.save_batch(updated, saved["_revision"], record_id="same-logical-save")
    row_count = len(ws.rows)
    assert store.list_batches()[0]["name"] == "伺服器實際已保存"
    assert store.save_batch(updated, saved["_revision"], record_id="same-logical-save")["name"] == updated["name"]
    assert len(ws.rows) == row_count


def test_header_collision_read_failure_and_invalid_assets_fail_closed():
    spreadsheet = FakeSpreadsheet()
    store = CloudDispatchStore(spreadsheet)
    ws = spreadsheet.add_worksheet(BATCH_SHEET, 10, 8)
    ws.rows = [["使用者原有內容"]]
    with pytest.raises(DispatchError, match="欄位不符"):
        store.list_batches()
    assert ws.rows == [["使用者原有內容"]]
    with pytest.raises(DispatchError, match="損壞"):
        validate_image(b"not an image", "x.jpg")
    asset = validate_image(image_data(), "x.png")
    asset["sha256"] = "wrong"
    with pytest.raises(DispatchError, match="校驗失敗"):
        asset_bytes(asset)


def test_receipt_save_reads_only_target_batch_payload_and_checks_readback(monkeypatch):
    store = CloudDispatchStore(FakeSpreadsheet())
    first = store.save_batch(ready_batch())
    second = store.save_batch(ready_batch())
    ws = store.spreadsheet.sheets[BATCH_SHEET]
    ws.read_ranges.clear()

    def no_full_read():
        raise AssertionError("must not read every historical payload on each receipt")

    monkeypatch.setattr(ws, "get_all_values", no_full_read)
    saved = store.save_batch(first, first["_revision"])
    assert saved["_revision"] != first["_revision"]
    assert ws.read_ranges.count("A2:B") == 2
    second_rows = {n for n, row in enumerate(ws.rows, 1) if len(row) > 1 and row[1] == second["id"]}
    import re
    for name in ws.read_ranges:
        match = re.fullmatch(r"A(\d+):H(\d+)", name)
        if match:
            a, b = map(int, match.groups())
            assert not second_rows.intersection(range(a, b + 1))


def test_targeted_read_rejects_truncated_ranges(monkeypatch):
    store = CloudDispatchStore(FakeSpreadsheet())
    batch = store.save_batch(ready_batch())
    ws = store.spreadsheet.sheets[BATCH_SHEET]
    monkeypatch.setattr(ws, "batch_get", lambda ranges: [[] for _ in ranges])
    with pytest.raises(DispatchError, match="讀取不完整"):
        store.get_batch(batch["id"])
