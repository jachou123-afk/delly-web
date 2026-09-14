"""Read-only batch cost checks. Selection is never a review or send receipt."""
from collections import Counter, defaultdict

from cost_audit import block
from dispatch_manager import DispatchError, digest, now


def merge_selection(previous, visible_ids, rows):
    """Map original dataframe positions to stable IDs, retaining hidden picks."""
    if any(type(row) is not int or not 0 <= row < len(visible_ids) for row in rows):
        return set(previous)
    return (set(previous) - set(visible_ids)) | {visible_ids[row] for row in rows}


def batch_signature(batch):
    return digest([batch["id"], batch.get("_revision", ""),
                   [(i["id"], i["order"], i["excluded"], i["source"]["source_hash"],
                     i["source"].get("row")) for i in batch["items"]]])


def result_row(item, checked):
    source = item["source"]
    report = checked.get("report") or {}
    rows = {r["項目"]: r for r in report.get("rows", [])}
    cost = rows.get("到手成本（TWD）", {})
    sale = rows.get("廣告售價（TWD）", {})
    issues = list(source.get("errors", [])) + list(report.get("errors", []))
    if not source.get("price"):
        issues = ["原廣告售價尚未產生，無法比對；請先處理來源／文案阻擋原因"
                  if issue == "廣告售價（TWD）與獨立驗算不一致或原表缺值" else issue for issue in issues]
    if checked.get("error"):
        issues.insert(0, checked["error"])
        status = checked.get("status", "讀取失敗")
    elif any(r["結果"] == "有差異" for r in report.get("rows", [])):
        status = "有差異"
    elif report.get("math_pass"):
        status = "計算一致"
    else:
        status = "資料／公式待處理"
    if not report.get("source_ready"):
        issues.append("缺少這版商品的廠商原文／參數依據")
    if item["excluded"]:
        issues.append("本批已暫緩／排除；驗算不改變排除狀態")
    return {"順序": item["order"], "品號": source.get("code") or item["id"],
            "商品": source["name"], "原成本": block(source.get("block", []))[1][10] or "缺資料",
            "重算成本": cost.get("重算結果", "—"), "成本差額": cost.get("差額（原表−重算）", "—"),
            "原廣告售價": source.get("price") or "尚未產生", "重算售價": sale.get("重算結果", "—"),
            "售價差額": sale.get("差額（原表−重算）", "—"), "計算結果": status,
            "來源依據": "已保存・待人工核對" if report.get("source_ready") else "待補資料",
            "需處理": "；".join(dict.fromkeys(issues)) or "仍需逐款核對原文、圖片、單位與文案"}


def run_batch_audit(store, batch, selected_ids, progress=None):
    """Exactly one result per selected ID, including stale/failed/excluded rows."""
    requested = set(selected_ids)
    ids = [i["id"] for i in batch["items"]]
    if not requested or not requested <= set(ids) or len(ids) != len(set(ids)):
        raise DispatchError("驗算選取範圍無效，請重新選擇本批商品")
    if len(requested) > 150 or batch["status"] != "draft":
        raise DispatchError("只可驗算草稿，每次最多 150 款")
    items = sorted((i for i in batch["items"] if i["id"] in requested), key=lambda i: i["order"])
    checks, eligible, fresh = {}, [], None
    try:
        fresh = store.catalog()
    except Exception as exc:
        for item in items:
            checks[item["id"]] = {"error": f"讀取原表失敗：{exc}", "status": "讀取失敗"}
    else:
        by_id = defaultdict(list)
        for source in fresh:
            by_id[source["identity"]].append(source)
        for item in items:
            matches, old = by_id[item["id"]], item["source"]
            if len(matches) != 1:
                checks[item["id"]] = {"error": "來源已刪除或品號不唯一，請核對原表", "status": "來源已變動"}
            elif matches[0]["source_hash"] != old["source_hash"] or matches[0]["row"] != old["row"]:
                checks[item["id"]] = {"error": "草稿與原表不同，先更新本款來源與文案再驗算", "status": "來源已變動"}
            else:
                eligible.append(matches[0])
        if eligible:
            already = len(checks)
            try:
                checks.update(store.cost_audits(eligible, on_progress=(
                    lambda done, total: progress(already + done, len(items))) if progress else None))
            except Exception as exc:
                for source in eligible:
                    checks[source["identity"]] = {"error": f"整批讀取失敗：{exc}", "status": "讀取失敗"}
    for item in items:
        checks.setdefault(item["id"], {"error": "本款未收到完整驗算結果，請重新驗算", "status": "讀取失敗"})
    rows = [result_row(item, checks[item["id"]]) for item in items]
    if progress:
        progress(len(items), len(items))
    return {"signature": batch_signature(batch), "at": now(), "ids": [i["id"] for i in items],
            "rows": rows, "checks": checks, "catalog": fresh,
            "counts": dict(Counter(row["計算結果"] for row in rows))}
