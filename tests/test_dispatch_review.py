from copy import deepcopy

import pytest

from dispatch_fakes import product_rows, ready_batch
from dispatch_manager import approve_batch, catalog, content_digest, edit_item, item_errors, new_batch
from dispatch_review import comparison_rows, hydrate_draft, unit_confirmed
from line_ad_copy import build_line_ad_copy_from_sheet_block


def legacy_products(unit="盒", vendor="其他供應商"):
    rows = product_rows((1,), vendor)["G正版"]
    rows[1][1] = "包裝:彩盒\n材質:陶瓷"
    rows[2][1] = f"裝箱 24{unit}/箱"
    return catalog({"G正版": rows})


@pytest.mark.parametrize("unit", ["個", "盒", "套", "瓶", "罐", "包", "袋"])
@pytest.mark.parametrize("vendor", ["v多品村", "優娜卡樂星", "其他供應商"])
def test_legacy_units_are_preview_candidates_not_approved_price_units(unit, vendor):
    products = legacy_products(unit, vendor)
    source = products[0]
    assert f"售價53元/{unit}" in source["copy"]
    assert not source["errors"]
    batch = new_batch("測試", "測試群組", products, "測試")
    item = batch["items"][0]
    assert not unit_confirmed(item)
    assert any("待確認計價單位" in e for e in item_errors(item))
    with pytest.raises(ValueError, match="計價單位"):
        build_line_ad_copy_from_sheet_block("G正版", source["block"])
    updated = edit_item(batch, item["id"], text=item["copy"], images=["image"], excluded=False,
                        reason="", reviewed=True, actor="測試", confirmed_unit=unit,
                        unit_evidence="已核對供應商原報價，按相同單位計價")
    assert unit_confirmed(updated["items"][0])
    assert approve_batch(updated, products, [], "測試")["status"] == "approved"
    assert updated["items"][0]["source"]["block"] == source["block"]


def test_conflicting_explicit_units_are_not_bypassed_by_carton_fallback():
    rows = product_rows((1,))["G正版"]
    for details in ("計價單位：個\n計價單位：盒", "計價單位：盒", "計價單位：", "計價單位：個/盒"):
        rows[1][1] = details
        source = catalog({"G正版": rows})[0]
        assert source["errors"] and not source["copy"]


def test_root_error_does_not_cascade_into_empty_copy_identity_or_price_errors():
    rows = product_rows((1,))["G正版"]
    rows[1][2] = "999"
    item = new_batch("測試", "測試群組", catalog({"G正版": rows}), "測試")["items"][0]
    errors = item_errors(item)
    assert any("成本不一致" in e for e in errors)
    assert not any("文案品號缺失" in e or "售價需與" in e or "裝箱需與" in e for e in errors)


def test_unit_confirmation_requires_matching_unit_evidence_and_current_source():
    batch = new_batch("測試", "測試群組", legacy_products(), "測試")
    item = batch["items"][0]
    for unit, evidence in (("個", "已核對"), ("盒", "")):
        with pytest.raises(ValueError):
            edit_item(batch, item["id"], text=item["copy"], images=["image"], excluded=False,
                      reason="", reviewed=True, actor="測試", confirmed_unit=unit, unit_evidence=evidence)
    item["unit_confirmation"] = {"unit": "盒", "source_hash": "old", "actor": "測試", "evidence": "已核對"}
    assert not unit_confirmed(item)


def test_same_source_old_draft_hydration_is_read_only_and_keeps_manual_content():
    products = legacy_products()
    batch = new_batch("舊草稿", "測試群組", products, "測試")
    old = batch["items"][0]
    old["source"] = {k: v for k, v in old["source"].items()
                     if k in ("key", "identity", "category", "row", "no", "number", "code", "name", "vendor", "date", "supplier_code", "source_hash")}
    old["source"].update(errors=["雲表中的計價單位缺失或不唯一"], copy="")
    old["copy"] = ""
    before = deepcopy(batch)
    upgraded = hydrate_draft(batch, products)
    assert batch == before
    assert upgraded["items"][0]["copy"] and upgraded["items"][0]["source"]["block"]
    assert not unit_confirmed(upgraded["items"][0])
    batch["items"][0]["copy"] = "人工編輯，不能覆蓋"
    assert hydrate_draft(batch, products)["items"][0]["copy"] == "人工編輯，不能覆蓋"
    changed = deepcopy(products)
    changed[0]["source_hash"] = "different"
    assert hydrate_draft(batch, changed) == batch


def test_approved_batch_hydration_preserves_frozen_content_exactly():
    batch = ready_batch(numbers=(1,))
    approved = approve_batch(batch, catalog(product_rows((1,))), [], "測試")
    assert hydrate_draft(approved, legacy_products()) == approved


def test_comparison_has_source_and_outbound_values_and_preserves_old_digest_shape():
    item = ready_batch(numbers=(1,))["items"][0]
    rows = comparison_rows(item)
    assert len(rows) == 6
    assert all({"核對項目", "報價表／依據", "準備發出的內容", "檢查結果"} == set(row) for row in rows)
    assert "52.9" in rows[2]["報價表／依據"] and rows[2]["準備發出的內容"] == "售價53元/個"
    from dispatch_manager import digest
    item.pop("cost_audit", None)  # A historical V83 record retains its digest shape.
    assert content_digest(item) == digest({k: item[k] for k in ("source", "copy", "images", "excluded", "reason")})
