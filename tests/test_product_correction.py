from copy import deepcopy
import uuid

import pytest

from correction_fakes import correction_store, TOOLS, RAW
from correction_storage import CORRECTION_SHEET, CorrectionUncertain, apply_correction, load_snapshot, read_outcome
from cost_audit import make_evidence, legacy_inputs
from dispatch_manager import DispatchError
from product_correction import current_fields, invalidate_correction, parse_proposal, plan_correction


def setup(evidence=False):
    store = correction_store()
    source = store.catalog()[0]
    if evidence:
        formulas = store.read_cost_source(source)
        store.put_quote_evidence(source, formulas, RAW, legacy_inputs(source, formulas),
                                 notes='合成測試：當時匯率4.8、國際8.5、內陸0', origin='review_attachment')
    snapshot = load_snapshot(store, source)
    proposal = parse_proposal(RAW, TOOLS['parse'])
    return store, snapshot, proposal


def plan(snapshot, proposal, fields=('supplier_code',), **edits):
    values = {**proposal['values'], **edits}
    return plan_correction(snapshot, fields, values, proposal,
                           actor='合成測試者', basis='對照合成原文', formula_builder=TOOLS['formulas'])


def test_code_only_preserves_every_unselected_cell_and_formula():
    _, snapshot, proposal = setup()
    result = plan(snapshot, proposal)
    assert result['changes'] == [dict(row=4, col=1, before='貨號 TEST-1', after='貨號 TEST-NEW')]
    assert not result['impacts'] and not result['parameters']
    assert snapshot['formulas'][1] == result['after'][1]


def test_size_preserves_packaging_and_does_not_change_money():
    _, snapshot, proposal = setup()
    result = plan(snapshot, proposal, ('prod_size',))
    assert len(result['changes']) == 1 and result['changes'][0]['col'] == 1
    assert '彩盒尺寸 10*8*3cm' in result['after'][1][1] and '包裝:彩盒' in result['after'][1][1]
    assert '尺寸 12*8*3cm' in result['after'][1][1]
    assert result['before'][1][2:] == result['after'][1][2:]


@pytest.mark.parametrize('fields', [(), ('no',), ('date',), ('vendor',), ('ex_rate',)])
def test_no_selection_and_protected_fields_rejected(fields):
    _, snapshot, proposal = setup()
    with pytest.raises(DispatchError):
        plan(snapshot, proposal, fields)


def test_financial_changes_require_saved_parameters_and_never_today_defaults():
    _, snapshot, proposal = setup()
    with pytest.raises(DispatchError, match='原報價依據'):
        plan(snapshot, proposal, ('price',))
    _, snapshot, proposal = setup(True)
    result = plan(snapshot, proposal, ('price',))
    assert result['parameters'] == dict(dom_rate='0', intl_rate='8.5', ex_rate='4.8')
    assert result['after'][1][6] == '10'
    assert result['after'][4][1] == '貨號 TEST-1'
    assert result['after'][3][1] == snapshot['formulas'][3][1]
    assert next(r for r in result['impacts'] if r['項目'] == '到手成本 TWD')['修正後'] == '50.9'
    assert next(r for r in result['impacts'] if r['項目'] == '廣告售價')['修正後'] == '57'
    snapshot['evidence']['source_hash'] = 'stale'
    with pytest.raises(DispatchError):
        plan(snapshot, proposal, ('price',))


def test_duplicate_detail_lines_and_cross_field_notes_rejected():
    _, snapshot, proposal = setup()
    snapshot['formulas'][1][1] += '\n尺寸 1cm\n尺寸 2cm'
    with pytest.raises(DispatchError, match='多行'):
        plan(snapshot, proposal, ('prod_size',))
    with pytest.raises(DispatchError, match='夾帶'):
        plan(snapshot, proposal, ('extra',), extra='計價單位：套')


def test_multi_product_parser_does_not_silently_choose_first():
    with pytest.raises(DispatchError, match='只有一款'):
        parse_proposal('合成原文', lambda raw: ({}, [{'name': 'A'}, {'name': 'B'}]))


def test_missing_actor_or_basis_blocks_plan():
    _, snapshot, proposal = setup()
    with pytest.raises(DispatchError, match='操作者'):
        plan_correction(snapshot, ['supplier_code'], proposal['values'], proposal,
                        actor='', basis='synthetic', formula_builder=TOOLS['formulas'])


def test_atomic_write_and_history_idempotence_and_no_other_product_changes():
    store, snapshot, proposal = setup()
    before = deepcopy(store.spreadsheet.sheets['G正版'].rows)
    result = plan(snapshot, proposal)
    operation = uuid.uuid4().hex
    saved = apply_correction(store, result, operation)
    assert saved['source']['supplier_code'] == 'TEST-NEW'
    assert len(store.spreadsheet.requests) == 1
    requests = store.spreadsheet.requests[0]['requests']
    assert len(requests) == 2 and 'updateCells' in requests[0] and 'appendCells' in requests[1]
    after = store.spreadsheet.sheets['G正版'].rows
    assert before[:4] == after[:4] and before[5:] == after[5:]
    again = apply_correction(store, result, operation)
    assert again == saved and len(store.spreadsheet.requests) == 1
    assert saved['record']['actor'] == '合成測試者' and saved['record']['proposal']['raw'] == RAW


