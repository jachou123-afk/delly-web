import ast
import re
import unittest
from pathlib import Path


SOURCE_PATH = Path(__file__).parents[1] / "dolly_parser.py"
SOURCE_TEXT = SOURCE_PATH.read_text(encoding="utf-8")
SOURCE_TREE = ast.parse(SOURCE_TEXT)


def load_selected_code(function_names, assignment_names=()):
    body = []
    for node in SOURCE_TREE.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Name) and target.id in assignment_names
                for target in targets
            ):
                body.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in function_names:
            body.append(node)

    namespace = {"re": re}
    exec(
        compile(ast.Module(body=body, type_ignores=[]), str(SOURCE_PATH), "exec"),
        namespace,
    )
    return namespace


class WeightSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        namespace = load_selected_code(
            {
                "normalize_code",
                "normalize_name",
                "clean_product_name",
                "parse_text",
                "resolve_weight_inputs",
                "build_cost_formulas",
            },
            {"UNIT_PAT", "EMOJI_PAT"},
        )
        cls.parse_text = staticmethod(namespace["parse_text"])
        cls.resolve_weight_inputs = staticmethod(namespace["resolve_weight_inputs"])
        cls.build_cost_formulas = staticmethod(namespace["build_cost_formulas"])

    def test_reported_product_parses_unit_weight(self):
        common, products = self.parse_text(
            """新品#正版授权
玩具总动员系列手提保温袋
带镭射标/4个图案
型号:LW-HAD-20-23
每箱数量:150pcs
单个价格:17.5元
单个尺寸:23*13*21cm
单个重量:150g
包装:吊牌+opp袋"""
        )

        self.assertEqual(common["price"], 17.5)
        self.assertEqual(common["qty"], 150)
        self.assertEqual(common["weight"], 0.0)
        self.assertEqual(common["unit_weight_g"], 150.0)
        self.assertEqual(products[0]["code"], "LW-HAD-20-23")

    def test_unit_weight_in_kg_does_not_become_carton_weight(self):
        common, _ = self.parse_text(
            "型號:A123\n每箱數量:20pcs\n單個重量:0.15kg"
        )

        self.assertEqual(common["unit_weight_g"], 150.0)
        self.assertEqual(common["weight"], 0.0)

    def test_unit_weight_keeps_cost_path_available(self):
        formulas = self.build_cost_formulas(18, 0.0, 150.0, 150, 1.5, 8.5, 4.85)

        self.assertEqual(formulas["weight"], "=ROUNDUP(150.0*1.03,2)")
        self.assertIn('H18=""', formulas["domestic"])
        self.assertIn('H18=""', formulas["international"])
        self.assertIn('H18=""', formulas["cost"])
        self.assertIn('I18=""', formulas["cost"])
        self.assertIn('J18=""', formulas["cost"])

    def test_missing_weight_blanks_all_calculated_amounts(self):
        formulas = self.build_cost_formulas(24, 0.0, 0.0, 72, 1.5, 8.5, 4.85)

        self.assertEqual(formulas["weight"], "")
        for key in ("domestic", "international", "cost"):
            self.assertIn('H24=""', formulas[key])
        for key in ("quote_10", "quote_13", "quote_15", "quote_20"):
            self.assertIn('K24=""', formulas[key])

    def test_both_weights_choose_the_higher_per_piece_value(self):
        state = self.resolve_weight_inputs(16.0, 150.0, 150)
        formulas = self.build_cost_formulas(30, 16.0, 150.0, 150, 1.5, 8.5, 4.85)

        self.assertEqual(state["source"], "both")
        self.assertEqual(state["base_weight_g"], 150.0)
        self.assertGreaterEqual(state["mismatch_ratio"], 0.2)
        self.assertEqual(
            formulas["weight"],
            "=ROUNDUP(MAX((16.0/150)*1000,150.0)*1.03,2)",
        )

    def test_carton_weight_keeps_existing_calculation(self):
        formulas = self.build_cost_formulas(36, 26.0, 0.0, 240, 1.5, 8.5, 4.7)

        self.assertIn(
            "ROUNDUP((26.0/240)*1000*1.03,2)",
            formulas["weight"],
        )
        self.assertIn("ROUND((G36+I36+J36)*4.7,1)", formulas["cost"])
        self.assertIn("ROUND(K36/0.9,1)", formulas["quote_10"])


if __name__ == "__main__":
    unittest.main()
