from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from cost_audit import audit, evidence_status, legacy_inputs
from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_storage import CloudDispatchStore, EVIDENCE_SHEET, decode_records
from quote_evidence import PENDING_KEY, clear_evidence_caches, queue_evidence, save_evidence


def fixture():
    store = CloudDispatchStore(FakeSpreadsheet(product_rows((1,))))
    source = store.catalog()[0]
    formulas = store.read_cost_source(source)
    state = {}
    identity, pending = queue_evidence(state, "G正版", 1, formulas,
                                      "  简体廠商原文\n\n進價9.3元、68g、300個/箱\n", legacy_inputs(source, formulas),
                                      {"price": 9.3}, "當時費率0／8.5，匯率4.8")
    return store, source, state, identity, pending


def test_exact_original_and_versioned_inputs_survive_new_session_without_quote_edits():
    store, source, state, identity, pending = fixture()
    before = deepcopy(store.spreadsheet.sheets["G正版"].rows)
    save_evidence(store, pending)
    fresh = CloudDispatchStore(store.spreadsheet)
    report, _ = fresh.cost_audit(source)
    assert report["raw_source"] == pending["raw_source"]
    assert report["saved_at"]
    assert report["saved_evidence"]["product"]["code"] == "BGD-G-1"
    assert report["saved_evidence"]["parsed"]["price"] == 9.3
    assert evidence_status(report) == "原文已保存，待核對"
    changed = deepcopy(pending)
    changed["raw_source"] += "新補充文字"
    save_evidence(fresh, changed)
    versions = decode_records(store.spreadsheet.sheets[EVIDENCE_SHEET].rows[1:])
    assert len(versions) == 2 and versions[0]["value"]["raw_source"] == pending["raw_source"]
    assert store.spreadsheet.sheets["G正版"].rows == before


def test_timeout_after_write_retry_is_idempotent_and_never_recreates_product():
    store, source, state, identity, pending = fixture()
    ws = store._sheet(EVIDENCE_SHEET, create=True)
    ws.fail_append_after_write = True
    before = deepcopy(store.spreadsheet.sheets["G正版"].rows)
    with pytest.raises(ValueError, match="不要重複新增"):
        save_evidence(store, pending)
    assert identity in state[PENDING_KEY]
    rows_after_timeout = deepcopy(ws.rows)
    save_evidence(CloudDispatchStore(store.spreadsheet), pending)
    assert ws.rows == rows_after_timeout
    assert store.spreadsheet.sheets["G正版"].rows == before


def test_pending_snapshot_is_immutable_to_draft_edits_and_rejects_changed_product():
    store, source, state, identity, pending = fixture()
    separate = deepcopy(pending["inputs"])
    other_state = {}
    _, queued = queue_evidence(other_state, "G正版", 1, pending["expected_block"], pending["raw_source"],
                               separate, {}, pending["notes"])
    separate["price"] = "999"
    assert queued["inputs"]["price"] == "9.3"
    store.spreadsheet.sheets["G正版"].rows[0][1] = "不同商品"
    with pytest.raises(ValueError, match="來源內容已變更"):
        save_evidence(store, pending)
    assert EVIDENCE_SHEET not in store.spreadsheet.sheets


def test_missing_stale_and_unread_original_are_distinguished_without_auto_review():
    store, source, state, identity, pending = fixture()
    report, formulas = store.cost_audit(source)
    assert evidence_status(report) == "缺廠商原文"
    assert evidence_status(None) == "尚未確認（讀取未完成）"
    record = save_evidence(store, pending)
    formulas[1][10] += "+0"
    stale = audit(source, formulas, record)
    assert evidence_status(stale) == "已保存舊版，需重新核對"
    assert not stale["source_ready"] and not stale["raw_source"]
    assert stale["saved_evidence"]["raw_source"] == pending["raw_source"]


