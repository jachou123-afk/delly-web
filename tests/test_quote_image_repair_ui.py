"""Standalone quote image repair against synthetic Sheets and images only."""
from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from dispatch_storage import BATCH_SHEET, CloudDispatchStore, validate_image


PREFIX = "quote_image_repair_"


def synthetic_image(number=0):
    output = BytesIO()
    Image.new("RGB", (80, 60), (number % 255, 120, 40)).save(output, "PNG")
    return validate_image(output.getvalue(), f"synthetic-{number}.png")


APP = '''
from copy import deepcopy
from unittest.mock import patch
import streamlit as st
import quote_image_repair_ui as ui
from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_manager import new_batch
from dispatch_storage import CloudDispatchStore, BATCH_SHEET

if "test_sheet" not in st.session_state:
    sheet = FakeSpreadsheet(product_rows((1, 2)))
    store = CloudDispatchStore(sheet)
    batch = new_batch("既有合成批次", "合成群組，不會發送", store.catalog(), "合成測試")
    store.save_batch(batch)
    st.session_state["test_sheet"] = sheet
    st.session_state["test_product_before"] = deepcopy(sheet.sheets["G正版"].rows)
    st.session_state["test_batch_before"] = deepcopy(sheet.sheets[BATCH_SHEET].rows)
    st.session_state["test_get_store_calls"] = 0
    st.session_state["test_save_calls"] = []
    st.session_state["test_render_keys"] = []
    st.session_state["test_uploads"] = {}
    st.session_state["test_cache_clears"] = 0

def get_store():
    st.session_state["test_get_store_calls"] += 1
    if st.session_state.get("test_read_error"):
        raise RuntimeError("合成雲表讀取失敗")
    return CloudDispatchStore(st.session_state["test_sheet"])

def render_images(key, **kwargs):
    st.session_state["test_render_keys"].append(key)
    st.session_state["test_image_key"] = key
    st.caption("合成圖片選擇器")
    selected = st.session_state["test_uploads"].get(key, {})
    return selected.get("assets", []), selected.get("errors", [])

real_save = ui.save_image_repair
def save_images(store, plan, assets, **kwargs):
    st.session_state["test_save_calls"].append({
        "identity": plan["source"]["identity"],
        "hashes": [image["sha256"] for image in assets],
    })
    if st.session_state.get("test_save_exception"):
        raise RuntimeError("合成讀回核對失敗")
    if st.session_state.get("test_nas_pending"):
        return [{"商品": plan["source"]["identity"], "結果": "待處理",
                 "原因": "NAS 原图待上传／尚未讀回核對"}]
    return real_save(store, plan, assets, **kwargs)

def clear_cache():
    st.session_state["test_cache_clears"] += 1

with patch.object(ui, "render_quote_images", render_images), \
     patch.object(ui, "save_image_repair", save_images), \
     patch.object(ui, "clear_library_cache", clear_cache):
    ui.render_image_repair(get_store)
'''


def widget(app, kind, label):
    return next(item for item in getattr(app, kind) if item.label == label)


def assert_clean_run(app):
    assert not app.exception
    return app


def start():
    return assert_clean_run(AppTest.from_string(APP, default_timeout=20).run())


def search(app, query="TEST"):
    widget(app, "text_input", "搜尋已保存商品（NO、貨號或品名）").set_value(query)
    widget(app, "button", "搜尋商品").click().run()
    return assert_clean_run(app)


def load(app):
    widget(app, "button", "載入本款補圖資料").click().run()
    return assert_clean_run(app)


def select_images(app, assets=None, errors=None):
    key = app.session_state["test_image_key"]
    uploads = deepcopy(app.session_state["test_uploads"])
    uploads[key] = {"assets": assets or [], "errors": errors or []}
    app.session_state["test_uploads"] = uploads
    return assert_clean_run(app.run())


def save(app):
    widget(app, "button", "只保存本款圖片").click().run()
    return assert_clean_run(app)


def assert_originals_unchanged(app):
    sheet = app.session_state["test_sheet"]
    assert sheet.sheets["G正版"].rows == app.session_state["test_product_before"]
    assert sheet.sheets[BATCH_SHEET].rows == app.session_state["test_batch_before"]
    assert len(CloudDispatchStore(sheet).catalog()) == 2


