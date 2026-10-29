from streamlit.testing.v1 import AppTest


APP = '''
from copy import deepcopy
from unittest.mock import patch
import streamlit as st
import quote_image_append_ui as ui
from test_quote_image_append import fixture
if "store" not in st.session_state:
    sheet, store, net, plan, old, new = fixture()
    st.session_state.update(store=store, sheet=sheet, net=net, plan=plan,
        assets=[new], error=False, io=0, pacing=0, clears=0,
        before={name:deepcopy(ws.rows) for name,ws in sheet.sheets.items()})
def get_store():
    st.session_state["io"] += 1
    return st.session_state["store"]
def paced(store, **kwargs):
    st.session_state["pacing"] += 1
    return store
def clear(): st.session_state["clears"] += 1
with patch.object(ui,"paced_nas_store",paced), patch.object(ui,"clear_library_cache",clear):
    ui.render_append_original(get_store, st.session_state["plan"],st.session_state["assets"],
        selection_errors=st.session_state["error"])
'''


def widget(app, kind, label):
    return next(w for w in getattr(app, kind) if w.label == label)


CONFIRM = "我已核對補充原圖與本款同款，並確認保留既有主圖與順序"
SAVE = "只追加本款補充原圖（保留主圖）"


def start():
    app = AppTest.from_string(APP, default_timeout=20).run()
    assert not app.exception
    return app


def test_open_no_io_and_explicit_confirmation_is_required():
    app = start()
    assert app.session_state["io"] == 0 and app.session_state["pacing"] == 0
    assert widget(app, "button", SAVE).disabled
    assert {k:v.rows for k,v in app.session_state["sheet"].sheets.items()} == app.session_state["before"]
    widget(app,"checkbox",CONFIRM).check().run()
    assert not widget(app,"button",SAVE).disabled
    assert app.session_state["io"] == 0


def test_click_only_appends_and_shows_machine_receipt_after_success():
    app = start()
    widget(app,"checkbox",CONFIRM).check().run()
    widget(app,"button",SAVE).click().run()
    assert not app.exception and not app.error and app.success
    assert app.session_state["io"] == 1 and app.session_state["pacing"] == 1
    assert app.session_state["clears"] == 1
    assert app.code and '"mode": "append_original"' in app.code[0].value
    app.run()
    assert app.session_state["io"] == 1  # A rerun is never another save.


def test_changed_selection_clears_receipt_and_confirmation():
    from test_product_image_library import picture
    app = start()
    widget(app,"checkbox",CONFIRM).check().run()
    widget(app,"button",SAVE).click().run()
    app.session_state["assets"] = [picture(91)]
    app.run()
    assert not app.success and not app.code
    assert widget(app,"button",SAVE).disabled


def test_invalid_selection_or_multiple_images_disables_append():
    from test_product_image_library import picture
    app = start()
    app.session_state["error"] = True
    app.run()
    widget(app,"checkbox",CONFIRM).check().run()
    assert widget(app,"button",SAVE).disabled
    app.session_state["error"] = False
    app.session_state["assets"] = [picture(90), picture(91)]
    app.run()
    widget(app,"checkbox",CONFIRM).check().run()
    assert widget(app,"button",SAVE).disabled


def test_unbound_plan_does_not_offer_append_control():
    app = start()
    plan = dict(app.session_state["plan"])
    plan["binding"] = None
    app.session_state["plan"] = plan
    app.run()
    assert not app.checkbox and not app.button and app.session_state["io"] == 0


def test_failure_never_shows_success_or_machine_receipt():
    app = start()
    plan = app.session_state["plan"]
    app.session_state["sheet"].sheets["G正版"].rows[0][1] = "changed source"
    widget(app,"checkbox",CONFIRM).check().run()
    widget(app,"button",SAVE).click().run()
    assert not app.exception and app.error and not app.success and not app.code
