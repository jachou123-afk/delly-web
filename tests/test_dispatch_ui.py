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
from dispatch_fakes import FakeSpreadsheet, image_data, product_rows, seed_evidence
if "test_spreadsheet" not in st.session_state:
    rows = product_rows((1126, 1127))
    for offset in (1, 7):
        rows["G正版"][offset][1] = "包裝:彩盒\\n材質:陶瓷"
    spreadsheet = FakeSpreadsheet(rows)
    st.session_state["test_spreadsheet"] = spreadsheet
    store = CloudDispatchStore(spreadsheet)
    seed_evidence(store)
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


def test_old_draft_can_fix_unknown_unit_without_individual_approval():
    from copy import deepcopy
    from dispatch_storage import CloudDispatchStore
    from dispatch_manager import item_errors
    app = AppTest.from_string(legacy_app_source(), default_timeout=15).run()
    assert not app.exception
    before = deepcopy(app.session_state["test_spreadsheet"].sheets["G正版"].rows)
    assert "售價53元/個" in widget(app, "text_area", "LINE 文案").value
    assert widget(app, "button", "確認本批內容，建立待發清單").disabled
    widget(app, "checkbox", "我已確認售價按「每個」計價，與裝箱單位相同").check().run()
    widget(app, "text_input", "單位確認依據").set_value("已對照廠商原文，售價與装箱均以個計算").run()
    widget(app, "button", "儲存本款修改").click().run()
    assert not app.exception
    assert "1126" in widget(app, "selectbox", "查看商品").value
    saved = CloudDispatchStore(app.session_state["test_spreadsheet"]).list_batches()[0]
    assert not item_errors(saved["items"][0], require_review=False)
    assert saved["items"][0]["review"] is None
    assert saved["status"] == "draft" and saved["items"][0]["image_receipts"] == []
    assert app.session_state["test_spreadsheet"].sheets["G正版"].rows == before


def test_source_change_is_visible_and_prevents_review_until_refresh():
    app = AppTest.from_string(app_source(ready=True), default_timeout=15).run()
    app.session_state["test_spreadsheet"].sheets["G正版"].rows[0][1] = "來源改為另一規格"
    widget(app, "button", "重新載入雲端").click().run()
    assert not app.exception
    assert any("原報價表與這份草稿不同" in value.value for value in app.warning)
    assert not any(w.label == "確認並下一款" for w in app.button)
    assert widget(app, "button", "確認本批內容，建立待發清單").disabled


def test_cost_panel_exposes_amounts_and_missing_original_requires_batch_acknowledgment():
    from dispatch_storage import EVIDENCE_SHEET
    app = AppTest.from_string(app_source(ready=True), default_timeout=15).run()
    assert {m.label: m.value for m in app.metric}["原表到手成本（TWD）"] == "47.6"
    assert {m.label: m.value for m in app.metric}["獨立重算成本（TWD）"] == "47.6"
    evidence_ws = app.session_state["test_spreadsheet"].sheets[EVIDENCE_SHEET]
    evidence_ws.rows = evidence_ws.rows[:1]
    widget(app, "button", "重新載入雲端").click().run()
    assert not app.exception
    assert any("缺廠商原文" in w.value for w in app.warning)
    assert any("並知悉 1 款缺廠商原文" in w.label for w in app.checkbox)
    assert not any(w.label == "確認並下一款" for w in app.button)


def test_v84_can_supplement_original_once_without_overwriting_quote_or_marking_review():
    from copy import deepcopy
    from dispatch_storage import CloudDispatchStore, EVIDENCE_SHEET
    app = AppTest.from_string(app_source(ready=True), default_timeout=15).run()
    spreadsheet = app.session_state["test_spreadsheet"]
    spreadsheet.sheets[EVIDENCE_SHEET].rows = spreadsheet.sheets[EVIDENCE_SHEET].rows[:1]
    before = deepcopy(spreadsheet.sheets["G正版"].rows)
    widget(app, "button", "重新載入雲端").click().run()
    widget(app, "text_area", "本款廠商完整原文（含補充費用）").set_value("合成原文：測試收納商品1126，進價9.3元，單重68g，300個/箱")
    widget(app, "text_area", "原文核對／參數來源／額外費用處理依據").set_value("合成歷史記錄：匯率4.8，國際費率8.5，內陸費率0；沒有附加費")
    widget(app, "checkbox", "我確認以上是這款商品的來源及參數，不是由售價倒推").check()
    widget(app, "button", "保存依據並重新驗算").click().run()
    assert not app.exception
    assert spreadsheet.sheets["G正版"].rows == before
    assert "合成原文" in widget(app, "text_area", "本款廠商完整原文（含補充費用）").value
    assert not any(w.label.startswith("我已核對原文、圖片") for w in app.checkbox)
    assert not widget(app, "checkbox", "我已確認整批商品、圖文內容、順序及目標聊天室").value
    assert CloudDispatchStore(spreadsheet).list_batches()[0]["status"] == "draft"


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
    assert not any(w.label.startswith("我已核對原文、圖片") for w in app.checkbox)
    widget(app, "button", "重新載入雲端").click().run()
    assert not app.exception
    assert len(widget(app, "selectbox", "查看商品").options) == 5
    assert any("1127" in option for option in widget(app, "selectbox", "查看商品").options)


