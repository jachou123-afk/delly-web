from copy import deepcopy

from streamlit.testing.v1 import AppTest

from batch_image_preview import preview_plan
from test_dispatch_ui import widget
from test_product_image_library import picture


def test_batch_choices_win_over_library_without_mutation():
    item = {"images": ["chosen"]}
    refs = [{"stored_id": "different", "binding_revision": "revision"}]
    before = deepcopy((item, refs))
    assert preview_plan(item, refs)["ids"] == ["chosen"]
    assert (item, refs) == before


def test_library_set_is_preview_only_and_raw_multiple_candidates_are_not_guessed():
    item = {"images": []}
    refs = [{"stored_id": "one", "binding_revision": "r"}, {"stored_id": "two", "binding_revision": "r"}]
    plan = preview_plan(item, refs)
    assert plan["ids"] == ["one", "two"] and "未保存本批核對" in plan["label"]
    assert not preview_plan(item, [picture(), picture(1)])["inline"]
    assert "多張候選" in preview_plan(item, [picture(), picture(1)])["pending"]
    assert len(preview_plan(item, [picture()])["inline"]) == 1
    assert "尚無可用配對" in preview_plan(item, [])["pending"]


def preview_source(fail=False, excluded=False):
    return f'''
import streamlit as st
from batch_image_preview import render_batch_image_preview
from dispatch_manager import new_batch
from dispatch_storage import CloudDispatchStore
from dispatch_fakes import FakeSpreadsheet, product_rows
from test_product_image_library import picture, assignment
st.set_page_config(layout="wide")
if "test_spreadsheet" not in st.session_state:
    spreadsheet = FakeSpreadsheet(product_rows(range(1, 70)))
    store = CloudDispatchStore(spreadsheet)
    products = store.catalog()
    store.save_product_images([assignment(p, [picture(n)]) for n, p in enumerate(products[:63])], actor="合成測試", origin="test")
    batch = new_batch("測試", "測試群組", products, "測試人")
    batch["items"][0]["excluded"] = {excluded!r}
    batch["items"][0]["reason"] = "測試排除"
    store.save_batch(batch)
    st.session_state["test_spreadsheet"] = spreadsheet
    st.session_state["test_batch"] = batch
    st.session_state["test_reads"] = []
store = CloudDispatchStore(st.session_state["test_spreadsheet"])
batch = st.session_state["test_batch"]
refs = store.product_image_refs([i["source"] for i in batch["items"]])
original = store.get_assets
def tracked(ids):
    st.session_state["test_reads"].append(list(ids))
    if {fail!r} and len(st.session_state["test_reads"]) == 1:
        raise ValueError("合成讀取中斷")
    return original(ids)
store.get_assets = tracked
render_batch_image_preview(store, batch["items"], refs["images"], batch["id"])
'''


def start(**kwargs):
    app = AppTest.from_string(preview_source(**kwargs), default_timeout=25).run()
    assert not app.exception
    return app


def test_69_item_preview_shows_63_library_images_not_zero_batch_images():
    app = start()
    sheets = app.session_state["test_spreadsheet"]
    before = {k: deepcopy(w.rows) for k, w in sheets.sheets.items()}
    batch_before = deepcopy(app.session_state["test_batch"])
    assert any("63 款有圖片可預覽，6 款待補圖" in c.value for c in app.caption)
    assert not app.checkbox
    assert not app.exception and len(app.get("image")) == 6
    assert len(app.code) == 6 and not app.warning
    assert list(map(len, app.session_state["test_reads"])) == [6]
    assert before == {k: w.rows for k, w in sheets.sheets.items()}
    assert batch_before == app.session_state["test_batch"]
    assert all(not i["images"] and not i["review"] for i in batch_before["items"])
    reads = deepcopy(app.session_state["test_reads"])
    app.run()
    assert len(app.get("image")) == 6 and app.session_state["test_reads"] == reads
    image_count, code_count, missing_count = 6, 6, 0
    for page in range(2, 13):
        widget(app, "selectbox", "預覽頁碼").select(page).run()
        assert not app.exception and len(app.code) <= 6
        image_count += len(app.get("image"))
        code_count += len(app.code)
        missing_count += len(app.warning)
    assert (image_count, code_count, missing_count) == (63, 69, 6)
    assert max(map(len, app.session_state["test_reads"])) == 6
    assert widget(app, "button", "下一頁").disabled
    assert before == {k: w.rows for k, w in sheets.sheets.items()}
    assert batch_before == app.session_state["test_batch"]


