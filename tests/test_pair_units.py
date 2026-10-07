"""Synthetic shoes only; isolated parser/UI checks never write cloud data."""

from decimal import Decimal

import pytest

from cost_audit import calculate
from test_v74_safety import ns
from test_v74_ui import paste, save_button


SHOE = """測試家居拖鞋
型號:TEST-SHOE
碼數:35-40雙碼
每箱數量:24雙
單雙價格:20元
產品尺寸:23/24/25cm
外箱尺寸:50*40*30cm
整箱重量:12kg"""


@pytest.mark.parametrize("raw", [SHOE, SHOE.replace("雙", "双").replace("單", "单")])
def test_pair_units_and_shoe_size_choices_preserve_source_without_conversion(raw):
    common, products = ns["parse_text"](raw)
    assert products == [{"code": "TEST-SHOE", "name": "測試家居拖鞋"}]
    assert (common["price"], common["qty"], common["weight"]) == (20, 24, 12)
    assert (common["price_unit"], common["qty_unit"]) == ("雙", "雙")
    assert common["raw_text"] == raw
    assert common["prod_size"] == ""
    assert "產品尺寸:23/24/25cm" in common["extra_tags"].splitlines()
    assert "碼數:35-40雙碼" in common["extra_tags"].splitlines()
    assert common["issues"] == []
    assert ns["canonical_unit"]("双") == "雙"
    assert ns["cost_blockers"](20, 24, 12, 0, 0, 9, 4.8, common["issues"], "雙", "雙") == []


@pytest.mark.parametrize("source_line", ["每雙價格:20元", "單雙價:20元", "價格:20元/雙"])
def test_explicit_pair_price_forms_are_recognized(source_line):
    raw = SHOE.replace("單雙價格:20元", source_line).replace("產品尺寸:23/24/25cm", "")
    common, _ = ns["parse_text"](raw)
    assert (common["price"], common["price_unit"], common["qty_unit"]) == (20, "雙", "雙")
    assert common["issues"] == []


@pytest.mark.parametrize("raw", [
    SHOE.replace("測試家居拖鞋", "測試商品"),
    SHOE.replace("單雙價格:20元", "單價:20元"),
    SHOE.replace("產品尺寸:23/24/25cm", "產品尺寸:23/24/25"),
    SHOE.replace("產品尺寸:23/24/25cm", "外箱尺寸:23/24/25cm"),
    SHOE.replace("產品尺寸:23/24/25cm", "產品尺寸:23/24*25cm"),
])
def test_ambiguous_slashes_and_missing_units_still_block(raw):
    common, _ = ns["parse_text"](raw)
    assert any("尺寸欄位" in issue for issue in common["issues"])


def test_pair_carton_cannot_silently_match_individual_price():
    raw = SHOE.replace("單雙價格:20元", "單個價格:20元").replace("產品尺寸:23/24/25cm", "")
    common, _ = ns["parse_text"](raw)
    reasons = ns["cost_blockers"](20, 24, 12, 0, 0, 9, 4.8, common["issues"], common["price_unit"], common["qty_unit"])
    assert "計價與裝箱單位不同，須先換算確認" in reasons


@pytest.mark.parametrize("quantity,price,expected_price_unit,expected_issue", [
    ("24個", "單雙價格:20元/個", "雙", "價格前後計價單位不同"),
    ("24雙", "單個價格:20元/雙", "個", "價格前後計價單位不同"),
    ("24雙/48個", "單雙價格:20元", "雙", "裝箱量含多個數量或單位"),
    ("24雙(48個)", "單雙價格:20元", "雙", "裝箱量含多個數量或單位"),
    ("24雙/48雙", "單雙價格:20元", "雙", "裝箱量含多個數量或單位"),
    ("24雙/個", "單雙價格:20元", "雙", "裝箱量含多個數量或單位"),
    ("24雙/箱(12盒×2個)", "單雙價格:20元", "雙", "裝箱量含多個數量或單位"),
    ("24雙/箱(10盒×2雙)", "單雙價格:20元", "雙", "裝箱量含多個數量或單位"),
    ("24雙", "單雙價格:20元/雙/個", "雙", "價格含多個計價單位"),
])
def test_mixed_price_or_carton_units_block_without_overwriting_first_unit(quantity, price, expected_price_unit, expected_issue):
    raw = f"測試拖鞋\n型號:TEST-MIXED\n每箱數量:{quantity}\n{price}\n整箱重量:12kg"
    common, _ = ns["parse_text"](raw)
    assert common["price"] == 20
    assert common["qty"] == 24
    assert common["price_unit"] == expected_price_unit
    assert common["raw_text"] == raw
    reasons = ns["cost_blockers"](20, 24, 12, 0, 0, 9, 4.8, common["issues"], common["price_unit"], common["qty_unit"])
    assert any(expected_issue in reason for reason in reasons)


@pytest.mark.parametrize("quantity,price,unit", [
    ("24雙/箱", "單雙價格:20元/雙", "雙"),
    ("24pcs", "單個價格:20元/pcs", "個"),
])
def test_equivalent_explicit_units_are_allowed(quantity, price, unit):
    common, _ = ns["parse_text"](f"測試商品\n型號:TEST-UNITS\n每箱數量:{quantity}\n{price}\n整箱重量:12kg")
    assert common["issues"] == []
    assert (common["price_unit"], common["qty_unit"]) == (unit, unit)


def test_cost_audit_keeps_per_pair_weight_and_price():
    result = calculate(dict(price="20", qty="24", unit="雙", carton_kg="12", unit_g="0", dom_rate="0", intl_rate="9", ex_rate="4.8"), "v多品村")
    expected = tuple(map(Decimal, ("525.00", "0", "4.73", "118.7", "131.9", "132")))
    assert tuple(result[key][0] for key in ("weight", "domestic", "international", "cost", "quote", "sale")) == expected


def test_ui_selects_pairs_and_saves_pair_labels_with_original_source():
    app = paste(SHOE)
    unit = next(field for field in app.selectbox if field.label.startswith("装箱及計價單位"))
    assert unit.value == "雙"
    assert next(field for field in app.text_input if field.label.startswith("產品尺寸")).value == ""
    next(field for field in app.checkbox if field.label.startswith("我已逐欄對照原文")).check()
    save_button(app).click().run()
    assert not app.exception
    rows = app.session_state["test_saved_rows"]
    assert rows[0][7] == "重量g/雙"
    assert "計價單位：雙" in rows[1][1]
    assert "產品尺寸:23/24/25cm" in rows[1][1]
    assert "碼數:35-40雙碼" in rows[1][1]
    assert rows[2][1] == "裝箱 24雙/箱"
    assert app.session_state["test_evidence"]["raw"] == SHOE
