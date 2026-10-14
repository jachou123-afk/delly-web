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
    formulas = ns["build_cost_formulas"](2, 0, 0, 120, 7, 18, 5, "方小姐", blocked=True)
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


SYNTHETIC_INLINE = "測試收納掛飾。独立袋子  装箱120个 一个包邮2.5元"


def test_inline_quote_separates_fields_without_inventing_weight_or_code():
    common, products = ns["parse_text"](SYNTHETIC_INLINE)
    assert products == [{"code": "", "name": "測試收納掛飾"}]
    assert (common["price"], common["qty"], common["price_unit"], common["qty_unit"]) == (2.5, 120, "個", "個")
    assert common["weight"] == common["unit_weight_g"] == 0
    assert common["raw_text"] == SYNTHETIC_INLINE
    assert "獨立袋子" in common["extra_tags"] and "包郵" in common["extra_tags"]
    assert not common["issues"]


@pytest.mark.parametrize("extra", [" 運費另加5元", " 價格待定", "\n另一款：6元", "，不包郵"])
def test_inline_unknown_clauses_are_not_removed(extra):
    from inline_quote_parser import expand_inline_bag_quote
    raw = "測試掛飾。獨立袋子 裝箱120個 一個包郵2.5元" + extra
    assert expand_inline_bag_quote(raw) == raw


@pytest.mark.parametrize("vendor,allowed", [("方小姐", True), ("V-方小姐", True),
                                           ("v多品村", False), ("v菲凡", False),
                                           ("方小姐其他店", False), ("", False)])
def test_optional_weight_is_only_for_explicit_fang_supplier(vendor, allowed):
    reasons = ns["cost_blockers"](2.5,120,0,0,1.5,8.5,4.8,qty_unit="個",vendor=vendor)
    assert (not reasons) == allowed
    formulas = ns["build_cost_formulas"](2,0,0,120,1.5,8.5,4.8,vendor,final_price=2.5)
    assert bool(formulas["cost"]) == allowed
    assert formulas["weight"] == ""
    if allowed:
        result = calculate(dict(price=2.5, qty=120, unit="個", carton_kg=0,
                                unit_g=0, dom_rate=1.5, intl_rate=8.5, ex_rate=4.8), vendor)
        assert result["weight"][0] is None
        assert result["cost"][0] == 12
        assert result["domestic"][0] == result["international"][0] == 0


@pytest.mark.parametrize("overrides", [{"price":0}, {"qty":0}, {"qty_unit":""},
                                      {"issues":["費用待確認"]}, {"price_unit":"盒"},
                                      {"carton_kg":-1}, {"carton_kg":100, "unit_g":1}])
def test_fang_other_source_uncertainties_still_block(overrides):
    args = dict(price=2.5,qty=120,carton_kg=0,unit_g=0,dom=1.5,intl=8.5,ex=4.8,
                price_unit="個",qty_unit="個",vendor="v方小姐")
    args.update(overrides)
    assert ns["cost_blockers"](**args)


def weightless_fang_store():
    from copy import deepcopy
    from dispatch_fakes import FakeSpreadsheet
    from dispatch_storage import CloudDispatchStore
    formulas = ns["build_product_block"]("no1","2026/1/1",2,"測試掛飾","",2.5,120,"個",
                                         0,0,1.5,8.5,4.8,"v方小姐",extra="包裝:獨立袋子")
    rows = deepcopy(formulas)
    rows[1][2:11] = ["13.3","13.8","14.1","15","2.5","","0","0","12"]
    store = CloudDispatchStore(FakeSpreadsheet({"G正版":rows}))
    store.spreadsheet.sheets["G正版"].formula_override = formulas
    source = store.catalog()[0]
    return store, source, formulas


def test_weightless_quote_preserves_exact_evidence_and_can_repair_later():
    from quote_evidence import queue_evidence, save_evidence, prepare_evidence_repair, save_evidence_repair
    from dispatch_storage import CloudDispatchStore
    store, source, formulas = weightless_fang_store()
    inputs = legacy_inputs(source, formulas)
    assert inputs["carton_kg"] == inputs["unit_g"] == inputs["intl_rate"] == inputs["dom_rate"] == "0"
    assert inputs["ex_rate"] == "4.8"
    assert audit(source, formulas)["quote_math_pass"]
    _, pending = queue_evidence({},"G正版",1,formulas,SYNTHETIC_INLINE,inputs,{},"合成包郵測試")
    save_evidence(store,pending)
    fresh = CloudDispatchStore(store.spreadsheet)
    report, _ = fresh.cost_audit(source)
    assert report["source_ready"] and report["quote_math_pass"]
    assert report["raw_source"] == SYNTHETIC_INLINE
    assert not report["math_pass"]  # Still no approved advertisement price.
    plan = prepare_evidence_repair(fresh,source["identity"])
    save_evidence_repair(fresh,plan,SYNTHETIC_INLINE,inputs,notes="合成補存測試")


@pytest.mark.parametrize("fake_weight", ["0","1","=0","=G99"])
def test_unknown_weight_cannot_be_fabricated_in_cloud(fake_weight):
    store, source, formulas = weightless_fang_store()
    formulas[1][7] = fake_weight
    source["block"][1][7] = "0"
    assert not audit(source,formulas)["quote_math_pass"]


def test_weightless_existing_fang_price_correction_keeps_blank_weight():
    from cost_audit import make_evidence
    from product_correction import parse_proposal, plan_correction
    store, source, formulas = weightless_fang_store()
    evidence = make_evidence(source,formulas,SYNTHETIC_INLINE,legacy_inputs(source,formulas),
                             notes="合成來源",origin="quote_save")
    snapshot = dict(source=source,formulas=formulas,evidence=evidence,spreadsheet_id="synthetic",worksheet_id=1)
    proposal = parse_proposal(SYNTHETIC_INLINE.replace("2.5元","2.6元"),ns["parse_text"])
    plan = plan_correction(snapshot,{"price"},{"price":"2.6"},proposal,
                           actor="合成測試",basis="更新合成價格",formula_builder=ns["build_cost_formulas"])
    assert plan["after"][1][7] == ""
    assert plan["after"][3][1] == "重量 未提供"
    assert next(v for v in plan["impacts"] if v["column"]==10)["修正後"] == "13"


def test_actual_quote_writer_accepts_blank_weight_and_reads_back_without_fabricated_sku(monkeypatch):
    from test_quote_save_performance import batch_cloud
    sheet, book, catalog, context, _ = batch_cloud(monkeypatch)
    rows = ns["build_product_block"]("no1","2026/1/1",4,"測試掛飾","",2.5,120,"個",
                                     0,0,1.5,8.5,4.8,"v方小姐",extra="包裝:獨立袋子")
    sheet.get.return_value = rows
    assert ns["save_bulk_to_worksheet"]("G正版",rows,3,expected_rows=[[]],save_context=context)
    saved = sheet.update.call_args.kwargs["values"]
    assert saved[1][7] == ""
    assert saved[3][1] == "重量 未提供" and saved[4][1] == "貨號 "
    assert saved[1][10] == "=ROUNDUP(G4*4.8,0)"
