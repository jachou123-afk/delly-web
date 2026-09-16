"""Independent, append-only derivative index keyed by original SHA-256."""
import re

from dispatch_manager import DispatchError
from dispatch_storage import CloudDispatchStore, decode_records, encode_record
from image_thumbnails import THUMB_BYTES, THUMB_VERSION, make_thumbnail
from synology_image_store import NAS_REDIRECT_ERROR, NasConfig

THUMB_SHEET = "_商品縮圖位置"


class NasThumbnailMixin:
    def thumbnail_locations(self, asset_ids):
        from nas_dispatch_storage import _ids, LOCATION_FIELDS, _same_location
        wanted = _ids(asset_ids)
        ws = self._sheet(THUMB_SHEET) if wanted else None
        if ws is None:
            return {}
        ranges = []
        for number, row in enumerate(ws.get("A2:B"), 2):
            if len(row) > 1 and row[1] in wanted:
                if ranges and ranges[-1][1] == number - 1:
                    ranges[-1][1] = number
                else:
                    ranges.append([number, number])
        rows = []
        for start in range(0, len(ranges), 50):
            rows.extend(row for group in ws.batch_get(
                [f"A{a}:H{b}" for a, b in ranges[start:start + 50]]) for row in group)
        result = {}
        for record in decode_records(rows):
            value, identity = record["value"], record["entity"]
            if (identity not in wanted or record["parent"] or not isinstance(value, dict)
                    or set(value) != {"version", "source_sha256", "image"}
                    or type(value["version"]) is not int or value["version"] != THUMB_VERSION
                    or value["source_sha256"] != identity):
                raise DispatchError("縮圖來源索引不符，停止讀寫")
            meta = value["image"]
            if (not isinstance(meta, dict) or set(meta) != LOCATION_FIELDS
                    or type(meta["schema"]) is not int or meta["schema"] != 1
                    or meta["storage"] != "synology" or meta["mime"] != "image/webp"
                    or not isinstance(meta["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", meta["sha256"])
                    or type(meta["size"]) is not int or not 0 < meta["size"] <= THUMB_BYTES
                    or not isinstance(meta["name"], str) or len(meta["name"]) > 512):
                raise DispatchError("縮圖檔案索引不完整或超限")
            NasConfig("https://validation.invalid", meta["root"], meta["library_id"], "validation", "validation-only")
            if self.nas_config is not None and (meta["root"] != self.nas_config.root
                    or meta["library_id"] != self.nas_config.library_id):
                raise DispatchError("縮圖指向不同 NAS 圖庫，停止讀寫")
            if identity in result and not _same_location(result[identity], meta):
                raise DispatchError("同一原圖存在衝突縮圖，不自動覆蓋")
            result.setdefault(identity, meta)
        return result

    def thumbnail_status(self):
        from nas_dispatch_storage import _ids
        bound = _ids(a for b in self.product_image_bindings().values() for a in b["assets"])
        # Explicitly chosen batch originals need previews even without library binding.
        bound.update(_ids(a for b in self.list_batches() for i in b["items"] for a in i["images"]))
        locations = self.thumbnail_locations(bound)
        return {"total": len(bound), "ready": len(locations), "pending": sorted(bound - locations.keys()),
                "bytes": sum(m["size"] for m in locations.values()),
                "configured": self.nas_config is not None and not self.nas_config_error}

    def prepare_next_thumbnails(self):
        from nas_dispatch_storage import _same_location
        status = self.thumbnail_status()
        if not status["configured"]:
            raise DispatchError("NAS 設定未就緒，未建立縮圖")
        selected = status["pending"][:10]
        if not selected:
            return []
        originals = self.get_assets(selected)
        verified = {}
        with self._nas() as nas:
            for identity in selected:
                verified[identity] = nas.put_thumbnail(make_thumbnail(originals[identity]))
        current = self.thumbnail_locations(selected)
        if any(not _same_location(meta, verified[k]) for k, meta in current.items()):
            raise DispatchError("縮圖索引已變動，停止發布")
        pending = {k: meta for k, meta in verified.items() if k not in current}
        if pending:
            ws = self._sheet(THUMB_SHEET, create=True)
            try:
                ws.append_rows([row for k, meta in pending.items() for row in encode_record(k,
                    {"version": THUMB_VERSION, "source_sha256": k, "image": meta})],
                    value_input_option="RAW", table_range="A:H")
            except Exception:
                raise DispatchError("縮圖索引儲存結果待確認，請重新讀取進度；原檔不變") from None
        observed = self.thumbnail_locations(selected)
        if set(observed) != set(verified) or any(not _same_location(observed[k], v) for k, v in verified.items()):
            raise DispatchError("縮圖索引寫後核對失敗")
        return selected

    def get_thumbnails(self, asset_ids):
        from nas_dispatch_storage import _ids
        wanted = _ids(asset_ids)
        if len(wanted) > 30:
            raise DispatchError("每頁最多讀取 30 張縮圖")
        if not wanted:
            return {}
        locations = self.thumbnail_locations(wanted)
        if self.nas_config_error or (locations and self.nas_config is None):
            self._nas()
        if self.nas_config is None:
            return {k: make_thumbnail(v) for k, v in self.get_assets(wanted).items()}
        result = {}
        if locations:
            try:
                with self._nas() as nas:
                    result = {k: nas.get_thumbnail(meta) for k, meta in locations.items()}
            except DispatchError as exc:
                if str(exc) != NAS_REDIRECT_ERROR:
                    raise
                # Derivative-only fallback for a verified legacy original.
                # The regular get_assets route must remain NAS-strict.
                backups = CloudDispatchStore.get_assets(self, locations)
                result = {k: {**make_thumbnail(v), "_cloud_backup": True}
                          for k, v in backups.items()}
        # Missing derivatives are made only in memory from the exact bound original.
        # Existing but invalid derivatives still fail above; viewing never writes.
        missing = wanted - locations.keys()
        if missing:
            for identity in sorted(missing):
                original = self.get_display_asset(identity)
                result[identity] = {**make_thumbnail(original),
                                    **({"_cloud_backup": True} if original.get("_cloud_backup") else {})}
        return result
