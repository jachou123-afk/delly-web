"""Read-only explanations and ordered downloads; never infer LINE delivery."""
from io import BytesIO
import hashlib
import re
from zipfile import ZipFile, ZIP_DEFLATED

from dispatch_manager import DispatchError, item_errors, item_status, reconciliation, prior_activity
from dispatch_storage import asset_bytes


def progress_detail(batch, item, history=()):
    status = item_status(item)
    reason = item.get("reason", "").strip()
    history_text = "；".join(prior_activity(batch, item, history))
    if item["excluded"]:
        vague = not reason or "疑點" in reason or "已發或" in reason
        issue = "未記錄本款具體排除原因" if vague else reason
        action = "查明本款原因後再決定是否另排；不要直接重發" if vague else "本批不發送；依排除原因處理"
        status = "原因待釐清" if vague else "本批排除"
    elif item.get("uncertain"):
        issue = item["uncertain"].get("evidence") or "操作中斷，尚未記錄具體情況"
        action = "先查 LINE 紀錄，再到單款處理解除待確認"
    elif status == "重複紀錄待處理":
        issue = f"圖片登記 {len(item['image_receipts'])} 次；文案登記 {len(item['text_receipts'])} 次"
        action = "查明是否重複發送；不要再發"
    elif status == "已確認完成":
        issue, action = "圖片、文案均已有人工查驗紀錄", "不用再發"
    else:
        errors = item_errors(item, require_review=False, require_source=False)
        missing = [label for part, label in (("image", "圖片"), ("text", "文案"))
                   if not item[part + "_receipts"]]
        issue = "；".join(errors) if errors else "、".join(missing) + "尚無發送確認紀錄（不代表一定未發）"
        action = "先到單款處理修正問題" if errors else "先查 LINE；未發才處理" + "、".join(missing) + "，已發則整批登記"
        if errors:
            status = "資料需處理"
    return {"順序": item["order"], "品號": item["source"]["code"] or item["id"],
            "商品": item["source"]["name"], "狀態": status, "具體情況": issue,
            "下一步": action, "原始排除備註": reason, "同聊天室其他批次紀錄": history_text or "未找到其他批次紀錄"}


def pending_items(batch):
    return [i for i in sorted(batch["items"], key=lambda i: i["order"])
            if not i["excluded"] and item_status(i) in ("待發", "部分完成")
            and not item_errors(i, require_review=False, require_source=False)]


def selected_pending(batch, item_ids):
    if batch["status"] not in ("approved", "in_progress") or reconciliation(batch)["integrity_error"]:
        raise DispatchError("批次未確認、已完成或內容已變動，請重新載入")
    wanted = set(item_ids)
    available = pending_items(batch)
    if not wanted or not wanted <= {i["id"] for i in available}:
        raise DispatchError("請只選擇本批尚待登記、沒有異常的商品")
    return [i for i in available if i["id"] in wanted]


def combined_copy(items):
    """Only missing text, preserving each approved advert verbatim."""
    return "\n\n──────────\n\n".join(i["copy"] for i in items if not i["text_receipts"])


def build_dispatch_package(batch, item_ids, read_asset, max_bytes=64 * 1024 * 1024):
    """Build one bounded archive of only missing parts, with verified originals."""
    items = selected_pending(batch, item_ids)
    output = BytesIO()
    total = 0
    manifest = [f"批次：{batch['name']}", f"目標聊天室：{batch['target']}",
                "只包含所選商品尚未登記的部分；下載不代表已發送。發送前先查 LINE，避免重複。",
                "依序處理每個商品資料夾的圖片與文案，勿把所有圖片當成同一款。", ""]
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for item in items:
            code = re.sub(r"[^A-Za-z0-9_-]", "_", item["source"]["code"] or item["id"])
            folder = f"{item['order']:03d}_{code}"
            parts = []
            if not item["text_receipts"]:
                archive.writestr(folder + "/文案.txt", item["copy"].encode("utf-8-sig"))
                parts.append("文案")
            if not item["image_receipts"]:
                for n, identity in enumerate(item["images"], 1):
                    asset = read_asset(identity)
                    data = asset_bytes(asset)
                    if hashlib.sha256(data).hexdigest() != identity:
                        raise DispatchError(f"{code}：圖片與本批配對不符，未產生下載包")
                    total += len(data)
                    if total > max_bytes:
                        raise DispatchError("所選原圖超過 64 MB，請減少本次選取商品")
                    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(asset["mime"])
                    if not extension:
                        raise DispatchError("圖片格式不支援，未產生下載包")
                    archive.writestr(f"{folder}/圖片{n:02d}.{extension}", data)
                parts.append(f"圖片 {len(item['images'])} 張")
            manifest.append(f"{folder}｜{item['source']['name']}｜" + "、".join(parts))
        archive.writestr("發送順序與說明.txt", "\n".join(manifest).encode("utf-8-sig"))
        archive.writestr("整批文案.txt", combined_copy(items).encode("utf-8-sig"))
    return output.getvalue()
