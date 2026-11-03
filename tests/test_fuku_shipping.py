"""Synthetic examples only; no supplier chat or production writes."""
from copy import deepcopy
from decimal import Decimal

import pytest

from cost_audit import audit, calculate, legacy_inputs
from supplier_names import international_rate_for_vendor, normalize_vendor, vendor_options
from quote_create_form import new_quote_destination
from category_codes import QUOTE_CATEGORIES
from test_v74_safety import ns


@pytest.mark.parametrize('vendor', ['富酷', 'v富酷', 'V-富酷', '富酷 (盒改)', '富酷（盒改）'])
@pytest.mark.parametrize('price,fx', [('13.5', '5'), ('20.001', '4.85')])
def test_direct_exact_cost_zero_freight_optional_weight(vendor, price, fx):
    assert normalize_vendor(vendor) == 'v富酷'
    assert international_rate_for_vendor(vendor, 99) == 0
    inputs=dict(price=price, qty=48, unit='盒', carton_kg=0, unit_g=0,
                dom_rate=7, intl_rate=99, ex_rate=fx)
    result=calculate(inputs, vendor)
    assert result['cost'][0] == Decimal(price)*Decimal(fx)
    assert result['weight'][0] is None
    assert result['domestic'][0] == result['international'][0] == 0
    formulas=ns['build_cost_formulas'](2,0,0,48,7,99,float(fx),vendor,final_price=float(price))
    assert formulas['weight'] == ''
    assert formulas['domestic'] == formulas['international']
    assert 'G2*'+fx in formulas['cost'] and 'ROUND' not in formulas['cost']
    assert 'ISNUMBER(G2)' in formulas['cost']
    assert not ns['cost_blockers'](float(price),48,0,0,7,99,float(fx),price_unit='盒',qty_unit='盒',vendor=vendor)


@pytest.mark.parametrize('line', ['包邮海快：13.5元/盒', '包郵 海快價: RMB13.5/盒',
                                   '包郵海快每盒13.5元', '一盒包郵海快13.5元'])
def test_parse_complete_price_keeps_raw_and_specs(line):
    raw='測試收納玩具\n貨號:TEST-FK-01\n'+line+'\n裝箱:48盒\n包裝:彩盒\n產品尺寸:12*8*5cm'
    c,p=ns['parse_text'](raw)
    assert c['raw_text'] == raw
    assert (c['price'],c['price_unit'],c['qty'],c['qty_unit']) == (13.5,'盒',48,'盒')
    assert c['unit_weight_g'] == c['weight'] == 0
    assert c['prod_size'] == '12*8*5cm'
    assert '包郵海快' in c['extra_tags'] and '包裝:彩盒' in c['extra_tags']
    assert p == [{'code':'TEST-FK-01','name':'測試收納玩具'}]
    assert not c['issues']


@pytest.mark.parametrize('extra', ['\n另加運費5元', '\n價格:20元/盒', '\n貨號:TEST-OTHER', '\n裝箱:60盒'])
def test_shipping_phrase_does_not_bypass_uncertainty(extra):
    raw='測試商品\n貨號:TEST-FK-01\n包郵海快13.5元/盒\n裝箱48盒'+extra
    c,p=ns['parse_text'](raw)
    assert c['raw_text']==raw and c['issues']
    assert ns['cost_blockers'](c['price'],c['qty'],c['weight'],c['unit_weight_g'],0,0,5,
                               issues=c['issues'],price_unit=c['price_unit'],qty_unit=c['qty_unit'],vendor='v富酷')


def test_units_conflict_still_blocks():
    c,_=ns['parse_text']('測試商品\n包郵海快13.5元/盒\n裝箱48個')
    assert ns['cost_blockers'](c['price'],c['qty'],0,0,0,0,5,
                               price_unit=c['price_unit'],qty_unit=c['qty_unit'],vendor='v富酷')


@pytest.mark.parametrize('vendor', ['富酷其他店', 'v菲凡', 'v多品村', ''])
def test_other_suppliers_do_not_inherit_missing_weight_exemption(vendor):
    assert ns['cost_blockers'](13.5,48,0,0,1.5,8.5,5,price_unit='盒',qty_unit='盒',vendor=vendor)


def test_routing_and_explicit_supplier():
    assert '富酷' in QUOTE_CATEGORIES and 'v富酷' in vendor_options()[0]
    assert new_quote_destination('', 'v富酷') == '富酷'
    assert new_quote_destination('G正版', '富酷 (盒改)') == '富酷'
    assert new_quote_destination('G正版', 'v菲凡') == 'G正版'
    with pytest.raises(ValueError): new_quote_destination('富酷', 'v菲凡')


def test_saved_block_independent_audit_and_evidence_roundtrip():
    from dispatch_fakes import FakeSpreadsheet
    from dispatch_storage import CloudDispatchStore
    from quote_evidence import queue_evidence, save_evidence
    formulas=ns['build_product_block']('no1','2026/11/3',2,'測試商品','TEST-FK-01',13.5,48,'盒',0,0,7,99,5,'v富酷')
    rows=deepcopy(formulas)
    rows[1][2:11]=['75','77.6','79.4','84.4','13.5','','0','0','67.5']
    store=CloudDispatchStore(FakeSpreadsheet({'富酷':rows}))
    store.spreadsheet.sheets['富酷'].formula_override=formulas
    source=store.catalog()[0]
    assert source['category']=='富酷'
    inputs=legacy_inputs(source,formulas)
    assert inputs['ex_rate']=='5' and inputs['dom_rate']==inputs['intl_rate']=='0'
    assert audit(source,formulas)['quote_math_pass']
    raw='測試商品\n貨號:TEST-FK-01\n包郵海快13.5元/盒\n裝箱48盒'
    _,pending=queue_evidence({},'富酷',1,formulas,raw,inputs,{},'合成富酷來源')
    save_evidence(store,pending)
    fresh=CloudDispatchStore(store.spreadsheet)
    report,_=fresh.cost_audit(source)
    assert report['source_ready'] and report['quote_math_pass'] and report['raw_source']==raw
    original=deepcopy(rows)
    for bad in ('=G99*5','=ROUNDUP(G2*5,0)',"='Other'!G2*5"):
        changed=deepcopy(formulas);changed[1][10]=bad
        assert legacy_inputs(source,changed)['ex_rate']==''
        assert not audit(source,changed)['quote_math_pass']
    assert rows==original


