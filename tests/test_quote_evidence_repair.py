"""Standalone evidence recovery uses synthetic Sheets only, never credentials."""
from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from cost_audit import legacy_inputs
from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_storage import CloudDispatchStore, EVIDENCE_SHEET
from quote_evidence import prepare_evidence_repair, save_evidence_repair


RAW = "  合成廠商原文\n型号:TEST-1\n單價9.3元\n每箱300個\n單重68g  \n"
NOTES = "合成核對人；原文與原表一致，費率採當時公式字面值。"


def fixture():
    sheet = FakeSpreadsheet(product_rows((1, 2)))
    store = CloudDispatchStore(sheet)
    plan = prepare_evidence_repair(store, "G正版:no1")
    inputs = legacy_inputs(plan["source"], plan["formulas"])
    return sheet, store, plan, inputs


def snapshot(sheet):
    return {name: deepcopy(ws.rows) for name, ws in sheet.sheets.items()}


def test_only_evidence_is_saved_and_reopened_with_original_text_and_manual_origin():
    sheet, store, plan, inputs = fixture()
    for name in ("_發送批次", "_商品圖庫", "_人工核對"):
        sheet.add_worksheet(name, 2, 8).rows = [["preserve"]]
    before = snapshot(sheet)
    saved = save_evidence_repair(store, plan, RAW, inputs, notes=NOTES)
    assert {name: ws.rows for name, ws in sheet.sheets.items() if name in before} == before
    assert set(sheet.sheets) - set(before) == {EVIDENCE_SHEET}
    report, _ = CloudDispatchStore(sheet).cost_audit(plan["source"])
    assert report["source_ready"] and report["math_pass"]
    assert report["raw_source"] == RAW
    assert saved["origin"] == "review_attachment" and saved["parsed"] == {}
    assert saved["product"]["no"] == "no1"


@pytest.mark.parametrize("change", ["price", "name", "formula", "duplicate", "snapshot"])
def test_source_changes_and_duplicate_identity_are_blocked_before_any_evidence_write(change):
    sheet, store, plan, inputs = fixture()
    ws = sheet.sheets["G正版"]
    if change == "price":
        ws.rows[1][6] = "10.3"
    elif change == "name":
        ws.rows[0][1] = "另一款商品"
    elif change == "formula":
        ws.formula_override = deepcopy(plan["formulas"])
        ws.formula_override[1][10] = "=ROUND((G2+I2+J2)*5,1)"
    elif change == "duplicate":
        ws.rows.extend(deepcopy(ws.rows[:6]))
    else:
        plan["source"]["no"] = "no2"
    before = snapshot(sheet)
    with pytest.raises(ValueError):
        save_evidence_repair(store, plan, RAW, inputs, notes=NOTES)
    assert snapshot(sheet) == before


@pytest.mark.parametrize("key,value", [
    ("price", "10.3"), ("qty", "30"), ("unit", "套"),
    ("intl_rate", ""), ("unit_g", ""), ("ex_rate", ""),
])
def test_unknown_or_different_inputs_do_not_save_guessed_evidence(key, value):
    sheet, store, plan, inputs = fixture()
    inputs[key] = value
    with pytest.raises(ValueError):
        save_evidence_repair(store, plan, RAW, inputs, notes=NOTES)
    assert EVIDENCE_SHEET not in sheet.sheets


def test_cross_product_formula_is_rejected_even_when_displayed_costs_match():
    sheet, store, plan, inputs = fixture()
    ws = sheet.sheets["G正版"]
    ws.formula_override = deepcopy(plan["formulas"])
    ws.formula_override[1][10] = "=ROUND((G8+I8+J8)*4.8,1)"
    plan = prepare_evidence_repair(store, "G正版:no1")
    with pytest.raises(ValueError, match="跨商品"):
        save_evidence_repair(store, plan, RAW, inputs, notes=NOTES)
    assert EVIDENCE_SHEET not in sheet.sheets


