"""Read controls with synthetic legacy originals, not real NAS or cloud files."""
from copy import deepcopy

from streamlit.testing.v1 import AppTest


APP = '''
from copy import deepcopy
from unittest.mock import patch
import streamlit as st
import quote_image_inspection_ui as ui
from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_storage import CloudDispatchStore
from product_images import PRODUCT_IMAGE_SHEET
from quote_image_repair import prepare_image_repair
from test_product_image_library import picture, assignment
if "test_store" not in st.session_state:
    sheet = FakeSpreadsheet(product_rows((1,)))
    store = CloudDispatchStore(sheet)
    source = store.catalog()[0]
    if BIND:
        store.save_product_images([assignment(source, [picture(2), picture(1)])], actor="synthetic", origin="test")
    plan = prepare_image_repair(store, source["identity"])
    st.session_state.update(test_store=store, test_sheet=sheet, test_plan=plan,
                            test_uploads=[], test_store_calls=0, test_stat_calls=0,
                            test_before={name: deepcopy(ws.rows) for name, ws in sheet.sheets.items()})
def get_store():
    st.session_state["test_store_calls"] += 1
    return st.session_state["test_store"]
def stat(store, plan, assets):
    st.session_state["test_stat_calls"] += 1
    return {"identity": plan["source"]["identity"], "checked_at":"synthetic-time",
            "snapshot_digest": plan["snapshot_digest"], "images": [{
                "sha256": a["sha256"],"name":a["name"],"expected_size":123,"size":123,
                "exists":True,"size_matches":True,"status":"exists","reason":"檔案存在；不是SHA驗證"} for a in assets]}
with patch.object(ui, "inspect_nas_files", stat):
    ui.render_image_inspection(get_store, st.session_state["test_plan"], st.session_state["test_uploads"],
                               selection_errors=st.session_state.get("test_selection_error", False))
'''


def widget(app, kind, label):
    return next(w for w in getattr(app, kind) if w.label == label)


def start(bound=True):
    app = AppTest.from_string(APP.replace("if BIND:", f"if {bound!r}:"), default_timeout=20).run()
    assert not app.exception
    return app


def unchanged(app):
    assert {name: ws.rows for name, ws in app.session_state["test_sheet"].sheets.items()} == app.session_state["test_before"]


def test_opening_controls_does_no_io_and_no_binding_or_upload_disables_buttons():
    app = start(False)
    assert app.session_state["test_store_calls"] == 0
    assert widget(app, "button", "讀取這款已綁定原圖").disabled
    assert widget(app, "button", "只查本款 NAS 檔案資訊").disabled
    assert not app.get("download_button")
    unchanged(app)


def test_bound_originals_are_ordered_displayed_downloadable_and_cached_without_writes():
    app = start()
    assert app.session_state["test_store_calls"] == 0
    widget(app, "button", "讀取這款已綁定原圖").click().run()
    assert not app.exception and not app.error
    readback = app.session_state["quote_image_inspection_bound"]
    assert readback["sha256"] == app.session_state["test_plan"]["binding"]["assets"]
    buttons = app.get("download_button")
    assert [w.proto.label for w in buttons] == ["下載原圖 1（原主圖）", "下載原圖 2"]
    assert len(app.get("image")) == 2
    app.run()
    assert app.session_state["test_store_calls"] == 1 and len(app.get("download_button")) == 2
    assert not app.success
    unchanged(app)


def test_stat_is_explicit_and_only_diagnostic_with_no_complete_claim():
    from test_product_image_library import picture
    app = start()
    app.session_state["test_uploads"] = [picture()]
    app.run()
    assert app.session_state["test_store_calls"] == 0
    widget(app, "button", "只查本款 NAS 檔案資訊").click().run()
    assert not app.exception and app.session_state["test_stat_calls"] == 1
    assert app.warning and not app.success
    assert any("不代表 SHA-256" in item.value for item in app.warning)
    app.run()
    assert app.session_state["test_store_calls"] == 1
    unchanged(app)


def test_new_upload_or_plan_revision_discards_cached_inspections():
    from test_product_image_library import picture
    app = start()
    widget(app, "button", "讀取這款已綁定原圖").click().run()
    assert "quote_image_inspection_bound" in app.session_state
    app.session_state["test_uploads"] = [picture(3)]
    app.run()
    assert "quote_image_inspection_bound" not in app.session_state
    assert not app.get("download_button")
    widget(app, "button", "只查本款 NAS 檔案資訊").click().run()
    assert "quote_image_inspection_stat" in app.session_state
    plan = deepcopy(app.session_state["test_plan"])
    plan["snapshot_digest"] = "different-snapshot"
    app.session_state["test_plan"] = plan
    app.run()
    assert "quote_image_inspection_stat" not in app.session_state
    unchanged(app)


def test_changed_binding_hides_old_images_and_asks_to_reload():
    from test_product_image_library import picture, assignment
    app = start()
    store = app.session_state["test_store"]
    original = store.product_image_bindings()["G正版:no1"]
    store.save_product_images([assignment(store.catalog()[0], [picture(4)], original["_revision"])],
                              actor="synthetic", origin="test", replace=True)
    widget(app, "button", "讀取這款已綁定原圖").click().run()
    assert not app.exception and app.error
    assert any("重新載入" in error.value for error in app.error)
    assert "quote_image_inspection_bound" not in app.session_state
    assert not app.get("download_button")


def test_invalid_upload_selection_disables_stat_and_clears_old_result():
    from test_product_image_library import picture
    app = start()
    app.session_state["test_uploads"] = [picture()]
    app.run()
    widget(app, "button", "只查本款 NAS 檔案資訊").click().run()
    app.session_state["test_selection_error"] = True
    app.run()
    assert not app.exception and widget(app, "button", "只查本款 NAS 檔案資訊").disabled
    assert "quote_image_inspection_stat" not in app.session_state
    unchanged(app)
