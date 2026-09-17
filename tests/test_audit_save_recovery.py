from copy import deepcopy
from types import SimpleNamespace

import pytest

from audit_persistence import prime_saved_result, restore_session
from audit_save_recovery import pending_save, attempt_save, rate_limit_delay, auto_retry_due
from batch_cost_audit import run_batch_audit
from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_manager import DispatchError, new_batch
from dispatch_storage import CloudDispatchStore, BATCH_SHEET


class Limited(Exception):
    response = SimpleNamespace(status_code=429, headers={'Retry-After': '75'})


def setup():
    store = CloudDispatchStore(FakeSpreadsheet(product_rows((1, 2))))
    batch = store.save_batch(new_batch('合成限流測試', '測試群', store.catalog(), '測試'))
    result = run_batch_audit(store, batch, [i['id'] for i in batch['items']])
    fresh = result.pop('catalog')
    return store, batch, result, fresh


def test_cooldown_does_not_even_create_store_and_retries_identical_payload():
    store, batch, result, _ = setup()
    pending = pending_save(store, batch, result)
    payload = deepcopy(pending['payload'])
    def fail():
        raise Limited('synthetic 429')
    assert attempt_save(fail, pending, clock=lambda: 100) is None
    assert pending['not_before'] == 175
    assert attempt_save(lambda: pytest.fail('no cloud calls during cooldown'), pending, clock=lambda: 174) is None
    assert not auto_retry_due(pending, clock=lambda: 174)
    assert auto_retry_due(pending, clock=lambda: 175)
    saved = attempt_save(lambda: store, pending, clock=lambda: 175)
    assert saved['_revision'] == pending['record_id']
    assert pending['payload'] == payload
    assert saved['status'] == 'draft' and all(i['review'] is None for i in saved['items'])


def test_written_but_readback_limited_resumes_without_second_append():
    store, batch, result, _ = setup()
    pending = pending_save(store, batch, result)
    get = store.get_batch
    calls = []
    def read(identity):
        calls.append(identity)
        if len(calls) == 2:
            raise Limited('synthetic readback limit')
        return get(identity)
    store.get_batch = read
    assert attempt_save(lambda: store, pending, clock=lambda: 100) is None
    assert pending['rate_limited']  # Storage wraps the HTTP exception.
    written = deepcopy(store.spreadsheet.sheets[BATCH_SHEET].rows)
    saved = attempt_save(lambda: store, pending, clock=lambda: 175)
    assert saved['_revision'] == pending['record_id']
    assert store.spreadsheet.sheets[BATCH_SHEET].rows == written


def test_concurrent_revision_or_different_destination_never_overwritten():
    store, batch, result, _ = setup()
    pending = pending_save(store, batch, result)
    store.save_batch(batch, expected_revision=batch['_revision'])
    before = deepcopy(store.spreadsheet.sheets[BATCH_SHEET].rows)
    assert attempt_save(lambda: store, pending, clock=lambda: 100) is None
    assert '其他視窗' in pending['error'] and not pending['rate_limited']
    assert store.spreadsheet.sheets[BATCH_SHEET].rows == before
    store.spreadsheet.id = 'different-sheet'
    assert attempt_save(lambda: store, pending, clock=lambda: 106) is None
    assert '雲表已切換' in pending['error']
    assert store.spreadsheet.sheets[BATCH_SHEET].rows == before


def test_retry_cap_and_exponential_cooldown():
    store, batch, result, _ = setup()
    pending = pending_save(store, batch, result)
    def fail():
        raise Limited('synthetic')
    for n in range(4):
        t = pending['not_before']
        attempt_save(fail, pending, clock=lambda: t)
        assert pending['attempts'] == n + 1
        assert pending['not_before'] >= t + 60 * 2 ** n
    assert not auto_retry_due(pending, clock=lambda: pending['not_before'])
    assert rate_limit_delay(Exception('text 429 is not an HTTP status')) is None


def test_immediate_success_reuses_checked_snapshot_but_new_session_checks_versions(monkeypatch):
    store, batch, result, fresh = setup()
    pending = pending_save(store, batch, result)
    saved = attempt_save(lambda: store, pending)
    state = {}
    assert prime_saved_result(state, saved, result, fresh)
    original = store._load_evidence
    monkeypatch.setattr(store, '_load_evidence', lambda *a: pytest.fail('no duplicate immediate read'))
    restore_session(store, saved, fresh, state)
    monkeypatch.setattr(store, '_load_evidence', original)
    store.spreadsheet.sheets['G正版'].rows[0][1] = '合成已變更標題'
    reloaded = {}
    restore_session(store, saved, store.catalog(), reloaded)
    assert reloaded['dispatch_bulk_result_' + batch['id']]['counts']['驗算已失效'] == 1


def test_failed_or_partial_snapshot_not_primed():
    store, batch, result, fresh = setup()
    pending = pending_save(store, batch, result)
    saved = attempt_save(lambda: store, pending)
    result['checks'][result['ids'][0]] = {'error': 'synthetic read error'}
    assert not prime_saved_result({}, saved, result, fresh)


class BulkSpreadsheet(FakeSpreadsheet):
    def __init__(self, sheets):
        super().__init__(sheets)
        self.calls = []

    def values_batch_get(self, ranges, params):
        self.calls.append(ranges)
        assert params == {'valueRenderOption': 'FORMATTED_VALUE', 'majorDimension': 'ROWS'}
        titles = [r[1:-5].replace("''", "'") for r in ranges]
        return {'valueRanges': [{'range': r, 'values': self.sheets[title].get_all_values()}
                                for r, title in zip(ranges, titles)]}


def test_catalog_batches_twenty_sheets_into_two_reads_without_changing_rows_or_duplicates():
    data = {}
    for n in range(20):
        data.update(product_rows((1, 1, 2), category=f"合成'{n}"))
    expected = CloudDispatchStore(FakeSpreadsheet(data)).catalog()
    sheet = BulkSpreadsheet(data)
    assert CloudDispatchStore(sheet).catalog() == expected
    assert len(sheet.calls) == 2 and all(len(c) == 10 for c in sheet.calls)
    assert len(expected) == 60 and expected[0]['errors']


def test_incomplete_bulk_catalog_fails_closed():
    sheet = BulkSpreadsheet(product_rows((1,)))
    sheet.values_batch_get = lambda *a, **k: {'valueRanges': []}
    with pytest.raises(DispatchError, match='讀取不完整'):
        CloudDispatchStore(sheet).catalog()
