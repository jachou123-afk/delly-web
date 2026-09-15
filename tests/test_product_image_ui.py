from copy import deepcopy

from streamlit.testing.v1 import AppTest

from dispatch_storage import CloudDispatchStore
from product_images import PRODUCT_IMAGE_SHEET
from test_dispatch_ui import widget


def app_source(bound=False, multi=False, imported=False):
    return f'''
import streamlit as st
from dispatch_ui import render_dispatch_manager
from dispatch_manager import new_batch
from dispatch_storage import CloudDispatchStore
from dispatch_fakes import FakeSpreadsheet, product_rows
from test_product_image_library import picture, assignment
st.set_page_config(layout="wide")
if "test_spreadsheet" not in st.session_state:
    rows = product_rows(range(1, 70))
    spreadsheet = FakeSpreadsheet(rows)
    store = CloudDispatchStore(spreadsheet)
    products = store.catalog()
    if {bound!r}:
        store.save_product_images([assignment(products[0], [picture(n) for n in range({2 if multi else 1})])], actor="合成測試", origin="test")
    batch = new_batch("合成圖片測試 69 款", "測試群組・不會發送", products, "測試人")
    store.save_batch(batch)
    st.session_state["test_spreadsheet"] = spreadsheet
    st.session_state["dispatch_active"] = batch["id"]
    if {imported!r}:
        st.session_state["dispatch_source_images_" + batch["id"]] = {{"images": {{
            products[0]["identity"]: [picture()], products[1]["identity"]: [picture(1), picture(2)]}}, "warnings": []}}
render_dispatch_manager(lambda: CloudDispatchStore(st.session_state["test_spreadsheet"]))
'''


def start(**kwargs):
    app = AppTest.from_string(app_source(**kwargs), default_timeout=15).run()
    assert not app.exception
    return app


def test_bound_images_auto_preview_without_writing_or_marking_review():
    app = start(bound=True, multi=True)
    spreadsheet = app.session_state["test_spreadsheet"]
    before = {k: deepcopy(ws.rows) for k, ws in spreadsheet.sheets.items()}
    assert len(widget(app, "multiselect", "採用的原表／圖片包圖片").value) == 2
    assert any("1／69 款已有配對圖片" in c.value for c in app.caption)
    assert not widget(app, "checkbox", "我已核對原文、圖片、售價、單位與交期，確認圖文是同一款商品").value
    assert widget(app, "button", "確認本批內容，建立待發清單").disabled
    widget(app, "button", "全選本批（69 款）").click().run()
    assert before == {k: ws.rows for k, ws in spreadsheet.sheets.items()}
    saved = CloudDispatchStore(spreadsheet).list_batches()[0]
    assert not saved["items"][0]["images"] and not saved["items"][0]["review"]


def test_save_unique_package_then_reload_autoloads_without_upload_again():
    app = start(imported=True)
    spreadsheet = app.session_state["test_spreadsheet"]
    quote_before = deepcopy(spreadsheet.sheets["G正版"].rows)
    assert widget(app, "button", "保存 1 款唯一配對圖片到圖庫").disabled is False
    widget(app, "button", "保存 1 款唯一配對圖片到圖庫").click().run()
    assert not app.exception
    assert len(CloudDispatchStore(spreadsheet).product_image_bindings()) == 1
    widget(app, "button", "重新載入雲端").click().run()
    assert not app.exception and len(widget(app, "multiselect", "採用的原表／圖片包圖片").value) == 1
    assert any("1／69 款已有配對圖片" in c.value for c in app.caption)
    assert spreadsheet.sheets["G正版"].rows == quote_before
    assert {m.label: m.value for m in app.metric}["可發送"] == "0"


def test_multiple_unbound_candidates_need_explicit_choice_before_binding():
    app = start(imported=True)
    choose = widget(app, "selectbox", "逐款核對")
    choose.set_value("G正版:no2").run()
    pictures = widget(app, "multiselect", "採用的原表／圖片包圖片")
    assert pictures.value == []
    assert widget(app, "button", "保存本款圖片到圖庫").disabled
    pictures.set_value(list(pictures.options)).run()
    widget(app, "button", "保存本款圖片到圖庫").click().run()
    assert not app.exception
    binding = CloudDispatchStore(app.session_state["test_spreadsheet"]).product_image_bindings()["G正版:no2"]
    assert len(binding["assets"]) == 2
    assert not widget(app, "checkbox", "我已核對原文、圖片、售價、單位與交期，確認圖文是同一款商品").value


def test_library_read_error_is_not_reported_as_missing_images(monkeypatch):
    app = start(bound=True)
    spreadsheet = app.session_state["test_spreadsheet"]
    spreadsheet.sheets[PRODUCT_IMAGE_SHEET].rows[1][6] += "bad"
    widget(app, "button", "重新載入雲端").click().run()
    assert not app.exception and any("圖庫讀取失敗" in e.value for e in app.error)
    assert any("狀態未能讀取" in c.value for c in app.caption)
    assert not any("0／69 款已有配對圖片" in c.value for c in app.caption)
    assert not any("採用的原表／圖片包圖片" == m.label for m in app.multiselect)


def test_quote_upload_interface_is_present_and_empty_is_optional():
    from test_v74_ui import paste, VALID, save_button
    app = paste(VALID)
    assert any("本款原圖" in m.value for m in app.markdown)
    assert not any("圖片" in e.value for e in app.error)
    confirm = next(w for w in app.checkbox if "確認新增這 1 款商品" in w.label)
    confirm.check().run()
    assert not save_button(app).disabled


def test_category_ui_saves_explicit_code_and_invalidates_catalog():
    app = start()
    widget(app, "checkbox", "顯示維護工具").check().run()
    spreadsheet = app.session_state["test_spreadsheet"]
    from dispatch_fakes import FakeWorksheet, product_rows
    spreadsheet.sheets["D吊飾"] = FakeWorksheet("D吊飾", product_rows((1,), category="D吊飾")["D吊飾"])
    widget(app, "text_input", "新增對應的商品分頁").set_value("D吊飾")
    widget(app, "text_input", "廣告分類代碼").set_value("D")
    widget(app, "text_input", "分類設定人").set_value("合成測試").run()
    widget(app, "button", "保存分類代碼").click().run()
    assert not app.exception
    assert CloudDispatchStore(spreadsheet).category_settings()["codes"]["D吊飾"] == "D"
    assert not any("尚未設定分頁代號" in p["errors"] for p in CloudDispatchStore(spreadsheet).catalog())
