from copy import deepcopy
from streamlit.testing.v1 import AppTest

from dispatch_pricing import TIERED
from dispatch_storage import CloudDispatchStore
from test_dispatch_ui import app_source, widget


def test_existing_draft_explicit_policy_save_keeps_id_images_and_source():
    app = AppTest.from_string(app_source(ready=True), default_timeout=60).run()
    assert not app.exception
    store = CloudDispatchStore(app.session_state["test_spreadsheet"])
    before = deepcopy(store.list_batches()[0])
    widget(app, "selectbox", "廣告定價規則").select(TIERED).run()
    # A preview is not a save, approval, or receipt.
    assert CloudDispatchStore(app.session_state["test_spreadsheet"]).list_batches()[0] == before
    widget(app, "button", "套用本批定價規則並儲存草稿").click().run()
    assert not app.exception
    saved = CloudDispatchStore(app.session_state["test_spreadsheet"]).list_batches()[0]
    assert saved["id"] == before["id"] and saved["status"] == "draft"
    assert saved["pricing_policy"] == TIERED
    assert not saved["observations"] and saved["approved_digest"] is None
    for old, item in zip(before["items"], saved["items"]):
        assert item["source"] == old["source"] and item["images"] == old["images"]
        assert item["cost_audit"] == old["cost_audit"]
        assert "售價55元/個" in item["copy"]
    table = next(frame.value for frame in app.dataframe if "原表售價" in frame.value.columns)
    assert set(table["原表售價"]) == {"53"}
    assert set(table["本批售價"]) == {"售價55元/個"}
