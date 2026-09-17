from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile

import pytest

from dispatch_fakes import FakeSpreadsheet, ready_batch, image_data
from dispatch_storage import CloudDispatchStore, validate_image
from dispatch_manager import approve_batch, record_observation, batch_digest, DispatchError
from dispatch_workflow import progress_detail, pending_items, combined_copy, build_dispatch_package


def setup_batch():
    store = CloudDispatchStore(FakeSpreadsheet())
    batch = ready_batch(store)
    return store, approve_batch(batch, store.catalog(), [], "測試")


def observe(batch, part, index=0):
    return record_observation(batch, item_id=batch["items"][index]["id"], part=part,
                              evidence="9/17 15:00 已查看 LINE", actor="測試", target=batch["target"])


def test_vague_exclusion_does_not_invent_a_product_defect_or_delivery():
    _, batch = setup_batch()
    item = batch["items"][0]
    item.update(excluded=True, reason="其餘59款已發或有疑點，本批只安排9款文字修正候選", images=[])
    before = deepcopy(batch)
    detail = progress_detail(batch, item)
    assert detail["狀態"] == "原因待釐清"
    assert detail["具體情況"] == "未記錄本款具體排除原因"
    assert "尚未加入商品圖片" not in detail["具體情況"]
    assert detail["原始排除備註"] == item["reason"]
    assert batch == before


def test_specific_exclusion_and_uncertain_evidence_are_readable():
    _, batch = setup_batch()
    uncertain = observe(batch, "uncertain")
    assert progress_detail(uncertain, uncertain["items"][0])["具體情況"] == "9/17 15:00 已查看 LINE"
    batch["items"][0].update(excluded=True, reason="售價單位：盒或個尚未確認")
    assert progress_detail(batch, batch["items"][0])["具體情況"] == "售價單位：盒或個尚未確認"


def test_missing_receipt_is_not_proof_of_missing_delivery_and_history_is_separate():
    _, batch = setup_batch()
    old = observe(observe(batch, "image"), "text")
    old["id"] = "other"
    old["name"] = "昨天批次"
    row = progress_detail(batch, batch["items"][0], [old])
    assert "不代表一定未發" in row["具體情況"]
    assert "昨天批次（已確認完成）" in row["同聊天室其他批次紀錄"]
    assert row["狀態"] == "待發"
    old["target"] = "另一個群組"
    assert progress_detail(batch, batch["items"][0], [old])["同聊天室其他批次紀錄"] == "未找到其他批次紀錄"


def test_actionable_items_omit_excluded_uncertain_complete_duplicates_and_data_errors():
    _, batch = setup_batch()
    batch = observe(observe(batch, "image"), "text")
    batch = observe(batch, "uncertain", 1)
    batch["items"][2]["excluded"] = True
    batch["items"][3]["copy"] += "\n售價1元/盒"
    assert [i["id"] for i in pending_items(batch)] == [batch["items"][4]["id"]]
    assert progress_detail(batch, batch["items"][3])["狀態"] == "資料需處理"


def test_zip_is_ordered_preserves_original_bytes_and_only_includes_missing_parts():
    store, batch = setup_batch()
    batch = observe(batch, "image", 0)
    batch = observe(batch, "text", 1)
    before = deepcopy(batch)
    ids = [batch["items"][1]["id"], batch["items"][0]["id"]]
    data = build_dispatch_package(batch, ids, store.get_asset)
    with ZipFile(BytesIO(data)) as archive:
        names = archive.namelist()
        assert names[:2] == ["001_BGD-G-1126/文案.txt", "002_BGD-G-1127/圖片01.png"]
        assert archive.read(names[0]).decode("utf-8-sig") == batch["items"][0]["copy"]
        assert archive.read(names[1]) == image_data()
        assert archive.read("整批文案.txt").decode("utf-8-sig") == batch["items"][0]["copy"]
        assert batch["target"] in archive.read("發送順序與說明.txt").decode("utf-8-sig")
    assert batch == before


@pytest.mark.parametrize("mode", ["excluded", "uncertain", "tampered", "draft", "completed", "empty"])
def test_zip_rejects_invalid_scope_before_reading_images(mode):
    _, batch = setup_batch()
    ids = [batch["items"][0]["id"]]
    if mode == "excluded":
        batch["items"][0]["excluded"] = True
        batch["approved_digest"] = batch_digest(batch)
    elif mode == "uncertain":
        batch = observe(batch, "uncertain")
    elif mode == "tampered":
        batch["items"][0]["copy"] += "改字"
    elif mode == "empty":
        ids = []
    else:
        batch["status"] = mode
    def no_read(_):
        pytest.fail("invalid scope must not read images")
    with pytest.raises(DispatchError):
        build_dispatch_package(batch, ids, no_read)


def test_zip_rejects_wrong_image_hash_and_size_limit():
    store, batch = setup_batch()
    ids = [batch["items"][0]["id"]]
    wrong = validate_image(image_data(), "wrong.png")
    wrong["data"] = "bm90LWFuLWltYWdl"
    with pytest.raises(DispatchError, match="圖片校驗"):
        build_dispatch_package(batch, ids, lambda _: wrong)
    with pytest.raises(DispatchError, match="64 MB"):
        build_dispatch_package(batch, ids, store.get_asset, max_bytes=1)
    from PIL import Image
    output = BytesIO()
    Image.new("RGB", (10, 10), "red").save(output, format="PNG")
    valid_but_wrong = validate_image(output.getvalue(), "other.png")
    with pytest.raises(DispatchError, match="配對不符"):
        build_dispatch_package(batch, ids, lambda _: valid_but_wrong)


def test_combined_copy_preserves_all_selected_adverts():
    _, batch = setup_batch()
    assert combined_copy(batch["items"]).split("\n\n──────────\n\n") == [i["copy"] for i in batch["items"]]
