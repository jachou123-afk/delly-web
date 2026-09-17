from copy import deepcopy
import json

from streamlit.testing.v1 import AppTest

from test_dispatch_ui import widget


def app_source():
    return '''
import streamlit as st
from dispatch_ui import render_dispatch_manager
from dispatch_storage import CloudDispatchStore
from dispatch_manager import new_batch
from dispatch_fakes import FakeSpreadsheet, product_rows
st.set_page_config(layout="wide")
if "test_spreadsheet" not in st.session_state:
    spreadsheet = FakeSpreadsheet(product_rows(range(1, 70)))
    st.session_state["test_spreadsheet"] = spreadsheet
    store = CloudDispatchStore(spreadsheet)
    batch = new_batch("合成測試 69 款", "測試群組・不會發送", store.catalog(), "測試核對人")
    store.save_batch(batch)
    st.session_state["dispatch_active"] = batch["id"]
render_dispatch_manager(lambda: CloudDispatchStore(st.session_state["test_spreadsheet"]))
'''


def start():
    app = AppTest.from_string(app_source(), default_timeout=15).run()
    assert not app.exception
    return app


def result(app):
    return app.session_state["dispatch_bulk_result_" + app.session_state["dispatch_active"]]


def selected(app):
    return app.session_state["dispatch_bulk_selected_" + app.session_state["dispatch_active"]]


def test_all_none_native_checkbox_state_and_detail_focus_are_separate():
    app = start()
    focus = widget(app, "selectbox", "查看商品").value
    assert widget(app, "button", "驗算所選商品（0 款）").disabled
    widget(app, "button", "全選含暫緩（69 款）").click().run()
    assert not app.exception
    assert len(selected(app)) == 69
    assert widget(app, "selectbox", "查看商品").value == focus
    assert len(json.loads(app.dataframe[0].proto.selection_state)["selection"]["rows"]) == 69
    widget(app, "button", "取消全選").click().run()
    assert not selected(app)
    assert json.loads(app.dataframe[0].proto.selection_state)["selection"]["rows"] == []
    assert widget(app, "selectbox", "查看商品").value == focus


def test_all_69_render_and_persist_results_without_changing_quotes_or_reviews():
    app = start()
    before = {k: deepcopy(v.rows) for k, v in app.session_state["test_spreadsheet"].sheets.items()}
    widget(app, "button", "全選含暫緩（69 款）").click().run()
    widget(app, "button", "驗算所選商品（69 款）").click().run()
    assert not app.exception
    assert len(result(app)["rows"]) == 69
    assert len(app.dataframe[1].value) == 69
    assert app.dataframe[1].value["品號"].tolist() == [f"BGD-G-{n}" for n in range(1, 70)]
    assert {m.label: m.value for m in app.metric}["表內重算一致"] == "69"
    assert any("缺原文 69 款" in m.value for m in app.markdown)
    assert {m.label: m.value for m in app.metric}["待整批確認"] == "0"
    assert widget(app, "button", "確認本批內容，建立待發清單").disabled
    from dispatch_storage import CloudDispatchStore
    sheet = app.session_state["test_spreadsheet"]
    assert before['G正版'] == sheet.sheets['G正版'].rows
    saved = CloudDispatchStore(sheet).get_batch(app.session_state['dispatch_active'])
    assert saved['status'] == 'draft' and all(i['review'] is None for i in saved['items'])
    assert len(saved['cost_snapshot']['entries']) == 69


def test_search_never_silently_drops_hidden_selections_or_limits_select_all():
    app = start()
    widget(app, "text_input", "搜尋本批品號／品名").set_value("BGD-G-69").run()
    assert len(app.dataframe[0].value) == 1
    widget(app, "button", "全選含暫緩（69 款）").click().run()
    assert len(selected(app)) == 69
    assert any("68 款未顯示" in i.value for i in app.info)
    widget(app, "text_input", "搜尋本批品號／品名").set_value("找不到的商品").run()
    assert len(selected(app)) == 69
    assert not widget(app, "button", "驗算所選商品（69 款）").disabled
    widget(app, "text_input", "搜尋本批品號／品名").set_value("").run()
    assert len(app.dataframe[0].value) == 69 and len(selected(app)) == 69
    widget(app, "button", "取消全選").click().run()
    assert not selected(app)


def test_changed_selection_labels_old_result_and_reload_restores_timestamp():
    app = start()
    widget(app, "button", "全選含暫緩（69 款）").click().run()
    widget(app, "button", "驗算所選商品（69 款）").click().run()
    widget(app, "button", "取消全選").click().run()
    assert any("下表仍是上次 69 款" in w.value for w in app.warning)
    stamp = result(app)['at']
    widget(app, "button", "重新載入雲端").click().run()
    assert result(app)['at'] == stamp and len(result(app)['rows']) == 69
    assert not any("尚未執行整批驗算" in i.value for i in app.info)


def test_one_changed_source_stays_in_results_instead_of_disappearing():
    app = start()
    app.session_state["test_spreadsheet"].sheets["G正版"].rows[-6][11] = "v不同來源"
    widget(app, "button", "全選含暫緩（69 款）").click().run()
    widget(app, "button", "驗算所選商品（69 款）").click().run()
    assert not app.exception and len(result(app)["rows"]) == 69
    assert result(app)["rows"][-1]["計算結果"] == "驗算已失效"
    assert {m.label: m.value for m in app.metric}["表內重算一致"] == "68"


def test_detail_navigation_updates_mounted_selectbox_and_survives_next_rerun():
    app = AppTest.from_string('''
import streamlit as st
from batch_cost_ui import _focus
from dispatch_ui import _select_item
batch = {"id": "focus-test", "_revision": "saved-revision"}
st.button("點第三款", on_click=lambda: _focus(batch, "three"))
st.button("其他操作")
_select_item("逐款核對", [{"id": i} for i in ("one", "two", "three")], batch, "draft", str)
''').run()
    assert app.selectbox[0].value == "one"
    widget(app, "button", "點第三款").click().run()
    assert not app.exception and app.selectbox[0].value == "three"
    assert app.selectbox[0].proto.set_value
    widget(app, "button", "其他操作").click().run()
    assert not app.exception and app.selectbox[0].value == "three"