def test_read_failure_keeps_product_rows_and_does_not_call_failed_images_missing():
    app = start(fail=True)
    assert not app.exception and len(app.code) == 6
    assert len(app.error) == 6 and all("圖片載入失敗" in e.value for e in app.error)
    assert len(app.get("image")) == 0 and not app.warning
    widget(app, "button", "重新載入圖片").click().run()
    assert len(app.get("image")) == 6 and not app.error
    widget(app, "button", "下一頁").click().run()
    assert len(app.get("image")) == 6 and not app.error
    assert all(not i["review"] for i in app.session_state["test_batch"]["items"])


def test_verified_cloud_backup_notice_survives_preview_cache():
    source = '''
import streamlit as st
from batch_image_preview import render_batch_image_preview
from test_product_image_library import picture
class Store:
    def get_thumbnails(self, ids):
        return {identity: {**picture(), "_cloud_backup": True} for identity in ids}
store = Store()
item = {"id": "one", "order": 1, "excluded": False, "images": [picture()["sha256"]],
        "source": {"code": "TEST-1", "name": "測試商品"}, "copy": "測試文案"}
render_batch_image_preview(store, [item], {}, "batch")
'''
    app = AppTest.from_string(source).run()
    assert not app.exception and len(app.get("image")) == 1
    assert any("雲表中校驗相符" in warning.value for warning in app.warning)
    app.run()
    assert not app.exception and len(app.get("image")) == 1
    assert any("雲表中校驗相符" in warning.value for warning in app.warning)


def test_excluded_item_is_named_but_its_image_is_not_loaded():
    app = start(excluded=True)
    assert not app.exception and len(app.get("image")) == 5 and len(app.code) == 5
    assert any("BGD-G-1 · 已排除" in c.value for c in app.caption)


def test_full_dispatch_page_connects_library_to_bulk_preview_and_labels_table():
    from test_product_image_ui import start as dispatch_start
    app = dispatch_start(bound=True, multi=True)
    spreadsheet = app.session_state["test_spreadsheet"]
    before = {k: deepcopy(w.rows) for k, w in spreadsheet.sheets.items()}
    assert any("1 款有圖片可預覽，68 款待補圖" in c.value for c in app.caption)
    assert any("2 張圖庫已存" in str(d.value) for d in app.dataframe)
    before_images = len(app.get("image"))
    gallery = next(e for e in app.expander if e.label == "商品圖片")
    assert len(gallery.get("image")) == 2
    widget(app, "button", "重新載入圖片").click().run()
    assert not app.exception and len(app.get("image")) == before_images
    assert before == {k: w.rows for k, w in spreadsheet.sheets.items()}
    assert widget(app, "button", "確認本批內容，建立待發清單").disabled


def test_search_filters_gallery_and_clear_restores_first_page():
    from test_product_image_ui import start as dispatch_start
    app = dispatch_start(bound=True)
    sheet = app.session_state["test_spreadsheet"]
    before = {k: deepcopy(w.rows) for k, w in sheet.sheets.items()}
    widget(app, "selectbox", "預覽頁碼").select(12).run()
    widget(app, "text_input", "搜尋本批品號／品名").set_value("BGD-G-1").run()
    assert not app.exception
    gallery = next(e for e in app.expander if e.label == "商品圖片")
    assert "BGD-G-69" not in " ".join(m.value for m in gallery.markdown)
    widget(app, "text_input", "搜尋本批品號／品名").set_value("不存在的商品").run()
    assert not app.exception and widget(app, "button", "下一頁").disabled
    assert not next(e for e in app.expander if e.label == "商品圖片").get("image")
    widget(app, "text_input", "搜尋本批品號／品名").set_value("").run()
    assert widget(app, "selectbox", "預覽頁碼").value == 1
    assert len(next(e for e in app.expander if e.label == "商品圖片").get("image")) == 1
    assert before == {k: w.rows for k, w in sheet.sheets.items()}
