from copy import deepcopy

import pytest

from dispatch_manager import (
    DispatchError, approve_batch, catalog, edit_item, finish_batch, item_errors,
    item_status, new_batch, prior_activity, reconciliation, record_observation,
    sequence_gaps, source_changes,
)
from dispatch_fakes import product_rows, ready_batch


def record(batch, item_id, part, **kwargs):
    return record_observation(batch, item_id=item_id, part=part,
                              evidence="測試：已查驗 20:30 的對應訊息", actor="測試核對人",
                              target=batch["target"], **kwargs)


def approved(numbers=(1126, 1127, 1128, 1129, 1130)):
    batch = ready_batch(numbers=numbers)
    return approve_batch(batch, catalog(product_rows(numbers)), [], "測試核對人")


def test_1127_1129_exist_in_manifest_and_reconciliation_catches_entire_skipped_range():
    batch = approved()
    for n in (1126, 1130):
        batch = record(batch, f"G正版:no{n}", "image")
        batch = record(batch, f"G正版:no{n}", "text")
    report = reconciliation(batch)
    assert report["expected"] == 5 and report["complete"] == 2
    assert report["missing"] == ["BGD-G-1127", "BGD-G-1128", "BGD-G-1129"]
    assert not report["can_finish"]
    with pytest.raises(DispatchError):
        finish_batch(batch, "測試", "已看總數")


@pytest.mark.parametrize("vendor", ["v多品村", "v菲凡", "優娜卡樂星", "自訂供應商"])
@pytest.mark.parametrize("category", ["G正版", "S生活用品"])
def test_reads_common_sheet_output_for_each_vendor_without_source_grammar(vendor, category):
    products = catalog(product_rows((1,), vendor, category))
    assert len(products) == 1 and not products[0]["errors"]
    assert products[0]["vendor"] == vendor
    assert "售價53元/個" in products[0]["copy"]


def test_sequence_gap_is_advisory_and_does_not_create_phantom_products():
    products = catalog(product_rows((1126, 1130)))
    assert sequence_gaps(products) == ["G正版：no1127～no1129"]
    assert len(approved((1126, 1130))["items"]) == 2


def test_scope_exclusions_remain_visible_and_require_reason():
    products = catalog(product_rows((1, 2)))
    with pytest.raises(DispatchError, match="排除原因"):
        new_batch("測試", "測試群組", products, "測試", [products[0]["key"]])
    batch = new_batch("測試", "測試群組", products, "測試", [products[0]["key"]], "本批暫緩")
    assert len(batch["items"]) == 2
    assert item_status(batch["items"][1]) == "已排除"


def test_unknown_category_and_duplicate_no_are_not_silently_dropped():
    unknown = catalog(product_rows((1,), category="未設定分頁"))
    assert len(unknown) == 1 and unknown[0]["errors"]
    duplicate = catalog(product_rows((1, 1)))
    assert all(p["errors"] for p in duplicate)
    with pytest.raises(DispatchError, match="重複 NO"):
        new_batch("測試", "測試群組", duplicate, "測試")


@pytest.mark.parametrize("field", ["copy", "images"])
def test_change_after_review_invalidates_confirmation(field):
    batch = ready_batch(numbers=(1,))
    item = batch["items"][0]
    if field == "copy":
        item["copy"] += "\n顏色混裝"
    else:
        item["images"] = ["different-image"]
    assert any("尚未逐款核對" in e for e in item_errors(item))
    with pytest.raises(DispatchError):
        approve_batch(batch, catalog(product_rows((1,))), [], "測試")


@pytest.mark.parametrize("change", ["price", "row", "delete", "duplicate"])
def test_approval_refuses_changed_or_ambiguous_source(change):
    batch = ready_batch(numbers=(1,))
    sheets = product_rows((1,))
    if change == "price":
        sheets["G正版"][1][2] = "60"
    elif change == "row":
        sheets["G正版"].insert(0, [])
    elif change == "delete":
        sheets["G正版"] = []
    else:
        sheets["G正版"] += deepcopy(sheets["G正版"])
    assert source_changes(batch, catalog(sheets))
    with pytest.raises(DispatchError):
        approve_batch(batch, catalog(sheets), [], "測試")


