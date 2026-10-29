from copy import deepcopy
import hashlib
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest
from PIL import Image

from dispatch_fakes import image_data
from dispatch_manager import DispatchError, record_observation
from dispatch_original_zip import original_zip_reader
from dispatch_storage import BATCH_SHEET, asset_bytes
from dispatch_workbench import card_images, complete_current, prepare_workbench
from test_dispatch_workbench import setup_batch


def packed(entries=None):
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, data in entries or [("original.png", image_data())]:
            if isinstance(name, str) and "\\" in name:
                original_name = name
                name = ZipInfo("placeholder.png")
                name.filename = original_name  # Preserve malformed ZIP names on Windows.
            archive.writestr(name, data)
    return output.getvalue()


def identity():
    return hashlib.sha256(image_data()).hexdigest()


def test_arbitrary_filename_and_folder_preserve_exact_bytes_and_dimensions():
    raw = image_data()
    reader = original_zip_reader(packed([("資料夾/任何檔名.png", raw)]), [identity(), identity()])
    assert asset_bytes(reader(identity())) == raw
    assert Image.open(BytesIO(asset_bytes(reader(identity())))).size == (80, 60)
    with pytest.raises(DispatchError, match="不在本輪"):
        reader("0" * 64)


@pytest.mark.parametrize("entries, expected, message", [
    ([("wrong.png", image_data())], ["0" * 64], "未匹配"),
    ([("original.png", image_data())], [identity(), "0" * 64], "缺少"),
    ([("one.png", image_data()), ("two.png", image_data())], [identity()], "重複圖片"),
    ([("../original.png", image_data())], [identity()], "路徑"),
    ([("/original.png", image_data())], [identity()], "路徑"),
    ([("C:/original.png", image_data())], [identity()], "路徑"),
    ([("copy.txt", b"text")], [identity()], "只能包含"),
    ([("original.png", b"not an image")], [identity()], "圖片損壞"),
])
def test_missing_foreign_duplicate_and_invalid_images_block(entries, expected, message):
    with pytest.raises(DispatchError, match=message):
        original_zip_reader(packed(entries), expected)


def test_corrupt_archive_and_crc_are_rejected():
    with pytest.raises(DispatchError, match="ZIP 損壞"):
        original_zip_reader(b"not zip", [identity()])
    payload = bytearray(packed())
    # Corrupt compressed member bytes without changing the directory metadata.
    name_len = int.from_bytes(payload[26:28], "little")
    payload[30 + name_len + 8] ^= 0x40
    with pytest.raises(DispatchError):
        original_zip_reader(payload, [identity()])


def test_symlink_is_never_read_as_image():
    link = ZipInfo("link.png")
    link.external_attr = (0o120777 << 16)
    with pytest.raises(DispatchError, match="連結"):
        original_zip_reader(packed([(link, image_data())]), [identity()])


@pytest.mark.parametrize("limit", ["MAX_ZIP_BYTES", "MAX_TOTAL_BYTES", "MAX_IMAGE_BYTES", "MAX_ENTRIES"])
def test_archive_limits_block_before_image_processing(monkeypatch, limit):
    monkeypatch.setattr("dispatch_original_zip." + limit, 0)
    with pytest.raises(DispatchError):
        original_zip_reader(packed(), [identity()])


def prepare_uploaded(store, batch, **kwargs):
    return prepare_workbench(store, batch, [i["id"] for i in batch["items"]],
                             "測試執行人", history_checked=True, original_zip=packed(), **kwargs)


def test_nas_outage_uses_verified_originals_without_writing_images_or_receipts(monkeypatch):
    store, batch = setup_batch()
    before = deepcopy(store.spreadsheet.sheets[BATCH_SHEET].rows)
    def unavailable(*args, **kwargs):
        raise AssertionError("NAS must not be used for uploaded originals")
    monkeypatch.setattr(store, "get_asset", unavailable)
    monkeypatch.setattr(store, "put_asset", unavailable)
    run = prepare_uploaded(store, batch)
    assert store.spreadsheet.sheets[BATCH_SHEET].rows == before
    assert run["completed"] == []
    for item in batch["items"]:
        assert card_images(run, item)[0][1] == image_data()
    assert len(run["original_zip_sha256"]) == 64
    result = complete_current(batch, run, batch["items"][0]["id"], observed=True)
    assert result["audit"][-1]["original_zip_sha256"] == run["original_zip_sha256"]
    assert "原圖包 SHA-256" in result["observations"][0]["evidence"]


@pytest.mark.parametrize("mutation", ["source", "formula", "revision", "copy", "partial", "uncertain", "review", "draft"])
def test_uploaded_originals_preserve_all_existing_second_check_guards(mutation):
    store, batch = setup_batch()
    if mutation == "source":
        store.spreadsheet.sheets["G正版"].rows[0][1] = "已變更規格"
    elif mutation == "formula":
        store.spreadsheet.sheets["G正版"].formula_override = [[""] * 12 for _ in range(6)]
    elif mutation == "revision":
        store.save_batch(batch, batch["_revision"])
    elif mutation == "copy":
        batch["items"][0]["copy"] += "改價"
    elif mutation in ("partial", "uncertain"):
        changed = record_observation(batch, item_id=batch["items"][0]["id"],
                                     part="image" if mutation == "partial" else "uncertain",
                                     evidence="實測合成結果", actor="測試人", target=batch["target"])
        batch = store.save_batch(changed, batch["_revision"])
    elif mutation == "review":
        batch["items"][0]["review"] = None
        batch = store.save_batch(batch, batch["_revision"])
    elif mutation == "draft":
        batch["status"] = "draft"
    with pytest.raises(DispatchError):
        prepare_uploaded(store, batch)


def test_empty_or_conflicting_reader_cannot_fall_back_to_nas():
    store, batch = setup_batch()
    with pytest.raises(DispatchError, match="兩種"):
        prepare_uploaded(store, batch, read_asset=store.get_asset)
    with pytest.raises(DispatchError, match="原圖 ZIP"):
        prepare_workbench(store, batch, [batch["items"][0]["id"]], "測試",
                           history_checked=True, original_zip=b"")


def test_uploaded_originals_cannot_bypass_other_batch_duplicate_history(monkeypatch):
    store, batch = setup_batch()
    other = deepcopy(batch)
    other["id"] = "another-batch"
    monkeypatch.setattr(store, "list_batches", lambda: [batch, other])
    with pytest.raises(DispatchError, match="其他安排／發送紀錄"):
        prepare_uploaded(store, batch)
