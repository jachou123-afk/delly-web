"""Send historical products while journaling new work only in the continuation book."""

from dispatch_manager import DispatchError
from dispatch_review import inspect_source
from dispatch_storage import asset_bytes, validate_image
from category_codes import add_code
from quote_workbooks import ACTIVE_ID, LEGACY_ID


class LegacyDispatchStore:
    """Old quote/image evidence is read-only; new batch revisions use no2."""

    _SOURCE_READS = frozenset({
        "catalog", "source_images", "read_cost_source", "cost_audit",
        "cost_audits", "verify_cost_checks", "get_asset", "get_assets",
        "get_display_asset", "get_thumbnails", "image_locations",
        "product_image_refs", "product_image_bindings", "review_issues",
        "migration_status", "verify_migrated_images",
        "worksheet", "_load_evidence", "_evidence", "_cost_report",
        "verify_known_issues",
    })
    _BLOCKED_WRITES = frozenset({
        "put_assets", "put_quote_evidence", "save_product_images",
        "migrate_next_images", "_publish_locations",
    })

    def __init__(self, journal, source):
        if (getattr(journal.spreadsheet, "id", None) != ACTIVE_ID
                or getattr(source.spreadsheet, "id", None) != LEGACY_ID):
            raise DispatchError("歷史商品發送只接受指定的新表紀錄與舊表來源")
        self.journal = journal
        self.source = source
        self.spreadsheet = journal.spreadsheet
        self.correction_tools = None

    def __getattr__(self, name):
        if name in self._BLOCKED_WRITES:
            raise DispatchError("原商品表永久唯讀；本入口不修改商品、圖片或報價依據")
        if name in self._SOURCE_READS:
            return getattr(self.source, name)
        return getattr(self.journal, name)

    def put_asset(self, data, name):
        """Allow selecting an unchanged historical original in a new batch.

        The draft editor calls this for every selected source image, even when
        only the advert text changed. Verify the exact old bytes and return
        their existing ID; never write an image or pointer to the old book.
        """
        candidate = validate_image(data, name)
        try:
            reader = getattr(self.source, "get_display_asset", self.source.get_asset)
            original = reader(candidate["sha256"])
            if asset_bytes(original) == data:
                return candidate["sha256"]
        except (DispatchError, KeyError):
            pass
        raise DispatchError("歷史商品只可採用已保存且位元組相同的原圖；不能在此新增或改寫圖片")

    def category_settings(self):
        historical = self.source.category_settings()
        current = self.journal.category_settings()
        if any(current["codes"].get(category) != code
               for category, code in historical["codes"].items()):
            raise DispatchError("新舊表既有分類代碼不一致，停止產生廣告品號")
        return {**current, "codes": {**historical["codes"], **current["codes"]}}

    def save_category_code(self, category, code, *, actor, expected_revision):
        state = self.category_settings()
        if state["_revision"] != expected_revision:
            raise DispatchError("分類設定已更新，請重新載入")
        if category not in {ws.title for ws in self.source.spreadsheet.worksheets()}:
            raise DispatchError("原商品表沒有指定分頁，停止設定舊商品代碼")
        add_code(state["codes"], category, code)
        self.journal.save_category_code(category, code, actor=actor,
                                        expected_revision=expected_revision)
        return self.category_settings()

    def catalog(self):
        products = self.source.catalog()
        historical_codes = self.source.category_settings()["codes"]
        codes = self.category_settings()["codes"]
        for product in products:
            if product["category"] in codes and product["category"] not in historical_codes:
                product.update(inspect_source(product["category"], product["block"], codes))
        return products

    def list_batches(self):
        current = [batch for batch in self.journal.list_batches()
                   if batch.get("source_workbook_id") == LEGACY_ID]
        historical = self.source.list_batches()
        current_ids = {batch["id"] for batch in current}
        if len(current_ids) != len(current) or current_ids.intersection(b["id"] for b in historical):
            raise DispatchError("新舊表批次識別碼重複，停止顯示與保存")
        return sorted(current + historical, key=lambda b: b["created_at"], reverse=True)

    def get_batch(self, batch_id):
        current = self.journal.get_batch(batch_id)
        if current is not None:
            if current.get("source_workbook_id") != LEGACY_ID:
                raise DispatchError("此批次來源不是舊商品表")
            return current
        return self.source.get_batch(batch_id)

    def save_batch(self, batch, expected_revision="", record_id=None):
        if batch.get("source_workbook_id") not in (None, LEGACY_ID):
            raise DispatchError("批次商品來源與舊表入口不符")
        current = self.journal.get_batch(batch["id"])
        if current is None and self.source.get_batch(batch["id"]) is not None:
            raise DispatchError("歷史批次永久唯讀；不能在新表修改舊批次")
        if current is not None and current.get("source_workbook_id") != LEGACY_ID:
            raise DispatchError("新表已有相同批次識別碼但來源不同")
        source_prefix = f"https://docs.google.com/spreadsheets/d/{LEGACY_ID}/"
        if any(not str(item.get("source", {}).get("source_url", "")).startswith(source_prefix)
               for item in batch.get("items", [])):
            raise DispatchError("批次中有商品不屬於原商品表")
        return self.journal.save_batch(
            {**batch, "source_workbook_id": LEGACY_ID},
            expected_revision=expected_revision, record_id=record_id,
        )


class ContinuationDispatchStore:
    """Read and save no2 work; include old batches in duplicate checks."""

    def __init__(self, journal, history):
        if (getattr(journal.spreadsheet, "id", None) != ACTIVE_ID
                or getattr(history.spreadsheet, "id", None) != LEGACY_ID):
            raise DispatchError("新商品發送只接受指定的 no2 與原商品表")
        self.journal = journal
        self.history = history
        self.spreadsheet = journal.spreadsheet

    def __getattr__(self, name):
        return getattr(self.journal, name)

    @staticmethod
    def can_open_batch(batch):
        items = batch.get("items", [])
        source_prefix = f"https://docs.google.com/spreadsheets/d/{ACTIVE_ID}/"
        return (batch.get("source_workbook_id") in (None, ACTIVE_ID) and bool(items)
                and all(str(item.get("source", {}).get("source_url", "")).startswith(source_prefix)
                        for item in items))

    def list_batches(self):
        batches = self.journal.list_batches() + self.history.list_batches()
        if len({batch["id"] for batch in batches}) != len(batches):
            raise DispatchError("新舊表批次識別碼重複，停止顯示與保存")
        return sorted(batches, key=lambda batch: batch["created_at"], reverse=True)

    def get_batch(self, batch_id):
        batch = self.journal.get_batch(batch_id)
        if batch is not None and not self.can_open_batch(batch):
            raise DispatchError("此批次來源不是 no2 商品表")
        return batch

    def save_batch(self, batch, expected_revision="", record_id=None):
        if not self.can_open_batch(batch):
            raise DispatchError("本批商品不全屬於 no2 商品表")
        if self.get_batch(batch["id"]) is None and self.history.get_batch(batch["id"]) is not None:
            raise DispatchError("歷史批次永久唯讀；不能在新表修改舊批次")
        return self.journal.save_batch(
            {**batch, "source_workbook_id": ACTIVE_ID},
            expected_revision=expected_revision, record_id=record_id,
        )
