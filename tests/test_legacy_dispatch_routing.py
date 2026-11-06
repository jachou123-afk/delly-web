"""Cross-workbook routing tests without network or third-party packages."""

import unittest
from copy import deepcopy
from types import SimpleNamespace

from dispatch_manager import DispatchError, batch_digest, new_batch
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
        self.spreadsheet = SimpleNamespace(id=spreadsheet_id)
        self.records = deepcopy(records or {})
        self.writes = 0

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
        return [{"source_url": f"https://docs.google.com/spreadsheets/d/{self.spreadsheet.id}/edit"}]

    def get_asset(self, identity):
        return (self.spreadsheet.id, identity)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.source = FakeStore(LEGACY_ID, {"old": {**batch("old"), "_revision": "historic"}})
        self.journal = FakeStore(ACTIVE_ID)
        self.store = LegacyDispatchStore(self.journal, self.source)

    def test_source_reads_and_new_journal_writes_are_separate(self):
        self.assertEqual(self.store.catalog(), self.source.catalog())
        self.assertEqual(self.store.get_asset("sha"), (LEGACY_ID, "sha"))
        saved = self.store.save_batch(batch("new"))
        self.assertEqual(saved["source_workbook_id"], LEGACY_ID)
        self.assertEqual(self.source.writes, 0)
        self.assertEqual(self.journal.writes, 1)
        self.assertEqual({b["id"] for b in self.store.list_batches()}, {"old", "new"})
        self.assertEqual(self.store.get_batch("new"), saved)

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

    def test_source_mutations_are_blocked(self):
        with self.assertRaisesRegex(DispatchError, "原商品表永久唯讀"):
            self.store.put_asset(b"bytes", "x.jpg")
        self.assertEqual(self.source.writes, 0)


if __name__ == "__main__":
    unittest.main()
