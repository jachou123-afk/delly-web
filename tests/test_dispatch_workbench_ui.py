from streamlit.testing.v1 import AppTest

from dispatch_storage import BATCH_SHEET, CloudDispatchStore
from dispatch_workbench_ui import RUN_KEY
from test_dispatch_ui import app_source, widget


def start():
    app = AppTest.from_string(app_source(approved=True), default_timeout=20).run()
    widget(app, "checkbox", "已核對 LINE「測試群組」與未發範圍，且本輪只有我執行").check()
    widget(app, "button", "第二次檢查並開啟工作台").click().run()
    assert not app.exception and not app.error
    return app


def test_one_card_done_next_keeps_second_check_and_never_auto_sends():
    app = start()
    store = CloudDispatchStore(app.session_state["test_spreadsheet"])
    assert not store.list_batches()[0]["observations"]
    assert len(app.code) == 1 and "BGD-G-1126" in app.code[0].value
    first_run = app.session_state[RUN_KEY]
    widget(app, "button", "完成並下一則").click().run()
    assert not app.exception and not app.error
    saved = store.list_batches()[0]
    assert len(saved["observations"]) == 2
    assert "BGD-G-1127" in app.code[0].value
    assert app.session_state[RUN_KEY]["checked_at"] == first_run["checked_at"]
    assert len(app.session_state[RUN_KEY]["completed"]) == 1
    assert app.session_state["dispatch_history"][0]["_revision"] == saved["_revision"]


def test_timeout_after_server_write_does_not_advance_or_allow_blind_retry():
    app = start()
    ws = app.session_state["test_spreadsheet"].sheets[BATCH_SHEET]
    ws.fail_append_after_write = True
    widget(app, "button", "完成並下一則").click().run()
    assert not app.exception
    assert any("保存結果待查" in e.value for e in app.error)
    assert app.session_state[RUN_KEY]["blocked"]
    assert not app.session_state[RUN_KEY]["completed"]
    app.run()
    assert not any(b.label == "完成並下一則" for b in app.button)
    assert len(CloudDispatchStore(app.session_state["test_spreadsheet"]).list_batches()[0]["observations"]) == 2


def test_reload_invalidates_ticket_without_receipts():
    app = start()
    widget(app, "button", "重新載入雲端").click().run()
    assert RUN_KEY not in app.session_state
    assert not CloudDispatchStore(app.session_state["test_spreadsheet"]).list_batches()[0]["observations"]


def test_changed_formula_does_not_open_fast_cards():
    app = AppTest.from_string(app_source(approved=True), default_timeout=20).run()
    app.session_state["test_spreadsheet"].sheets["G正版"].formula_override = [[""] * 12 for _ in range(6)]
    widget(app, "checkbox", "已核對 LINE「測試群組」與未發範圍，且本輪只有我執行").check()
    widget(app, "button", "第二次檢查並開啟工作台").click().run()
    assert not app.exception
    assert any("第二次檢查未通過" in e.value for e in app.error)
    assert RUN_KEY not in app.session_state
    assert not any(b.label == "完成並下一則" for b in app.button)
