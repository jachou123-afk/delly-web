from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from batch_approval import confirm_prepared_batch, preparation_issues, prepare_batch
from dispatch_fakes import FakeSpreadsheet, product_rows, image_data, seed_evidence
from dispatch_manager import DispatchError, approve_batch, batch_digest, new_batch, reconciliation
from dispatch_storage import CloudDispatchStore, EVIDENCE_SHEET, IMAGE_SHEET
from test_dispatch_ui import widget
from test_product_image_library import assignment, picture


def setup_batch(count=2, evidence=False):
    store = CloudDispatchStore(FakeSpreadsheet(product_rows(range(1, count + 1))))
    sources = store.catalog()
    if evidence:
        seed_evidence(store)
    store.save_product_images([assignment(p, [picture()]) for p in sources], actor="測試", origin="test")
    batch = new_batch("合成整批確認", "測試群組", sources, "測試")
    references = store.product_image_refs(sources)["images"]
    reports = {k: v["report"] for k, v in store.cost_audits(sources).items()}
    return store, batch, references, reports


def test_prepare_69_adopts_exact_bindings_without_cloud_writes_or_manual_reviews():
    store, batch, refs, reports = setup_batch(69)
    original = deepcopy(batch)
    before = {k: deepcopy(w.rows) for k, w in store.spreadsheet.sheets.items()}
    prepared, adopted = prepare_batch(batch, refs, reports)
    assert len(adopted) == 69
    assert all(i["images"] and i["review"] is None for i in prepared["items"])
    assert all(not i["cost_audit"]["source_ready"] for i in prepared["items"])
    assert not preparation_issues(prepared, store.catalog(), [])
    assert batch == original
    assert before == {k: w.rows for k, w in store.spreadsheet.sheets.items()}


def test_transient_package_candidates_are_never_silently_adopted():
    store, batch, refs, reports = setup_batch()
    refs[batch["items"][0]["id"]] = [picture()]
    prepared, adopted = prepare_batch(batch, refs, reports)
    assert prepared["items"][0]["images"] == []
    assert batch["items"][0]["id"] not in adopted
    assert len(preparation_issues(prepared, store.catalog(), [])) == 1


def test_saved_batch_image_overrides_library_and_excluded_item_not_adopted():
    store, batch, refs, reports = setup_batch()
    batch["items"][0]["images"] = [store.put_asset(image_data(), "original.png")]
    batch["items"][1].update(excluded=True, reason="測試暫緩")
    prepared, adopted = prepare_batch(batch, refs, reports)
    assert not adopted
    assert prepared["items"][0]["images"] == batch["items"][0]["images"]
    assert prepared["items"][1]["images"] == []


def test_one_batch_approval_does_not_fake_original_review_or_line_receipts():
    store, batch, refs, reports = setup_batch()
    prepared, adopted = prepare_batch(batch, refs, reports)
    with pytest.raises(DispatchError, match="缺廠商原文"):
        confirm_prepared_batch(store, prepared, adopted, "測試")
    approved = confirm_prepared_batch(store, prepared, adopted, "測試", acknowledge_missing_source=True)
    assert approved["status"] == "approved"
    assert approved["approved_digest"] == batch_digest(approved)
    assert approved["batch_confirmation"]["missing_source_ids"] == [i["id"] for i in batch["items"]]
    assert all(i["review"] is None and not i["cost_audit"]["source_ready"] for i in approved["items"])
    assert reconciliation(approved)["complete"] == 0
    assert approved["observations"] == [] and store.list_batches() == []
    assert EVIDENCE_SHEET not in store.spreadsheet.sheets
    saved = store.save_batch(approved)
    assert store.list_batches()[0] == saved
    assert saved["items"][0]["image_receipts"] == saved["items"][0]["text_receipts"] == []


@pytest.mark.parametrize("change, expected", [
    (lambda i: i.update(images=[]), "商品圖片"),
    (lambda i: i.update(images=[str(n) for n in range(6)]), "最多 5 張"),
    (lambda i: i.update(copy=i["copy"].replace("售價53", "售價54")), "售價需與雲表一致"),
    (lambda i: i.update(copy=i["copy"] + "\n到手成本47.6"), "內部成本"),
    (lambda i: i["source"].update(unit_mode="legacy"), "待確認計價單位"),
    (lambda i: i["cost_audit"].update(math_pass=False), "成本驗算尚未通過"),
    (lambda i: i["cost_audit"].update(errors=["原始依據已變更"]), "原始依據已變更"),
    (lambda i: i.update(cost_audit=None), "成本尚未獨立驗算"),
])
def test_batch_approval_still_blocks_substantive_errors(change, expected):
    store, batch, refs, reports = setup_batch()
    prepared, _ = prepare_batch(batch, refs, reports)
    change(prepared["items"][0])
    with pytest.raises(DispatchError, match=expected):
        approve_batch(prepared, store.catalog(), [], "測試", batch_review=True, acknowledge_missing_source=True)