def test_draft_cannot_approve_unsaved_copy_changes_or_reuse_review_checkbox():
    app = AppTest.from_string(app_source(ready=True), default_timeout=15).run()
    assert not app.exception
    assert not widget(app, "button", "確認本批內容，建立待發清單").disabled
    original = widget(app, "text_area", "LINE 文案").value
    widget(app, "text_area", "LINE 文案").set_value(original + "\n顏色混裝").run()
    assert any("尚未儲存的修改" in w.value for w in app.warning)
    assert widget(app, "button", "確認本批內容，建立待發清單").disabled


def test_default_batch_name_uses_sequence_and_preserves_custom_name():
    app = AppTest.from_string(app_source(), default_timeout=15).run()
    app.session_state["test_spreadsheet"].sheets["G正版"].rows[1][0] = "2026/9/13"
    widget(app, "button", "載入報價表商品").click().run()
    assert widget(app, "text_input", "批次名稱").value == "商品批次 001"
    widget(app, "multiselect", "商品日期").set_value(["2026-09-12"]).run()
    assert widget(app, "text_input", "批次名稱").value == "商品批次 001"
    widget(app, "text_input", "批次名稱").set_value("週末精選・第一批").run()
    widget(app, "multiselect", "商品日期").set_value(["2026-09-13"]).run()
    assert widget(app, "text_input", "批次名稱").value == "週末精選・第一批"
    assert not app.exception
    assert set(app.session_state["test_spreadsheet"].sheets) == {"G正版"}


def test_sequence_continues_past_legacy_names_and_existing_numbers():
    from dispatch_ui import _next_batch_name
    assert _next_batch_name([]) == "商品批次 001"
    assert _next_batch_name([{"name": "商品 廣告"}]) == "商品批次 002"
    assert _next_batch_name([{"name": "商品批次 009"}, {"name": "週末精選"}]) == "商品批次 010"
    assert _next_batch_name([{"name": "商品批次 999"}]) == "商品批次 1000"


def test_batch_selector_date_uses_taipei_creation_date_without_changing_name():
    from copy import deepcopy
    from dispatch_ui import _batch_label
    batch = {"name": "商品 廣告", "target": "周俊安", "status": "draft",
             "created_at": "2026-09-14T17:30:00+00:00"}
    before = deepcopy(batch)
    assert _batch_label(batch) == "2026/09/15｜商品 廣告｜周俊安｜草稿・待核對"
    assert batch == before
    batch["created_at"] = ""
    assert _batch_label(batch).startswith("日期未記錄｜")


def test_new_batch_refreshes_cloud_sequence_before_save_and_offers_two_chats():
    from copy import deepcopy
    from dispatch_storage import CloudDispatchStore
    from dispatch_fakes import ready_batch
    app = AppTest.from_string(app_source(), default_timeout=15).run()
    widget(app, "button", "載入報價表商品").click().run()
    assert widget(app, "text_input", "批次名稱").value == "商品批次 001"
    destination = widget(app, "selectbox", "目標聊天室")
    assert destination.options == ["周俊安", "【自動排廣告群組】"]
    assert destination.value is None
    assert widget(app, "button", "建立雲端草稿").disabled
    # Another computer creates a batch after this form was opened.
    spreadsheet = app.session_state["test_spreadsheet"]
    store = CloudDispatchStore(spreadsheet)
    other = ready_batch(store)
    other["name"] = "商品批次 007"
    store.save_batch(other)
    quote_before = deepcopy(spreadsheet.sheets["G正版"].rows)
    destination.set_value("周俊安")
    widget(app, "text_input", "核對人").set_value("測試人").run()
    widget(app, "button", "建立雲端草稿").click().run()
    assert not app.exception
    new = next(b for b in store.list_batches() if b["id"] != other["id"])
    assert new["name"] == "商品批次 008" and new["target"] == "周俊安"
    assert new["status"] == "draft" and all(not i["review"] for i in new["items"])
    assert quote_before == spreadsheet.sheets["G正版"].rows
    assert any(new["created_at"][:10].replace("-", "/") in label
               for label in widget(app, "selectbox", "廣告批次").options)


def test_edit_chat_dropdown_preserves_old_target_until_explicit_save():
    from copy import deepcopy
    from dispatch_storage import CloudDispatchStore
    app = AppTest.from_string(app_source(ready=True), default_timeout=15).run()
    store = CloudDispatchStore(app.session_state["test_spreadsheet"])
    before = deepcopy(store.list_batches()[0])
    destination = widget(app, "selectbox", "目標聊天室")
    assert destination.options[:2] == ["周俊安", "【自動排廣告群組】"]
    assert destination.value == before["target"] == "測試群組"
    assert not any(w.label == "目標聊天室完整名稱" for w in app.text_input)
    destination.set_value("【自動排廣告群組】").run()
    assert store.list_batches()[0] == before
    widget(app, "button", "儲存批次設定").click().run()
    assert not app.exception
    saved = store.list_batches()[0]
    assert saved["target"] == "【自動排廣告群組】"
    assert saved["name"] == before["name"] and saved["created_at"] == before["created_at"]
    assert saved["status"] == "draft" and saved["observations"] == []
    assert widget(app, "selectbox", "目標聊天室").options == ["周俊安", "【自動排廣告群組】"]


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