def test_timeout_after_append_then_explicit_retry_is_idempotent():
    sheet, store, plan, inputs = fixture()
    ws = store._sheet(EVIDENCE_SHEET, create=True)
    ws.fail_append_after_write = True
    with pytest.raises(Exception, match="待確認"):
        save_evidence_repair(store, plan, RAW, inputs, notes=NOTES)
    after = snapshot(sheet)
    ws.fail_append_after_write = False
    save_evidence_repair(CloudDispatchStore(sheet), plan, RAW, inputs, notes=NOTES)
    assert snapshot(sheet) == after


def test_same_store_retry_reloads_cloud_instead_of_claiming_cached_evidence():
    sheet, store, plan, inputs = fixture()
    save_evidence_repair(store, plan, RAW, inputs, notes=NOTES)
    ws = sheet.sheets[EVIDENCE_SHEET]
    ws.rows = ws.rows[:1]
    save_evidence_repair(store, plan, RAW, inputs, notes=NOTES)
    assert len(ws.rows) > 1
    report, _ = CloudDispatchStore(sheet).cost_audit(plan["source"])
    assert report["source_ready"] and report["raw_source"] == RAW


def test_final_readback_reloads_storage_after_put_and_rejects_missing_record(monkeypatch):
    sheet, store, plan, inputs = fixture()
    original = store.put_quote_evidence

    def disappears_after_put(*args, **kwargs):
        result = original(*args, **kwargs)
        ws = sheet.sheets[EVIDENCE_SHEET]
        ws.rows = ws.rows[:1]
        return result

    monkeypatch.setattr(store, "put_quote_evidence", disappears_after_put)
    with pytest.raises(ValueError, match="讀回尚未確認"):
        save_evidence_repair(store, plan, RAW, inputs, notes=NOTES)


APP = '''
from copy import deepcopy
import streamlit as st
from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_storage import CloudDispatchStore
from quote_evidence_repair_ui import render_evidence_repair
if 'test_sheet' not in st.session_state:
    st.session_state['test_sheet'] = FakeSpreadsheet(product_rows((1, 2)))
    st.session_state['test_before'] = deepcopy(st.session_state['test_sheet'].sheets['G正版'].rows)
    st.session_state['test_reads'] = 0
def get_store():
    st.session_state['test_reads'] += 1
    if st.session_state.get('test_read_error'):
        raise RuntimeError('synthetic read failure')
    store = CloudDispatchStore(st.session_state['test_sheet'])
    if st.session_state.get('test_save_readback_error'):
        def readback_error(source):
            raise RuntimeError('synthetic evidence readback failure')
        store.cost_audit = readback_error
    return store
render_evidence_repair(get_store)
'''


def widget(app, kind, label):
    return next(item for item in getattr(app, kind) if item.label == label)


def start():
    app = AppTest.from_string(APP, default_timeout=20).run()
    assert not app.exception
    return app


def search(app, query="TEST-1"):
    widget(app, "text_input", "搜尋已保存商品（NO、貨號或品名）").set_value(query)
    widget(app, "button", "搜尋商品").click().run()
    assert not app.exception
    return app


def load(app):
    widget(app, "button", "載入本款原文與參數").click().run()
    assert not app.exception
    return app


def fill(app):
    widget(app, "text_input", "補存核對人").set_value("合成核對人").run()
    widget(app, "text_area", "本款廠商完整原文（含補充費用）").set_value(RAW)
    widget(app, "text_area", "原文核對／參數來源／額外費用處理依據").set_value(NOTES)
    widget(app, "checkbox", "我確認以上是這款商品的來源及參數，不是由售價倒推").check()
    return app


def test_opening_does_not_read_cloud_or_require_quote_settings():
    app = start()
    assert app.session_state["test_reads"] == 0
    assert not app.text_area
    assert [item.label for item in app.button] == ["搜尋商品"]


def test_ui_search_load_and_save_evidence_without_batch_or_product_changes():
    app = load(search(start()))
    assert any("G正版:no1" in item.value and "TEST-1" in item.value for item in app.markdown)
    fill(app)
    widget(app, "button", "保存依據並重新驗算").click().run()
    assert not app.exception
    assert any("原文與參數已存入 Google 雲表" in item.value for item in app.success)
    sheet = app.session_state["test_sheet"]
    assert sheet.sheets["G正版"].rows == app.session_state["test_before"]
    assert set(sheet.sheets) == {"G正版", EVIDENCE_SHEET}
    store = CloudDispatchStore(sheet)
    report, _ = store.cost_audit(store.catalog()[0])
    assert report["raw_source"] == RAW and report["source_ready"]
    assert report["origin"] == "review_attachment"


