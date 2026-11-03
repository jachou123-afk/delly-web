"""Synthetic display/evidence regression cases; no private supplier source."""
from copy import deepcopy
import pytest
from fuku_quote_display import compact_specs, unassigned_weight_note, layout_requests
from test_v74_safety import ns


NOTES = "USB充電接口、充電更方便\n包裝尺寸:10×12×14cm\n鐵盒重量:510g/680g\n產品貨號:TEST-FK\n典藏拉絲鐵盒+凹面設計+定型珍珠棉\n鐵盒尺寸:10×12×14cm\n箱規:48/箱\n包郵海快:13.5元\n人工確認依據：使用者確認每盒（含測試品及鐵盒）\n富酷重量僅保留原文；計重欄留空。確認依據：測試確認"


def test_compact_display_keeps_packaging_and_moves_weight_without_audit_prose():
    result=compact_specs('盒','','','',NOTES)
    assert result.splitlines()==['計價單位：盒','內容：測試品及鐵盒','鐵盒：10×12×14cm','包裝：拉絲鐵盒＋凹面設計＋定型珍珠棉','特色：USB充電']
    for excluded in ('人工確認','TEST-FK','包郵海快','510g','13.5','48/箱'):
        assert excluded not in result
    assert unassigned_weight_note(NOTES)=='重量 510g/680g（原文，未指定淨重／毛重）'


def test_display_change_never_changes_financial_columns_or_source_text():
    raw='測試商品\n貨號TEST-FK\n包郵海快13.5元/盒\n裝箱48盒'
    c,_=ns['parse_text'](raw)
    before=deepcopy(c)
    block=ns['build_product_block']('no1','2026/11/3',4,'測試商品','TEST-FK',13.5,48,'盒',0,0,0,0,5,'v富酷',extra=NOTES)
    formulas=ns['build_cost_formulas'](4,0,0,48,0,0,5,'v富酷',final_price=13.5)
    assert block[1][2:11]==[formulas[k] for k in ('quote_10','quote_13','quote_15','quote_20')]+[13.5]+[formulas[k] for k in ('weight','domestic','international','cost')]
    assert block[2][1]=='裝箱 48盒/箱' and block[4][1]=='貨號 TEST-FK'
    assert c==before and c['raw_text']==raw


def test_other_supplier_display_is_unchanged():
    for vendor in ('v多品村','v菲凡','v方小姐'):
        b=ns['build_product_block']('no1','2026/11/3',4,'測試商品','TEST-FK',13.5,48,'盒',0,510,0,9,5,vendor,extra=NOTES)
        assert b[1][1]=='計價單位：盒\n'+NOTES


def test_important_cautions_and_overflow_are_visible():
    notes='包裝:彩盒\n台灣控價:999元\n隨機混裝:3色\n電池:不含\n插腳:台灣型\n另加:需確認'
    text=compact_specs('盒','10cm','12cm','24cm',notes)
    assert len(text.splitlines())==6 and '其餘規格與注意事項' in text
    assert notes.endswith('另加:需確認')


def test_layout_scope_has_no_values_or_formula_writes():
    requests=layout_requests(123,3,1)
    assert len(requests)==8
    assert all(set(r)<= {'updateDimensionProperties','repeatCell'} for r in requests)
    for r in requests[1:7]:
        assert 2 <= r['updateDimensionProperties']['range']['startIndex'] < 8
    assert requests[0]['updateDimensionProperties']['properties']['pixelSize']==340
    assert requests[-1]['repeatCell']['fields']=='userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment'


@pytest.mark.parametrize('label', ['鐵盒重量', '單盒'])
def test_compact_unit_and_confirmed_weight_survive_formal_evidence_roundtrip(label):
    from dispatch_fakes import FakeSpreadsheet
    from dispatch_storage import CloudDispatchStore
    from quote_evidence import queue_evidence, save_evidence
    from cost_audit import audit, legacy_inputs, make_evidence
    notes = NOTES.replace('鐵盒重量', label)
    rows = ns['build_product_block']('no1','2026/11/3',2,'測試商品','TEST-FK',13.5,48,'盒',0,0,0,0,5,'v富酷',extra=notes)
    formulas = deepcopy(rows)
    rows[1][2:11] = ['75','77.6','79.4','84.4','13.5','','0','0','67.5']
    store = CloudDispatchStore(FakeSpreadsheet({'富酷': rows}))
    store.spreadsheet.sheets['富酷'].formula_override = formulas
    source = store.catalog()[0]
    assert source['unit'] == '盒'
    raw = f'測試商品\n{label}：510g／680g\n包郵海快13.5元／盒\n裝箱48盒'
    inputs = legacy_inputs(source, formulas)
    inputs.update(carton_kg="0", unit_g="0")  # Explicit saved confirmation; never infer a weight.
    evidence = make_evidence(source, formulas, raw, inputs, notes=notes, origin="test")
    assert audit(source, formulas, evidence)['quote_math_pass']
    _, pending = queue_evidence({}, '富酷', 1, formulas, raw, inputs, {}, notes)
    save_evidence(store, pending)
    report, _ = store.cost_audit(source)
    assert report['source_ready'] and report['quote_math_pass']
    assert report['raw_source'] == raw and report['notes'] == notes
    for bad in (notes.replace('確認依據：測試確認', ''), notes.replace('510g/680g', '511g/680g')):
        e = make_evidence(source, formulas, raw, inputs, notes=bad, origin="test")
        assert not audit(source, formulas, e)['quote_math_pass']
    for bad_weight in ('0', '510'):
        bad = deepcopy(source); bad['block'][1][7] = bad_weight
        assert not audit(bad, formulas, evidence)['quote_math_pass']


def test_package_contents_require_explicit_parenthesized_confirmation():
    assert '內容：' not in compact_specs('盒','','','','包裝（不含電池）')
