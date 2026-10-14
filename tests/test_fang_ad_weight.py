"""Synthetic coverage for Taiwan-inclusive delivery's omitted weight."""
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import line_ad_copy
from category_codes import DEFAULT_CATEGORY_CODES
from cost_audit import audit, fingerprint, make_evidence
from dispatch_review import inspect_source
from line_ad_copy import build_line_ad_copy_from_sheet_block, validate_sheet_pricing
from quote_dispatch.config import supplier_rule

CODES = {**DEFAULT_CATEGORY_CODES, "F方小姐": "F"}


def saved_block(vendor="v方小姐"):
    return [
        ["no91", "合成測試商品", "10%報價", "", "", "", "", "", "", "", "", vendor],
        ["2026/1/1", "計價單位：個\n包裝:獨立袋子", "13.3", "", "", "", "2.5", "", "0", "0", "12"],
        ["", "裝箱 120個/箱", "", "", "", "", "", "", "台灣包郵"],
        ["", "重量 未提供"], ["", "貨號 TEST"], [""],
    ]


class InclusiveAdWeightTests(unittest.TestCase):
    def test_inclusive_quote_recovers_ad_price_without_mutating_weight(self):
        for vendor in ("v方小姐", "方小姐", "V-方小姐"):
            with self.subTest(vendor=vendor):
                rows = saved_block(vendor)
                before = deepcopy(rows)
                source = inspect_source("F方小姐", rows, CODES)
                self.assertEqual(source["errors"], [])
                self.assertEqual(source["price"], "14")
                self.assertEqual(source["unit"], "個")
                self.assertIn("售價14元/個", source["copy"])
                self.assertNotIn("重量", source["copy"])
                self.assertEqual(rows, before)
                self.assertEqual(rows[1][7], "")

    def test_other_supplier_still_requires_weight_even_with_copied_notes(self):
        for vendor in ("v多品村", "v菲凡", "方小姐其他店", "", "自訂供應商"):
            with self.subTest(vendor=vendor), self.assertRaises(ValueError):
                validate_sheet_pricing(saved_block(vendor))

    def test_nonblank_bad_weight_is_never_treated_as_missing(self):
        for value in (0, False, "0", -1, "-1", "待補", "#VALUE!", "=0", True):
            rows = saved_block()
            rows[1][7] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_sheet_pricing(rows)

    def test_omission_does_not_relax_notes_money_or_quote_checks(self):
        cases = [
            (3, 1, ""), (3, 1, "重量 待確認"), (3, 1, "整箱毛重 1KG"),
            (2, 8, ""), (2, 8, "廣州包郵"),
            (1, 8, ""), (1, 9, ""), (1, 8, "0.01"), (1, 9, "0.01"),
            (1, 8, False), (1, 9, "#VALUE!"), (1, 9, "-1"),
            (1, 6, "0"), (1, 10, "0"), (1, 2, "15"),
        ]
        for row, col, value in cases:
            rows = saved_block()
            rows[row][col] = value
            with self.subTest(row=row, col=col, value=value), self.assertRaises(ValueError):
                validate_sheet_pricing(rows)

    def test_supplier_name_alone_cannot_enable_exception(self):
        rule = supplier_rule("v方小姐")
        for changes in (
            dict(domestic_shipping="calculated", domestic_shipping_note=""),
            dict(domestic_shipping="free", domestic_shipping_note="廣州包郵"),
        ):
            with self.subTest(changes=changes):
                with patch.object(line_ad_copy, "supplier_rule", return_value=replace(rule, **changes), create=True):
                    with self.assertRaises(ValueError):
                        validate_sheet_pricing(saved_block())

    def test_known_weight_quotes_remain_supported_for_other_suppliers(self):
        for vendor in ("v方小姐", "v多品村", "v菲凡", "v優娜卡樂星", "自訂供應商"):
            with self.subTest(vendor=vendor):
                rows = saved_block(vendor)
                rows[1][7] = "71.4"
                rows[3][1] = "單個重量 68g"
                self.assertIn("售價14元/個", build_line_ad_copy_from_sheet_block("F方小姐", rows, CODES))

    def test_units_and_unknown_codes_are_still_checked(self):
        rows = saved_block()
        rows[2][1] = "裝箱 120盒/箱"
        self.assertTrue(inspect_source("F方小姐", rows, CODES)["errors"])
        self.assertTrue(inspect_source("未設定分頁", saved_block(), CODES)["errors"])

    def test_recovered_price_passes_existing_audit_but_stale_evidence_fails(self):
        rows = saved_block()
        source = inspect_source("F方小姐", rows, CODES)
        source.update(identity="F方小姐:no91", category="F方小姐", no="no91",
                      name=rows[0][1], vendor=rows[0][11], row=1, source_hash=fingerprint(rows))
        formulas = deepcopy(rows)
        formulas[1][2] = "=ROUND(K2/0.9,1)"
        formulas[1][8] = "=0"
        formulas[1][9] = "=0"
        formulas[1][10] = "=ROUNDUP(G2*4.8,0)"
        inputs = dict(price="2.5", qty="120", unit="個", carton_kg="0", unit_g="0",
                      dom_rate="0", intl_rate="0", ex_rate="4.8")
        evidence = make_evidence(source, formulas, "合成商品 120個/箱 2.5元台灣包郵",
                                 inputs, notes="合成測試：重量未提供，既定含運成本", origin="quote_save")
        report = audit(source, formulas, evidence)
        self.assertTrue(report["source_ready"] and report["quote_math_pass"] and report["math_pass"])
        self.assertEqual(source["block"][1][7], "")
        evidence["source_hash"] = "stale"
        self.assertFalse(audit(source, formulas, evidence)["math_pass"])


if __name__ == "__main__":
    unittest.main()
