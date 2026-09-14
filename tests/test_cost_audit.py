from copy import deepcopy
from decimal import Decimal

import pytest

from cost_audit import audit, blockers, calculate, legacy_inputs, make_evidence, number
from dispatch_fakes import FakeSpreadsheet, product_rows, ready_batch, seed_evidence
from dispatch_manager import content_digest, edit_item, item_errors
from dispatch_storage import CloudDispatchStore, EVIDENCE_SHEET


def inputs(**changes):
    return dict(price="9.3", qty="300", unit="個", carton_kg="0", unit_g="68",
                dom_rate="1.5", intl_rate="8.5", ex_rate="4.8", **changes)


@pytest.mark.parametrize("vendor,expected", [
    ("v多品村", ("71.40", "0", "0.61", "47.6", "52.9", "53")),
    ("多品村", ("71.40", "0", "0.61", "47.6", "52.9", "53")),
    ("v菲凡", ("71.40", "0.11", "0.61", "48.1", "53.4", "54")),
    ("v優娜卡樂星", ("71.40", "0.11", "0.61", "48.1", "53.4", "54")),
    ("多品村其他店", ("71.40", "0.11", "0.61", "48.1", "53.4", "54")),
])
@pytest.mark.parametrize("unit", ["個", "盒", "套", "瓶", "罐", "包", "袋"])
def test_hand_calculated_golden_answers_vendor_exceptions_and_units(vendor, expected, unit):
    data = inputs()
    data["unit"] = unit
    result = calculate(data, vendor)
    assert tuple(result[k][0] for k in ("weight", "domestic", "international", "cost", "quote", "sale")) == tuple(map(Decimal, expected))


@pytest.mark.parametrize("price,qty,kg,expected", [
    ("24.6", "30", "27.5", ("962.50", "1.45", "8.19", "164.4", "182.7", "183")),
    ("9.9", "30", "4.7", ("164.50", "0.25", "1.40", "55.4", "61.6", "62")),
])
def test_hand_calculated_carton_weight_and_rounding(price, qty, kg, expected):
    data = inputs()
    data.update(price=price, qty=qty, carton_kg=kg, unit_g="0")
    result = calculate(data, "v菲凡")
    assert tuple(value[0] for value in result.values()) == tuple(map(Decimal, expected))


@pytest.mark.parametrize("changes", [{"ex_rate": ""}, {"unit_g": "0"}, {"price": "-1"},
                                    {"qty": "1.5"}, {"unit": "箱"}, {"carton_kg": "40"}])
def test_missing_invalid_or_conflicting_inputs_never_inferred(changes):
    data = inputs()
    data.update(changes)
    with pytest.raises(ValueError):
        calculate(data, "v菲凡")


def test_strict_numbers_reject_malformed_grouping_formulas_nan_and_booleans():
    assert number("1,000.5") == Decimal("1000.5")
    for value in ("1,2", "1e3", "NaN", True, "=9.3", ""):
        with pytest.raises(ValueError):
            number(value)


def sample_store():
    store = CloudDispatchStore(FakeSpreadsheet(product_rows((1,))))
    source = store.catalog()[0]
    return store, source


def test_old_data_can_show_cost_and_check_math_but_not_claim_source_verified():
    store, source = sample_store()
    report, formulas = store.cost_audit(source)
    assert report["math_pass"] and not report["source_ready"]
    assert report["inputs"]["ex_rate"] == "4.8"
    assert report["rows"][3]["原表／原售價"] == "47.6"
    assert report["rows"][3]["重算結果"] == "47.6"
    assert any("廠商原文" in e for e in blockers(source, report))
    assert set(store.spreadsheet.sheets) == {"G正版"}
    formulas[1][10] = '=SOMETHING_UNKNOWN()'
    assert legacy_inputs(source, formulas)["ex_rate"] == ""


def test_matching_wrong_cost_and_quote_are_caught_by_independent_recalculation():
    store, source = sample_store()
    store.spreadsheet.sheets["G正版"].rows[1][10] = "99"
    store.spreadsheet.sheets["G正版"].rows[1][2] = "110"
    source = store.catalog()[0]
    assert not source["errors"]  # The V83 cost / 0.9 check alone passes.
    report, _ = store.cost_audit(source)
    assert not report["math_pass"]
    assert report["rows"][3]["重算結果"] == "47.6"
    assert report["rows"][3]["差額（原表−重算）"] == "51.4"


