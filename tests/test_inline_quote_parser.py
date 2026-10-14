"""Synthetic one-line supplier quotations; exercise the real parser and block."""
import pytest

from inline_quote_parser import expand_inline_quote
from test_v74_safety import ns


@pytest.mark.parametrize('raw', [
    '3寸測試太陽能擺件彩盒尺寸9.1x7.2x6.3Cm  一箱120盒一盒包郵6.2元',
    '3寸測試太陽能擺件，彩盒尺寸：9.1x7.2x6.3Cm；一箱120盒，一盒包郵6.2元。',
    '3寸測試太陽能擺件 每盒價格6.2元 裝箱120盒 彩盒尺寸9.1x7.2x6.3Cm',
])
def test_name_is_only_name_and_box_dimensions_are_below_it(raw):
    common, products = ns['parse_text'](raw)
    assert products == [{'name': '3寸測試太陽能擺件', 'code': ''}]
    assert common['raw_text'] == raw
    assert (common['price'], common['qty'], common['price_unit'], common['qty_unit']) == (6.2, 120, '盒', '盒')
    assert common['color_box_size'] == '9.1x7.2x6.3Cm'
    assert not common['prod_size'] and not common['outer_box_size']
    assert not common['extra_tags'] and not common['issues']
    block = ns['build_product_block']('no1', '2026/1/1', 4, products[0]['name'], '',
        common['price'], common['qty'], common['qty_unit'], 0, 0, 1.5, 8.5, 4.8,
        'v方小姐', color_size=common['color_box_size'])
    assert block[0][1] == '3寸測試太陽能擺件'
    assert block[1][1] == '計價單位：盒\n彩盒尺寸 9.1x7.2x6.3Cm'
    assert block[2][1] == '裝箱 120盒/箱'
    assert block[1][6] == 6.2
    assert block[1][7] == ''


def test_inline_dimensions_remain_separate_and_unit_conflict_is_not_hidden():
    raw = '測試擺件產品尺寸5cm 彩盒尺寸8x9x6cm 外箱尺寸40x30x20cm 一箱120盒每個包郵6元'
    common, products = ns['parse_text'](raw)
    assert products[0]['name'] == '測試擺件'
    assert (common['prod_size'], common['color_box_size'], common['outer_box_size']) == ('5cm','8x9x6cm','40x30x20cm')
    assert ns['cost_blockers'](common['price'], common['qty'], 0, 0, 0, 0, 4.8,
        price_unit=common['price_unit'], qty_unit=common['qty_unit'], vendor='v方小姐')


@pytest.mark.parametrize('tail', [
    ' 運費另加5元', '，價格待定', '，不包郵', ' 整箱重量需確認',
    ' 彩盒尺寸10x10x10cm', ' 一箱240盒', ' 一盒包郵7元',
    ' 另一款擺件一箱20盒一盒包郵8元', '\n另一款：8元',
])
def test_unknown_or_conflicting_tail_is_never_discarded(tail):
    raw = '測試擺件彩盒尺寸8x9x6cm 一箱120盒一盒包郵6元' + tail
    assert expand_inline_quote(raw) == raw


@pytest.mark.parametrize('raw', [
    '測試擺件彩盒尺寸8x9x6cm 一盒包郵6元',
    '測試擺件彩盒尺寸8x9x6cm 一箱120盒',
    '測試擺件彩盒尺寸8+9x6cm 一箱120盒一盒包郵6元',
    '測試擺件彩盒尺寸8x9x6 一箱120盒一盒包郵6元',
])
def test_incomplete_or_invalid_dimensions_are_not_silently_rewritten(raw):
    assert expand_inline_quote(raw) == raw


def test_bag_quote_without_forced_punctuation_keeps_packaging_below_name():
    raw = '測試收納掛飾 獨立袋子裝箱120個一個包郵2.5元'
    common, products = ns['parse_text'](raw)
    assert products == [{'name': '測試收納掛飾', 'code': ''}]
    assert common['extra_tags'] == '包裝:獨立袋子'
    assert common['raw_text'] == raw
