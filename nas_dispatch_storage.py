"""NAS image locations; legacy originals remain untouched as a migration backup."""
import re

from dispatch_manager import DispatchError
from dispatch_storage import (CloudDispatchStore, IMAGE_SHEET, MAX_IMAGE_BYTES,
                              asset_bytes, decode_records, encode_record, validate_image)
from synology_image_store import EXTENSIONS, NasConfig, SynologyImageStore

LOCATION_SHEET = "_商品圖片位置"
LOCATION_FIELDS = {"schema", "storage", "library_id", "root", "sha256", "name", "mime", "size"}
GROUP_SIZE = 10


def _ids(values):
    values = set(values)
    if any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in values):
        raise DispatchError("圖片識別碼格式不合法")
    return values


def _same_location(left, right):
    # Identical original bytes may have different user-facing filenames.
    return all(left.get(k) == right.get(k) for k in LOCATION_FIELDS - {"name"})


class NasDispatchStore(CloudDispatchStore):
    def __init__(self, spreadsheet, config=None, *, config_error=False,
                 nas_factory=SynologyImageStore):
        super().__init__(spreadsheet)
        self.nas_config = config
        self.nas_config_error = config_error
        self._nas_factory = nas_factory

    def _nas(self):
        if self.nas_config is None or self.nas_config_error:
            raise DispatchError("NAS 私密設定未就緒；已切換圖片不會退回舊圖，請先修正設定")
        return self._nas_factory(self.nas_config)

    def image_locations(self, asset_ids):
        wanted = _ids(asset_ids)
        if not wanted:
            return {}
        ws = self._sheet(LOCATION_SHEET)
        if ws is None:
            return {}
        # Separate short row index, never reuse the legacy payload row cache.
        ranges = []
        for number, row in enumerate(ws.get("A2:B"), 2):
            if len(row) > 1 and row[1] in wanted:
                if ranges and ranges[-1][1] == number - 1:
                    ranges[-1][1] = number
                else:
                    ranges.append([number, number])
        rows = []
        for offset in range(0, len(ranges), 50):
            rows.extend(row for group in ws.batch_get(
                [f"A{a}:H{b}" for a, b in ranges[offset:offset + 50]]) for row in group)
        locations = {}
        for record in decode_records(rows):
            value, identity = record["value"], record["entity"]
            if (identity not in wanted or record["parent"] or not isinstance(value, dict)
                    or set(value) != LOCATION_FIELDS or type(value["schema"]) is not int
                    or value["schema"] != 1 or value["storage"] != "synology"
                    or value["sha256"] != identity or type(value["size"]) is not int
                    or not 0 < value["size"] <= MAX_IMAGE_BYTES
                    or not isinstance(value["name"], str) or len(value["name"]) > 512
                    or not isinstance(value["mime"], str) or value["mime"] not in EXTENSIONS):
                raise DispatchError("NAS 圖片位置索引不完整或不合法，停止讀寫")
            # Validate the root/library without needing or inventing real credentials.
            NasConfig("https://validation.invalid", value["root"], value["library_id"],
                      "validation", "validation-only")
            if self.nas_config is not None and (value["root"] != self.nas_config.root
                    or value["library_id"] != self.nas_config.library_id):
                raise DispatchError("NAS 圖片索引指向不同圖庫，停止讀寫")
            if identity in locations and not _same_location(locations[identity], value):
                raise DispatchError("同一原圖存在衝突的 NAS 位置，未自動覆蓋或改讀舊圖")
            locations.setdefault(identity, value)
        return locations

    def get_assets(self, asset_ids):
        wanted = _ids(asset_ids)
        locations = self.image_locations(wanted)
        assets = {}
        if locations:
            with self._nas() as nas:
                for identity, metadata in locations.items():
                    assets[identity] = nas.get_asset(metadata)
        legacy = wanted - locations.keys()
        if legacy:
            assets.update(super().get_assets(legacy))
        return assets

    def _publish_locations(self, metadata):
        if not metadata:
            return
        expected = {m["sha256"]: m for m in metadata}
        current = self.image_locations(expected)
        for identity, value in current.items():
            if not _same_location(value, expected[identity]):
                raise DispatchError("NAS 圖片位置已變更，停止發布索引")
        pending = [m for identity, m in expected.items() if identity not in current]
        if pending:
            ws = self._sheet(LOCATION_SHEET, create=True)
            try:
                ws.append_rows([row for m in pending for row in encode_record(m["sha256"], m)],
                               value_input_option="RAW", table_range="A:H")
            except Exception:
                raise DispatchError("NAS 圖片索引儲存結果待確認；重新載入核對，不重複新增商品") from None
        observed = self.image_locations(expected)
        if set(observed) != set(expected) or any(
                not _same_location(observed[k], value) for k, value in expected.items()):
            raise DispatchError("NAS 圖片索引寫後核對失敗，未視為搬移完成")

    def put_assets(self, assets):
        if self.nas_config_error:
            self._nas()  # Fail closed; do not write new legacy payloads by accident.
        if self.nas_config is None:
            # Existing installations without NAS retain their original write path,
            # but a missing NAS secret must not silently rewrite migrated images.
            clean = list(assets)
            if self.image_locations(a["sha256"] for a in clean):
                self._nas()
            return super().put_assets(clean)
        unique = {}
        for asset in assets:
            checked = validate_image(asset_bytes(asset), asset["name"])
            if len(checked["name"]) > 512:
                raise DispatchError("原圖檔名過長；請保留可識別商品的短檔名後再保存")
            unique[checked["sha256"]] = checked
        if len(unique) > 750 or sum(len(a["data"]) for a in unique.values()) > 70 * 1024 * 1024:
            raise DispatchError("本次原圖總量太大，請將圖片包分批保存")
        if not unique:
            return []
        # Validate every existing pointer before performing any new writes.
        locations = self.image_locations(unique)
        with self._nas() as nas:
            for metadata in locations.values():
                nas.get_asset(metadata)
            pending = [a for k, a in unique.items() if k not in locations]
            for offset in range(0, len(pending), GROUP_SIZE):
                # Upload skips existing paths and verifies the exact bytes BEFORE
                # publishing each bounded group. Failures leave old originals intact.
                verified = [nas.put_asset(asset) for asset in pending[offset:offset + GROUP_SIZE]]
                self._publish_locations(verified)
        return list(unique)

    def put_asset(self, data, name):
        asset = validate_image(data, name)
        self.put_assets([asset])
        return asset["sha256"]

    def migration_status(self):
        bindings = self.product_image_bindings()
        bound = _ids(a for b in bindings.values() for a in b["assets"])
        legacy_ws = self._sheet(IMAGE_SHEET)
        legacy_ids = {row[1] for row in legacy_ws.get("A2:B") if len(row) > 1} if legacy_ws else set()
        eligible = bound & legacy_ids
        locations = self.image_locations(eligible)
        return {"eligible": sorted(eligible), "migrated": sorted(locations),
                "pending": sorted(eligible - locations.keys()), "bound_products": len(bindings),
                "nas_ready": self.nas_config is not None and not self.nas_config_error}

    def migrate_next_images(self):
        status = self.migration_status()
        if not status["nas_ready"]:
            raise DispatchError("NAS 私密設定未就緒，未開始搬移")
        selected = status["pending"][:GROUP_SIZE]
        if not selected:
            return []
        originals = super().get_assets(selected)
        self.put_assets([originals[k] for k in selected])
        return selected

    def verify_migrated_images(self, asset_ids):
        selected = _ids(asset_ids)
        if not 1 <= len(selected) <= GROUP_SIZE:
            raise DispatchError("每次僅核對 1～10 張已搬移原圖")
        status = self.migration_status()
        if not selected <= set(status["migrated"]):
            raise DispatchError("核對範圍含尚未搬移或未綁定的原圖")
        originals = super().get_assets(selected)
        loaded = self.get_assets(selected)  # Fresh NAS session; not a UI image cache.
        if any(asset_bytes(originals[k]) != asset_bytes(loaded[k]) for k in selected):
            raise DispatchError("NAS 與舊圖庫原檔不一致，停止核對")
        return [{"sha256": k, "name": loaded[k]["name"], "bytes": len(asset_bytes(loaded[k]))}
                for k in sorted(selected)]


def configured_store(spreadsheet, read_section):
    """Never expose a malformed Secrets value in normal quotation error messages."""
    try:
        section = read_section()
        config = None if section is None else NasConfig(**{
            name: section[name] for name in ("base_url", "root", "library_id", "account", "password")})
        return NasDispatchStore(spreadsheet, config)
    except Exception:
        return NasDispatchStore(spreadsheet, config_error=True)
