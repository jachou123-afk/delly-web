"""Read-only, exact-scope inspection; never publishes indices or bindings."""
from copy import deepcopy

from dispatch_manager import DispatchError, now
from dispatch_storage import asset_bytes, validate_image
from nas_dispatch_storage import NasDispatchStore
from product_images import checked_assets, subject_key
from quote_image_repair import _current_source
from quote_image_repair_batch import safe_repair_error


def inspection_error(exc):
    known = {
        "商品圖片版本已變更，請重新載入後查核；未讀取其他圖片",
        "這款沒有可核對的目前綁定原圖，請重新載入商品",
        "檔案資訊查核需要已就緒的 NAS 商品圖庫；不改用其他位置",
        "NAS 檔案資訊回應不一致；未當成原圖完成",
        "商品或圖片版本在查核期間已變更；檔案資訊尚未確認",
        "已綁定原圖讀回不完整；未使用其他圖片替代",
        "已綁定原圖 SHA-256 不一致；未使用其他圖片替代",
        "商品或圖片版本在讀回期間已變更；未顯示部分原圖",
    }
    return str(exc) if isinstance(exc, DispatchError) and str(exc) in known else safe_repair_error(exc)


def _current_binding(store, plan, *, required=False):
    source = _current_source(store, plan)
    observed = deepcopy(store.product_image_bindings().get(source["identity"]))
    if observed != plan.get("binding"):
        raise DispatchError("商品圖片版本已變更，請重新載入後查核；未讀取其他圖片")
    if required and (not observed or observed["subject"] != subject_key(source)):
        raise DispatchError("這款沒有可核對的目前綁定原圖，請重新載入商品")
    return source, observed


def inspect_nas_files(store, plan, assets):
    """Only stat the uploaded originals for this current source; no Download/Upload."""
    if (not isinstance(store, NasDispatchStore) or store.nas_config is None
            or store.nas_config_error):
        raise DispatchError("檔案資訊查核需要已就緒的 NAS 商品圖庫；不改用其他位置")
    clean = checked_assets(assets)
    source, binding = _current_binding(store, plan)
    result = []
    with store._nas() as nas:
        nas.check_root()
        for asset in clean:
            expected_size = len(asset_bytes(asset))
            row = {"sha256": asset["sha256"], "name": asset["name"],
                   "expected_size": expected_size, "exists": None, "size": None,
                   "size_matches": False, "status": "error", "reason": ""}
            try:
                observed = nas.stat_asset(nas._metadata(asset))
                if (not isinstance(observed, dict) or observed.get("sha256") != asset["sha256"]
                        or observed.get("expected_size") != expected_size
                        or type(observed.get("exists")) is not bool
                        or type(observed.get("size_matches")) is not bool):
                    raise DispatchError("NAS 檔案資訊回應不一致；未當成原圖完成")
                if observed["exists"]:
                    if type(observed.get("size")) is not int or observed["size"] <= 0:
                        raise DispatchError("NAS 檔案資訊回應不一致；未當成原圖完成")
                    if observed["size_matches"] != (observed["size"] == expected_size):
                        raise DispatchError("NAS 檔案資訊回應不一致；未當成原圖完成")
                    row.update(exists=True, size=observed["size"], size_matches=observed["size_matches"],
                               status="exists", reason="檔案存在；大小核對不代表 SHA-256 校驗完成")
                else:
                    if observed.get("size") is not None or observed["size_matches"]:
                        raise DispatchError("NAS 檔案資訊回應不一致；未當成原圖完成")
                    row.update(exists=False, status="missing", reason="File Station API 408：檔案不存在；未自動上傳")
            except Exception as exc:
                row["reason"] = inspection_error(exc)
            result.append(row)
    final_source, final_binding = _current_binding(store, plan)
    if final_source != source or final_binding != binding:
        raise DispatchError("商品或圖片版本在查核期間已變更；檔案資訊尚未確認")
    return {"identity": source["identity"], "checked_at": now(),
            "snapshot_digest": plan["snapshot_digest"], "images": result}


def read_bound_originals(store, plan):
    """Read exactly the current binding, in its saved order, with no display fallback."""
    source, binding = _current_binding(store, plan, required=True)
    identities = list(binding["assets"])
    loaded = store.get_assets(identities)
    if not isinstance(loaded, dict) or set(loaded) != set(identities):
        raise DispatchError("已綁定原圖讀回不完整；未使用其他圖片替代")
    originals = []
    for identity in identities:
        asset = loaded[identity]
        clean = validate_image(asset_bytes(asset), asset["name"])
        if clean["sha256"] != identity or clean["mime"] != asset.get("mime") or asset.get("_cloud_backup"):
            raise DispatchError("已綁定原圖 SHA-256 不一致；未使用其他圖片替代")
        originals.append(clean)
    final_source, final_binding = _current_binding(store, plan, required=True)
    if final_source != source or final_binding != binding:
        raise DispatchError("商品或圖片版本在讀回期間已變更；未顯示部分原圖")
    return {"identity": source["identity"], "checked_at": now(),
            "snapshot_digest": plan["snapshot_digest"],
            "binding_revision": binding["_revision"], "sha256": identities,
            "assets": originals}