def test_opening_repair_does_not_request_cloud_or_create_repair_state():
    app = start()
    assert app.session_state["test_get_store_calls"] == 0
    assert app.session_state["test_save_calls"] == []
    assert app.session_state["test_render_keys"] == []
    assert PREFIX + "plan" not in app.session_state
    assert [button.label for button in app.button] == ["搜尋商品"]
    assert_originals_unchanged(app)


def test_search_load_save_binds_only_images_and_displays_source_identity():
    app = start()
    search(app, "TEST-1")
    assert app.session_state["test_get_store_calls"] == 1
    assert PREFIX + "plan" not in app.session_state
    load(app)
    assert any("G正版:no1" in row.value and "TEST-1" in row.value for row in app.markdown)
    assert any("測試供應商" in row.value and "G正版 第 1 列" in row.value for row in app.caption)
    assert widget(app, "button", "只保存本款圖片").disabled
    picture = synthetic_image(1)
    select_images(app, [picture])
    save(app)
    assert any("圖片已保存並完成讀回核對" in row.value for row in app.success)
    store = CloudDispatchStore(app.session_state["test_sheet"])
    assert set(store.product_image_bindings()) == {"G正版:no1"}
    assert app.session_state["test_save_calls"] == [{"identity": "G正版:no1", "hashes": [picture["sha256"]]}]
    assert app.session_state["test_cache_clears"] == 1
    assert_originals_unchanged(app)


def test_failed_search_discards_old_plan_and_cannot_save_the_old_target():
    app = load(search(start(), "TEST-1"))
    select_images(app, [synthetic_image(1)])
    assert PREFIX + "plan" in app.session_state
    app.session_state["test_read_error"] = True
    search(app, "TEST-2")
    assert any("合成雲表讀取失敗" in row.value for row in app.error)
    for suffix in ("plan", "report", "matches", "target"):
        assert PREFIX + suffix not in app.session_state
    assert not any(button.label == "只保存本款圖片" for button in app.button)
    assert app.session_state["test_save_calls"] == []
    app.session_state["test_read_error"] = False
    search(app, "TEST-2")
    assert PREFIX + "plan" not in app.session_state
    assert not any(button.label == "只保存本款圖片" for button in app.button)
    assert_originals_unchanged(app)


def test_switching_product_requires_reload_and_does_not_reuse_old_uploads():
    app = load(search(start()))
    first_key = app.session_state["test_image_key"]
    select_images(app, [synthetic_image(1)])
    widget(app, "selectbox", "選擇要補圖的商品").set_value("G正版:no2").run()
    assert_clean_run(app)
    assert PREFIX + "plan" not in app.session_state
    assert not any(button.label == "只保存本款圖片" for button in app.button)
    load(app)
    assert app.session_state["test_image_key"] != first_key
    assert widget(app, "button", "只保存本款圖片").disabled
    assert app.session_state[PREFIX + "plan"]["source"]["identity"] == "G正版:no2"
    assert any("G正版:no2" in row.value and "TEST-2" in row.value for row in app.markdown)
    select_images(app, [synthetic_image(2)])
    save(app)
    assert set(CloudDispatchStore(app.session_state["test_sheet"]).product_image_bindings()) == {"G正版:no2"}
    assert app.session_state["test_save_calls"][0]["hashes"] == [synthetic_image(2)["sha256"]]
    assert_originals_unchanged(app)


