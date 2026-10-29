"""Bounded existing-only adoption with independent pre/post original reads.

This is the batch counterpart to ExistingNasOriginalStore, not an upload or
replacement API. Every selected physical original must already exist in the
configured library. Source/formula/binding guards are fresh at each write
boundary; no outcome is automatically retried.
"""
from copy import deepcopy

from dispatch_manager import DispatchError, now
from dispatch_storage import asset_bytes, encode_record
from nas_dispatch_storage import NasDispatchStore, _same_location
from product_images import PRODUCT_IMAGE_SHEET, checked_assets, subject_key
from quote_image_repair import _snapshot_digest, prepare_image_repair


def _guard_plan(plan):
    try:
        if (plan["schema"] != 1 or plan["snapshot_digest"] != _snapshot_digest(
                plan["source"], plan["values"], plan["formulas"])):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise DispatchError("補圖快照不完整或已變更，請重新載入商品") from None


def _guard_binding(binding, source, expected):
    if binding and (binding["subject"] != subject_key(source)
                    or binding["assets"] != expected):
        raise DispatchError("本款原圖順序或商品配對不符；未確認完成")


def _fresh_guard(store, plan, binding, locations):
    fresh = prepare_image_repair(store, plan["source"]["identity"])
    if (fresh["snapshot_digest"] != plan["snapshot_digest"]
            or fresh["binding"] != binding):
        raise DispatchError("既有商品或圖片版本在讀回期間已變更；未確認完成")
    if store.image_locations(locations[0]) != locations[1]:
        raise DispatchError("既有 NAS 原圖位置在讀回期間已變更；未確認完成")


def _read_originals(store, assets, metadata):
    # Always a new NAS session. No normal get_assets/display fallback, upload,
    # thumbnail or unrelated path is reachable through this verifier.
    with store._nas() as nas:
        nas.check_root()
        for asset in assets:
            loaded = nas.get_asset(metadata[asset["sha256"]])
            if asset_bytes(loaded) != asset_bytes(asset):
                raise DispatchError("既有 NAS 原圖與本次原檔不一致；未確認完成")


def adopt_existing_nas_originals(store, plan, assets, *, actor, on_phase=None):
    """Return (one-row report, binding) only after fresh full-byte readback.

    ``plan`` is the caller's just-prepared unique source and values/formulas.
    Incomplete indices/bindings may be filled once, but a different binding,
    order, library or any observed concurrent change is never merged.
    """
    if (not isinstance(store, NasDispatchStore) or store.nas_config is None
            or store.nas_config_error):
        raise DispatchError("批次補圖需要已就緒的 NAS 商品圖庫；不改用舊圖庫")
    if not isinstance(actor, str) or not actor.strip():
        raise DispatchError("請填圖片保存人")
    _guard_plan(plan)
    clean = checked_assets(assets)
    if (any(len(a["name"]) > 512 for a in clean)
            or sum(len(a["data"]) for a in clean) > 70 * 1024 * 1024):
        raise DispatchError("本次原圖檔名或總量超過限制，未核對 NAS")
    expected = [a["sha256"] for a in clean]
    source, original_binding = plan["source"], deepcopy(plan["binding"])
    _guard_binding(original_binding, source, expected)
    before = deepcopy(store.image_locations(expected))
    nas = store._nas()
    metadata = {a["sha256"]: nas._metadata(a) for a in clean}
    if any(not _same_location(value, metadata[key]) for key, value in before.items()):
        raise DispatchError("既有 NAS 原圖位置不符合本次原檔；未確認完成")

    def phase(value):
        if on_phase is not None:
            on_phase(value)

    phase("readback")
    _read_originals(store, clean, metadata)
    # The physical bytes are known only for this read; independently guard all
    # source/formula/binding and existing pointers before any index publication.
    _fresh_guard(store, plan, original_binding, (expected, before))
    phase("save")
    store._publish_locations(list(metadata.values()))
    indexed = {**before, **{key: value for key, value in metadata.items() if key not in before}}
    # Location publication can take time. Do not bind if source or another
    # writer changed while that separate write was in progress.
    _fresh_guard(store, plan, original_binding, (expected, indexed))
    binding = original_binding
    result = "已存在"
    if binding is None:
        value = {"schema": 1, "identity": source["identity"], "subject": subject_key(source),
                 "source_hash": source["source_hash"], "assets": expected,
                 "actor": actor.strip(), "origin": "quote_image_repair_existing_nas", "at": now()}
        records = encode_record(source["identity"], value, "")
        try:
            ws = store._sheet(PRODUCT_IMAGE_SHEET, create=True)
            ws.append_rows(records, value_input_option="RAW", table_range="A:H")
        except Exception as exc:
            raise DispatchError("圖片綁定儲存結果待確認；重新載入核對，未自動重試") from exc
        binding = {**value, "_revision": records[0][0]}
        if store.product_image_bindings().get(source["identity"]) != binding:
            raise DispatchError("圖片綁定寫後核對不符；未確認完成")
        result = "已綁定"

    phase("readback")
    # Verify EVERY original again in a separate session after publication.
    _read_originals(store, clean, indexed)
    _fresh_guard(store, plan, binding, (expected, indexed))
    return [{"品號": source.get("code") or source["identity"], "商品": source["name"],
             "結果": result, "原因": "既有 NAS 原圖、來源與綁定版本均已重新讀回核對"}], binding
