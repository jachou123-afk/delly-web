from copy import deepcopy

from streamlit.testing.v1 import AppTest

from test_dispatch_ui import widget


SOURCE = '''
import streamlit as st
from thumbnail_ui import render_thumbnail_management
from batch_image_preview import render_batch_image_preview
from test_nas_dispatch_storage import seeded
from nas_dispatch_storage import NasDispatchStore
from test_synology_image_store import config
if "sheet" not in st.session_state:
    sheet, store, network = seeded(11, 13)
    st.session_state["sheet"] = sheet
    st.session_state["network"] = network
store = NasDispatchStore(st.session_state["sheet"], config(), nas_factory=st.session_state["network"].factory)
render_thumbnail_management(store)
batch = store.list_batches()[0]
refs = store.product_image_refs([i["source"] for i in batch["items"]])
render_batch_image_preview(store, batch["items"], refs["images"], batch["id"])
'''


def test_explicit_build_and_readonly_paged_preview_preserves_all_business_data():
    app = AppTest.from_string(SOURCE, default_timeout=20).run()
    sheet = app.session_state["sheet"]
    before = {k: deepcopy(ws.rows) for k, ws in sheet.sheets.items()}
    assert not app.exception and not app.session_state["network"].sessions
    widget(app, "checkbox", "載入本頁縮圖").check().run()
    assert not app.get("image") and len(app.info) == 6
    assert not app.session_state["network"].sessions
    widget(app, "checkbox", "讀取縮圖進度").check().run()
    widget(app, "button", "建立下一批縮圖（最多 10 張）").click().run()
    assert 5 <= len(app.get("image")) <= 6 and not app.exception
    widget(app, "button", "建立下一批縮圖（最多 10 張）").click().run()
    assert app.success and widget(app, "button", "建立下一批縮圖（最多 10 張）").disabled
    assert widget(app, "checkbox", "載入本頁縮圖").value and len(app.get("image")) == 6
    assert before == {k: sheet.sheets[k].rows for k in before}
    all_before = {k: deepcopy(ws.rows) for k, ws in sheet.sheets.items()}
    objects_before = dict(app.session_state["network"].objects)
    widget(app, "button", "下一頁").click().run()
    assert not app.exception and len(app.get("image")) == 5 and len(app.warning) == 1
    widget(app, "button", "下一頁").click().run()
    assert not app.get("image") and len(app.warning) == 1 and len(app.code) == 1
    assert widget(app, "button", "下一頁").disabled
    widget(app, "button", "上一頁").click().run()
    assert len(app.get("image")) == 5
    assert all_before == {k: ws.rows for k, ws in sheet.sheets.items()}
    assert objects_before == app.session_state["network"].objects
    assert all(not i["review"] for i in store_batch(app)["items"])


def store_batch(app):
    from dispatch_storage import CloudDispatchStore
    return CloudDispatchStore(app.session_state["sheet"]).list_batches()[0]


def test_failed_build_does_not_claim_success():
    app = AppTest.from_string(SOURCE, default_timeout=20).run()
    app.session_state["network"].fail_upload = True
    widget(app, "checkbox", "讀取縮圖進度").check().run()
    widget(app, "button", "建立下一批縮圖（最多 10 張）").click().run()
    assert app.error and not app.success and not app.exception
    assert any("NAS 縮圖 0 張" in m.value for m in app.markdown)
