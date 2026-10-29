"""Real batch repair UI against synthetic memory-only ZIPs and NAS transport."""
from copy import deepcopy
from io import BytesIO

import pytest
from streamlit.testing.v1 import AppTest

from test_quote_image_repair_batch import setup, upload_count


APP = '''
from unittest.mock import patch
from io import BytesIO
import streamlit as st
import quote_image_repair_batch_ui as ui
from test_quote_image_repair_batch import setup
if "test_sheet" not in st.session_state:
    sheet, store, network, package = setup(3)
    st.session_state.update(test_sheet=sheet, test_store=store, test_network=network,
                            test_package=package, test_zip=None, test_store_calls=0)
def get_store():
    st.session_state["test_store_calls"] += 1
    return st.session_state["test_store"]
def uploader(*args, **kwargs):
    return BytesIO(st.session_state["test_zip"]) if st.session_state["test_zip"] is not None else None
with patch.object(st, "file_uploader", uploader):
    ui.render_batch_image_repair(get_store)
'''


def widget(app, kind, label):
    return next(w for w in getattr(app, kind) if w.label == label)


def start():
    app = AppTest.from_string(APP, default_timeout=20).run()
    assert not app.exception
    return app


def enable(app):
    widget(app, "checkbox", "開啟批次 NAS 原圖補存").check().run()
    assert not app.exception
    return app


def load_zip(app, mode="保存原圖並核對"):
    from test_quote_image_repair_zip import image, pack, sha
    value = {"schema": 1, "products": [{"sheet": "G正版", "actual_no": f"no{n + 1}", "sha256": [sha(image(n))]} for n in range(3)]}
    app.session_state["test_zip"] = pack(value, [(f"images/{sha(image(n))}.png", image(n)) for n in range(3)])
    widget(app, "radio", "本包 NAS 保存方式").set_value(mode).run()
    widget(app, "button", "查核原圖 ZIP").click().run()
    assert not app.exception
    return app


def confirm(app):
    widget(app, "checkbox", "我已逐款核對查核表的實際 NO、主圖與原圖組").check().run()
    return app


def test_open_and_enable_are_read_only_without_cloud_or_nas():
    app = start()
    assert app.session_state["test_store_calls"] == 0 and not app.button
    enable(app)
    assert app.session_state["test_store_calls"] == 0
    assert widget(app, "button", "查核原圖 ZIP").disabled
    assert not app.session_state["test_network"].sessions


def test_zip_check_requires_explicit_confirmation_and_chunk_controls_then_retains_receipts():
    app = load_zip(enable(start()))
    assert app.session_state["test_store_calls"] == 1
    assert not app.session_state["test_network"].sessions
    assert widget(app, "button", "處理下一批").disabled
    confirm(app)
    widget(app, "number_input", "本次最多處理款數").set_value(2).run()
    widget(app, "button", "處理下一批").click().run()
    assert not app.exception
    run = app.session_state["quote_image_repair_batch_run"]
    assert [i["status"] for i in run["items"]] == ["completed", "completed", "ready"]
    assert upload_count(app.session_state["test_network"]) == 2
    assert not app.success
    widget(app, "button", "處理下一批").click().run()
    assert not app.exception and len(app.success) == 1
    assert upload_count(app.session_state["test_network"]) == 3
    assert widget(app, "button", "處理下一批").disabled
    assert len([i["receipt"] for i in app.session_state["quote_image_repair_batch_run"]["items"]]) == 3


def test_switching_mode_discards_run_and_confirmation_before_any_write():
    app = confirm(load_zip(enable(start())))
    assert not widget(app, "button", "處理下一批").disabled
    widget(app, "radio", "本包 NAS 保存方式").set_value("只核對既有 NAS 原圖（不重新上傳）").run()
    assert not app.exception and "quote_image_repair_batch_run" not in app.session_state
    assert not any(w.label == "處理下一批" for w in app.button)
    assert not app.session_state["test_network"].sessions


def test_bad_zip_does_not_read_cloud_or_preserve_old_run():
    app = load_zip(enable(start()))
    app.session_state["test_zip"] = b"not a zip"
    app.run()
    widget(app, "button", "查核原圖 ZIP").click().run()
    assert not app.exception and app.error
    assert app.session_state["test_store_calls"] == 1
    assert "quote_image_repair_batch_run" not in app.session_state
    assert not app.session_state["test_network"].sessions


def test_existing_only_failure_does_not_fallback_to_upload():
    app = confirm(load_zip(enable(start()), mode="只核對既有 NAS 原圖（不重新上傳）"))
    widget(app, "button", "處理下一批").click().run()
    assert not app.exception and not app.success
    assert upload_count(app.session_state["test_network"]) == 0
    assert all(i["status"] == "failed" for i in app.session_state["quote_image_repair_batch_run"]["items"])
    assert not widget(app, "button", "只重新排入失敗款（不處理已完成）").disabled
