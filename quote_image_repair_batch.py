"""Bounded, explicit NAS repair submissions through the normal single-item API."""
from collections import Counter
from copy import deepcopy
import re

from dispatch_manager import DispatchError, digest, now
from dispatch_storage import asset_bytes
from nas_dispatch_storage import NasDispatchStore, _same_location
from nas_original_adoption import adopt_existing_nas_originals
from product_images import source_problem, subject_key
from product_image_storage import checked_image_diagnostic, image_error_diagnostic, image_error_summary
from quote_image_repair import (prepare_image_repair, save_image_repair,
                                save_existing_nas_image_repair)
from quote_image_repair_zip import package_digest
from synology_image_store import NasDiagnosticError

MAX_CHUNK_PRODUCTS = 10
MODES = {"upload", "existing_only"}
SUCCESS = {"已綁定", "已存在"}
PHASES = {"prepare", "save", "readback"}


def _require_nas(store):
    if (not isinstance(store, NasDispatchStore) or store.nas_config is None
            or store.nas_config_error):
        raise DispatchError("批次補圖需要已就緒的 NAS 商品圖庫；不改用舊圖庫")


def _package_stamp(package):
    observed = package_digest(package)
    if package.get("digest") != observed:
        raise DispatchError("原圖包查核資料已變更，請重新載入 ZIP")
    return observed


def _scope_digest(run):
    return digest({"schema": run["schema"], "package_digest": run["package_digest"],
                   "package_sha256": run["package_sha256"], "mode": run["mode"],
                   "scope": [{k: item[k] for k in ("identity", "sha256", "source")}
                             for item in run["items"]]})


class ImageStorageFailure(DispatchError):
    def __init__(self, diagnostic):
        super().__init__("圖片保存未完成；僅保留安全診斷分類")
        self.diagnostic = checked_image_diagnostic(diagnostic)


def safe_service_diagnostic(exc):
    """Use structured storage metadata or native exception/cause metadata only."""
    if isinstance(exc, ImageStorageFailure):
        return checked_image_diagnostic(exc.diagnostic)
    return image_error_diagnostic(exc)


def _service_summary(diagnostic):
    return image_error_summary(diagnostic)


def safe_repair_error(exc):
    """Never echo arbitrary remote errors, paths, file names, credentials or bodies."""
    if isinstance(exc, NasDiagnosticError):
        return exc.safe_summary()
    diagnostic = safe_service_diagnostic(exc)
    if diagnostic is not None:
        return _service_summary(diagnostic)
    message = str(exc) if isinstance(exc, DispatchError) else ""
    if "NAS" in message:
        http = re.search(r"HTTP ([1-5][0-9]{2})(?![0-9])", message)
        if http:
            return "NAS HTTP " + http[1] + "；未確認完成，未自動切換保存方式"
        api = re.search(r"(?:API 代碼 |錯誤碼 )([0-9]{1,6})(?![0-9])", message)
        if api:
            return "NAS API 錯誤碼 " + api[1] + "；未確認完成"
        for text in ("入口已轉向", "TLS", "憑證", "逾時", "結果待確認"):
            if text in message:
                return {"入口已轉向": "NAS 入口已轉向；未跟隨、未重試",
                        "TLS": "NAS TLS／憑證驗證未通過", "憑證": "NAS TLS／憑證驗證未通過",
                        "逾時": "NAS 連線或讀回逾時；未確認完成",
                        "結果待確認": "NAS 保存結果待確認；未自動重試"}[text]
    allowed = {
        "批次補圖清單格式不符；只接受分頁、實際 NO 與原圖 SHA-256",
        "批次補圖清單含重複商品；每款實際 NO 只能出現一次",
        "批次補圖清單含重複 JSON 欄位",
        "批次原圖包解壓後超過大小限制或含空檔案",
        "批次原圖包檔案大小不符，停止使用",
        "批次原圖包與清單的圖片集合不符",
        "請提供不超過 50 MB 的批次商品原圖 ZIP",
        "批次原圖包檔案數超過限制",
        "批次原圖包含不支援的路徑、重複檔案、連結、壓縮或加密格式",
        "批次原圖包解壓後超過總大小限制",
        "批次原圖包缺少根目錄 manifest.json",
        "批次原圖包含重複 SHA 原圖，每張實體檔只能保存一次",
        "批次原圖包含清單未引用的圖片",
        "批次原圖檔名與原始位元組 SHA-256 不符",
        "批次原圖副檔名與實際圖片格式不符",
        "批次原圖 ZIP 或清單損壞，停止使用",
        "批次原圖包缺少清單指定的原圖",
        "批次補圖需要已就緒的 NAS 商品圖庫；不改用舊圖庫",
        "批次補圖範圍或收據已變更，請重新查核原圖包",
        "原圖包查核資料已變更，請重新載入 ZIP",
        "商品不存在或身分重複，無法單獨補圖；請重新載入",
        "商品位置無法唯一識別，未補存圖片",
        "商品已刪除或 NO 重複，未綁定圖片",
        "商品資料已變動，請重新載入並核對圖片",
        "商品身分不一致，未綁定圖片",
        "商品已有不同圖片或圖片身分不符；補圖不會替換既有綁定",
        "商品來源或公式已變更，未補存圖片；請重新載入並核對",
        "本款 NAS 原圖位置未完整保存；未視為 NAS 完成",
        "本款 NAS 原圖讀回不一致；未確認完成",
        "本款原圖順序或商品配對不符；未確認完成",
        "本款商品或圖片版本在 NAS 讀回期間已變更；未確認完成",
        "既有 NAS 原圖位置不符合本次原檔；未確認完成",
        "既有 NAS 原圖與本次原檔不一致；未確認完成",
        "既有 NAS 原圖位置在讀回期間已變更；未確認完成",
        "既有商品或圖片版本在讀回期間已變更；未確認完成",
        "圖片綁定儲存結果待確認；重新載入核對，未自動重試",
        "圖片綁定寫後核對不符；未確認完成",
    }
    return message if message in allowed else "本款保存或讀回未通過；請單款核對，未自動重試"


