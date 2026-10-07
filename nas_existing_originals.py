"""Adopt verified physical NAS originals without any upload or legacy fallback."""
from dispatch_manager import DispatchError
from dispatch_storage import asset_bytes
from nas_dispatch_storage import NasDispatchStore, _ids, _same_location
from product_images import checked_assets


class ExistingNasOriginalStore(NasDispatchStore):
    """A per-submission, original-only adapter restricted to the selected bytes."""

    def __init__(self, store, assets, *, before_publish):
        if (not isinstance(store, NasDispatchStore) or store.nas_config is None
                or store.nas_config_error):
            raise DispatchError("只核對既有原圖需要已就緒的 NAS 商品圖庫；不會改用舊圖庫或上傳")
        super().__init__(store.spreadsheet, store.nas_config,
                         nas_factory=store._nas_factory)
        clean = checked_assets(assets)
        if (any(len(a["name"]) > 512 for a in clean)
                or sum(len(a["data"]) for a in clean) > 70 * 1024 * 1024):
            raise DispatchError("本次原圖檔名或總量超過限制，未核對 NAS")
        self._originals = {a["sha256"]: a for a in clean}
        nas = self._nas()
        self._expected = {k: nas._metadata(a) for k, a in self._originals.items()}
        self._before_publish = before_publish

    def _read_originals(self, wanted):
        wanted = _ids(wanted)
        if not wanted or not wanted <= self._originals.keys():
            raise DispatchError("核對範圍不是本次選取的原圖，未讀取其他圖片")
        loaded = {}
        with self._nas() as nas:
            nas.check_root()
            for identity in wanted:
                # FileStation.Download is read-only; never call put_asset/Upload.
                asset = nas.get_asset(self._expected[identity])
                if asset_bytes(asset) != asset_bytes(self._originals[identity]):
                    raise DispatchError("NAS 已存在原圖與本次原檔不一致，未確認保存")
                loaded[identity] = asset
        return loaded

    def put_assets(self, assets):
        clean = {a["sha256"]: a for a in checked_assets(assets)}
        if (set(clean) != set(self._originals) or any(
                asset_bytes(a) != asset_bytes(self._originals[k]) for k, a in clean.items())):
            raise DispatchError("本次核對原圖已變更，未發布圖片索引")
        # Validate existing pointers, then verify EVERY physical original before
        # publishing any index. Missing/denied/corrupt responses always stop.
        locations = self.image_locations(clean)
        if any(not _same_location(value, self._expected[k]) for k, value in locations.items()):
            raise DispatchError("NAS 圖片位置不符合本次原圖，未發布索引")
        self._read_originals(clean)
        self._before_publish()
        self._publish_locations(list(self._expected.values()))
        return list(clean)

    def get_assets(self, asset_ids):
        wanted = _ids(asset_ids)
        if not wanted or not wanted <= self._originals.keys():
            raise DispatchError("核對範圍不是本次選取的原圖，未讀取其他圖片")
        locations = self.image_locations(wanted)
        if set(locations) != wanted or any(
                not _same_location(value, self._expected[k]) for k, value in locations.items()):
            raise DispatchError("NAS 原圖位置讀回不完整或不一致，未確認補圖成功")
        loaded = self._read_originals(wanted)
        observed = self.image_locations(wanted)
        if set(observed) != wanted or any(
                not _same_location(value, self._expected[k]) for k, value in observed.items()):
            raise DispatchError("NAS 原圖位置在讀回期間已變更，未確認補圖成功")
        return loaded

    def get_display_asset(self, asset_id):
        # Explicitly disallow the normal display-only legacy backup fallback.
        return self.get_asset(asset_id)