@pytest.mark.parametrize("formula", ["=G999", "='Other'!G2", '=IMPORTXML("x","y")', "=SUM(G2:J2)", "99"])
def test_wrong_reference_external_or_unsupported_formulas_block_even_if_display_matches(formula):
    store, source = sample_store()
    report, formulas = store.cost_audit(source)
    formulas[1][10] = formula
    assert not audit(source, formulas)["math_pass"]


def test_evidence_capture_is_append_only_source_bound_and_read_only_to_quote():
    store, source = sample_store()
    before = deepcopy(store.spreadsheet.sheets["G正版"].rows)
    seed_evidence(store)
    report, formulas = store.cost_audit(source)
    assert report["math_pass"] and report["source_ready"]
    assert not blockers(source, report)
    assert store.spreadsheet.sheets["G正版"].rows == before
    assert EVIDENCE_SHEET in store.spreadsheet.sheets
    original = store._evidence(source["identity"])
    updated = deepcopy(formulas)
    updated[1][10] += "+0"
    assert not audit(source, updated, original)["source_ready"]
    source["source_hash"] = "changed"
    assert not audit(source, formulas, original)["source_ready"]


def test_evidence_failure_does_not_trigger_quote_rewrite_or_assume_success():
    store, source = sample_store()
    seed_evidence(store)
    ws = store.spreadsheet.sheets[EVIDENCE_SHEET]
    ws.fail_append_after_write = True
    before = deepcopy(store.spreadsheet.sheets["G正版"].rows)
    report, formulas = store.cost_audit(source)
    with pytest.raises(ValueError, match="不要重複新增"):
        store.put_quote_evidence(source, formulas, "補充原文", report["inputs"], notes="測試更正說明", origin="review_attachment")
    assert store.spreadsheet.sheets["G正版"].rows == before


def test_cost_record_is_part_of_review_and_rechecked_before_batch_approval():
    store, source = sample_store()
    batch = ready_batch(store)
    store.verify_cost_checks(batch)
    item = batch["items"][0]
    assert not item_errors(item)
    original = content_digest(item)
    item["cost_audit"]["inputs"]["ex_rate"] = "5"
    assert original != content_digest(item)
    assert any("尚未逐款核對" in e for e in item_errors(item))
    with pytest.raises(ValueError, match="成本公式或原始依據"):
        store.verify_cost_checks(batch)


def test_formula_change_with_same_display_is_detected_before_approval():
    store, source = sample_store()
    batch = ready_batch(store)
    formulas = store.read_cost_source(source)
    formulas[1][10] += "+0"
    store.spreadsheet.sheets["G正版"].formula_override = formulas
    with pytest.raises(ValueError, match="成本公式或原始依據"):
        store.verify_cost_checks(batch)


def test_new_record_preserves_initial_parse_separate_from_manual_inputs():
    store, source = sample_store()
    report, formulas = store.cost_audit(source)
    record = make_evidence(source, formulas, "合成原文進價9.3，單重68g", report["inputs"],
                           notes="操作者修正最初擷取錯誤", origin="quote_save", parsed={"price": 3.9})
    updated = audit(source, formulas, record)
    assert updated["input_rows"][0]["最初擷取值"] == "3.9"
    assert updated["input_rows"][0]["驗算使用值"] == "9.3"
    assert updated["source_ready"] and updated["math_pass"]


def test_real_quote_capture_hook_saves_source_only_after_matching_saved_formulas():
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import Mock
    store, source = sample_store()
    formulas = store.read_cost_source(source)
    tree = ast.parse((Path(__file__).parents[1] / "dolly_parser.py").read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "persist_quote_evidence")
    ui = SimpleNamespace(session_state={}, success=Mock(), warning=Mock(), error=Mock())
    namespace = {"get_dispatch_store": lambda: store, "st": ui}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "capture_hook", "exec"), namespace)
    hook = namespace["persist_quote_evidence"]
    parameters = legacy_inputs(source, formulas)
    assert hook("G正版", 1, formulas, "合成原文：進價9.3、單重68g", parameters, {"price": 9.3}, "已確認無其他费用")
    assert store.cost_audit(source)[0]["source_ready"]
    changed = deepcopy(formulas)
    changed[0][1] = "其他商品"
    assert not hook("G正版", 1, changed, "不同商品原文", parameters, {}, "測試")
    assert "不要重複新增" in ui.warning.call_args.args[0]
    assert store._evidence(source["identity"])["raw_source"] == "合成原文：進價9.3、單重68g"
