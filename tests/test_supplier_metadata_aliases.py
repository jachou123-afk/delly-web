"""Supplier metadata must not invent a SKU or an unlabelled weight basis."""
import pytest
from test_v74_safety import ns


def test_quantity_spec_and_box_aliases_keep_one_product_and_original_source():
    raw = ('T123 測試保護套\n藍牙耳機\n單彩盒尺寸16.8x13x5cm\n'
           '裝箱規格：100個\n裝箱尺寸：69*37*56CM\n20KG   一個包郵17.3元')
    common, products = ns['parse_text'](raw)
    assert len(products) == 1 and products[0]['code'] == 'T123'
    assert common['raw_text'] == raw
    assert (common['price'], common['qty'], common['qty_unit']) == (17.3, 100, '個')
    assert common['color_box_size'] == '16.8x13x5cm'
    assert common['outer_box_size'] == '69*37*56CM'
    assert common['weight'] == 0 and common['unit_weight_g'] == 0
    assert '20KG' in common['extra_tags']
    assert not common['issues']
    assert not ns['cost_blockers'](17.3,100,0,0,0,0,4.9,
        issues=common['issues'],qty_unit='個',vendor='v方小姐')
    assert ns['cost_blockers'](17.3,100,0,0,1.5,8.5,4.9,
        issues=common['issues'],qty_unit='個',vendor='v菲凡')


@pytest.mark.parametrize('quantity', ['100-200個','100.5個','100個/20盒'])
def test_ambiguous_quantity_spec_is_not_normalized(quantity):
    common, _ = ns['parse_text']('T123 測試商品\n裝箱規格：'+quantity+'\n單個價格17.3元')
    assert common['issues']


def test_real_second_sku_is_still_blocked():
    common, products = ns['parse_text']('T123 測試保護套\nT124 測試耳機\n裝箱規格：100個\n單個價格17.3元')
    assert len(products) == 2
    assert any('多款商品' in issue for issue in common['issues'])
