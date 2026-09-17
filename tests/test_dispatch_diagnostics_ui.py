from copy import deepcopy

from streamlit.testing.v1 import AppTest

from test_dispatch_ui import widget


def app_source():
    return '''
import streamlit as st
from dispatch_ui import render_dispatch_manager
from dispatch_diagnostics_ui import _choose_problem
from dispatch_storage import CloudDispatchStore
from dispatch_manager import new_batch
from dispatch_fakes import FakeSpreadsheet, product_rows
st.set_page_config(layout="wide")
if "test_spreadsheet" not in st.session_state:
    rows = product_rows((1, 2, 3))
    rows["G正版"][1][1] = "包裝：彩盒"
    st.session_state["test_spreadsheet"] = FakeSpreadsheet(rows)
    store = CloudDispatchStore(st.session_state["test_spreadsheet"])
    products = store.catalog()
    batch = new_batch("合成問題顯示", "合成群組", products, "測試人",
                      [p["key"] for p in products[:2]], "本次範圍外，不代表有錯")
    store.save_batch(batch)
    st.session_state["dispatch_active"] = batch["id"]
store = CloudDispatchStore(st.session_state["test_spreadsheet"])
render_dispatch_manager(lambda: store)
batch = store.list_batches()[0]
def click_second_problem():
    st.session_state["synthetic_problem_selection"] = {"selection": {"rows": [1]}}
    _choose_problem("synthetic_problem_selection", [{"id": i["id"]} for i in batch["items"][:2]], batch)
st.button("合成點選第二列", on_click=click_second_problem)
'''


def problem_table(app):
    return next(d.value for d in app.dataframe if "阻擋原因" in d.value.columns)


def test_problem_table_shows_only_active_scope_with_blockers_and_reminders_separate():
    app = AppTest.from_string(app_source(), default_timeout=15).run()
    before = {k: deepcopy(v.rows) for k, v in app.session_state["test_spreadsheet"].sheets.items()}
    widget(app, "button", "全選待核對（2 款）").click().run()
    widget(app, "button", "驗算所選商品（2 款）").click().run()
    assert not app.exception
    table = problem_table(app)
    assert table["品號"].tolist() == ["BGD-G-1", "BGD-G-2"]
    assert "售價單位" in table.iloc[0]["阻擋原因"]
    assert "商品圖片" in table.iloc[0]["阻擋原因"]
    assert "廠商原文" not in table.iloc[0]["阻擋原因"]
    assert "缺廠商原文" in table.iloc[0]["提醒（非算價錯誤）"]
    assert any("目前資料：原表：裝箱 300個/箱" in v.value for v in app.markdown)
    assert any("修正方式：對照廠商原文" in v.value for v in app.markdown)
    assert any("操作位置：" in v.value and "單位確認依據" in v.value for v in app.caption)
    assert widget(app, "button", "確認本批內容，建立待發清單").disabled
    assert {k: v for k, v in before.items() if k != '_發送批次'} == {
        k: v.rows for k, v in app.session_state["test_spreadsheet"].sheets.items() if k != '_發送批次'}


def test_click_problem_focuses_correct_item_and_expands_editor_without_writes():
    app = AppTest.from_string(app_source(), default_timeout=15).run()
    before = {k: deepcopy(v.rows) for k, v in app.session_state["test_spreadsheet"].sheets.items()}
    widget(app, "button", "合成點選第二列").click().run()
    assert not app.exception
    assert widget(app, "selectbox", "查看商品").value == "G正版:no2"
    assert app.session_state["dispatch_problem_open_" + app.session_state["dispatch_active"]]
    assert any("第 2 款｜BGD-G-2" == s.value for s in app.subheader)
    assert before == {k: v.rows for k, v in app.session_state["test_spreadsheet"].sheets.items()}


def test_invalid_table_selection_does_not_jump_or_change_scope():
    app = AppTest.from_string('''
import streamlit as st
from dispatch_diagnostics_ui import _choose_problem
st.session_state["selection_test"] = {"selection": {"rows": [99]}}
_choose_problem("selection_test", [{"id": "one"}], {"id": "batch"})
''').run()
    assert not app.exception
    assert "dispatch_problem_open_batch" not in app.session_state
