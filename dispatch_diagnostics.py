"""Explain existing preparation gates; never approve, repair, or infer evidence."""
from cost_audit import RULE_VERSION


def _issue(field, reason, current, action, location):
    return dict(field=field, reason=reason, current=current, action=action, location=location)


def explain_blocker(item, reason, references=(), checked=None):
    source = item["source"]
    report = item.get("cost_audit") or {}
    where = f"原報價表 {source.get('category', '未記錄分頁')}!A{source.get('row', '?')} 起的商品區塊"
    detail = "下方「查看／修改單款」"
    from review_issues import issue_reason
    for issue in item.get("known_issues", []):
        if issue.get("status") == "open" and reason == issue_reason(issue):
            return _issue(issue['field'], issue['detail'],
                          f"原紀錄：{issue['current']}；核對依據：{issue['expected']}。證據：{issue['evidence']}（{issue['at']}，{issue['actor']}）",
                          issue['action'] + '；修正後以實際證據登記解除，驗算通過不會自動清除此問題。',
                          '既有問題紀錄 → 查看／解除問題；' + where)
    if reason.startswith("待確認計價單位"):
        return _issue("售價單位", "售價單位尚未確認（不是已判定單位填錯）",
                      f"原表：{source.get('unit_evidence') or '未明示單位'}；目前候選：每{source.get('unit') or '？'}；尚無有效確認紀錄。",
                      "對照廠商原文或可定位的既有核對證據；若售價確實與裝箱採相同單位，填確認依據後儲存。不同時先回原表換算，不可直接勾過。",
                      detail + " → 單位確認依據／儲存本款修改；多款可用上方「批次確認計價單位」")
    if reason == "尚未加入商品圖片":
        count = len(references)
        return _issue("商品圖片", "未有可採用的商品原圖",
                      f"本批已存 {len(item.get('images', []))} 張；圖庫候選 {count} 張" +
                      ("，但尚未成為有效保存配對。" if count else "；未找到有效配對，不代表原報價表一定沒有圖片。"),
                      "核對本款原圖，採用正確候選或上傳原圖，再保存圖片／本款修改；不可拿相似商品圖片代替。",
                      detail + " → 採用的原表／圖片包圖片、加入商品圖片")
    if reason == "每款最多 5 張圖片":
        return _issue("商品圖片", reason, f"目前 {len(item.get('images', []))} 張",
                      "只保留本款需要的原圖，減至 5 張內並儲存。", detail + " → 保留的圖片")
    if reason.startswith("成本尚未獨立驗算"):
        if checked and checked.get("error"):
            return _issue("來源讀取／驗算", checked["error"], "本次沒有可用的完整驗算結果，不能當作算價通過。",
                          "來源變動時更新本款來源；讀取失敗時先排除連線問題，再驗算。", detail + " → 來源已修改？重新載入本款；或上方「驗算所選商品」")
        return _issue("成本驗算", "尚未驗算，或舊驗算已失效", "沒有符合目前來源版本的有效驗算結果。",
                      "選取本款後執行驗算；若顯示來源已變動，先更新來源再驗算。", "① 選取驗算範圍 → 驗算所選商品")
    if "來源資料或列位置已變更" in reason or "來源已刪除或 NO 重複" in reason:
        return _issue("來源版本", reason, f"這份草稿與目前雲表不同；{where}。",
                      "先核對雲表的品號及修改內容，再更新本款來源與文案、重新驗算；不要沿用舊結果。",
                      detail + " → 來源已修改？重新載入本款 → 更新本款來源與文案")
    if reason.startswith("同聊天室已有安排／發送紀錄"):
        return _issue("重複安排", reason, "同一目標聊天室的其他批次已有本款紀錄，不代表這次一定未發。",
                      "先查同群 LINE 與其他批次；已發款排除，確有再次安排理由才填寫，不能為通過檢查隨便補字。",
                      detail + " → 再次安排原因／本批暫緩／排除這款")
    if reason.startswith("文案品號"):
        return _issue("文案品號", reason, f"本款應使用唯一品號：{source.get('code') or '來源品號未產生'}",
                      "對照原表，修正文案中的品號，不可混入其他款。", detail + " → LINE 文案 → 儲存本款修改")
    if reason.startswith(("售價需與雲表一致", "裝箱需與雲表一致")):
        prefix = "售價" if reason.startswith("售價") else "裝箱"
        expected = "；".join(line for line in source.get("copy", "").splitlines() if line.startswith(prefix)) or "來源未產生"
        actual = "；".join(line for line in item.get("copy", "").splitlines() if line.startswith(prefix)) or "文案缺少此行"
        return _issue(prefix, reason, f"原表要求：{expected}；目前文案：{actual}",
                      "文案填錯就修正文案；若要調整價格／單位，先改原表，再更新本款來源並重算。", detail + " → LINE 文案；或「" + where + "」")
    if reason.startswith(("文案含內部", "文案過長")):
        return _issue("LINE 文案", reason, f"目前文案 {len(item.get('copy', ''))} 字；請查看下方完整內容。",
                      "移除內部成本、重量等不應外發資訊，或縮短至 4500 字內；保留正確品號、售價及裝箱行。", detail + " → LINE 文案 → 儲存本款修改")
    if reason == "排除商品需填寫原因":
        return _issue("排除原因", reason, "已選排除但原因為空。", "填入這款實際排除原因並儲存。", detail + " → 暫緩／排除原因")
    if reason in source.get("errors", []) or reason.startswith("先處理來源資料"):
        return _issue("原報價資料", reason,
                      f"計價單位：{source.get('unit_raw') or '未明示'}；裝箱：{source.get('carton') or '缺資料'}；10% 報價：{source.get('quote_10') or '缺資料'}。",
                      "依這項錯誤核對原表及廠商資料，修正後更新本款來源與文案，再驗算。", where + " → 「更新本款來源與文案」")
    if reason in report.get("errors", []) or reason == "成本驗算尚未通過":
        differences = [f"{r['項目']}：原表 {r.get('原表／原售價', '缺資料')}，重算 {r.get('重算結果', '未完成')}，差額 {r.get('差額（原表−重算）', '—')}"
                       for r in report.get("rows", []) if r.get("結果") != "一致"]
        return _issue("成本／公式", reason, "；".join(differences) or "詳見本款參數／公式檢查；不能只看售價數字相同。",
                      "對照原始進價、重量、裝箱、費率與公式，修正有差異的來源或依據後再驗算；不要直接把數字改成重算值。",
                      detail + " → 報價表原文、參數與詳細算式；原表位置：" + where)
    # Unknown/new gate messages remain blockers, with the original reason intact.
    return _issue("其他檢查", reason, "此原因來自現行發送檢查，尚未分類。",
                  "查看本款原表、原圖與文案，依原始訊息排除問題後重新檢查；未釐清前暫緩。", detail)