def test_same_source_duplicates_remain_blocked():
    rows=ns['build_product_block']('no7','2026/11/3',2,'測試商品','TEST-FK-01',13.5,48,'盒',0,0,0,0,5,'v富酷')
    assert ns['duplicate_messages']([{'code':'TEST-FK-01','name':'測試商品'}], {'富酷':rows})


def test_weight_present_still_preserved_without_freight_charge():
    formulas=ns['build_cost_formulas'](2,24,0,48,7,99,5,'v富酷',final_price=13.5)
    assert '1.05' in formulas['weight'] and 'H2' not in formulas['cost']
    with pytest.raises(ValueError):
        calculate(dict(price=13.5,qty=48,unit='盒',carton_kg=24,unit_g=1,dom_rate=0,intl_rate=0,ex_rate=5),'v富酷')


def test_missing_price_unit_is_never_inferred_from_carton():
    c,_=ns['parse_text']('測試商品\n包郵海快13.5元\n裝箱48盒')
    assert c['price_unit']==''
    assert '富酷計價單位未確認' in ns['cost_blockers'](13.5,48,0,0,0,0,5,qty_unit='盒',vendor='v富酷')


def test_actual_writer_uses_dedicated_sheet_and_readback(monkeypatch):
    from test_quote_save_performance import batch_cloud
    from unittest.mock import Mock
    sheet,book,catalog,context,_=batch_cloud(monkeypatch, titles=('富酷','G正版'))
    sheet.title='富酷'
    book.worksheets.return_value=[sheet,Mock(title='G正版')]
    context['category']='富酷'
    rows=ns['build_product_block']('no1','2026/11/3',4,'測試商品','TEST-FK-01',13.5,48,'盒',0,0,0,0,5,'v富酷')
    sheet.get.return_value=rows
    assert ns['save_bulk_to_worksheet']('富酷',rows,3,expected_rows=[[]],save_context=context)
    assert sheet.update.call_args.kwargs['values'][1][10]==rows[1][10]
    sheet.get.assert_called_once_with('A3:L8',value_render_option='FORMULA')


@pytest.mark.parametrize('heading,product_line', [
    ('巨無霸鐵盒｜測試系列', '測試風扇'),
    ('【方形鐵盒系列｜測試品牌】', '📷測試相機'),
    ('#長盒#透明PVC盒系列', '——測試工具——'),
    ('【巨無霸 · 全版印刷PVC盒】', '⚡測試遙控玩具'),
])
@pytest.mark.parametrize('qty_line', ['箱規：36／箱', '-數量：36/箱', '· 數量：36 /箱'])
def test_actual_layout_shapes_with_synthetic_data(heading, product_line, qty_line):
    raw=('👏現貨！現貨！\n💥下單可馬上發貨！\n\n'+heading+'\n'+product_line+
         '\n✔食品級材質\n'+qty_line+'\n-編號：TEST-FK-02\n-重量：510g/680g'+
         '\n· 包裝：16*11*24cm\n🚢 包郵海快：26.5 元\n💰臺灣控價不低於999臺幣')
    c, products=ns['parse_text'](raw)
    assert c['raw_text']==raw
    assert (c['price'],c['qty'],c['price_unit'],c['qty_unit'])==(26.5,36,'','')
    assert products[0]['code']=='TEST-FK-02'
    assert products[0]['name'] in {'測試風扇','測試相機','測試工具','測試遙控玩具'}
    assert c['weight']==c['unit_weight_g']==0
    assert '510g/680g' in c['extra_tags'] and '999臺幣' in c['extra_tags']
    assert '16*11*24cm' in c['extra_tags']
    assert any('並列兩個重量' in issue for issue in c['issues'])
    assert ns['cost_blockers'](26.5,36,0,0,0,0,5,c['issues'],c['price_unit'],c['qty_unit'],'v富酷')


def test_inclusive_layout_retains_mixed_variants_and_no_code():
    raw='👏現貨！\n#長方盒#透明PVC盒\n——工具混搭——\n拿一件相當於拿10款\n①測試品A\n②測試品B\n數量:100/箱\n包郵海快:18.5元'
    c,p=ns['parse_text'](raw)
    assert p==[{'code':'','name':'工具混搭'}]
    assert '拿一件相當於拿10款' in c['extra_tags']
    assert c['raw_text']==raw and c['qty_unit']==c['price_unit']==''


def test_layout_never_hides_conflicting_prices_codes_or_fees():
    raw='👏現貨！\n測試商品\n貨號:TEST-FK-03\n包郵海快:13.5元\n箱規:48/箱'
    for extra in ('\n價格:20元','\n貨號:TEST-FK-04','\n箱規:60/箱','\n另加運費5元'):
        c,_=ns['parse_text'](raw+extra)
        assert c['issues']
