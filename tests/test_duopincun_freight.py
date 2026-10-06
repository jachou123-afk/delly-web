from decimal import Decimal

import pytest

from cost_audit import calculate
from supplier_names import international_rate_for_vendor
from test_v74_safety import ns


@pytest.mark.parametrize("vendor", ["多品村", "v多品村", "V多品村", "V-多品村", "Ｖ多品村", "多品"])
@pytest.mark.parametrize("selected_rate", [8.5, 12.0])
def test_confirmed_supplier_aliases_use_nine_and_preserve_other_formulas(vendor, selected_rate):
    builder = ns["build_cost_formulas"]
    actual = builder(18, 17.5, 0, 100, 1.5, selected_rate, 4.8, vendor, final_price=30)
    reference = builder(18, 17.5, 0, 100, 1.5, 9, 4.8, "v多品村", final_price=30)
    assert actual == reference
    assert 'ROUNDUP((H18/1000)*9,2)' in actual["international"]
    assert 'ISNUMBER(G18)' in actual["international"] and 'H18=""' in actual["international"]


@pytest.mark.parametrize("vendor", ["v菲凡", "其他供應商", "多品村其他店", ""])
@pytest.mark.parametrize("rate", [8.5, 12.0])
def test_other_suppliers_keep_selected_rate(vendor, rate):
    assert international_rate_for_vendor(vendor, rate) == rate
    formula = ns["build_cost_formulas"](18, 17.5, 0, 100, 1.5, rate, 4.8, vendor)["international"]
    assert f'ROUNDUP((H18/1000)*{rate:g},2)' in formula


def test_saved_effective_rate_matches_independent_weight_and_cost_check():
    inputs = dict(price=30, qty=100, unit="個", carton_kg=17.5, unit_g=0,
                  dom_rate=1.5, intl_rate=international_rate_for_vendor("多品村", 8.5), ex_rate=4.8)
    computed = calculate(inputs, "v多品村")
    assert computed["weight"][0] == Decimal("183.75")
    assert computed["international"][0] == Decimal("1.66")
    assert computed["cost"][0] == Decimal("152.0")
