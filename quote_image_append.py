"""Explicit one-original NAS supplement append; existing main/order are immutable."""
from copy import deepcopy

from dispatch_manager import DispatchError, digest, now
from dispatch_storage import asset_bytes, encode_record, validate_image
from image_limits import MAX_PRODUCT_ORIGINALS
from nas_dispatch_storage import NasDispatchStore, _same_location
from product_images import PRODUCT_IMAGE_SHEET, checked_assets, subject_key
from quote_image_repair import _current_source


ORIGIN = "quote_image_repair_append_original"


def _binding(store, source):
    return deepcopy(store.product_image_bindings().get(source["identity"]))


def _require_plan_binding(plan, source):
    saved = deepcopy(plan.get("binding"))
    if (not isinstance(saved, dict) or not saved.get("_revision")
            or saved.get("subject") != subject_key(source)
            or saved.get("source_hash") != source["source_hash"]
            or not isinstance(saved.get("assets"), list)
            or not 1 <= len(saved["assets"]) < MAX_PRODUCT_ORIGINALS
            or len(set(saved["assets"])) != len(saved["assets"])):
        raise DispatchError("追加原圖需要目前商品的完整 NAS 綁定；不可替換主圖或使用其他商品圖片")
    return saved


def _same_expected_binding(observed, old, source, ordered, plan):
    return (isinstance(observed, dict)
            and observed.get("subject") == old["subject"]
            and observed.get("source_hash") == source["source_hash"]
            and observed.get("assets") == ordered
            and observed.get("origin") == ORIGIN
            and observed.get("append_parent") == old["_revision"]
            and observed.get("append_snapshot_digest") == plan["snapshot_digest"])


def _unchanged(store, plan, old):
    current = _current_source(store, plan)
    if _binding(store, current) != old:
        raise DispatchError("商品圖片版本在追加期間已變更；未發布本次配對，請重新載入")
    return current


def _strict_originals(store, ids, expected=None):
    """Require every index, then fresh NAS originals; no legacy/display fallback."""
    locations = store.image_locations(ids)
    if set(locations) != set(ids):
        raise DispatchError("既有原圖缺少完整 NAS 位置；未追加或重傳舊圖")
    if expected is not None and (set(expected) != set(locations) or any(
            not _same_location(locations[k], expected[k]) for k in locations)):
        raise DispatchError("NAS 原圖位置已變更；未確認追加完成")
    originals = {}
    with store._nas() as nas:
        nas.check_root()
        for identity in ids:
            loaded = nas.get_asset(locations[identity])
            clean = validate_image(asset_bytes(loaded), loaded["name"])
            if (clean["sha256"] != identity or clean["mime"] != locations[identity]["mime"]
                    or len(asset_bytes(clean)) != locations[identity]["size"]
                    or loaded.get("_cloud_backup")):
                raise DispatchError("NAS 原圖校驗不符；未使用其他圖片替代")
            originals[identity] = clean
    observed = store.image_locations(ids)
    if set(observed) != set(locations) or any(
            not _same_location(observed[k], locations[k]) for k in locations):
        raise DispatchError("NAS 原圖位置在讀回期間已變更；未確認追加完成")
    return originals, locations


def _success(store, plan, old, source, ordered, expected_binding, expected_locations):
    current = _current_source(store, plan)
    observed = _binding(store, current)
    if observed != expected_binding or not _same_expected_binding(observed, old, source, ordered, plan):
        raise DispatchError("追加綁定寫後核對不符；請重新載入核對，不重送保存")
    _, locations = _strict_originals(store, ordered, expected_locations)
    current = _current_source(store, plan)
    if _binding(store, current) != observed:
        raise DispatchError("圖片版本在追加讀回期間已變更；未確認完成")
    receipt = {"identity": source["identity"], "source_hash": source["source_hash"],
               "snapshot_digest": plan["snapshot_digest"], "binding_revision": observed["_revision"],
               "previous_binding_revision": old["_revision"], "sha256": ordered,
               "main_sha256": old["assets"][0], "appended_sha256": ordered[len(old["assets"]):],
               "mode": "append_original", "checked_at": now()}
    receipt["digest"] = digest(receipt)
    return {"結果": "已追加", "identity": source["identity"], "receipt": receipt}