def diagnose_item(item, blocking_reasons, references=(), checked=None):
    source = item["source"]
    blockers = [explain_blocker(item, reason, references, checked) for reason in dict.fromkeys(blocking_reasons)]
    report = item.get("cost_audit") or {}
    current_report = bool(report and report.get("rule") == RULE_VERSION
                          and report.get("source_hash") == source.get("source_hash"))
    warnings = []
    if not item.get("excluded") and current_report and not report.get("source_ready"):
        warnings.append(_issue(
            "廠商原文", "缺廠商原文／有效依據（不是算價錯誤）",
            "已存舊版依據，但不適用目前來源。" if report.get("saved_evidence") else "未保存本款有效廠商原文；表內重算不能證明原始進價、規格正確。",
            "優先補存真實原文與核對依據；若採缺原文限制，最後需明確知悉。這項提醒不會解除其他阻擋，也不代表已核對原文。",
            "查看／修改單款 → 報價表原文、參數與詳細算式 → 補存／修正原文；或最後整批確認的知悉聲明"))
    status = f"阻擋確認（{len(blockers)}項）" if blockers else (
        "本批已排除" if item.get("excluded") else "僅提醒，待確認" if warnings else "待整批確認")
    return dict(id=item["id"], code=source.get("code") or item["id"], name=source.get("name", ""),
                status=status, blockers=blockers, warnings=warnings)


def batch_diagnostics(batch, issues, references=None, checks=None):
    references, checks = references or {}, checks or {}
    return [diagnose_item(item, issues.get(item["id"], []), references.get(item["id"], []), checks.get(item["id"]))
            for item in sorted(batch["items"], key=lambda i: i["order"])
            if not item.get("excluded") or item["id"] in issues]


def overview_rows(diagnostics):
    return [{"品號": d["code"], "確認狀態": d["status"],
             "阻擋原因": "；".join(x["field"] + "：" + x["reason"] for x in d["blockers"]) or "無程式阻擋",
             "提醒（非算價錯誤）": "；".join(x["reason"] for x in d["warnings"]) or "—"} for d in diagnostics]