def test_post_approval_content_and_target_changes_block_result_registration():
    for change in ("text", "target"):
        batch = approved((1,))
        if change == "text":
            batch["items"][0]["copy"] += "改動"
        else:
            batch["target"] = "其他群組"
        with pytest.raises(DispatchError, match="內容遭變更"):
            record(batch, "G正版:no1", "image")


def test_same_target_needs_explicit_repeat_reason_but_other_target_is_independent():
    old = approved((1,))
    batch = ready_batch(numbers=(1,))
    assert prior_activity(batch, batch["items"][0], [old])
    with pytest.raises(DispatchError, match="再次安排原因"):
        approve_batch(batch, catalog(product_rows((1,))), [old], "測試")
    batch["items"][0]["duplicate_note"] = "確認為另一期活動"
    assert approve_batch(batch, catalog(product_rows((1,))), [old], "測試")["status"] == "approved"
    batch["target"] = "另一個群組"
    assert not prior_activity(batch, batch["items"][0], [old])


def test_partial_receipt_does_not_complete_item_and_duplicate_registration_is_blocked():
    batch = record(approved((1,)), "G正版:no1", "image", action_id="one-action")
    assert item_status(batch["items"][0]) == "部分完成"
    assert reconciliation(batch)["partial"] == ["BGD-G-1：缺文案"]
    assert record(batch, "G正版:no1", "image", action_id="one-action") == batch
    with pytest.raises(DispatchError, match="已有確認"):
        record(batch, "G正版:no1", "image")


def test_batch_observation_only_fills_missing_parts_without_duplicates():
    from dispatch_manager import record_observation_batch

    batch = record(approved((1, 2)), "G正版:no1", "image")
    result = record_observation_batch(
        batch, item_ids=["G正版:no1", "G正版:no2"], actor="測試",
        evidence="逐款查看 LINE 匯出紀錄", target=batch["target"], action_id="retro",
    )
    first, second = result["items"][:2]
    assert len(first["image_receipts"]) == len(first["text_receipts"]) == 1
    assert len(second["image_receipts"]) == len(second["text_receipts"]) == 1
    assert reconciliation(result)["complete"] == 2
    assert any(event["action"] == "事後批次補登已核對 LINE 圖文" for event in result["audit"])


def test_uncertain_result_blocks_retry_until_resolution_is_documented():
    batch = record(approved((1,)), "G正版:no1", "image")
    batch = record(batch, "G正版:no1", "uncertain")
    with pytest.raises(DispatchError, match="先查明"):
        record(batch, "G正版:no1", "text")
    batch = record(batch, "G正版:no1", "resolve")
    assert item_status(batch["items"][0]) == "部分完成"
    assert batch["items"][0]["image_receipts"]
    batch = record(batch, "G正版:no1", "text")
    assert finish_batch(batch, "測試", "逐款核對完畢")["status"] == "completed"


def test_receipt_requires_correct_target_and_cannot_mark_another_product():
    batch = approved((1,))
    with pytest.raises(DispatchError, match="正確聊天室"):
        record_observation(batch, item_id="G正版:no1", part="image", actor="測試", evidence="紀錄", target="錯誤群組")
    with pytest.raises(DispatchError, match="不在本批"):
        record(batch, "G正版:no999", "text")


@pytest.mark.parametrize("bad", ["BGD-G-999", "售價1元/盒", "進價10元", "國際運費5元", "木架另加15元"])
def test_changed_identity_price_or_internal_fields_cannot_be_reviewed(bad):
    batch = ready_batch(numbers=(1,))
    item = batch["items"][0]
    with pytest.raises(DispatchError):
        edit_item(batch, item["id"], text=item["copy"] + "\n" + bad, images=item["images"],
                  excluded=False, reason="", reviewed=True, actor="測試")


def test_unexpected_send_prevents_count_only_completion():
    batch = record(approved((1,)), "G正版:no1", "image")
    batch = record(batch, "G正版:no1", "text")
    batch = record(batch, "", "unexpected")
    report = reconciliation(batch)
    assert report["expected"] == report["complete"] == 1
    assert report["unexpected"] and not report["can_finish"]
    exception_id = batch["observations"][-1]["id"]
    batch = record(batch, exception_id, "resolve_unexpected")
    assert reconciliation(batch)["can_finish"]
    assert any(e["part"] == "unexpected" for e in batch["observations"])
    with pytest.raises(DispatchError, match="未處理的異常"):
        record(batch, exception_id, "resolve_unexpected")