def prepare_batch_repair(store, package, *, mode):
    """Read the current catalog/bindings once; snapshots are taken per submission."""
    _require_nas(store)
    stamp = _package_stamp(package)
    if mode not in MODES:
        raise DispatchError("請明確選擇本包的 NAS 保存方式")
    products = store.catalog()
    counts = Counter(p["identity"] for p in products)
    positions = Counter((p["category"], p["row"]) for p in products)
    sources = {p["identity"]: p for p in products}
    bindings = store.product_image_bindings()
    items = []
    for spec in package["products"]:
        identity = spec["identity"]
        source = sources.get(identity)
        reason = ""
        if counts[identity] != 1:
            reason = "商品不存在或身分重複，無法單獨補圖；請重新載入"
        elif positions[(source["category"], source["row"])] != 1:
            reason = "商品位置無法唯一識別，未補存圖片"
        else:
            binding = bindings.get(identity)
            if binding and (binding["subject"] != subject_key(source)
                            or binding["assets"] != spec["sha256"]):
                reason = "商品已有不同圖片或圖片身分不符；補圖不會替換既有綁定"
        items.append({"identity": identity, "sha256": list(spec["sha256"]),
                      "source": deepcopy(source), "status": "blocked" if reason else "ready",
                      "reason": reason, "attempts": 0, "receipt": None,
                      "failure_phase": "", "failure_at": "", "diagnostic": None})
    run = {"schema": 1, "package_digest": stamp,
           "package_sha256": package["package_sha256"], "mode": mode, "items": items}
    run["scope_digest"] = _scope_digest(run)
    return run


def _check_run(package, run):
    try:
        if (run["schema"] != 1 or run["mode"] not in MODES
                or run["package_digest"] != _package_stamp(package)
                or run["package_sha256"] != package["package_sha256"]
                or run["scope_digest"] != _scope_digest(run)
                or [(i["identity"], i["sha256"]) for i in run["items"]]
                   != [(p["identity"], p["sha256"]) for p in package["products"]]):
            raise ValueError
        for item in run["items"]:
            if item["status"] not in {"ready", "blocked", "failed", "completed"}:
                raise ValueError
            if item["status"] == "completed":
                receipt = item["receipt"]
                if (not isinstance(receipt, dict) or receipt["identity"] != item["identity"]
                        or receipt["sha256"] != item["sha256"]
                        or receipt["scope_digest"] != run["scope_digest"]
                        or receipt["result"] not in SUCCESS
                        or receipt["digest"] != digest({k: v for k, v in receipt.items() if k != "digest"})):
                    raise ValueError
    except (KeyError, TypeError, ValueError):
        raise DispatchError("批次補圖範圍或收據已變更，請重新查核原圖包") from None


