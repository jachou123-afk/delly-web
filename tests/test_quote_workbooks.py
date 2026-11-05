from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from quote_workbooks import (ACTIVE_ID, LEGACY_ID, active_id, combined_catalog,
                             next_product_no, validate_next_numbers)
from test_v74_safety import block, ns
from test_quote_save_performance import batch_cloud


def test_existing_deployment_secret_moves_to_explicit_new_id():
    assert active_id(LEGACY_ID) == ACTIVE_ID
    assert active_id('') == ACTIVE_ID
    assert active_id('another-deployment') == 'another-deployment'


def test_numbering_continues_per_category_without_copying_old_rows():
    old = {'W玩具': [['no1867']], '富酷': [['no20']]}
    new = {'W玩具': [[]], '富酷': [['no21']]}
    before = deepcopy((old, new))
    assert next_product_no('W玩具', new, old) == 'no1868'
    assert next_product_no('富酷', new, old) == 'no22'
    assert (old, new) == before


def test_missing_history_and_reused_numbers_block():
    with pytest.raises(ValueError, match='缺少'):
        next_product_no('W玩具', {'W玩具': [[]]}, {})
    with pytest.raises(ValueError, match='重複 NO'):
        next_product_no('W玩具', {'W玩具': [['no2']]}, {'W玩具': [['no2']]})
    with pytest.raises(ValueError, match='已變動'):
        validate_next_numbers('W玩具', {'W玩具': [[]]}, {'W玩具': [['no8']]}, [{'no':'no8'}])


def test_legacy_duplicate_blocks_even_when_new_book_empty():
    old = {'G正版': block()}
    merged = combined_catalog({'G正版': [[]]}, old)
    messages = ns['duplicate_messages']([{'code':'TEST001', 'name':'新品名'}], merged)
    assert messages and '舊表 · G正版' in messages[0]


def test_history_outage_cannot_be_treated_as_no_products(monkeypatch):
    sheet, book, catalog, context, _ = batch_cloud(monkeypatch)
    book.id = ACTIVE_ID
    monkeypatch.setitem(ns, 'get_quote_history', Mock(side_effect=RuntimeError('history unavailable')))
    assert not ns['save_bulk_to_worksheet']('G正版', block(), 3, expected_rows=[[]], save_context=context)
    sheet.update.assert_not_called()


def test_new_save_rechecks_historical_duplicate_and_no(monkeypatch):
    sheet, book, catalog, context, _ = batch_cloud(monkeypatch)
    book.id = ACTIVE_ID
    old = block()
    old[0][0] = 'no9'
    rows = block()
    rows[0][0] = 'no10'
    monkeypatch.setitem(ns, 'get_quote_history', Mock(return_value={'G正版': old}))
    assert not ns['save_bulk_to_worksheet']('G正版', rows, 3, expected_rows=[[]], save_context=context)
    sheet.update.assert_not_called()
    assert '舊表' in ns['st'].error.call_args.args[0]


def test_stale_no_blocks_before_any_write(monkeypatch):
    sheet, book, catalog, context, _ = batch_cloud(monkeypatch)
    book.id = ACTIVE_ID
    rows = block()
    rows[0][0] = 'no10'
    monkeypatch.setitem(ns, 'get_quote_history', Mock(return_value={'G正版': [['no10']]}))
    assert not ns['save_bulk_to_worksheet']('G正版', rows, 3, expected_rows=[[]], save_context=context)
    sheet.update.assert_not_called()


def test_success_writes_only_new_book(monkeypatch):
    sheet, book, catalog, context, _ = batch_cloud(monkeypatch)
    book.id = ACTIVE_ID
    rows = block()
    rows[0][0] = 'no10'
    sheet.get.return_value = rows
    old = {'G正版': [['no9']]}
    monkeypatch.setitem(ns, 'get_quote_history', Mock(return_value=old))
    assert ns['save_bulk_to_worksheet']('G正版', rows, 3, expected_rows=[[]], save_context=context)
    assert sheet.update.call_count == 1
    assert old == {'G正版': [['no9']]}
    book.batch_update.assert_called_once()


def test_snapshot_uses_live_history_for_next_no(monkeypatch):
    sheet, book, catalog, context, _ = batch_cloud(monkeypatch)
    book.id = ACTIVE_ID
    monkeypatch.setitem(ns, 'get_dispatch_store', Mock(return_value=context['store']))
    monkeypatch.setitem(ns, 'get_quote_history', Mock(return_value={'G正版': [['no1483']]}))
    snapshot = ns['get_quote_save_snapshot']('G正版')
    assert snapshot['next_no'] == 'no1484'
    sheet.update.assert_not_called()
