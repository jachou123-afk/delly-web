from streamlit.testing.v1 import AppTest


def app_source(ready=False, approved=False, fail=False):
    return f'''
import streamlit as st
from dispatch_ui import render_dispatch_manager
from dispatch_storage import CloudDispatchStore
from dispatch_manager import approve_batch
from dispatch_fakes import FakeSpreadsheet, ready_batch, product_rows
st.set_page_config(layout="wide")
if "test_spreadsheet" not in st.session_state:
    st.session_state["test_spreadsheet"] = FakeSpreadsheet(product_rows((1126, 1127, 1128, 1129, 1130)))
    initial_store = CloudDispatchStore(st.session_state["test_spreadsheet"])
    if {ready or approved!r}:
        batch = ready_batch(initial_store)
        if {approved!r}:
            batch = approve_batch(batch, initial_store.catalog(), [], "測試核對人")
        initial_store.save_batch(batch)
        st.session_state["dispatch_active"] = batch["id"]
def store_factory():
    if {fail!r}:
        raise RuntimeError("模擬讀取失敗")
    return CloudDispatchStore(st.session_state["test_spreadsheet"])
render_dispatch_manager(store_factory)
'''


def widget(app, kind, label):
    return next(w for w in getattr(app, kind) if w.label == label)


def legacy_app_source():
    return '''
import streamlit as st
from dispatch_ui import render_dispatch_manager
from dispatch_storage import CloudDispatchStore, validate_image
from dispatch_manager import new_batch
from dispatch_fakes import FakeSpreadsheet, image_data, product_rows
if "test_spreadsheet" not in st.session_state:
    rows = product_rows((1126, 1127))
    for offset in (1, 7):
        rows["G正版"][offset][1] = "包裝:彩盒\\n材質:陶瓷"
    spreadsheet = FakeSpreadsheet(rows)
    st.session_state["test_spreadsheet"] = spreadsheet
    store = CloudDispatchStore(spreadsheet)
    batch = new_batch("舊資料核對", "測試群組", store.catalog(), "測試人")
    for item in batch["items"]:
        source = item["source"]
        item["source"] = {k: v for k, v in source.items() if k in ("key", "identity", "category", "row", "no", "number", "code", "name", "vendor", "date", "supplier_code", "source_hash")}
        item["source"].update(errors=["雲表中的計價單位缺失或不唯一"], copy="")
        item["copy"] = ""
    store.save_batch(batch)
    st.session_state["dispatch_active"] = batch["id"]
    asset = validate_image(image_data(), "測試原圖.png")
    st.session_state["dispatch_source_images_" + batch["id"]] = {"images": {i["id"]: [asset] for i in batch["items"]}, "warnings": []}
render_dispatch_manager(lambda: CloudDispatchStore(st.session_state["test_spreadsheet"]))
'''


def test_old_draft_can_compare_source_confirm_unit_and_save_then_advance():
    from copy import deepcopy
    from dispatch_storage import CloudDispatchStore
    from dispatch_manager import item_errors
    app = AppTest.from_string(legacy_app_source(), default_timeout=15).run()
    assert not app.exception
    before = deepcopy(app.session_state["test_spreadsheet"].sheets["G正版"].rows)
    assert "售價53元/個" in widget(app, "text_area", "LINE 文案").value
    assert any("逐欄核對表" in value.value for value in app.markdown)
    assert widget(app, "checkbox", "我已核對原文、圖片、售價、單位與交期，確認圖文是同一款商品").disabled
    widget(app, "checkbox", "我已確認售價按「每個」計價，與裝箱單位相同").check().run()
    widget(app, "text_input", "單位確認依據").set_value("已對照廠商原文，售價與装箱均以個計算").run()
    widget(app, "checkbox", "我已核對原文、圖片、售價、單位與交期，確認圖文是同一款商品").check().run()
    widget(app, "button", "確認並下一款").click().run()
    assert not app.exception
    assert "1127" in widget(app, "selectbox", "逐款核對").value
    saved = CloudDispatchStore(app.session_state["test_spreadsheet"]).list_batches()[0]
    assert not item_errors(saved["items"][0])
    assert saved["status"] == "draft" and saved["items"][0]["image_receipts"] == []
    assert app.session_state["test_spreadsheet"].sheets["G正版"].rows == before


def test_source_change_is_visible_and_prevents_review_until_refresh():
    app = AppTest.from_string(app_source(ready=True), default_timeout=15).run()
    app.session_state["test_spreadsheet"].sheets["G正版"].rows[0][1] = "來源改為另一規格"
    widget(app, "button", "重新載入雲端").click().run()
    assert not app.exception
    assert any("原報價表與這份草稿不同" in value.value for value in app.warning)
    assert widget(app, "checkbox", "我已核對原文、圖片、售價、單位與交期，確認圖文是同一款商品").disabled
    assert widget(app, "button", "確認並下一款").disabled
    assert widget(app, "button", "確認本批內容，建立待發清單").disabled


def test_management_landing_is_read_only_and_read_error_does_not_look_empty():
    app = AppTest.from_string(app_source()).run()
    assert not app.exception
    assert set(app.session_state["test_spreadsheet"].sheets) == {"G正版"}
    failed = AppTest.from_string(app_source(fail=True)).run()
    assert not failed.exception
    assert any("模擬讀取失敗" in e.value for e in failed.error)
    assert not any(b.label == "建立雲端草稿" for b in failed.button)