def test_failed_search_drops_old_target_and_form_and_success_notice():
    app = fill(load(search(start())))
    app.session_state["test_read_error"] = True
    search(app, "TEST-2")
    assert not app.text_area
    assert "quote_evidence_repair_plan" not in app.session_state
    assert "quote_evidence_repair_matches" not in app.session_state
    assert not any(item.label == "保存依據並重新驗算" for item in app.button)
    assert EVIDENCE_SHEET not in app.session_state["test_sheet"].sheets


def test_consecutive_searches_change_selector_and_do_not_reuse_raw_or_confirmation():
    app = fill(load(search(start(), "TEST-1")))
    first_key = widget(app, "selectbox", "選擇要補存原文的商品").key
    search(app, "TEST-2")
    selector = widget(app, "selectbox", "選擇要補存原文的商品")
    assert selector.key != first_key and selector.value == "G正版:no2"
    assert not app.text_area
    load(app)
    assert widget(app, "text_area", "本款廠商完整原文（含補充費用）").value == ""
    assert not widget(app, "checkbox", "我確認以上是這款商品的來源及參數，不是由售價倒推").value


def test_changing_selection_discards_loaded_source_before_showing_another_form():
    app = fill(load(search(start(), "TEST")))
    widget(app, "selectbox", "選擇要補存原文的商品").set_value("G正版:no2").run()
    assert not app.text_area and "quote_evidence_repair_plan" not in app.session_state
    load(app)
    assert widget(app, "text_area", "本款廠商完整原文（含補充費用）").value == ""


def test_duplicate_no_is_not_available_as_a_repair_target():
    app = start()
    ws = app.session_state["test_sheet"].sheets["G正版"]
    ws.rows.extend(deepcopy(ws.rows[:6]))
    search(app, "TEST-1")
    assert any("NO 重複" in item.value for item in app.error)
    assert not app.selectbox and not app.text_area


def test_ui_rejects_a_different_purchase_price_without_saving_evidence():
    app = fill(load(search(start())))
    widget(app, "text_input", "進價（RMB／計價單位）").set_value("10.3")
    widget(app, "button", "保存依據並重新驗算").click().run()
    assert not app.exception
    assert any("進價與核對依據不同" in item.value for item in app.error)
    assert EVIDENCE_SHEET not in app.session_state["test_sheet"].sheets
    assert app.session_state["test_sheet"].sheets["G正版"].rows == app.session_state["test_before"]


def test_readback_failure_retains_raw_and_retry_does_not_append_duplicate_evidence():
    app = fill(load(search(start())))
    app.session_state["test_save_readback_error"] = True
    widget(app, "button", "保存依據並重新驗算").click().run()
    assert not app.exception
    assert any("synthetic evidence readback failure" in item.value for item in app.error)
    assert not any("原文與參數已存入 Google 雲表" in item.value for item in app.success)
    assert widget(app, "text_area", "本款廠商完整原文（含補充費用）").value == RAW
    after = snapshot(app.session_state["test_sheet"])
    app.session_state["test_save_readback_error"] = False
    widget(app, "button", "保存依據並重新驗算").click().run()
    assert not app.exception
    assert any("原文與參數已存入 Google 雲表" in item.value for item in app.success)
    assert snapshot(app.session_state["test_sheet"]) == after


def test_main_routes_to_standalone_evidence_without_cloud_cost_settings_or_parser():
    import ast
    from test_v74_ui import app_source
    tree = ast.parse(app_source())
    forbidden = {"get_dispatch_store", "get_settings_cached", "parse_text"}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in forbidden:
            node.body = ast.parse("raise AssertionError('initial source-only tab must be lazy')").body
    ast.fix_missing_locations(tree)
    app = AppTest.from_string(ast.unparse(tree), default_timeout=20)
    app.session_state["tool_page"] = "📄 原文補存"
    app.run()
    assert not app.exception and not app.error
    assert any(item.value == "原文補存" for item in app.subheader)
    assert widget(app, "button", "搜尋商品")
    assert not app.number_input and not app.text_area
