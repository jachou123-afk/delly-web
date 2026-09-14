"""Naming-only regression fixtures; no real catalog data or network writes."""
from copy import deepcopy

import pytest

from supplier_names import normalize_vendor, vendor_options, vendor_filter_label
from cost_audit import free_shipping
from dispatch_manager import catalog, new_batch, source_changes
from dispatch_fakes import product_rows
from test_v74_safety import ns


@pytest.mark.parametrize("prefix", ["", "v", "V-", "v-", "V", "v ", "Ｖ－"])
@pytest.mark.parametrize("name,expected", [
    ("多品村", "v多品村"), ("多品", "v多品村"),
    ("菲凡", "v菲凡"), ("非凡", "v菲凡"),
    ("卡樂星", "v優娜卡樂星"), ("優娜卡樂星", "v優娜卡樂星"),
    ("阿汤", "v阿湯"), ("阿湯", "v阿湯"), ("東之星", "v東之星"),
])
def test_confirmed_aliases_and_prefixes_are_idempotent(prefix, name, expected):
    result = normalize_vendor(" " + prefix + name + " ")
    assert result == expected
    assert normalize_vendor(result) == expected


@pytest.mark.parametrize("value,expected", [
    (None, ""), ("", ""), (" v- ", ""), ("V", ""),
    ("自訂供應商", "v自訂供應商"), ("v多品村其他店", "v多品村其他店"),
    ("V-甲乙-分店", "v甲乙-分店"), ("V-ACME Shop", "vACME Shop"),
    ("Venture Shop", "vVenture Shop"), ("vVenture Shop", "vVenture Shop"),
    ("https://example.com/item", "https://example.com/item"),
    ("7/13價格調整", "7/13價格調整"), ("價格調降", "價格調降"),
])
def test_no_fuzzy_merges_or_changes_to_internal_name(value, expected):
    assert normalize_vendor(value) == expected
    assert normalize_vendor(expected) == expected


@pytest.mark.parametrize("vendor,free", [
    ("V-多品村", True), ("v 多品村", True), ("V多品", True),
    ("V-非凡", False), ("v-卡樂星", False), ("v阿汤", False),
    ("v多品村其他店", False), ("v東之星", False),
])
def test_shipping_rule_is_only_for_exact_confirmed_supplier(vendor, free):
    assert ns["is_free_shipping_vendor"](vendor) is free
    assert free_shipping(vendor) is free


@pytest.mark.parametrize("vendor", ["V-多品村", "v-非凡", "v-卡樂星", "v阿汤", "v東之星"])
def test_writes_use_canonical_vendor_without_changing_equivalent_costs(vendor):
    args = dict(no_value="no1", date_value="2026/9/12", value_row=2,
                name="測試商品", code="TEST", price=9.3, qty=300, qty_unit="個",
                carton_weight_kg=0, unit_weight_g=68, dom_rate=1.5,
                international_rate=8.5, exchange_rate=4.8)
    actual = ns["build_product_block"](**args, vendor=vendor)
    expected = ns["build_product_block"](**args, vendor=normalize_vendor(vendor))
    assert actual == expected
    assert actual[0][11] == normalize_vendor(vendor)


def test_dropdown_deduplicates_aliases_but_keeps_other_suppliers():
    options, selected = vendor_options("V-卡樂星")
    assert options == ["", "v菲凡", "v多品村", "v優娜卡樂星"]
    assert selected == "v優娜卡樂星"
    assert vendor_options("v東之星")[0][-1] == "v東之星"
    assert vendor_filter_label("") == "（未填廠商）"


def test_filter_labels_do_not_rewrite_source_snapshots_or_bypass_staleness():
    rows = product_rows((1,), "V-多品村")
    before = deepcopy(rows)
    source = catalog(rows)[0]
    batch = new_batch("測試", "測試群", [source], "測試人")
    assert vendor_filter_label(source["vendor"]) == "v多品村"
    assert source["block"][0][11] == "V-多品村"
    assert rows == before
    rows["G正版"][0][11] = "v多品村"
    assert source_changes(batch, catalog(rows))


def test_dispatch_filter_merges_alias_options_and_keeps_all_matching_items():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
import streamlit as st
from dispatch_ui import _create
from dispatch_manager import catalog
from dispatch_fakes import product_rows
rows = product_rows((1, 2, 3, 4))
for offset, vendor in zip((0, 6, 12, 18), ("V-多品村", "v 多品村", "v多品", "V-非凡")):
    rows["G正版"][offset][11] = vendor
st.session_state["dispatch_catalog"] = catalog(rows)
_create(None, [])
''', default_timeout=15).run()
    assert not app.exception
    control = next(w for w in app.multiselect if w.label == "供應商")
    assert set(control.options) == {"v多品村", "v菲凡"}
    control.set_value(["v多品村"]).run()
    assert not app.exception
    assert any("本次範圍：3 款" in m.value for m in app.markdown)
    assert app.dataframe[0].value["供應商"].tolist() == ["v多品村"] * 3


@pytest.mark.parametrize("legacy", ["V-多品村", "v-非凡", "V-卡樂星", "v阿汤"])
def test_existing_product_ui_selects_canonical_name_without_writing(legacy):
    from test_v74_ui import paste, VALID, enter_correction_mode, choose_target
    app = paste(VALID, same_identity=True, existing_vendor=legacy)
    enter_correction_mode(app)
    choose_target(app)
    assert not app.exception
    control = next(w for w in app.selectbox if w.label == "🏷️ 廠商")
    assert control.value == normalize_vendor(legacy)
    assert legacy not in control.options
    assert len(control.options) == len(set(control.options))
    assert "test_updated" not in app.session_state
