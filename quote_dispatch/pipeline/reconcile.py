"""Pure post-send reconciliation shared by UI and tests."""


def reconcile_batch(batch, status_resolver, digest_resolver):
    missing, partial, uncertain, duplicates = [], [], [], []
    complete = 0
    for item in batch["items"]:
        code = item["source"]["code"] or item["id"]
        status = status_resolver(item)
        if status == "待發":
            missing.append(code)
        elif status == "部分完成":
            partial.append(code + ("：缺文案" if item["image_receipts"] else "：缺圖片"))
        elif status == "結果待確認":
            uncertain.append(code)
        elif status == "重複紀錄待處理":
            duplicates.append(code)
        elif status == "已確認完成":
            complete += 1
    resolved = {entry["item"] for entry in batch["observations"]
                if entry["part"] == "resolve_unexpected"}
    unexpected = [entry["evidence"] for entry in batch["observations"]
                  if entry["part"] == "unexpected" and entry["id"] not in resolved]
    integrity = batch["approved_digest"] != digest_resolver(batch)
    return {
        "expected": sum(not item["excluded"] for item in batch["items"]),
        "complete": complete,
        "excluded": sum(item["excluded"] for item in batch["items"]),
        "missing": missing,
        "partial": partial,
        "uncertain": uncertain,
        "duplicates": duplicates,
        "unexpected": unexpected,
        "integrity_error": integrity,
        "can_finish": batch["status"] != "draft" and not any(
            (missing, partial, uncertain, duplicates, unexpected, integrity)
        ),
    }