def test_clear_results_does_not_clear_manual_reviews_or_other_pending_originals():
    state = {"dispatch_cost_a": 1, "dispatch_bulk_result_b": 2, "review": 3, PENDING_KEY: {"a": 4}}
    clear_evidence_caches(state)
    assert state == {"review": 3, PENDING_KEY: {"a": 4}}


def test_real_retry_button_saves_only_pending_evidence_and_clears_it():
    app = AppTest.from_string('''
import streamlit as st
from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_storage import CloudDispatchStore
from cost_audit import legacy_inputs
from quote_evidence import queue_evidence, render_pending_evidence
if 'sheet' not in st.session_state:
    st.session_state['sheet'] = FakeSpreadsheet(product_rows((1,)))
    store = CloudDispatchStore(st.session_state['sheet'])
    source = store.catalog()[0]
    formulas = store.read_cost_source(source)
    queue_evidence(st.session_state, 'G正版', 1, formulas, '合成原文', legacy_inputs(source, formulas), {}, '合成說明')
render_pending_evidence(lambda: CloudDispatchStore(st.session_state['sheet']))
''').run()
    before = deepcopy(app.session_state['sheet'].sheets['G正版'].rows)
    app.button[0].click().run()
    assert not app.exception
    assert not app.session_state[PENDING_KEY]
    assert app.session_state['sheet'].sheets['G正版'].rows == before
    assert EVIDENCE_SHEET in app.session_state['sheet'].sheets


def test_saved_location_link_uses_actual_spreadsheet_and_sheet_ids():
    store, source, state, identity, pending = fixture()
    store.spreadsheet.id, store.spreadsheet.title = "synthetic-id", "合成原雲表"
    save_evidence(store, pending)
    store.spreadsheet.sheets[EVIDENCE_SHEET].id = 42
    report, _ = CloudDispatchStore(store.spreadsheet).cost_audit(source)
    assert report["storage_title"] == "合成原雲表"
    assert report["storage_url"] == "https://docs.google.com/spreadsheets/d/synthetic-id/edit#gid=42"


def test_saved_original_viewer_is_collapsed_read_only_and_shows_saved_time():
    app = AppTest.from_string('''
import streamlit as st
from dispatch_fakes import FakeSpreadsheet, product_rows, seed_evidence
from dispatch_storage import CloudDispatchStore
from cost_audit_ui import render_cost_review
store = CloudDispatchStore(FakeSpreadsheet(product_rows((1,))))
seed_evidence(store)
render_cost_review(store, store.catalog()[0], 'test', '測試人')
''').run()
    assert not app.exception
    viewer = next(e for e in app.expander if e.label == "查看原文與計算參數")
    assert not viewer.proto.expanded
    assert any("原文已保存，供需要時查看" in i.value for i in app.info)
    assert any("保存時間：20" in c.value for c in app.caption)
    assert any("合成廠商原文" in t.value for t in app.text)


def test_supplement_clears_previous_batch_result_without_changing_quote_or_review():
    from test_dispatch_ui import app_source, widget
    app = AppTest.from_string(app_source(ready=True), default_timeout=15).run()
    before = deepcopy(app.session_state["test_spreadsheet"].sheets["G正版"].rows)
    app.session_state["dispatch_bulk_result_test"] = {"old": True}
    widget(app, "text_area", "本款廠商完整原文（含補充費用）").set_value("合成更正原文：進價9.3元，每箱300個，68g")
    widget(app, "text_area", "原文核對／參數來源／額外費用處理依據").set_value("測試費率不變")
    widget(app, "checkbox", "我確認以上是這款商品的來源及參數，不是由售價倒推").check()
    widget(app, "button", "保存依據並重新驗算").click().run()
    assert not app.exception
    assert "dispatch_bulk_result_test" not in app.session_state
    assert app.session_state["test_spreadsheet"].sheets["G正版"].rows == before
    assert not any(w.label.startswith("我已核對原文、圖片") for w in app.checkbox)
