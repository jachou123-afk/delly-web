import ast
import unittest
from pathlib import Path


SOURCE_PATH = Path(__file__).parents[1] / "dolly_parser.py"
SOURCE_TEXT = SOURCE_PATH.read_text(encoding="utf-8")
SOURCE_TREE = ast.parse(SOURCE_TEXT)


def load_open_spreadsheet(st):
    body = []
    for node in SOURCE_TREE.body:
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == "SHEET_NAME"
                for target in node.targets
            ):
                body.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in {
            "clean_str",
            "open_spreadsheet",
        }:
            body.append(node)

    namespace = {"st": st}
    exec(
        compile(ast.Module(body=body, type_ignores=[]), str(SOURCE_PATH), "exec"),
        namespace,
    )
    return namespace["open_spreadsheet"]


class FakeClient:
    def __init__(self):
        self.calls = []

    def open_by_key(self, spreadsheet_id):
        self.calls.append(("id", spreadsheet_id))
        return "opened-by-id"

    def open(self, title):
        self.calls.append(("title", title))
        return "opened-by-title"


class SettingsStorageTests(unittest.TestCase):
    def test_spreadsheet_id_is_preferred_when_secret_exists(self):
        fake_st = type("FakeStreamlit", (), {"secrets": {"spreadsheet_id": "  sheet-id  "}})
        client = FakeClient()

        result = load_open_spreadsheet(fake_st)(client)

        self.assertEqual(result, "opened-by-id")
        self.assertEqual(client.calls, [("id", "sheet-id")])

    def test_current_cloud_title_is_used_as_safe_fallback(self):
        fake_st = type("FakeStreamlit", (), {"secrets": {}})
        client = FakeClient()

        result = load_open_spreadsheet(fake_st)(client)

        self.assertEqual(result, "opened-by-title")
        self.assertEqual(client.calls, [("title", "半自動 - 採購報價彙整表BGD")])

    def test_gspread_updates_use_keyword_arguments(self):
        update_calls = [
            node
            for node in ast.walk(SOURCE_TREE)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
        ]

        self.assertEqual(len(update_calls), 2)
        for call in update_calls:
            self.assertEqual(call.args, [])
            keyword_names = {keyword.arg for keyword in call.keywords}
            self.assertIn("values", keyword_names)
            self.assertIn("range_name", keyword_names)
            self.assertIn("value_input_option", keyword_names)


if __name__ == "__main__":
    unittest.main()