@pytest.mark.parametrize("same_no_other_category", [False, True])
def test_successive_searches_replace_selector_identity_and_load_new_product(same_no_other_category):
    from dispatch_fakes import FakeWorksheet, product_rows

    app = start()
    sheet = app.session_state["test_sheet"]
    if same_no_other_category:
        other_rows = product_rows((2,), category="W玩具")["W玩具"]
        sheet.sheets["W玩具"] = FakeWorksheet("W玩具", other_rows)
    products_before = {name: deepcopy(worksheet.rows) for name, worksheet in sheet.sheets.items()}
    load(search(app, "no1"))
    first_selector_key = widget(app, "selectbox", "選擇要補圖的商品").key
    first_image_key = app.session_state["test_image_key"]
    first_upload_generation = app.session_state[PREFIX + "generation"]
    first_selector_generation = app.session_state[PREFIX + "selector_generation"]
    select_images(app, [synthetic_image(21)])
    save(app)
    assert app.success

    search(app, "no2")
    selector = widget(app, "selectbox", "選擇要補圖的商品")
    assert selector.key != first_selector_key
    assert first_selector_key not in app.session_state
    assert app.session_state[PREFIX + "selector_generation"] == first_selector_generation + 1
    assert app.session_state[PREFIX + "generation"] == first_upload_generation
    assert selector.value == "G正版:no2"
    expected = {"G正版:no2", "W玩具:no2"} if same_no_other_category else {"G正版:no2"}
    assert {option.split(" · ")[0] for option in selector.options} == expected
    assert all("no1" not in option for option in selector.options)
    assert PREFIX + "plan" not in app.session_state
    assert PREFIX + "report" not in app.session_state
    assert not app.success
    assert not any(button.label == "只保存本款圖片" for button in app.button)

    load(app)
    assert app.session_state[PREFIX + "plan"]["source"]["identity"] == "G正版:no2"
    assert app.session_state["test_image_key"] != first_image_key
    assert widget(app, "button", "只保存本款圖片").disabled
    assert any("G正版:no2" in row.value and "TEST-2" in row.value for row in app.markdown)
    assert not any("G正版:no1" in row.value for row in app.markdown)
    select_images(app, [synthetic_image(22)])
    save(app)
    assert app.success
    assert [call["identity"] for call in app.session_state["test_save_calls"]] == ["G正版:no1", "G正版:no2"]
    assert set(CloudDispatchStore(sheet).product_image_bindings()) == {"G正版:no1", "G正版:no2"}
    assert all(sheet.sheets[name].rows == rows for name, rows in products_before.items())


@pytest.mark.parametrize("assets,errors", [([], []), ([synthetic_image(3)], ["合成圖片超過大小限制"])])
def test_missing_or_invalid_images_disable_save(assets, errors):
    app = load(search(start(), "TEST-1"))
    select_images(app, assets, errors)
    assert widget(app, "button", "只保存本款圖片").disabled
    assert app.session_state["test_save_calls"] == []
    assert not app.success
    if errors:
        assert any(errors[0] in row.value for row in app.error)
    assert_originals_unchanged(app)


def test_nas_pending_is_incomplete_and_retry_uses_same_product_without_creation():
    app = load(search(start(), "TEST-1"))
    select_images(app, [synthetic_image(4)])
    key = app.session_state["test_image_key"]
    app.session_state["test_nas_pending"] = True
    save(app)
    assert not app.success
    assert any("圖片尚未全部保存" in row.value for row in app.error)
    assert any("NAS" in str(frame.value) for frame in app.dataframe)
    assert not widget(app, "button", "只保存本款圖片").disabled
    assert app.session_state["test_image_key"] == key
    assert app.session_state["test_cache_clears"] == 0
    assert CloudDispatchStore(app.session_state["test_sheet"]).product_image_bindings() == {}
    assert_originals_unchanged(app)
    app.session_state["test_nas_pending"] = False
    save(app)
    assert any("圖片已保存並完成讀回核對" in row.value for row in app.success)
    assert len(app.session_state["test_save_calls"]) == 2
    assert {call["identity"] for call in app.session_state["test_save_calls"]} == {"G正版:no1"}
    assert set(CloudDispatchStore(app.session_state["test_sheet"]).product_image_bindings()) == {"G正版:no1"}
    assert_originals_unchanged(app)


@pytest.mark.parametrize("mode", ["changed", "removed", "error"])
def test_success_message_does_not_survive_changed_removed_or_invalid_selection(mode):
    app = load(search(start(), "TEST-1"))
    select_images(app, [synthetic_image(5)])
    save(app)
    assert app.success
    if mode == "changed":
        select_images(app, [synthetic_image(6)])
    elif mode == "removed":
        select_images(app)
    else:
        select_images(app, [synthetic_image(5)], ["合成圖像驗證失敗"])
    assert not app.success
    assert PREFIX + "report" not in app.session_state
    assert len(app.session_state["test_save_calls"]) == 1
    assert_originals_unchanged(app)