def test_select_source_create_draft_and_reload_keeps_all_products():
    app = AppTest.from_string(app_source(), default_timeout=15).run()
    widget(app, "button", "載入報價表商品").click().run()
    widget(app, "selectbox", "目標聊天室").set_value("【自動排廣告群組】").run()
    widget(app, "text_input", "核對人").set_value("測試人").run()
    widget(app, "button", "建立雲端草稿").click().run()
    assert not app.exception
    assert widget(app, "button", "確認本批內容，建立待發清單").disabled
    assert widget(app, "checkbox", "我已核對原文、圖片、售價、單位與交期，確認圖文是同一款商品").disabled
    widget(app, "button", "重新載入雲端").click().run()
    assert not app.exception
    assert len(widget(app, "selectbox", "逐款核對").options) == 5
    assert any("1127" in option for option in widget(app, "selectbox", "逐款核對").options)


def test_draft_cannot_approve_unsaved_copy_changes_or_reuse_review_checkbox():
    app = AppTest.from_string(app_source(ready=True), default_timeout=15).run()
    assert not app.exception
    assert not widget(app, "button", "確認本批內容，建立待發清單").disabled
    original = widget(app, "text_area", "LINE 文案").value
    widget(app, "text_area", "LINE 文案").set_value(original + "\n顏色混裝").run()
    assert not widget(app, "checkbox", "我已核對原文、圖片、售價、單位與交期，確認圖文是同一款商品").value
    assert widget(app, "button", "確認本批內容，建立待發清單").disabled


def test_default_batch_name_follows_date_but_preserves_custom_name():
    app = AppTest.from_string(app_source(), default_timeout=15).run()
    app.session_state["test_spreadsheet"].sheets["G正版"].rows[1][0] = "2026/9/13"
    widget(app, "button", "載入報價表商品").click().run()
    assert widget(app, "text_input", "批次名稱").value == "2026-09-13 廣告"
    widget(app, "multiselect", "商品日期").set_value(["2026-09-12"]).run()
    assert widget(app, "text_input", "批次名稱").value == "2026-09-12 廣告"
    widget(app, "text_input", "批次名稱").set_value("週末精選・第一批").run()
    widget(app, "multiselect", "商品日期").set_value(["2026-09-13"]).run()
    assert widget(app, "text_input", "批次名稱").value == "週末精選・第一批"
    assert not app.exception
    assert set(app.session_state["test_spreadsheet"].sheets) == {"G正版"}


def test_ready_draft_can_be_approved_but_no_item_becomes_sent():
    app = AppTest.from_string(app_source(ready=True), default_timeout=15).run()
    widget(app, "checkbox", "我已確認整批商品、圖文內容、順序及目標聊天室").check()
    widget(app, "button", "確認本批內容，建立待發清單").click().run()
    assert not app.exception
    assert {m.label: m.value for m in app.metric}["已確認完成"] == "0"
    assert widget(app, "button", "完成本批對帳").disabled
    assert any("已確認・待發送" in option for option in widget(app, "selectbox", "廣告批次").options)


def test_visual_confirmation_is_target_specific_and_tracks_only_one_part():
    app = AppTest.from_string(app_source(approved=True), default_timeout=15).run()
    widget(app, "text_input", "請輸入實際核對的聊天室名稱").set_value("錯誤群組")
    widget(app, "text_input", "LINE 訊息日期時間／核對依據").set_value("測試 20:35 已對照圖片")
    widget(app, "checkbox", "我已實際查看 LINE 紀錄，以上是查驗結果").check()
    widget(app, "button", "儲存核對結果").click().run()
    assert any("正確聊天室" in e.value for e in app.error)
    widget(app, "text_input", "請輸入實際核對的聊天室名稱").set_value("測試群組")
    widget(app, "button", "儲存核對結果").click().run()
    assert not app.exception
    assert {m.label: m.value for m in app.metric}["已確認完成"] == "0"
    assert any("圖片已確認" in s.value for s in app.success)
    widget(app, "button", "重新載入雲端").click().run()
    assert any("圖片已確認" in s.value for s in app.success)
    assert any("部分完成" in option for option in widget(app, "selectbox", "目前要處理的商品").options)


def test_main_app_can_switch_to_dispatch_without_loading_cost_settings():
    import ast
    from test_v74_ui import app_source as quote_app_source
    tree = ast.parse(quote_app_source())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "get_dispatch_store":
            node.body = ast.parse("return CloudDispatchStore(FakeSpreadsheet())").body
        elif isinstance(node, ast.FunctionDef) and node.name == "get_settings_cached":
            node.body = ast.parse("raise AssertionError('dispatch page must not load cost settings')").body
    ast.fix_missing_locations(tree)
    source = "from dispatch_fakes import FakeSpreadsheet\n" + ast.unparse(tree)
    app = AppTest.from_string(source, default_timeout=15)
    app.session_state["tool_page"] = "📣 發送管理"
    app.run()
    assert not app.exception
    assert any(h.value == "📣 發送管理" for h in app.header)
    assert any(b.label == "載入報價表商品" for b in app.button)
