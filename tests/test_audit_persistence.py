from copy import deepcopy

import pytest

from audit_persistence import save_result, restore_result, restore_session
from batch_cost_audit import run_batch_audit
from dispatch_fakes import FakeSpreadsheet, product_rows, seed_evidence
from dispatch_manager import DispatchError, new_batch
from dispatch_storage import CloudDispatchStore, BATCH_SHEET


def setup():
    store = CloudDispatchStore(FakeSpreadsheet(product_rows((1, 2))))
    batch = store.save_batch(new_batch('合成驗算', '測試群', store.catalog(), '測試'))
    result = run_batch_audit(store, batch, [i['id'] for i in batch['items']])
    return store, save_result(store, batch, result)


def test_fresh_session_preserves_time_results_and_never_reruns_math(monkeypatch):
    store, batch = setup()
    before = deepcopy(store.spreadsheet.sheets['G正版'].rows)
    monkeypatch.setattr(store, '_cost_report', lambda *a: pytest.fail('restoring must not recompute'))
    state = {}
    restore_session(store, batch, store.catalog(), state)
    result = state['dispatch_bulk_result_' + batch['id']]
    assert result['at'] == batch['cost_snapshot']['at']
    assert result['counts'] == {'表內重算一致': 2}
    assert before == store.spreadsheet.sheets['G正版'].rows
    assert all(i['review'] is None and not i['image_receipts'] for i in batch['items'])
    reads = len(store.spreadsheet.sheets['G正版'].read_ranges)
    restore_session(store, batch, store.catalog(), state)
    assert reads == len(store.spreadsheet.sheets['G正版'].read_ranges)


def test_one_changed_source_invalidates_only_one_saved_row():
    store, batch = setup()
    store.spreadsheet.sheets['G正版'].rows[0][1] = '已變更合成標題'
    result = restore_result(store, batch, store.catalog())
    assert result['counts'] == {'驗算已失效': 1, '表內重算一致': 1}
    assert not result['checks'][batch['items'][0]['id']].get('report')


def test_formula_change_even_same_formatted_value_invalidates():
    store, batch = setup()
    ws = store.spreadsheet.sheets['G正版']
    original = ws.batch_get
    def read(ranges, value_render_option=None):
        result = original(ranges, value_render_option=value_render_option)
        if value_render_option == 'FORMULA':
            result[0][1][2] += '+0'
        return result
    ws.batch_get = read
    assert restore_result(store, batch, store.catalog())['counts'] == {'驗算已失效': 1, '表內重算一致': 1}


def test_evidence_change_rule_change_and_read_failure_fail_closed():
    store, batch = setup()
    seed_evidence(store)
    result = restore_result(store, batch, store.catalog())
    assert result['counts'] == {'驗算已失效': 2}
    assert all('原文或計算參數' in c['error'] for c in result['checks'].values())
    batch['cost_snapshot']['rule'] = 'obsolete'
    assert restore_result(store, batch, store.catalog())['counts'] == {'驗算已失效': 2}
    store, batch = setup()
    store.spreadsheet.sheets['G正版'].batch_get = lambda *a, **k: []
    assert restore_result(store, batch, store.catalog())['counts'] == {'驗算已失效': 2}


def test_partial_recheck_keeps_other_products_original_time():
    store, batch = setup()
    old = deepcopy(batch['cost_snapshot']['entries'])
    result = run_batch_audit(store, batch, [batch['items'][0]['id']])
    result['at'] = '2099-01-01T01:02:03+08:00'
    saved = save_result(store, batch, result)
    assert saved['cost_snapshot']['entries'][batch['items'][1]['id']] == old[batch['items'][1]['id']]
    assert len(restore_result(store, saved, store.catalog())['rows']) == 2


def test_concurrent_or_uncertain_save_cannot_report_success():
    store, batch = setup()
    result = run_batch_audit(store, batch, [batch['items'][0]['id']])
    store.save_batch(batch, expected_revision=batch['_revision'])
    with pytest.raises(DispatchError, match='其他視窗'):
        save_result(store, batch, result)
    current = store.get_batch(batch['id'])
    store.spreadsheet.sheets[BATCH_SHEET].fail_append_after_write = True
    with pytest.raises(DispatchError, match='待確認'):
        save_result(store, current, result)


def test_metadata_and_order_changes_do_not_require_new_math():
    store, batch = setup()
    batch['name'] = '重新命名'
    for i in batch['items']:
        i['order'] = 3 - i['order']
    assert restore_result(store, batch, store.catalog())['counts'] == {'表內重算一致': 2}