def _fresh_nas_readback(store, plan, assets):
    """Every selected byte must come from an indexed, new NAS read session."""
    identity = plan["source"]["identity"]
    expected = [asset["sha256"] for asset in assets]
    current = prepare_image_repair(store, identity)
    binding = current["binding"]
    if (not binding or binding["subject"] != subject_key(plan["source"])
            or binding["assets"] != expected):
        raise DispatchError("本款原圖順序或商品配對不符；未確認完成")
    if set(store.image_locations(expected)) != set(expected):
        raise DispatchError("本款 NAS 原圖位置未完整保存；未視為 NAS 完成")
    loaded = store.get_assets(expected)  # NasDispatchStore opens a fresh NAS session.
    if set(loaded) != set(expected) or any(
            asset_bytes(loaded[a["sha256"]]) != asset_bytes(a) for a in assets):
        raise DispatchError("本款 NAS 原圖讀回不一致；未確認完成")
    after = prepare_image_repair(store, identity)
    if (current["snapshot_digest"] != plan["snapshot_digest"]
            or after["snapshot_digest"] != plan["snapshot_digest"]
            or not after["binding"] or after["binding"] != binding):
        raise DispatchError("本款商品或圖片版本在 NAS 讀回期間已變更；未確認完成")
    return binding


def _verify_complete_existing_nas(store, plan, assets):
    """Read-only fast path; incomplete bindings/indices keep the adoption path.

    The caller has just prepared and scope-checked ``plan``. This path never
    publishes locations/bindings or substitutes legacy/display-only images.
    """
    binding = plan.get("binding")
    expected = [asset["sha256"] for asset in assets]
    if (not binding or binding["subject"] != subject_key(plan["source"])
            or binding["assets"] != expected):
        return None
    before = deepcopy(store.image_locations(expected))
    if set(before) != set(expected):
        return None
    nas = store._nas()
    if any(not _same_location(before[asset["sha256"]], nas._metadata(asset)) for asset in assets):
        raise DispatchError("既有 NAS 原圖位置不符合本次原檔；未確認完成")
    with nas:
        nas.check_root()
        for asset in assets:
            loaded = nas.get_asset(before[asset["sha256"]])
            if asset_bytes(loaded) != asset_bytes(asset):
                raise DispatchError("既有 NAS 原圖與本次原檔不一致；未確認完成")
    after = store.image_locations(expected)
    if after != before:
        raise DispatchError("既有 NAS 原圖位置在讀回期間已變更；未確認完成")
    final = prepare_image_repair(store, plan["source"]["identity"])
    if final["snapshot_digest"] != plan["snapshot_digest"] or final["binding"] != binding:
        raise DispatchError("既有商品或圖片版本在讀回期間已變更；未確認完成")
    return binding


