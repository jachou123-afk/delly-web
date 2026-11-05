"""Synthetic regressions for inclusive quote layout, without cloud writes."""
import pytest
from fuku_quote_parser import prepare_inclusive_sea_layout
from test_v74_safety import ns


def test_boilerplate_and_trailing_dimension_separator_preserve_source():
    raw = ('🔥現貨！\n‼下單可馬上安排發貨！\n測試桌面機\n編號:TEST-LAYOUT\n'
           '數量:12/箱\n重量:500g/700g\n包裝:30*14*16*cm\n包郵海快:20元')
    common, products = ns['parse_text'](raw)
    assert common['raw_text'] == raw
    assert products == [{'code': 'TEST-LAYOUT', 'name': '測試桌面機'}]
    assert common['price'] == 20 and common['qty'] == 12
    assert common['price_unit'] == common['qty_unit'] == ''
    assert common['weight'] == common['unit_weight_g'] == 0
    assert '包裝:30*14*16*cm' in common['extra_tags']
    assert not any('尺寸欄位' in issue for issue in common['issues'])
    assert any('並列兩個重量' in issue for issue in common['issues'])
    assert ns['cost_blockers'](20,12,0,0,0,0,5,common['issues'],'','','v富酷')


@pytest.mark.parametrize('line', ['包裝:30*14**cm', '包裝:30*14*16*',
                                '包裝:30*14*16*2cm', '尺寸:30*14*16*cm'])
def test_no_guess_for_incomplete_or_other_fields(line):
    raw = '測試桌面機\n'+line+'\n包郵海快:20元'
    normalized, _ = prepare_inclusive_sea_layout(raw)
    assert line in normalized


def test_non_inclusive_input_is_unchanged():
    raw = '下單可馬上安排發貨！\n測試桌面機\n包裝:30*14*16*cm\n單價20元'
    assert prepare_inclusive_sea_layout(raw) == (raw, None)
