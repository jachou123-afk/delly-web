"""Synthetic quotes: Taiwan-inclusive freight must not be charged twice."""
from decimal import Decimal

import pytest

from cost_audit import calculate, legacy_inputs, audit
from supplier_names import international_rate_for_vendor
from test_v74_safety import ns


@pytest.mark.parametrize("vendor", ["方小姐", "v方小姐", "V-方小姐"])
def test_included_delivery_overrides_nonzero_selected_freight(vendor):
    formulas = ns["build_cost_formulas"](2, 12, 0, 120, 7, 18, 5, vendor, final_price=20)
    assert '"",0)' in formulas["domestic"]
    assert 'ROUNDUP((H2/1000)*0,2)' in formulas["international"]
    assert '*1.05' in formulas["weight"]
    assert ns["build_carton_note_row"](120, vendor, "盒")[8] == "台灣包郵"
    effective = international_rate_for_vendor(vendor, 18)
    check = calculate(dict(price=20, qty=120, unit="盒", carton_kg=12,
                           unit_g=0, dom_rate=7, intl_rate=effective, ex_rate=5), vendor)
    assert check["domestic"][0] == check["international"][0] == 0
    assert check["cost"][0] == Decimal("100.0")
    assert check["quote"][0] == Decimal("111.1")
    assert "台灣包郵" in check["domestic"][1]


def test_similar_supplier_name_does_not_inherit_taiwan_inclusive_terms():
    vendor = "方小姐其他店"
    assert international_rate_for_vendor(vendor, 18) == 18
    formulas = ns["build_cost_formulas"](2, 12, 0, 120, 7, 18, 5, vendor)
    assert '*7,2)' in formulas["domestic"]
    assert '*18,2)' in formulas["international"]


def test_included_delivery_does_not_bypass_unresolved_cost_inputs():
    formulas = ns["build_cost_formulas"](2, 0, 0, 120, 7, 18, 5, "方小姐")
    assert not any(formulas.values())


@pytest.mark.parametrize("vendor", ["方小姐", "v方小姐", "V-方小姐"])
@pytest.mark.parametrize("price,expected", [("21.5", "104"), ("20", "96"), ("20.001", "97")])
def test_fang_direct_cost_ceil_to_whole_currency(vendor, price, expected):
    formulas = ns["build_cost_formulas"](4, 14, 0, 100, 0, 0, 4.8, vendor, final_price=float(price))
    assert formulas["cost"] == "=ROUNDUP(G4*4.8,0)"
    result = calculate(dict(price=price, qty=100, unit="盒", carton_kg=14,
                            unit_g=0, dom_rate=0, intl_rate=0, ex_rate="4.8"), vendor)
    assert result["cost"][0] == Decimal(expected)
    assert "無條件進位整數" in result["cost"][1]


def test_fang_saved_formula_recovers_original_fx_and_passes_cost_audit():
    from dispatch_fakes import FakeSpreadsheet, product_rows
    from dispatch_storage import CloudDispatchStore
    rows = product_rows((1,), vendor="v方小姐")
    rows["G正版"][1][2] = "50"
    rows["G正版"][1][8:11] = ["0", "0", "45"]
    store = CloudDispatchStore(FakeSpreadsheet(rows))
    source = store.catalog()[0]
    _, formulas = store.cost_audit(source)
    formulas[1][9] = "=ROUNDUP((H2/1000)*0,2)"
    formulas[1][10] = "=ROUNDUP(G2*4.8,0)"
    assert legacy_inputs(source, formulas)["ex_rate"] == "4.8"
    assert audit(source, formulas)["quote_math_pass"]
    for bad_formula in ("=ROUNDUP(G99*4.8,0)", "=ROUNDUP('Other'!G2*4.8,0)"):
        formulas[1][10] = bad_formula
        assert legacy_inputs(source, formulas)["ex_rate"] == ""
        assert not audit(source, formulas)["quote_math_pass"]


def test_other_supplier_keeps_one_decimal_cost():
    vendor = "方小姐其他店"
    formulas = ns["build_cost_formulas"](4, 14, 0, 100, 0, 0, 4.8, vendor, final_price=21.5)
    assert "ROUND((G4+I4+J4)*4.8,1)" in formulas["cost"]
    result = calculate(dict(price="21.5", qty=100, unit="盒", carton_kg=14,
                            unit_g=0, dom_rate=0, intl_rate=0, ex_rate="4.8"), vendor)
    assert result["cost"][0] == Decimal("103.2")
