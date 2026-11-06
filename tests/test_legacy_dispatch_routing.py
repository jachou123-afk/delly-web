"""Cross-workbook routing tests without network or third-party packages."""

import unittest
from io import BytesIO

from PIL import Image
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from dispatch_manager import DispatchError, batch_digest, new_batch
from dispatch_storage import validate_image
from legacy_dispatch_routing import LegacyDispatchStore
from quote_workbooks import ACTIVE_ID, LEGACY_ID


def batch(identity, *, source_id=LEGACY_ID):
    return {
        "id": identity, "name": identity, "created_at": "2026-11-06T00:00:00+08:00",
        "items": [{"source": {"source_url":
            f"https://docs.google.com/spreadsheets/d/{source_id}/edit#gid=1"}}],
    }


class FakeStore:
    def __init__(self, spreadsheet_id, records=None):
        self.spreadsheet = SimpleNamespace(id=spreadsheet_id,
                                           worksheets=lambda: [SimpleNamespace(title="富酷")])
        self.records = deepcopy(records or {})
        self.writes = 0
        self.asset_data = None
        self.codes = {"G正版": "G", "S生活用品": "S"}
        self.category_writes = 0

    def list_batches(self):
        return list(deepcopy(self.records).values())

    def get_batch(self, identity):
        return deepcopy(self.records.get(identity))

    def save_batch(self, value, expected_revision="", record_id=None):
        old = self.records.get(value["id"])
        if (old or {}).get("_revision", "") != expected_revision:
            raise DispatchError("revision mismatch")
        saved = {**deepcopy(value), "_revision": record_id or str(self.writes + 1)}
        self.records[value["id"]] = saved
        self.writes += 1
        return deepcopy(saved)

    def catalog(self):
        return deepcopy(getattr(self, "products", []))

    def category_settings(self):
        return {"codes": deepcopy(self.codes), "_revision": str(self.category_writes)}

    def save_category_code(self, category, code, *, actor, expected_revision):
        if expected_revision != str(self.category_writes):
            raise DispatchError("revision mismatch")
        self.codes[category] = code
        self.category_writes += 1
        return self.category_settings()

    def worksheet(self, category):
        return self.spreadsheet.id, category

    def _load_evidence(self, identities):
        return self.spreadsheet.id, identities

    def _evidence(self, identity):
        return self.spreadsheet.id, identity

    def get_asset(self, identity):
        return (self.spreadsheet.id, identity)

    def get_display_asset(self, identity):
        if self.asset_data is None:
            raise DispatchError("找不到已保存的商品圖片")
        asset = validate_image(self.asset_data, "original.png")
        if asset["sha256"] != identity:
            raise DispatchError("找不到已保存的商品圖片")
        return asset


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.source = FakeStore(LEGACY_ID, {"old": {**batch("old"), "_revision": "historic"}})
        self.journal = FakeStore(ACTIVE_ID)
        self.store = LegacyDispatchStore(self.journal, self.source)

    def test_source_reads_and_new_journal_writes_are_separate(self):
        self.assertEqual(self.store.catalog(), self.source.catalog())
        self.assertEqual(self.store.worksheet("G正版"), (LEGACY_ID, "G正版"))
        self.assertEqual(self.store._load_evidence(["G正版:no1"]), (LEGACY_ID, ["G正版:no1"]))
        self.assertEqual(self.store._evidence("G正版:no1"), (LEGACY_ID, "G正版:no1"))
        self.assertEqual(self.store.get_asset("sha"), (LEGACY_ID, "sha"))
        saved = self.store.save_batch(batch("new"))
        self.assertEqual(saved["source_workbook_id"], LEGACY_ID)
        self.assertEqual(self.source.writes, 0)
        self.assertEqual(self.journal.writes, 1)
        self.assertEqual({b["id"] for b in self.store.list_batches()}, {"old", "new"})
        self.assertEqual(self.store.get_batch("new"), saved)

    def test_new_category_code_is_saved_only_to_journal_and_reparses_old_product(self):
        self.source.products = [{"category": "富酷", "block": [["no1"]],
                                 "code": "", "copy": "", "source_hash": "unchanged"}]
        with patch("legacy_dispatch_routing.inspect_source", return_value={
                "code": "BGD-FK-1", "copy": "測試文案", "errors": []}) as inspect:
            self.store.save_category_code("富酷", "FK", actor="Codex",
                                          expected_revision=self.store.category_settings()["_revision"])
            catalog = self.store.catalog()
        self.assertEqual(self.journal.codes["富酷"], "FK")
        self.assertNotIn("富酷", self.source.codes)
        self.assertEqual(self.source.category_writes, 0)
        self.assertEqual(catalog[0]["code"], "BGD-FK-1")
        self.assertEqual(catalog[0]["source_hash"], "unchanged")
        inspect.assert_called_once()

    def test_regular_batch_creation_and_digest_survive_routing_marker(self):
        product = {"key": "富酷:3:no1", "identity": "富酷:no1", "copy": "合成測試文案",
                   "source_url": f"https://docs.google.com/spreadsheets/d/{LEGACY_ID}/edit#gid=1"}
        draft = new_batch("11/3 測試", "測試群組", [product], "測試核對人")
        before = batch_digest(draft)
        saved = self.store.save_batch(draft)
        self.assertEqual(batch_digest(saved), before)
        self.assertEqual(saved["source_workbook_id"], LEGACY_ID)

    def test_old_batch_cannot_be_revised_or_copied_to_new_journal(self):
        with self.assertRaisesRegex(DispatchError, "歷史批次永久唯讀"):
            self.store.save_batch(self.source.get_batch("old"), "historic")
        self.assertEqual(self.source.writes, 0)
        self.assertEqual(self.journal.writes, 0)

    def test_wrong_source_is_rejected_before_writing(self):
        with self.assertRaisesRegex(DispatchError, "商品不屬於原商品表"):
            self.store.save_batch(batch("bad", source_id="other"))
        self.assertEqual(self.journal.writes, 0)
        with self.assertRaisesRegex(DispatchError, "指定的新表紀錄"):
            LegacyDispatchStore(self.source, self.journal)

    def test_existing_original_is_reused_without_any_write(self):
        image = BytesIO()
        Image.new("RGB", (2, 2), "red").save(image, format="PNG")
        data = image.getvalue()
        self.source.asset_data = data
        identity = validate_image(data, "original.png")["sha256"]
        self.assertEqual(self.store.put_asset(data, "original.png"), identity)
        self.assertEqual(self.source.writes, 0)
        self.assertEqual(self.journal.writes, 0)
        self.source.asset_data = None
        with self.assertRaisesRegex(DispatchError, "歷史商品只可採用已保存"):
            self.store.put_asset(data, "new.png")
        self.assertEqual(self.source.writes, 0)
        with self.assertRaisesRegex(DispatchError, "原商品表永久唯讀"):
            self.store.put_assets([])


if __name__ == "__main__":
    unittest.main()
