from copy import deepcopy

from audit_save_recovery import PENDING_KEY
from dispatch_storage import CloudDispatchStore
from test_audit_save_recovery import Limited
from test_batch_cost_ui import start, result
from test_dispatch_ui import widget


def test_ui_keeps_all_results_and_blocks_reads_during_cooldown(monkeypatch):
    app = start()
    before = deepcopy(app.session_state['test_spreadsheet'].sheets['G正版'].rows)
    widget(app, 'button', '全選含暫緩（69 款）').click().run()
    original = CloudDispatchStore.save_batch
    def limited(self, batch, **kwargs):
        if batch.get('cost_snapshot'):
            raise Limited('synthetic 429')
        return original(self, batch, **kwargs)
    monkeypatch.setattr(CloudDispatchStore, 'save_batch', limited)
    widget(app, 'button', '驗算所選商品（69 款）').click().run()
    assert not app.exception
    pending = app.session_state[PENDING_KEY]
    assert len(pending['result']['rows']) == 69
    assert len(app.dataframe[0].value) == 69
    assert widget(app, 'button', '查詢保存狀態並接續保存（不重新驗算）').disabled
    assert not any(b.label == '確認本批內容，建立待發清單' for b in app.button)
    with monkeypatch.context() as scoped:
        def unexpected(*a, **k):
            raise AssertionError('ordinary store creation is forbidden during cooldown')
        scoped.setattr(CloudDispatchStore, '__init__', unexpected)
        app.run()
        assert not app.exception
    monkeypatch.setattr(CloudDispatchStore, 'save_batch', original)
    pending['not_before'] = 0
    app.run()  # Automatic recovery, without running the audit again.
    assert not app.exception
    assert PENDING_KEY not in app.session_state
    assert result(app)['at'] == pending['result']['at']
    assert len(result(app)['rows']) == 69
    assert app.session_state['test_spreadsheet'].sheets['G正版'].rows == before


def test_delay_revalidates_source_without_recalculating(monkeypatch):
    app = start()
    widget(app, 'button', '全選含暫緩（69 款）').click().run()
    original = CloudDispatchStore.save_batch
    monkeypatch.setattr(CloudDispatchStore, 'save_batch', lambda *a, **k: (_ for _ in ()).throw(Limited('synthetic')))
    widget(app, 'button', '驗算所選商品（69 款）').click().run()
    assert not app.exception
    pending = app.session_state[PENDING_KEY]
    app.session_state['test_spreadsheet'].sheets['G正版'].rows[0][1] = '合成等待期間變更'
    monkeypatch.setattr(CloudDispatchStore, 'save_batch', original)
    pending['not_before'] = 0
    app.run()
    assert not app.exception
    assert result(app)['counts']['驗算已失效'] == 1
    assert result(app)['at'] == pending['result']['at']