def append_nas_image_repair(store, plan, assets, *, actor, confirmed_same_product=False):
    """Append exactly one explicit supplement; never replace/remove/reorder old SHA.

    An uncertain upload or binding result is never retried automatically. An
    explicit resubmission of the same plan safely reads an exact completed
    append, or checks the exact new path before deciding whether it is missing.
    """
    if confirmed_same_product is not True:
        raise DispatchError("請先核對這張補充原圖與目前商品同款，並確認保留既有主圖")
    if not isinstance(actor, str) or not actor.strip():
        raise DispatchError("請填圖片保存人")
    if (not isinstance(store, NasDispatchStore) or store.nas_config is None
            or store.nas_config_error):
        raise DispatchError("追加原圖只支援已就緒的 NAS 商品圖庫；不改用其他位置")
    assets = list(assets)
    if len(assets) != 1:
        raise DispatchError("每次只追加一張全新的補充原圖")
    clean = checked_assets(assets)[0]
    if len(clean["name"]) > 512:
        raise DispatchError("補充原圖檔名超過限制；未讀寫 NAS")
    source = _current_source(store, plan)
    old = _require_plan_binding(plan, source)
    if clean["sha256"] in old["assets"]:
        raise DispatchError("這張原圖已在既有圖組；追加入口不重傳或重新排序")
    ordered = [*old["assets"], clean["sha256"]]
    observed = _binding(store, source)
    if observed != old:
        if _same_expected_binding(observed, old, source, ordered, plan):
            locations = store.image_locations(ordered)
            return _success(store, plan, old, source, ordered, observed, locations)
        raise DispatchError("商品圖片版本已變更；未追加，請重新載入")
    _, old_locations = _strict_originals(store, old["assets"])
    _unchanged(store, plan, old)

    expected = store._nas()._metadata(clean)
    indexed = store.image_locations([clean["sha256"]])
    if indexed and not _same_location(indexed[clean["sha256"]], expected):
        raise DispatchError("補充原圖已存在不同 NAS 位置；未上傳或覆蓋")
    with store._nas() as nas:
        nas.check_root()
        stat = nas.stat_asset(expected)
        if (not isinstance(stat, dict) or stat.get("sha256") != clean["sha256"]
                or stat.get("expected_size") != len(asset_bytes(clean))
                or type(stat.get("exists")) is not bool
                or type(stat.get("size_matches")) is not bool):
            raise DispatchError("補充原圖 NAS 檔案資訊不完整；未上傳")
        if stat.get("exists") is True:
            if (type(stat.get("size")) is not int
                    or stat.get("size") != len(asset_bytes(clean))
                    or stat.get("size_matches") is not True):
                raise DispatchError("補充原圖 NAS 大小不符；未覆蓋或上傳")
            present = nas.get_asset(expected)
            if asset_bytes(present) != asset_bytes(clean):
                raise DispatchError("補充原圖 NAS 原檔不符；未覆蓋或上傳")
        elif stat.get("exists") is False and stat.get("size") is None and stat.get("size_matches") is False:
            # Only a strictly classified missing result may reach Upload.
            _unchanged(store, plan, old)
            uploaded = nas.put_asset(clean)
            if not _same_location(uploaded, expected):
                raise DispatchError("補充原圖保存位置不符；未發布配對")
        else:
            raise DispatchError("補充原圖 NAS 檔案資訊不完整；未上傳")
    # New connection, exact bytes, before publishing any image location.
    with store._nas() as nas:
        nas.check_root()
        loaded = nas.get_asset(expected)
        if asset_bytes(loaded) != asset_bytes(clean):
            raise DispatchError("補充原圖新連線讀回不符；未發布配對")
    source = _unchanged(store, plan, old)
    store._publish_locations([expected])
    all_locations = {**old_locations, clean["sha256"]: expected}
    _strict_originals(store, ordered, all_locations)
    source = _unchanged(store, plan, old)
    value = {"schema": 1, "identity": source["identity"], "subject": old["subject"],
             "source_hash": source["source_hash"], "assets": ordered,
             "actor": actor.strip(), "origin": ORIGIN, "at": now(),
             "append_parent": old["_revision"], "append_snapshot_digest": plan["snapshot_digest"]}
    rows = encode_record(source["identity"], value, old["_revision"])
    worksheet = store._sheet(PRODUCT_IMAGE_SHEET)
    if worksheet is None:
        raise DispatchError("既有圖片對應分頁已不存在；未建立新分頁或寫入")
    _unchanged(store, plan, old)
    try:
        worksheet.append_rows(rows, value_input_option="RAW", table_range="A:H")
    except Exception:
        raise DispatchError("追加圖片配對結果待確認；請重新載入核對，不自動重送") from None
    expected_binding = {**value, "_revision": rows[0][0]}
    return _success(store, plan, old, source, ordered, expected_binding, all_locations)
