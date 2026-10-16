"""Synthetic price-policy and frozen-version regressions; no production data."""
from copy import deepcopy
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile

import pytest

from dispatch_pricing import (LEGACY, TIERED, tier_price, apply_item_pricing,
                              batch_pricing_errors, pricing_check, pricing_errors)
from dispatch_manager import (DispatchError, apply_batch_pricing, approve_batch,
                              batch_digest, catalog, content_digest, digest,
                              item_errors, new_batch, prior_activity, source_changes)
from dispatch_review import comparison_rows, hydrate_draft
from dispatch_storage import CloudDispatchStore
from dispatch_workbench import prepare_workbench, current_item
from dispatch_fakes import FakeSpreadsheet, product_rows, ready_batch
from batch_cost_audit import batch_signature, run_batch_audit


@pytest.mark.parametrize("cost,margin,price", [
    ("39.9", 18, 49), ("40", 13, 46), ("40.1", 13, 47),
    ("365.4", 13, 420), ("86.13", 13, 99),
    ("86.13000000000000000000001", 13, 100),
    ("0.82", 18, 1), ("0.82000000000000000000001", 18, 2),
])
def test_direct_decimal_ceiling_and_threshold(cost, margin, price):
    assert tier_price(cost) == (Decimal(cost), margin, price)


@pytest.mark.parametrize("value", [True, False, None, "", "NaN", "Infinity", "0", "-1", "=40"])
def test_invalid_costs_fail_closed(value):
    with pytest.raises(ValueError):
        tier_price(value)


def test_apply_to_existing_draft_preserves_public_text_source_reports_and_images():
    store = CloudDispatchStore(FakeSpreadsheet())
    old = ready_batch(store)
    old["items"][0]["copy"] += "\n首批預售，月底出貨\n展示盒尺寸 10×10×10cm"
    before = deepcopy(old)
    changed = apply_batch_pricing(old, TIERED, "測試人")
    assert old == before
    assert changed["id"] == old["id"]
    for previous, item in zip(old["items"], changed["items"]):
        assert item["source"] == previous["source"]
        assert item["cost_audit"] == previous["cost_audit"]
        assert item["images"] == previous["images"]
        assert item["image_receipts"] == item["text_receipts"] == []
        assert item["review"] is None
        assert [x for x in item["copy"].splitlines() if not x.startswith("售價")] == [
            x for x in previous["copy"].splitlines() if not x.startswith("售價")]
        assert "售價55元/個" in item["copy"]
        assert not item_errors(item, require_review=False)
        assert pricing_check(item, item["cost_audit"])["pass"]
    assert not batch_pricing_errors(changed)
    assert changed["status"] == "draft" and changed["approved_digest"] is None
    assert batch_signature(changed) != batch_signature(old)
    assert comparison_rows(changed["items"][0])[2]["檢查結果"] == "一致"
    assert hydrate_draft(changed, store.catalog())["items"] == changed["items"]


def test_box_unit_and_legacy_quote_validation_are_preserved():
    rows = product_rows((1,))["G正版"]
    rows[0][7] = "重量g/盒"
    rows[1][1] = "計價單位：盒"
    rows[2][1] = "裝箱 24盒/箱"
    products = catalog({"G正版": rows})
    batch = new_batch("合成批次", "合成群", products, "測試", pricing_policy=TIERED)
    assert "售價55元/盒" in batch["items"][0]["copy"]
    assert batch["items"][0]["source"]["price"] == "53"
    rows[1][2] = "55"  # Replacing C with an advertising price remains invalid.
    assert catalog({"G正版": rows})[0]["errors"]


def test_legacy_digest_shape_and_existing_approved_batch_are_unchanged():
    batch = ready_batch(numbers=(1,))
    batch["target"] = "【利潤10%】 自動排廣告群組"
    item = batch["items"][0]
    old_content = {k: item[k] for k in ("source", "copy", "images", "excluded", "reason")}
    for key in ("unit_confirmation", "cost_audit"):
        if item.get(key):
            old_content[key] = item[key]
    assert content_digest(item) == digest(old_content)
    expected = digest({"target": batch["target"], "items": [
        {"id": item["id"], "order": item["order"], "content": digest(old_content)}]})
    assert batch_digest(batch) == expected
    approved = approve_batch(batch, catalog(product_rows((1,))), [], "測試")
    before = deepcopy(approved)
    with pytest.raises(DispatchError):
        apply_batch_pricing(approved, TIERED, "測試")
    assert approved == before and approved["approved_digest"] == expected
    assert hydrate_draft(approved, catalog(product_rows((1,)))) == before
    assert "pricing_policy" not in batch and "pricing" not in item


@pytest.mark.parametrize("policy", ["unknown", None, False, {}, []])
def test_unknown_policy_is_not_legacy_fallback(policy):
    with pytest.raises(ValueError):
        apply_batch_pricing(ready_batch(numbers=(1,)), policy, "測試")


@pytest.mark.parametrize("field,value", [
    ("price", 54), ("margin_percent", 18), ("cost", "48"),
    ("source_hash", "stale"), ("unit", "盒"), ("policy", LEGACY),
])
def test_tampered_price_snapshot_or_source_version_blocks(field, value):
    store = CloudDispatchStore(FakeSpreadsheet())
    batch = apply_batch_pricing(ready_batch(store), TIERED, "測試")
    item = batch["items"][0]
    item["pricing"][field] = value
    assert pricing_errors(item)
    with pytest.raises(ValueError):
        approve_batch(batch, store.catalog(), [], "測試", batch_review=True)