def test_save_exception_keeps_upload_for_retry_and_does_not_report_success():
    app = load(search(start(), "TEST-1"))
    select_images(app, [synthetic_image(7)])
    key = app.session_state["test_image_key"]
    app.session_state["test_save_exception"] = True
    save(app)
    assert not app.success
    assert any("圖片尚未確認完成" in row.value and "合成讀回核對失敗" in row.value for row in app.error)
    assert PREFIX + "report" not in app.session_state
    assert app.session_state["test_image_key"] == key
    assert not widget(app, "button", "只保存本款圖片").disabled
    assert_originals_unchanged(app)


@pytest.mark.parametrize("failed", [False, True])
def test_existing_nas_button_is_separate_and_never_calls_normal_upload(failed):
    existing_app = APP.replace("real_save = ui.save_image_repair", '''
def existing_images(store, plan, assets, **kwargs):
    st.session_state["test_existing_calls"] = st.session_state.get("test_existing_calls", 0) + 1
    if st.session_state.get("test_existing_fail"):
        raise RuntimeError("合成 NAS 既有原圖缺檔")
    return [{"商品": plan["source"]["identity"], "結果": "已存在", "原因": "合成原圖核對"}]

real_save = ui.save_image_repair''')
    existing_app = existing_app.replace(
        'patch.object(ui, "save_image_repair", save_images),',
        'patch.object(ui, "save_image_repair", save_images), '
        'patch.object(ui, "save_existing_nas_image_repair", existing_images),')
    app = assert_clean_run(AppTest.from_string(existing_app, default_timeout=20).run())
    load(search(app, "TEST-1"))
    assert widget(app, "button", "只核對 NAS 已存在的本款原圖").disabled
    select_images(app, [synthetic_image(9)])
    key = app.session_state["test_image_key"]
    app.session_state["test_existing_fail"] = failed
    widget(app, "button", "只核對 NAS 已存在的本款原圖").click().run()
    assert_clean_run(app)
    assert app.session_state["test_existing_calls"] == 1
    assert app.session_state["test_save_calls"] == []
    assert app.session_state["test_image_key"] == key
    assert bool(app.success) is (not failed)
    assert app.session_state["test_cache_clears"] == (0 if failed else 1)
    if failed:
        assert any("既有原圖缺檔" in row.value for row in app.error)
        assert PREFIX + "report" not in app.session_state
    assert_originals_unchanged(app)


def test_main_app_routes_to_image_repair_without_cost_settings_parsing_or_cloud_reads():
    import ast
    from test_v74_ui import app_source as quote_app_source

    tree = ast.parse(quote_app_source())
    replacements = {
        "get_dispatch_store": (
            "st.session_state['test_repair_store_calls'] += 1\n"
            "raise AssertionError('opening image repair must not open cloud store')"
        ),
        "get_settings_cached": (
            "st.session_state['test_repair_settings_calls'] += 1\n"
            "raise AssertionError('image repair must not load cost settings')"
        ),
        "parse_text": "raise AssertionError('image repair must not parse a new quote')",
    }
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in replacements:
            node.body = ast.parse(replacements[node.name]).body
    ast.fix_missing_locations(tree)
    app = AppTest.from_string(ast.unparse(tree), default_timeout=20)
    app.session_state["tool_page"] = "🖼️ 商品補圖"
    app.session_state["test_repair_store_calls"] = 0
    app.session_state["test_repair_settings_calls"] = 0
    app.run()
    assert_clean_run(app)
    assert any(row.value == "商品補圖" for row in app.subheader)
    assert widget(app, "button", "搜尋商品")
    assert widget(app, "text_input", "搜尋已保存商品（NO、貨號或品名）")
    assert app.session_state["test_repair_store_calls"] == 0
    assert app.session_state["test_repair_settings_calls"] == 0
    assert not app.text_area and not app.number_input
    assert not app.error
