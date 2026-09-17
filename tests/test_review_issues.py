from copy import deepcopy
import json

import pytest

from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_manager import DispatchError, new_batch, item_errors
from dispatch_storage import CloudDispatchStore
from review_issues import attach_issues, parse_import, ISSUE_SHEET
from dispatch_diagnostics import diagnose_item


def setup():
    store = CloudDispatchStore(FakeSpreadsheet(product_rows((1, 2))))
    batch = new_batch('合成問題清單', '測試群', store.catalog(), '測試')
    text = json.dumps([dict(code='BGD-G-1', field='廠商貨號', detail='貨號與原圖不同', current='TEST-A',
                            expected='TEST-B', evidence='合成原圖的商品資訊欄', action='先核實原文和圖，再改貨號及配對')])
    return store, batch, text


def test_import_is_durable_idempotent_and_does_not_touch_products_or_approval():
    store, batch, text = setup()
    before = deepcopy(store.spreadsheet.sheets['G正版'].rows)
    registry = store.import_review_issues(text, batch, '測試', '')
    rows = len(store.spreadsheet.sheets[ISSUE_SHEET].rows)
    assert store.import_review_issues(text, batch, '測試', registry['_revision']) == registry
    assert len(store.spreadsheet.sheets[ISSUE_SHEET].rows) == rows
    assert before == store.spreadsheet.sheets['G正版'].rows
    assert batch['status'] == 'draft' and all(i['review'] is None for i in batch['items'])
    assert CloudDispatchStore(store.spreadsheet).review_issues() == registry


def test_math_pass_does_not_remove_issue_and_other_products_are_unaffected():
    store, batch, text = setup()
    registry = store.import_review_issues(text, batch, '測試', '')
    prepared = attach_issues(batch, registry)
    item = prepared['items'][0]
    item['cost_audit'] = store.cost_audit(item['source'])[0]
    errors = item_errors(item, require_review=False, require_source=False)
    assert any(e.startswith('既有問題') for e in errors)
    diagnosis = diagnose_item(item, errors)
    assert any('TEST-A' in d['current'] and 'TEST-B' in d['current'] for d in diagnosis['blockers'])
    assert prepared['items'][1]['known_issues'] == []
    with pytest.raises(DispatchError, match='既有問題'):
        store.verify_cost_checks(batch, require_source=False)


def test_new_batch_carries_issue_but_not_different_supplier_or_date():
    store, batch, text = setup()
    registry = store.import_review_issues(text, batch, '測試', '')
    other = new_batch('另一批', '另一群', store.catalog(), '測試')
    assert attach_issues(other, registry)['items'][0]['known_issues']
    for field in ('vendor', 'date'):
        changed = deepcopy(other)
        changed['items'][0]['source'][field] = '不同來源'
        assert not attach_issues(changed, registry)['items'][0]['known_issues']


def test_resolution_requires_evidence_and_retains_history_without_reopening_duplicate():
    store, batch, text = setup()
    registry = store.import_review_issues(text, batch, '測試', '')
    identity = next(iter(registry['issues']))
    source = batch['items'][0]['source']
    with pytest.raises(DispatchError, match='證據'):
        store.resolve_review_issue(identity, source, '測試', '', registry['_revision'])
    resolved = store.resolve_review_issue(identity, source, '測試', '合成測試已逐欄核實', registry['_revision'])
    assert resolved['issues'][identity]['resolution']['source_hash'] == source['source_hash']
    assert not attach_issues(batch, resolved)['items'][0]['known_issues']
    assert store.import_review_issues(text, batch, '測試', resolved['_revision']) == resolved
    assert len(store.spreadsheet.sheets[ISSUE_SHEET].rows) >= 3


@pytest.mark.parametrize('change', ['code', 'status', 'evidence'])
def test_bad_import_is_atomic_and_does_not_create_sheet(change):
    store, batch, text = setup()
    records = json.loads(text)
    if change == 'code':
        records.append(dict(records[0], code='BGD-G-9999'))
    elif change == 'status':
        records[0]['status'] = 'resolved'
    else:
        records[0]['evidence'] = ''
    with pytest.raises(DispatchError):
        store.import_review_issues(json.dumps(records), batch, '測試', '')
    assert ISSUE_SHEET not in store.spreadsheet.sheets


def test_stale_revision_and_corrupt_register_stop_confirmation():
    store, batch, text = setup()
    store.import_review_issues(text, batch, '測試', '')
    with pytest.raises(DispatchError, match='更新'):
        store.import_review_issues(text, batch, '測試', '')
    store.spreadsheet.sheets[ISSUE_SHEET].rows[-1][6] = 'tampered'
    with pytest.raises(DispatchError, match='校驗'):
        store.verify_cost_checks(batch)