def test_duplicate_target_and_changed_source_still_block():
    store, batch, refs, reports = setup_batch()
    prepared, _ = prepare_batch(batch, refs, reports)
    old = deepcopy(prepared)
    old.update(id="other", status="approved")
    assert "再次安排原因" in str(preparation_issues(prepared, store.catalog(), [old]))
    with pytest.raises(DispatchError, match="再次安排原因"):
        approve_batch(prepared, store.catalog(), [old], "測試", batch_review=True, acknowledge_missing_source=True)
    old["target"] = "其他群組"
    assert not preparation_issues(prepared, store.catalog(), [old])
    store.spreadsheet.sheets["G正版"].rows[0][1] = "已改品名"
    with pytest.raises(DispatchError, match="來源資料"):
        approve_batch(prepared, store.catalog(), [], "測試", batch_review=True, acknowledge_missing_source=True)


@pytest.mark.parametrize("change", ["formula", "evidence", "binding", "asset", "source"])
def test_last_moment_cloud_changes_fail_closed_without_saving(change):
    store, batch, refs, reports = setup_batch(evidence=True)
    prepared, adopted = prepare_batch(batch, refs, reports)
    if change == "formula":
        formulas = store.read_cost_source(batch["items"][0]["source"])
        formulas[1][10] += "+0"
        store.spreadsheet.sheets["G正版"].formula_override = formulas
    elif change == "evidence":
        store.spreadsheet.sheets[EVIDENCE_SHEET].rows = store.spreadsheet.sheets[EVIDENCE_SHEET].rows[:1]
    elif change == "binding":
        source = batch["items"][0]["source"]
        saved = store.product_image_bindings()[source["identity"]]
        plan = assignment(source, [picture(1)])
        plan["expected_revision"] = saved["_revision"]
        store.save_product_images([plan], actor="其他人", origin="test", replace=True)
    elif change == "asset":
        store.spreadsheet.sheets[IMAGE_SHEET].rows[1][6] += "bad"
    else:
        store.spreadsheet.sheets["G正版"].rows[0][1] = "改變規格"
    with pytest.raises(DispatchError):
        confirm_prepared_batch(store, prepared, adopted, "測試", acknowledge_missing_source=True)
    assert store.list_batches() == []


def test_no_change_to_strict_legacy_approval_path():
    store, batch, refs, reports = setup_batch(evidence=True)
    prepared, _ = prepare_batch(batch, refs, reports)
    with pytest.raises(DispatchError, match="尚未逐款核對"):
        approve_batch(prepared, store.catalog(), [], "測試")


def test_ui_one_confirmation_for_all_images_without_per_item_saves():
    app = AppTest.from_string('''
import streamlit as st
from test_batch_approval import setup_batch
from dispatch_ui import render_dispatch_manager
from dispatch_storage import CloudDispatchStore
if 'test_spreadsheet' not in st.session_state:
    store, batch, _, _ = setup_batch(3)
    store.save_batch(batch)
    st.session_state['test_spreadsheet'] = store.spreadsheet
    st.session_state['dispatch_active'] = batch['id']
render_dispatch_manager(lambda: CloudDispatchStore(st.session_state['test_spreadsheet']))
''', default_timeout=15).run()
    assert not app.exception
    before = {k: deepcopy(w.rows) for k, w in app.session_state["test_spreadsheet"].sheets.items()}
    assert not next(e for e in app.expander if e.label == "查看／修改單款（需要時再展開）").proto.expanded
    assert not next(e for e in app.expander if e.label == "報價表原文、參數與詳細算式").proto.expanded
    assert not any(w.label == "本次核對人" for w in app.text_input)
    assert not any(w.label.startswith("我已核對原文、圖片") for w in app.checkbox)
    widget(app, "button", "全選本批（3 款）").click().run()
    widget(app, "button", "驗算所選商品（3 款）").click().run()
    assert not app.exception
    assert {m.label: m.value for m in app.metric}["待整批確認"] == "3"
    assert not any("尚未儲存的修改" in w.value for w in app.warning)
    assert before == {k: w.rows for k, w in app.session_state["test_spreadsheet"].sheets.items()}
    confirm = widget(app, "button", "確認本批內容，建立待發清單")
    assert not confirm.disabled
    confirm.click().run()
    assert any("先勾選整批確認" in e.value for e in app.error)
    next(w for w in app.checkbox if "並知悉 3 款缺廠商原文" in w.label).check()
    widget(app, "button", "確認本批內容，建立待發清單").click().run()
    assert not app.exception
    saved = CloudDispatchStore(app.session_state["test_spreadsheet"]).list_batches()[0]
    assert saved["status"] == "approved"
    assert all(i["review"] is None and i["images"] for i in saved["items"])
    assert {m.label: m.value for m in app.metric}["已確認完成"] == "0"
