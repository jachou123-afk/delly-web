"""One read-only second check, then frozen cards and explicit visual receipts.

This module never sends LINE messages. A ticket is session-local, not a send lock.
"""
from copy import deepcopy
from io import BytesIO
import uuid
from zipfile import ZipFile

from dispatch_manager import (
    DispatchError, batch_digest, digest, item_errors, item_status, now,
    prior_activity, record_observation_batch, source_changes,
)
from dispatch_workflow import build_dispatch_package, selected_pending


def prepare_workbench(store, batch, item_ids, actor, *, history_checked=False, read_asset=None):
    """Revalidate prices/formulas, original evidence, source and original images once."""
    if not actor.strip() or not history_checked:
        raise DispatchError("請先填執行人，並確認聊天室、未發範圍及單一執行者")
    items = selected_pending(batch, item_ids)
    if any(item_status(i) != "待發" for i in items):
        raise DispatchError("部分完成或結果不明的商品請走單款處理，不可整組重發")
    history = store.list_batches()
    latest = next((b for b in history if b["id"] == batch["id"]), None)
    if latest is None or digest(latest) != digest(batch):
        raise DispatchError("另一台裝置已更新本批，請先重新載入雲端")
    confirmation = batch.get("batch_confirmation") or {}
    batch_review = confirmation.get("digest") == batch["approved_digest"]
    acknowledged = set(confirmation.get("missing_source_ids", [])) if batch_review else set()
    missing = []
    errors = []
    for item in items:
        # Preserve the original approval rules, including explicitly acknowledged
        # legacy missing-source warnings; never turn such warnings into proof.
        source_missing = item["source"].get("cost_audit_required") and not (item.get("cost_audit") or {}).get("source_ready")
        if source_missing:
            missing.append(item["id"])
        errors += [f"{item['source']['code']}：{error}" for error in item_errors(
            item, require_review=not batch_review, require_source=item["id"] not in acknowledged)]
        if prior_activity(batch, item, history) and not item.get("duplicate_note", "").strip():
            errors.append(f"{item['source']['code']}：同聊天室已有其他安排／發送紀錄")
    selected = {**batch, "items": items}
    errors += source_changes(selected, store.catalog())
    if errors:
        raise DispatchError("\n".join(errors))
    store.verify_cost_checks(selected, require_source=not bool(missing))
    reader = read_asset or getattr(store, "get_display_asset", store.get_asset)
    package = build_dispatch_package(batch, [i["id"] for i in items], reader)
    return {
        "id": uuid.uuid4().hex, "batch_id": batch["id"], "target": batch["target"],
        "revision": batch["_revision"], "approved_digest": batch["approved_digest"],
        "actor": actor.strip(), "checked_at": now(), "items": [i["id"] for i in items],
        "completed": [], "missing_source_ids": missing, "package": package, "blocked": False,
    }


def current_item(batch, run):
    if run.get("blocked"):
        raise DispatchError("工作台已暫停；先查 LINE 和雲端紀錄，不可直接重發")
    if (run["batch_id"] != batch["id"] or run["target"] != batch["target"]
            or run["revision"] != batch.get("_revision")
            or run["approved_digest"] != batch.get("approved_digest")
            or run["approved_digest"] != batch_digest(batch)
            or batch["status"] not in ("approved", "in_progress")):
        raise DispatchError("批次／圖文版本已變更，請重新做第二次檢查")
    by_id = {i["id"]: i for i in batch["items"]}
    for identity in run["items"]:
        item = by_id[identity]
        if identity in run["completed"]:
            if item_status(item) != "已確認完成":
                raise DispatchError("已保存的結果與工作台不符，請重新載入")
        else:
            if item_status(item) != "待發":
                raise DispatchError("目前商品已有紀錄／異常，請查明後再接續")
            return item
    return None


def card_images(run, item):
    """Use the verified ZIP bytes, with no repeated NAS/network reads."""
    import re
    code = re.sub(r"[^A-Za-z0-9_-]", "_", item["source"]["code"] or item["id"])
    folder = f"{item['order']:03d}_{code}/"
    with ZipFile(BytesIO(run["package"])) as archive:
        return [(name.rsplit("/", 1)[1], archive.read(name)) for name in archive.namelist()
                if name.startswith(folder + "圖片")]


def complete_current(batch, run, item_id, *, observed=False, note=""):
    """The explicit button attests actual adjacent image+text, not a LINE API receipt."""
    item = current_item(batch, run)
    if item is None or item["id"] != item_id or not observed:
        raise DispatchError("只能登記目前這款，且必須已實際看到完整圖文")
    evidence = (f"工作台 {run['id'][:8]}；第二次檢查 {run['checked_at']}；"
                f"{item['source']['code']}：執行人按下完成，確認全部 {len(item['images'])} 張原圖"
                "與本版完整文案已在指定聊天室相鄰出現、無傳送中／失敗。"
                "本紀錄時間是登記時間，非 LINE 訊息時間。")
    if note.strip():
        evidence += " 補充查驗依據：" + note.strip()
    updated = record_observation_batch(batch, item_ids=[item_id], actor=run["actor"],
                                       target=run["target"], evidence=evidence,
                                       action_id=run["id"] + ":" + item_id)
    updated["audit"].append({"at": now(), "action": "工作台第二次檢查後完成本款",
                             "actor": run["actor"], "run_id": run["id"], "item": item_id,
                             "checked_at": run["checked_at"], "digest": run["approved_digest"],
                             "missing_source_ids": run["missing_source_ids"]})
    return updated


def advance_run(run, saved, item_id):
    """Advance only using the store's verified write/read-back result."""
    result = deepcopy(run)
    item = next(i for i in saved["items"] if i["id"] == item_id)
    expected = run["id"] + ":" + item_id + ":" + item_id + ":"
    if (item_status(item) != "已確認完成" or saved.get("_revision") == run["revision"]
            or any(item[part + "_receipts"][0]["id"] != expected + part for part in ("image", "text"))):
        raise DispatchError("寫後核對不符，不前進下一款")
    result["revision"] = saved["_revision"]
    result["completed"].append(item_id)
    current_item(saved, result)
    return result
