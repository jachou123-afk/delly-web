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


def _paired_weight_source():
    return '測試商品\n貨號:TEST-PAIR\n鐵盒重量:510g/680g\n包郵海快:13.5元/盒\n裝箱48盒'


def test_explicit_paired_weight_notes_keep_original_and_exact_cost():
    raw=_paired_weight_source(); c,_=ns['parse_text'](raw)
    issues,applied=ns['confirmed_fuku_weight_notes'](c['issues'],raw,c['extra_tags'],0,0,
        vendor='v富酷',confirmed=True,evidence='使用者確認重量保留原文')
    assert applied and not issues and c['raw_text']==raw
    assert '510g/680g' in c['extra_tags'] and c['weight']==c['unit_weight_g']==0
    assert not ns['cost_blockers'](13.5,48,0,0,0,0,5,issues,'盒','盒','v富酷')
    block=ns['build_product_block']('no1','2026/11/3',2,'測試商品','TEST-PAIR',13.5,48,'盒',
        0,0,0,0,5,'v富酷',extra=c['extra_tags']+'\n富酷重量僅保留原文；計重欄留空。')
    assert block[1][7]=='' and '510g/680g' not in block[1][1]
    assert block[3][1]=='重量 510g/680g（原文，未指定淨重／毛重）'


@pytest.mark.parametrize('vendor,confirmed,evidence,kg,g', [
    ('v菲凡',True,'yes',0,0),('v多品村',True,'yes',0,0),('v方小姐',True,'yes',0,0),
    ('v富酷',False,'yes',0,0),('v富酷',True,'',0,0),('v富酷',True,'yes',1,0),
    ('v富酷',True,'yes',0,510)])
def test_weight_notes_cannot_guess_weight_or_cross_supplier(vendor,confirmed,evidence,kg,g):
    raw=_paired_weight_source();c,_=ns['parse_text'](raw)
    result,applied=ns['confirmed_fuku_weight_notes'](c['issues'],raw,c['extra_tags'],kg,g,
        vendor=vendor,confirmed=confirmed,evidence=evidence)
    assert not applied and result==c['issues']


@pytest.mark.parametrize('extra', ['\n另加運費5元','\n價格20元/盒','\n裝箱60盒','\n貨號TEST-OTHER'])
def test_weight_notes_leave_every_unrelated_guard(extra):
    raw=_paired_weight_source()+extra;c,_=ns['parse_text'](raw)
    result,_=ns['confirmed_fuku_weight_notes'](c['issues'],raw,c['extra_tags'],0,0,
        vendor='v富酷',confirmed=True,evidence='重量保留原文')
    assert result and ns['cost_blockers'](13.5,48,0,0,0,0,5,result,'盒','盒','v富酷')


@pytest.mark.parametrize('change', ['removed_note','changed_note','scoped_weight','no_inclusive_price','negative_weight'])
def test_weight_notes_require_exact_retained_unscoped_source(change):
    raw=_paired_weight_source()
    if change=='scoped_weight':raw+='\n整箱毛重24kg'
    if change=='no_inclusive_price':raw=raw.replace('包郵海快','單盒價格')
    if change=='negative_weight':raw=raw.replace('510g','-510g')
    c,_=ns['parse_text'](raw);notes=c['extra_tags']
    if change=='removed_note':notes=''
    if change=='changed_note':notes=notes.replace('510g','500g')
    result,applied=ns['confirmed_fuku_weight_notes'](c['issues'],raw,notes,0,0,
        vendor='v富酷',confirmed=True,evidence='重量保留原文')
    assert not applied and result==c['issues']


@pytest.mark.parametrize('vendor', ['v富酷','v菲凡','v方小姐'])
def test_form_weight_notes_confirmation_is_bound_and_supplier_gated(vendor):
    from test_v74_ui import paste,save_button
    raw=_paired_weight_source();app=paste(raw,select_source=False)
    assert save_button(app).disabled and not app.exception
    next(x for x in app.text_input if x.label=='富酷重量原文保留確認依據').set_value('使用者確認重量保留原文').run()
    next(x for x in app.checkbox if x.label.startswith('已確認本款為富酷包郵海快')).check().run()
    assert not save_button(app).disabled
    next(x for x in app.selectbox if x.label=='📂 分頁').set_value('富酷' if vendor=='v富酷' else 'G正版')
    next(x for x in app.selectbox if x.label=='🏷️ 廠商').set_value(vendor)
    next(x for x in app.checkbox if x.label.startswith('我已逐欄對照')).check()
    save_button(app).click().run()
    assert not app.exception
    if vendor=='v富酷':
        assert app.session_state['test_save_calls']==1
        assert app.session_state['test_saved_category']=='富酷'
        assert app.session_state['test_evidence']['raw']==raw
        assert app.session_state['test_evidence']['inputs']['unit_g']==0
    else:
        assert 'test_save_calls' not in app.session_state
    next(x for x in app.number_input if x.label=='進價(RMB)').set_value(14.0).run()
    assert save_button(app).disabled
    assert not next(x for x in app.checkbox if x.label.startswith('已確認本款為富酷包郵海快')).value


