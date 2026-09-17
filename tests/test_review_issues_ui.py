import json

from test_batch_cost_ui import start, result
from test_dispatch_ui import widget
from dispatch_storage import CloudDispatchStore


def test_ui_import_survives_reload_and_math_without_approving_or_changing_quotes():
    app = start()
    payload = json.dumps([dict(code='BGD-G-2', field='合成貨號', detail='原圖與紀錄待核實',
                               current='TEST-A', expected='TEST-B', evidence='合成來源記錄', action='核對兩份來源')])
    widget(app, 'text_area', '問題清單 JSON').set_value(payload).run()
    widget(app, 'button', '保存既有問題（不修改商品）').click().run()
    assert not app.exception
    store = CloudDispatchStore(app.session_state['test_spreadsheet'])
    registry = store.review_issues()
    assert len(registry['issues']) == 1
    widget(app, 'button', '全選待核對（69 款）').click().run()
    widget(app, 'button', '驗算所選商品（69 款）').click().run()
    assert not app.exception
    assert result(app)['counts'] == {'表內重算一致': 69}
    widget(app, 'button', '重新載入雲端').click().run()
    assert not app.exception and store.review_issues() == registry
    tables = [df.value for df in app.dataframe if '阻擋原因' in df.value.columns]
    assert '原圖與紀錄待核實' in tables[0].query('品號 == "BGD-G-2"').iloc[0]['阻擋原因']
    assert store.get_batch(app.session_state['dispatch_active'])['status'] == 'draft'


def test_ui_resolution_needs_explicit_ack_and_real_evidence():
    app = start()
    payload = json.dumps([dict(code='BGD-G-2', field='合成貨號', detail='差異', current='A',
                               expected='B', evidence='合成證據', action='核對')])
    widget(app, 'text_area', '問題清單 JSON').set_value(payload).run()
    widget(app, 'button', '保存既有問題（不修改商品）').click().run()
    widget(app, 'button', '登記此問題已解決').click().run()
    assert any('請先確認' in e.value for e in app.error)
    widget(app, 'text_input', '問題解除核對人').set_value('測試')
    widget(app, 'text_area', '實際修正／核對證據（必填）').set_value('合成測試證據：已對照來源')
    widget(app, 'checkbox', '已處理此項差異並核對實際來源，不只是表內重算一致').check()
    widget(app, 'button', '登記此問題已解決').click().run()
    assert not app.exception
    registry = CloudDispatchStore(app.session_state['test_spreadsheet']).review_issues()
    assert next(iter(registry['issues'].values()))['status'] == 'resolved'
