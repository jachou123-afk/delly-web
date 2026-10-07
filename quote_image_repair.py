"""Fill missing product images without quotes, batches, review or LINE writes."""
from copy import deepcopy

from cost_audit import block
from dispatch_manager import DispatchError, digest
from dispatch_storage import asset_bytes, validate_image
from product_images import checked_assets, source_problem, subject_key


def _unique_source(store, identity):
    products = store.catalog()
    matches = [source for source in products if source["identity"] == identity]
    if len(matches) != 1:
        raise DispatchError("商品不存在或身分重複，無法單獨補圖；請重新載入")
    source = matches[0]
    if (type(source.get("row")) is not int or source["row"] < 1
            or sum(p["category"] == source["category"] and p["row"] == source["row"]
                   for p in products) != 1):
        raise DispatchError("商品位置無法唯一識別，未補存圖片")
    return source


def _snapshot(store, source):
    row = source["row"]
    area = f"A{row}:L{row + 5}"
    sheet = store.worksheet(source["category"])
    values = block(sheet.get(area, value_render_option="FORMATTED_VALUE"))
    formulas = block(sheet.get(area, value_render_option="FORMULA"))
    if values != block(source["block"]) or digest(values) != source["source_hash"]:
        raise DispatchError("商品來源在讀取期間已變更；請重新載入並核對圖片")
    return values, formulas


def _snapshot_digest(source, values, formulas):
    return digest({"source": source, "values": values, "formulas": formulas})


def prepare_image_repair(store, identity):
    """Read one unique current product and its values/formulas; never writes."""
    source = deepcopy(_unique_source(store, identity))
    values, formulas = _snapshot(store, source)
    return {
        "schema": 1,
        "source": source,
        "values": values,
        "formulas": formulas,
        "snapshot_digest": _snapshot_digest(source, values, formulas),
        "binding": deepcopy(store.product_image_bindings().get(identity)),
    }


def _current_source(store, plan):
    try:
        source = plan["source"]
        if (plan["schema"] != 1 or plan["snapshot_digest"] != _snapshot_digest(
                source, plan["values"], plan["formulas"])):
            raise ValueError
        identity = source["identity"]
    except (KeyError, TypeError, ValueError):
        raise DispatchError("補圖快照不完整或已變更，請重新載入商品") from None
    current = _unique_source(store, identity)
    if current.get("source_url") != source.get("source_url"):
        raise DispatchError("商品雲表來源已變更，請重新載入商品")
    problem = source_problem(source, [current])
    if problem:
        raise DispatchError(problem)
    values, formulas = _snapshot(store, current)
    if values != plan["values"] or formulas != plan["formulas"]:
        raise DispatchError("商品來源或公式已變更，未補存圖片；請重新載入並核對")
    return current


def _matching_binding(binding, source, assets):
    if not binding:
        return
    expected = {asset["sha256"] for asset in assets}
    if binding["subject"] != subject_key(source) or set(binding["assets"]) != expected:
        raise DispatchError("商品已有不同圖片或圖片身分不符；補圖不會替換既有綁定")


def _verify_saved_images(store, source, assets):
    """Strict original-byte readback, never the display-only backup fallback."""
    binding = store.product_image_bindings().get(source["identity"])
    if not binding:
        raise DispatchError("圖片綁定讀回未完成，請重新載入後只重試補圖")
    _matching_binding(binding, source, assets)
    returned = store.get_assets(binding["assets"])
    for asset in assets:
        stored = returned.get(asset["sha256"])
        if stored is None or asset_bytes(stored) != asset_bytes(asset):
            raise DispatchError("圖片原檔讀回不一致，未確認補圖成功")
        validate_image(asset_bytes(stored), stored["name"])
    return binding


def save_image_repair(store, plan, assets, *, actor):
    """Fill an empty binding or verify an identical set; never replace or retry.

    A pending outcome is returned unchanged. A later explicit submission reads
    current bindings and originals again, including after an uncertain write.
    """
    return _save_image_repair(store, plan, assets, actor=actor,
                              origin="quote_image_repair")


def _save_image_repair(store, plan, assets, *, actor, origin, expected_revision=None):
    if not isinstance(actor, str) or not actor.strip():
        raise DispatchError("請填圖片保存人")
    assets = checked_assets(assets)
    source = _current_source(store, plan)
    binding = store.product_image_bindings().get(source["identity"])
    _matching_binding(binding, source, assets)
    if expected_revision is not None and (binding or {}).get("_revision", "") != expected_revision:
        raise DispatchError("商品圖片版本在核對期間已變更；請重新載入後核對")
    if binding:
        binding = _verify_saved_images(store, source, assets)
        by_id = {asset["sha256"]: asset for asset in assets}
        # The same set in a different upload order must not reorder saved images.
        assets = [by_id[identity] for identity in binding["assets"]]
    # Recheck after any original-byte read and immediately before storage writes.
    source = _current_source(store, plan)
    result = store.save_product_images(
        [{"source": source, "assets": assets,
          "expected_revision": binding["_revision"] if binding else ""}],
        actor=actor.strip(), origin=origin, replace=False,
    )
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
        raise DispatchError("圖片保存結果待確認，請重新載入後只重試補圖")
    if result[0].get("結果") in {"已綁定", "已存在"}:
        verified_binding = _verify_saved_images(store, source, assets)
        if expected_revision is not None:
            observed = store.product_image_bindings().get(source["identity"])
            if not observed or observed["_revision"] != verified_binding["_revision"]:
                raise DispatchError("圖片綁定版本在讀回期間已變更，未確認補圖成功")
        try:
            _current_source(store, plan)
        except DispatchError:
            raise DispatchError(
                "圖片保存後商品來源或公式已變更；保存結果待核對，請重新載入商品與圖片"
            ) from None
    return result


def save_existing_nas_image_repair(store, plan, assets, *, actor):
    """Verify existing physical NAS originals, then bind; never upload files."""
    from nas_existing_originals import ExistingNasOriginalStore

    if not isinstance(actor, str) or not actor.strip():
        raise DispatchError("請填圖片保存人")
    assets = checked_assets(assets)
    source = _current_source(store, plan)
    binding = store.product_image_bindings().get(source["identity"])
    _matching_binding(binding, source, assets)
    revision = (binding or {}).get("_revision", "")

    def before_publish():
        current = _current_source(strict, plan)
        observed = strict.product_image_bindings().get(current["identity"])
        _matching_binding(observed, current, assets)
        if (observed or {}).get("_revision", "") != revision:
            raise DispatchError("商品圖片版本在核對期間已變更；未發布索引，請重新載入")

    strict = ExistingNasOriginalStore(store, assets, before_publish=before_publish)
    # Recover missing location metadata only AFTER every original byte passes.
    strict.put_assets(assets)
    return _save_image_repair(strict, plan, assets, actor=actor,
                              origin="quote_image_repair_existing_nas",
                              expected_revision=revision)
