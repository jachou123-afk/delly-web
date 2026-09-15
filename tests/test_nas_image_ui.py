from copy import deepcopy

from streamlit.testing.v1 import AppTest

from test_dispatch_ui import widget


def source(count=11, full=False):
    return f'''
import streamlit as st
from nas_image_ui import render_nas_image_management
from test_nas_dispatch_storage import seeded
from nas_dispatch_storage import NasDispatchStore
from test_synology_image_store import config
if "nas_test_sheet" not in st.session_state:
    sheet, store, network = seeded({count})
    st.session_state["nas_test_sheet"] = sheet
    st.session_state["nas_test_network"] = network
store = NasDispatchStore(st.session_state["nas_test_sheet"], config(),
                         nas_factory=st.session_state["nas_test_network"].factory)
if {full!r}:
    from dispatch_ui import render_dispatch_manager
    render_dispatch_manager(lambda: store)
else:
    render_nas_image_management(store)
'''


def test_real_ui_batches_verify_details_and_no_quote_or_approval_changes():
    app = AppTest.from_string(source(), default_timeout=15).run()
    assert not app.exception
    sheet = app.session_state["nas_test_sheet"]
    before = {k: deepcopy(ws.rows) for k, ws in sheet.sheets.items()}
    assert not app.session_state["nas_test_network"].sessions and not app.button
    widget(app, "checkbox", "讀取 NAS 搬移進度").check().run()
    assert not app.session_state["nas_test_network"].sessions
    assert widget(app, "button", "讀回對帳下一批（最多 10 張）").disabled
    widget(app, "button", "搬移下一批（最多 10 張）").click().run()
    assert len(app.session_state["nas_test_network"].objects) == 10
    assert any("待搬 1 張" in m.value for m in app.markdown)
    assert widget(app, "button", "讀回對帳下一批（最多 10 張）").disabled
    widget(app, "button", "搬移下一批（最多 10 張）").click().run()
    assert len(app.session_state["nas_test_network"].objects) == 11
    assert widget(app, "button", "搬移下一批（最多 10 張）").disabled
    widget(app, "button", "讀回對帳下一批（最多 10 張）").click().run()
    assert len(app.dataframe[0].value) == 10
    assert not app.success
    widget(app, "button", "讀回對帳下一批（最多 10 張）").click().run()
    assert len(app.dataframe[0].value) == 11
    assert app.success and not app.exception
    assert before == {k: sheet.sheets[k].rows for k in before}


def test_failed_write_reports_pending_and_keeps_verification_zero():
    app = AppTest.from_string(source(1), default_timeout=15).run()
    app.session_state["nas_test_network"].fail_upload = True
    widget(app, "checkbox", "讀取 NAS 搬移進度").check().run()
    widget(app, "button", "搬移下一批（最多 10 張）").click().run()
    assert app.error and not app.exception and not app.session_state["nas_verified"]
    assert widget(app, "button", "讀回對帳下一批（最多 10 張）").disabled


def test_full_dispatch_page_has_management_and_no_network_when_closed():
    app = AppTest.from_string(source(1, full=True), default_timeout=15).run()
    assert not app.exception
    assert widget(app, "checkbox", "讀取 NAS 搬移進度")
    assert not app.session_state["nas_test_network"].sessions