def process_next_repair(store, package, run, *, actor, limit=MAX_CHUNK_PRODUCTS, on_update=None):
    """Process ready items once in order; failures never trigger automatic retries."""
    _require_nas(store)
    _check_run(package, run)
    if not isinstance(actor, str) or not actor.strip() or type(limit) is not int or not 1 <= limit <= MAX_CHUNK_PRODUCTS:
        raise DispatchError("請填保存人，且每次僅處理 1～10 款")
    updated = deepcopy(run)
    updated.pop("halt", None)
    ready = [item for item in updated["items"] if item["status"] == "ready"][:limit]
    for item in ready:
        item["attempts"] += 1
        phase = "prepare"
        try:
            plan = prepare_image_repair(store, item["identity"])
            problem = source_problem(item["source"], [plan["source"]])
            if problem:
                raise DispatchError(problem)
            assets = [package["assets"][sha] for sha in item["sha256"]]
            phase = "readback"
            binding = (_verify_complete_existing_nas(store, plan, assets)
                       if updated["mode"] == "existing_only" else None)
            if binding is not None:
                report = [{"結果": "已存在"}]
            elif updated["mode"] == "existing_only":
                def set_phase(value):
                    nonlocal phase
                    phase = value
                report, binding = adopt_existing_nas_originals(
                    store, plan, assets, actor=actor.strip(), on_phase=set_phase)
            else:
                saver = save_image_repair if updated["mode"] == "upload" else save_existing_nas_image_repair
                phase = "save"
                report = saver(store, plan, assets, actor=actor.strip())
                if (not isinstance(report, list) or len(report) != 1
                        or not isinstance(report[0], dict) or report[0].get("結果") not in SUCCESS):
                    if (isinstance(report, list) and report and isinstance(report[0], dict)
                            and checked_image_diagnostic(report[0].get("diagnostic")) is not None):
                        raise ImageStorageFailure(report[0]["diagnostic"])
                    reason = report[0].get("原因", "") if isinstance(report, list) and report and isinstance(report[0], dict) else ""
                    raise DispatchError(reason)
                phase = "readback"
                binding = _fresh_nas_readback(store, plan, assets)
            receipt = {"identity": item["identity"], "sha256": list(item["sha256"]),
                       "result": report[0]["結果"], "mode": updated["mode"],
                       "source_hash": plan["source"]["source_hash"],
                       "snapshot_digest": plan["snapshot_digest"],
                       "binding_revision": binding["_revision"],
                       "scope_digest": updated["scope_digest"], "checked_at": now()}
            receipt["digest"] = digest(receipt)
            item.update(status="completed", reason="NAS 原圖與商品配對已重新讀回核對", receipt=receipt,
                        failure_phase="", failure_at="", diagnostic=None)
        except Exception as exc:
            diagnostic = safe_service_diagnostic(exc)
            item.update(status="failed", reason=safe_repair_error(exc), receipt=None,
                        failure_phase=phase, failure_at=now(), diagnostic=diagnostic)
            if diagnostic and (diagnostic.get("http_status") == 429 or diagnostic.get("api_code") == 429
                               or diagnostic["kind"] == "read_budget_wait"):
                reason = ("批次讀取等待已達上限；尚未送出請求，請稍後明確接續"
                          if diagnostic["kind"] == "read_budget_wait" else "服務回報 429；本批已立即停止，未自動重試")
                updated["halt"] = {"reason": reason,
                                   "identity": item["identity"], "phase": phase,
                                   "checked_at": now(), "diagnostic": diagnostic}
        if on_update is not None:
            on_update(deepcopy(updated))
        if updated.get("halt"):
            break
    return updated


def retry_failed_repair(package, run):
    """Only an explicit user action requeues failures; original source scope stays fixed."""
    _check_run(package, run)
    updated = deepcopy(run)
    for item in updated["items"]:
        if item["status"] == "failed":
            item.update(status="ready", reason="等待本次明確重試；保存方式與原範圍保留")
    return updated


def repair_table(run):
    labels = {"ready": "待處理", "blocked": "查核未通過", "failed": "待人工核對／重試", "completed": "NAS 已讀回核對"}
    return [{"商品": item["identity"], "品名": (item["source"] or {}).get("name", ""),
             "廠商": (item["source"] or {}).get("vendor", ""), "原圖數": len(item["sha256"]),
             "主圖 SHA-256": item["sha256"][0], "狀態": labels[item["status"]],
             "嘗試次數": item["attempts"], "失敗階段": item.get("failure_phase") or ("未記錄（舊版）" if item["status"] == "failed" else ""),
             "原因": item["reason"]} for item in run["items"]]


def repair_receipt_payload(package, run):
    """Export explicit receipts and allowlisted failure metadata, never source blocks."""
    _check_run(package, run)
    items = []
    for item in run["items"]:
        diagnostic = checked_image_diagnostic(item.get("diagnostic"))
        items.append({"identity": item["identity"], "sha256": list(item["sha256"]),
                      "status": item["status"], "attempts": item["attempts"],
                      "failure_phase": item.get("failure_phase") if item.get("failure_phase") in PHASES else None,
                      "failure_at": item.get("failure_at") or None, "diagnostic": diagnostic})
    payload = {"schema": 1, "package_sha256": package["package_sha256"], "mode": run["mode"],
               "items": items, "receipts": [item["receipt"] for item in run["items"] if item["status"] == "completed"]}
    halt = run.get("halt")
    if isinstance(halt, dict):
        diagnostic = checked_image_diagnostic(halt.get("diagnostic"))
        if diagnostic and (diagnostic.get("http_status") == 429 or diagnostic.get("api_code") == 429
                           or diagnostic["kind"] == "read_budget_wait"):
            reason = ("批次讀取等待已達上限；尚未送出請求，請稍後明確接續"
                      if diagnostic["kind"] == "read_budget_wait" else "服務回報 429；本批已立即停止，未自動重試")
            payload["halt"] = {"reason": reason,
                               "identity": halt.get("identity") if halt.get("identity") in {i["identity"] for i in run["items"]} else None,
                               "phase": halt.get("phase") if halt.get("phase") in PHASES else None,
                               "diagnostic": diagnostic}
    return payload
