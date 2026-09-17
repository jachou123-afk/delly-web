from copy import deepcopy
from streamlit.testing.v1 import AppTest

from dispatch_storage import CloudDispatchStore
from test_dispatch_ui import app_source, widget


def run_app():
    return AppTest.from_string(app_source(approved=True), default_timeout=20).run()


def check_form(app):
    widget(app, "text_input", "本次 LINE 訊息時間／核對依據").set_value("9/17 15:20 逐款對照 LINE 圖文")
    widget(app, "checkbox", "我已查看「測試群組」，所選商品的圖片和文案都已出現").check()


def test_landing_is_batch_first_and_read_only_with_concrete_next_actions():
    app = run_app()
    assert not app.exception
    store = CloudDispatchStore(app.session_state["test_spreadsheet"])
    before = deepcopy(store.list_batches())
    assert widget(app, "radio", "查看範圍").value == "待處理"
    assert len(widget(app, "multiselect", "本次一起處理的商品").value) == 5
    assert not any(w.label == "目前要處理的商品" for w in app.selectbox)
    rows = app.dataframe[0].value
    assert len(rows) == 5 and "具體情況" in rows and "下一步" in rows
    widget(app, "radio", "查看範圍").set_value("本批排除").run()
    assert any("此範圍沒有商品" in i.value for i in app.info)
    assert store.list_batches() == before


def test_bulk_registration_requires_confirmation_and_can_close_in_one_save():
    app = run_app()
    store = CloudDispatchStore(app.session_state["test_spreadsheet"])
    widget(app, "button", "一次保存所選商品的發送紀錄").click().run()
    assert any("請先確認" in e.value for e in app.error)
    assert not store.list_batches()[0]["observations"]
    check_form(app)
    widget(app, "button", "一次保存所選商品的發送紀錄").click().run()
    assert not app.exception
    saved = store.list_batches()[0]
    assert saved["status"] == "completed"
    assert all(len(i["image_receipts"]) == len(i["text_receipts"]) == 1 for i in saved["items"])


def test_subset_registration_does_not_touch_unselected_items_or_finish():
    app = run_app()
    selector = widget(app, "multiselect", "本次一起處理的商品")
    selector.set_value(selector.value[:2]).run()
    check_form(app)
    widget(app, "button", "一次保存所選商品的發送紀錄").click().run()
    assert not app.exception
    saved = CloudDispatchStore(app.session_state["test_spreadsheet"]).list_batches()[0]
    assert saved["status"] == "in_progress"
    assert sum(bool(i["text_receipts"]) for i in saved["items"]) == 2
    assert len(widget(app, "multiselect", "本次一起處理的商品").value) == 3


def test_package_preparation_is_read_only_and_selection_invalidates_download():
    app = run_app()
    store = CloudDispatchStore(app.session_state["test_spreadsheet"])
    before = deepcopy(store.list_batches())
    widget(app, "button", "核對雲表並準備整批圖文").click().run()
    assert not app.exception and not app.error
    assert any("尚未發送" in s.value for s in app.success)
    assert "dispatch_download_package" in app.session_state
    assert len(app.code) == 1 and "BGD-G-1130" in app.code[0].value
    selector = widget(app, "multiselect", "本次一起處理的商品")
    selector.set_value(selector.value[:1]).run()
    assert not app.code
    assert store.list_batches() == before


def test_changed_source_blocks_entire_package_without_saved_results():
    app = run_app()
    app.session_state["test_spreadsheet"].sheets["G正版"].rows[0][1] = "其他品名"
    widget(app, "button", "核對雲表並準備整批圖文").click().run()
    assert not app.exception
    assert any("來源資料或列位置已變更" in e.value for e in app.error)
    assert "dispatch_download_package" not in app.session_state


def test_other_device_update_blocks_package_and_receipt_save():
    from dispatch_manager import record_observation
    app = run_app()
    store = CloudDispatchStore(app.session_state["test_spreadsheet"])
    original = store.list_batches()[0]
    other = record_observation(original, item_id=original["items"][0]["id"], part="image",
                               evidence="另一台已查驗", actor="另一人", target=original["target"])
    store.save_batch(other, expected_revision=original["_revision"])
    widget(app, "button", "核對雲表並準備整批圖文").click().run()
    assert any("另一台裝置已更新" in e.value for e in app.error)
    check_form(app)
    widget(app, "button", "一次保存所選商品的發送紀錄").click().run()
    assert app.error
    assert len(store.list_batches()[0]["observations"]) == 1


def test_excluded_items_are_separate_and_reasons_fully_readable():
    source = app_source(approved=True).replace(
        'batch = approve_batch(batch,',
        'batch["items"][0].update(excluded=True, reason="其餘59款已發或有疑點，本批只安排9款文字修正候選")\n'
        '        batch = approve_batch(batch,')
    app = AppTest.from_string(source, default_timeout=20).run()
    assert not app.exception
    assert len(app.dataframe[0].value) == 4
    assert len(widget(app, "multiselect", "本次一起處理的商品").value) == 4
    widget(app, "radio", "查看範圍").set_value("本批排除").run()
    assert len(app.dataframe[0].value) == 1
    assert app.dataframe[0].value.iloc[0]["狀態"] == "原因待釐清"
    assert any("沒有逐款說明" in w.value for w in app.warning)
    assert any("原始排除備註：其餘59款" in m.value for m in app.markdown)
