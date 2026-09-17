from copy import deepcopy

from streamlit.testing.v1 import AppTest

from correction_fakes import RAW
from product_correction_ui import PENDING
from test_dispatch_ui import widget


def start():
    app = AppTest.from_string('''
import streamlit as st
from correction_fakes import correction_store, TOOLS
from dispatch_manager import new_batch
from product_correction_ui import render_correction_editor, render_pending_correction, PENDING
if 'test_store' not in st.session_state:
    store = correction_store()
    st.session_state['test_store'] = store
    st.session_state['test_batch'] = new_batch('合成修正測試', '測試群', store.catalog(), '合成測試')
store = st.session_state['test_store']
batch = st.session_state['test_batch']
if st.session_state.get('dispatch_notice'):
    st.success(st.session_state['dispatch_notice'])
if st.session_state.get(PENDING):
    render_pending_correction(lambda: store)
else:
    render_correction_editor(store, batch, batch['items'][0])
''', default_timeout=15).run()
    assert not app.exception
    return app


def parse(app):
    widget(app, 'button', '修正本款原雲表（逐欄）').click().run()
    widget(app, 'text_area', '修正用廠商原文（解析只提供建議）').set_value(RAW).run()
    widget(app, 'button', '解析原文並列出欄位差異（不寫入）').click().run()
    assert not app.exception


def preview(app, fields=('supplier_code',)):
    widget(app, 'multiselect', '只選要修正的欄位（預設全不選）').set_value(list(fields)).run()
    widget(app, 'text_input', '本次修正人').set_value('合成操作人')
    widget(app, 'text_area', '修正依據／原文位置及參數核實說明').set_value('已核對合成原文貨號，其他欄不變')
    widget(app, 'button', '預覽選定修正與連動價格').click().run()
    assert not app.exception


def accept(app):
    widget(app, 'checkbox', '我已對照原文／原圖確認同一商品，並同意以上選定欄位及連動價格變更').check().run()
    widget(app, 'button', '確認套用選定欄位到原雲表').click().run()


def test_parse_has_zero_selected_and_preview_has_no_cloud_writes():
    app = start()
    before = deepcopy(app.session_state['test_store'].spreadsheet.sheets['G正版'].rows)
    parse(app)
    assert not widget(app, 'multiselect', '只選要修正的欄位（預設全不選）').value
    assert widget(app, 'button', '預覽選定修正與連動價格').disabled
    preview(app)
    assert widget(app, 'button', '確認套用選定欄位到原雲表').disabled
    assert len(app.dataframe[0].value) == 1
    assert app.session_state['test_store'].spreadsheet.sheets['G正版'].rows == before
    assert not app.session_state['test_store'].spreadsheet.requests
    assert any('不變更成本' in s.value for s in app.success)
    accept(app)
    assert not app.exception
    sheet = app.session_state['test_store'].spreadsheet
    assert sheet.sheets['G正版'].rows[4][1] == '貨號 TEST-NEW'
    assert sheet.sheets['G正版'].rows[:4] == before[:4]
    assert any('未核准、未發 LINE' in s.value for s in app.success)


def test_missing_parameters_blocks_price_preview_but_not_text_correction():
    app = start()
    parse(app)
    preview(app, ('price',))
    assert any('原報價依據' in e.value for e in app.error)
    assert not any(b.label == '確認套用選定欄位到原雲表' for b in app.button)
    assert not app.session_state['test_store'].spreadsheet.requests
    preview(app)
    assert widget(app, 'button', '確認套用選定欄位到原雲表').disabled


def test_cancel_preview_never_writes_and_preserves_false_approval():
    app = start()
    parse(app)
    preview(app)
    widget(app, 'button', '返回修改選取／修正值').click().run()
    assert not any(b.label == '確認套用選定欄位到原雲表' for b in app.button)
    assert not app.session_state['test_store'].spreadsheet.requests


def test_uncertain_write_can_only_query_and_does_not_duplicate():
    app = start()
    parse(app)
    preview(app)
    app.session_state['test_store'].spreadsheet.fail_after_write = True
    accept(app)
    assert not app.exception and PENDING in app.session_state
    assert not any(b.label == '確認套用選定欄位到原雲表' for b in app.button)
    widget(app, 'button', '只查詢修正結果（不重送）').click().run()
    assert not app.exception and PENDING not in app.session_state
    assert len(app.session_state['test_store'].spreadsheet.requests) == 1


def test_changed_source_after_preview_is_not_overwritten():
    app = start()
    parse(app)
    preview(app)
    app.session_state['test_store'].spreadsheet.sheets['G正版'].rows[0][1] = '他人已改'
    accept(app)
    assert not app.exception
    assert any('已被修改' in e.value for e in app.error)
    assert not app.session_state['test_store'].spreadsheet.requests