@pytest.mark.parametrize('bullet', ['-', '· ', '• ', '▪'])
@pytest.mark.parametrize('label', ['鐵盒重量', '單盒'])
def test_paired_weight_list_bullets_preserve_original_and_other_guards(bullet, label):
    from fuku_quote_display import confirmed_unassigned_weight_display, unassigned_weight_note
    raw = _paired_weight_source().replace('鐵盒重量:', bullet + label + ':')
    c, _ = ns['parse_text'](raw)
    issues, applied = ns['confirmed_fuku_weight_notes'](c['issues'], raw, c['extra_tags'], 0, 0,
        vendor='v富酷', confirmed=True, evidence='使用者確認雙重量僅保留原文')
    assert applied and not issues and c['raw_text'] == raw
    blocked, applied_without_confirmation = ns['confirmed_fuku_weight_notes'](c['issues'], raw, c['extra_tags'], 0, 0,
        vendor='v富酷', confirmed=False, evidence='')
    assert blocked and not applied_without_confirmation
    notes = c['extra_tags'] + '\n富酷重量僅保留原文；計重欄留空。確認依據：測試確認'
    display = unassigned_weight_note(notes)
    assert confirmed_unassigned_weight_display(raw, notes, display)
    assert not confirmed_unassigned_weight_display(raw, notes.replace('510g', '511g'), display)
    for bad in ('整箱毛重:24kg', '另加運費5元'):
        changed = raw + '\n' + bad
        parsed, _ = ns['parse_text'](changed)
        left, _ = ns['confirmed_fuku_weight_notes'](parsed['issues'], changed, parsed['extra_tags'], 0, 0,
            vendor='v富酷', confirmed=True, evidence='測試確認')
        assert left


@pytest.mark.parametrize('promo', ['‼下單可馬上發貨！', '下單可立馬安排發貨!', '日本商超熱賣款', '💥爆品上新💥'])
@pytest.mark.parametrize('quantity', ['一箱數量:9/箱', '箱裝:9/箱', '箱規:9盒/箱'])
def test_new_inclusive_layout_keeps_name_count_and_missing_price_unit(promo, quantity):
    raw=promo+'\n測試暖風機\n編號:TEST-NEW\n'+quantity+'\n包郵海快:21.5元'
    c,p=ns['parse_text'](raw)
    assert p==[{'code':'TEST-NEW','name':'測試暖風機'}]
    assert c['qty']==9 and c['price']==21.5 and c['price_unit']==''
    assert c['raw_text']==raw
    c,_=ns['parse_text'](raw+'\n數量:11/箱\n另加運費3元')
    assert c['issues']

@pytest.mark.parametrize('label',['一盒重量','產品重量'])
def test_paired_weight_aliases_require_confirmation_and_exact_source(label):
    from fuku_quote_display import unassigned_weight_note,confirmed_unassigned_weight_display
    raw=_paired_weight_source().replace('鐵盒重量',label)
    c,_=ns['parse_text'](raw)
    issues,ok=ns['confirmed_fuku_weight_notes'](c['issues'],raw,c['extra_tags'],0,0,
        vendor='v富酷',confirmed=True,evidence='已確認雙重量僅保留原文')
    assert ok and not issues and c['weight']==c['unit_weight_g']==0
    notes=c['extra_tags']+'\n富酷重量僅保留原文；計重欄留空。確認依據：測試確認'
    assert confirmed_unassigned_weight_display(raw,notes,unassigned_weight_note(notes))
    assert not confirmed_unassigned_weight_display(raw,notes.replace('510g','511g'),unassigned_weight_note(notes))
    blocked,ok=ns['confirmed_fuku_weight_notes'](c['issues'],raw,c['extra_tags'],0,0,
        vendor='v富酷',confirmed=False,evidence='')
    assert blocked and not ok

def test_explicit_umbrella_unit_is_preserved_and_price_stays_unconfirmed():
    raw='測試傘\n編號:TEST-UMBRELLA\n數量:24把/箱\n包郵海快:11元'
    c,_=ns['parse_text'](raw)
    assert c['qty']==24 and c['qty_unit']=='把' and c['price_unit']==''
    issues,unit=ns['confirmed_cost_basis'](c['issues'],'','把',unit_confirmed=True,evidence='每把報價確認')
    assert unit=='把' and not issues
    result=calculate(dict(price=11,qty=24,unit='把',carton_kg=0,unit_g=0,dom_rate=0,intl_rate=0,ex_rate=5),'v富酷')
    assert result['cost'][0]==55