def test_copy_only_override_and_missing_item_policy_cannot_bypass_confirmation():
    store = CloudDispatchStore(FakeSpreadsheet())
    batch = apply_batch_pricing(ready_batch(store), TIERED, "測試")
    bad = deepcopy(batch)
    bad["items"][0]["copy"] = bad["items"][0]["copy"].replace("售價55", "售價54")
    assert item_errors(bad["items"][0], require_review=False)
    bad = deepcopy(batch)
    bad["items"][0].pop("pricing")
    bad["items"][0]["copy"] = bad["items"][0]["source"]["copy"]
    assert batch_pricing_errors(bad)
    assert source_changes(bad, store.catalog())
    with pytest.raises(ValueError):
        approve_batch(bad, store.catalog(), [], "測試", batch_review=True)


def test_missing_or_inconsistent_cost_evidence_still_blocks_tiered_price():
    store = CloudDispatchStore(FakeSpreadsheet())
    item = apply_batch_pricing(ready_batch(store), TIERED, "測試")["items"][0]
    item["cost_audit"]["source_ready"] = False
    assert any("原文" in x for x in item_errors(item, require_review=False))
    item["cost_audit"]["math_pass"] = False
    assert not pricing_check(item, item["cost_audit"])["pass"]
    item.pop("cost_audit")
    assert item_errors(item, require_review=False)


def test_repricing_never_discards_existing_receipts_or_uncertain_state():
    batch = ready_batch(numbers=(1,))
    for key in ("image_receipts", "text_receipts", "uncertain"):
        bad = deepcopy(batch)
        bad["items"][0][key] = [{"synthetic": True}]
        before = deepcopy(bad)
        with pytest.raises(DispatchError):
            apply_batch_pricing(bad, TIERED, "測試")
        assert bad == before


def test_tiered_cost_gate_does_not_depend_on_legacy_source_flag():
    products = catalog(product_rows((1,)))
    assert not products[0].get("cost_audit_required")
    batch = new_batch("合成批次", "合成群", products, "測試", pricing_policy=TIERED)
    item = batch["items"][0]
    item["images"] = ["synthetic-image"]
    assert item_errors(item, require_review=False)
    assert not pricing_check(item, None)["pass"]
    with pytest.raises(DispatchError):
        approve_batch(batch, products, [], "測試", batch_review=True)


def test_fresh_cost_audit_approval_second_check_and_zip_share_frozen_new_price():
    store = CloudDispatchStore(FakeSpreadsheet())
    batch = apply_batch_pricing(ready_batch(store), TIERED, "測試")
    audit = run_batch_audit(store, batch, [i["id"] for i in batch["items"]])
    assert all(row["本批重算售價"] == 55 and row["本批定價核對"] == "一致" for row in audit["rows"])
    approved = approve_batch(batch, store.catalog(), [], "測試", batch_review=True)
    saved = store.save_batch(approved)
    run = prepare_workbench(store, saved, [i["id"] for i in saved["items"]], "測試", history_checked=True)
    with ZipFile(BytesIO(run["package"])) as package:
        copies = [package.read(name).decode("utf-8-sig") for name in package.namelist()
                  if name.endswith(".txt") and "/" in name]
    assert copies == [i["copy"] for i in saved["items"]]
    assert all("售價55元/個" in copy for copy in copies)
    tampered = deepcopy(saved)
    tampered["pricing_policy"] = LEGACY
    with pytest.raises(DispatchError):
        current_item(tampered, run)
    store.spreadsheet.sheets["G正版"].rows[1][10] = "48"
    with pytest.raises(DispatchError, match="來源"):
        prepare_workbench(store, saved, run["items"], "測試", history_checked=True)


def test_new_source_requires_refresh_of_pricing_and_preserves_image_identity():
    batch = apply_batch_pricing(ready_batch(numbers=(1,)), TIERED, "測試")
    item = batch["items"][0]
    changed = deepcopy(item)
    changed["source"]["source_hash"] = "synthetic-updated-source"
    changed["source"]["block"][1][10] = "40.1"
    assert pricing_errors(changed)
    renewed = apply_item_pricing(changed, TIERED)
    assert renewed["images"] == item["images"]
    assert renewed["pricing"]["source_hash"] == "synthetic-updated-source"
    assert renewed["pricing"]["price"] == 47
    assert renewed["review"] is None
    assert content_digest(renewed) != content_digest(item)


def test_direct_room_alias_preserves_cross_batch_dedup_and_historical_digest():
    from dispatch_targets import ADVERTISING_TARGET, canonical_target, same_target
    assert ADVERTISING_TARGET == "【直發】自動排廣告群組"
    for old in ("【利潤10%】 自動排廣告群組", "【利潤10%】自動排廣告群組",
                "【自動排廣告群組】", "自動排廣告群組", "【直發】 自動排廣告群組"):
        assert canonical_target(old) == ADVERTISING_TARGET
        history = ready_batch(numbers=(1,))
        history["target"] = old
        history = approve_batch(history, catalog(product_rows((1,))), [], "測試")
        before = deepcopy(history)
        draft = ready_batch(numbers=(1,))
        draft["target"] = ADVERTISING_TARGET
        assert prior_activity(draft, draft["items"][0], [history])
        assert history == before and history["approved_digest"] == batch_digest(history)
    assert not same_target("【利潤15%】自動排廣告群組", ADVERTISING_TARGET)
