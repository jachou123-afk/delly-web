"""Supplier-independent guards; anonymized established shapes, not full catalogs.

The Yuna/custom-vendor cases below exercise routing only. They do NOT certify
an unprovided Yuna/custom supplier's original message grammar.
"""
import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_v74_safety import ns


SOURCE_SHAPES = [
    ("line_fields", "測試收納包\n編號:T001\n箱數:300pcs\n單價:9.3元\n重量:68g(單個)\n包裝:12個/opp袋", 9.3, 300, 0, 68),
    ("inline_fields", "FF000001，不帶電 線控測試玩具是24.6元，一箱30只，27.5KG\n彩盒尺寸46.5*6.3*40.2CM\n外箱規格98.5*48*83.5CM", 24.6, 30, 27.5, 0),
    ("box_unit", "測試密實袋\n編號:T003\n每箱數量:30盒\n單盒價格:9.9元\n整箱重量:4.7kg\n包裝尺寸:19*6.5*4.5cm", 9.9, 30, 4.7, 0),
]


@pytest.mark.parametrize("shape,raw,price,qty,kg,g", SOURCE_SHAPES)
@pytest.mark.parametrize("vendor,free", [
    ("v多品村", True), ("多品村", True), ("v菲凡", False),
    ("v優娜卡樂星", False), ("自訂供應商", False), ("多品村其他店", False),
])
def test_format_and_shipping_are_independent(shape, raw, price, qty, kg, g, vendor, free):
    common, products = ns["parse_text"](raw)
    assert len(products) == 1
    assert not common["issues"]
    assert (common["price"], common["qty"], common["weight"], common["unit_weight_g"]) == (price, qty, kg, g)
    assert ns["is_free_shipping_vendor"](vendor) is free
    formulas = ns["build_cost_formulas"](2, kg, g, qty, 1.5, 8.5, 4.8, vendor=vendor, final_price=price)
    assert formulas["weight"]
    if free:
        assert "*1.5" not in formulas["domestic"]
    else:
        assert "*1.5" in formulas["domestic"]
    assert formulas["international"] and formulas["cost"] and formulas["quote_10"]


def fresh_read(monkeypatch, rows, expected):
    worksheet = Mock()
    worksheet.get_all_values.return_value = rows
    spreadsheet = Mock()
    spreadsheet.worksheet.return_value = worksheet
    monkeypatch.setitem(ns, "get_credentials", Mock(return_value="fake"))
    monkeypatch.setitem(ns, "gspread", SimpleNamespace(authorize=Mock()))
    monkeypatch.setitem(ns, "open_spreadsheet", Mock(return_value=spreadsheet))
    result = ns["get_fresh_line_ad_block"]("G正版", 1, expected)
    worksheet.get_all_values.assert_called_once_with()
    return result


def saved_block():
    return ns["pad_block"]([["no1", "測試商品"], ["2026/9/12", "計價單位：個", "52.9"], [], [], ["", "貨號 A1"]])


def test_outbound_fresh_read_does_not_use_cached_list(monkeypatch):
    expected = saved_block()
    assert fresh_read(monkeypatch, copy.deepcopy(expected), expected) == expected


@pytest.mark.parametrize("change", ["changed", "moved", "duplicate", "deleted"])
def test_outbound_changed_or_ambiguous_record_is_blocked(monkeypatch, change):
    expected = saved_block()
    rows = copy.deepcopy(expected)
    if change == "changed":
        rows[1][2] = "53.1"
    elif change == "moved":
        rows.insert(0, [])
    elif change == "duplicate":
        rows += copy.deepcopy(expected)
    else:
        rows = []
    with pytest.raises(ValueError):
        fresh_read(monkeypatch, rows, expected)