def test_price_write_checks_formula_and_display_values():
    store, snapshot, proposal = setup(True)
    result = plan(snapshot, proposal, ('price',))
    store.spreadsheet.plan = result
    saved = apply_correction(store, result, uuid.uuid4().hex)
    assert saved['source']['price'] == '57'
    assert saved['source']['block'][1][10] == '50.9'


def test_uncertain_commit_is_query_only_and_never_appended_again():
    store, snapshot, proposal = setup()
    result = plan(snapshot, proposal)
    store.spreadsheet.fail_after_write = True
    operation = uuid.uuid4().hex
    with pytest.raises(CorrectionUncertain):
        apply_correction(store, result, operation)
    assert read_outcome(store, result, operation)['source']['supplier_code'] == 'TEST-NEW'
    assert len(store.spreadsheet.requests) == 1


@pytest.mark.parametrize('mutation', ['value', 'formula', 'move', 'duplicate', 'sheet', 'destination', 'tamper'])
def test_source_drift_and_tampered_plan_stop_without_product_write(mutation):
    store, snapshot, proposal = setup()
    result = plan(snapshot, proposal)
    ws = store.spreadsheet.sheets['G正版']
    if mutation == 'value':
        ws.rows[0][1] = '其他人修改'
    elif mutation == 'formula':
        ws.formula_rows[1][2] += '+0'
    elif mutation == 'move':
        ws.rows.insert(0, [])
    elif mutation == 'duplicate':
        ws.rows.extend(deepcopy(ws.rows[:6]))
    elif mutation == 'sheet':
        ws.id += 1
    elif mutation == 'destination':
        store.spreadsheet.id = 'different'
    elif mutation == 'tamper':
        result['after'][0][0] = 'no999'
    with pytest.raises(DispatchError):
        apply_correction(store, result, uuid.uuid4().hex)
    assert not store.spreadsheet.requests


def test_duplicate_supplier_code_blocks_even_if_target_no_unique():
    store, snapshot, proposal = setup()
    result = plan(snapshot, proposal, supplier_code='TEST-2')
    with pytest.raises(DispatchError, match='重複'):
        apply_correction(store, result, uuid.uuid4().hex)
    assert not store.spreadsheet.requests


def test_formula_like_name_written_as_literal_text():
    store, snapshot, proposal = setup()
    result = plan(snapshot, proposal, ('name',), name='=IMPORTXML("synthetic","x")')
    apply_correction(store, result, uuid.uuid4().hex)
    cell = store.spreadsheet.requests[0]['requests'][0]['updateCells']['rows'][0]['values'][0]
    assert 'stringValue' in cell['userEnteredValue']


def test_cache_invalidation_only_drops_affected_checks_and_never_approves():
    state = {'dispatch_review_catalog': [], 'dispatch_history': [],
             'dispatch_bulk_result_one': {'ids': ['G:1']}, 'dispatch_bulk_result_two': {'ids': ['G:2']},
             'dispatch_cost_G:1abc': 'old', 'dispatch_cost_G:2abc': 'keep'}
    invalidate_correction(state, 'G:1')
    assert state == {'dispatch_bulk_result_two': {'ids': ['G:2']}, 'dispatch_cost_G:2abc': 'keep'}


def test_other_supplier_keeps_its_own_domestic_rate():
    store = correction_store()
    ws = store.spreadsheet.sheets['G正版']
    ws.rows[0][11] = ws.formula_rows[0][11] = 'v菲凡'
    ws.formula_rows[1][8] = '=ROUNDUP((H2/1000)*2,2)'
    source = store.catalog()[0]
    formulas = store.read_cost_source(source)
    store.put_quote_evidence(source, formulas, RAW, legacy_inputs(source, formulas),
                             notes='合成歷史參數：境內費率2', origin='review_attachment')
    result = plan(load_snapshot(store, source), parse_proposal(RAW, TOOLS['parse']), ('price',))
    assert result['parameters']['dom_rate'] == '2'
    assert next(r for r in result['impacts'] if r['項目'] == '到手成本 TWD')['修正後'] == '51.6'


def test_unit_change_needs_explicit_price_and_quantity_selection():
    _, snapshot, proposal = setup(True)
    with pytest.raises(DispatchError, match='一併勾選'):
        plan(snapshot, proposal, ('unit',), unit='套')
    result = plan(snapshot, proposal, ('unit', 'price', 'qty'), unit='套')
    assert '計價單位：套' in result['after'][1][1] and result['after'][2][1] == '裝箱 300套/箱'


def test_last_check_catches_change_during_preparation():
    store, snapshot, proposal = setup()
    result = plan(snapshot, proposal)
    original = store._sheet
    def sheet(title, create=False):
        ws = original(title, create)
        if title == CORRECTION_SHEET and create:
            store.spreadsheet.sheets['G正版'].rows[0][1] = '合成競態修改'
        return ws
    store._sheet = sheet
    with pytest.raises(DispatchError, match='已被修改'):
        apply_correction(store, result, uuid.uuid4().hex)
    assert not store.spreadsheet.requests


def test_postwrite_disagreement_is_not_success_and_preserves_audit():
    store, snapshot, proposal = setup()
    result = plan(snapshot, proposal)
    original = store.spreadsheet.batch_update
    def update(body):
        original(body)
        store.spreadsheet.sheets['G正版'].formula_rows[0][1] = '合成同時修改'
    store.spreadsheet.batch_update = update
    with pytest.raises(CorrectionUncertain, match='讀回不符'):
        apply_correction(store, result, uuid.uuid4().hex)
    assert len(store.spreadsheet.sheets[CORRECTION_SHEET].rows) > 1
