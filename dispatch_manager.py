"""Supplier-independent advertising batches. No LINE send API lives here."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import re
import uuid
from zoneinfo import ZoneInfo

from dispatch_review import inspect_source, unit_confirmed
from dispatch_targets import canonical_target, same_target
from quote_dispatch.pipeline.reconcile import reconcile_batch


class DispatchError(ValueError):
    pass


def now():
    return datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def normalized_block(rows):
    block = [[str(v).strip() for v in list(row)[:12]] for row in list(rows)[:6]]
    block += [[] for _ in range(6 - len(block))]
    return [row + [""] * (12 - len(row)) for row in block]


def catalog(sheets, category_codes=None):
    """Read the common six-row output, never parse supplier source messages."""
    products = []
    for category, rows in sheets.items():
        if category.startswith("_"):
            continue
        numbers = Counter(str(row[0]).strip().lower() for row in rows if row)
        for index, row in enumerate(rows):
            match = re.fullmatch(r"no(\d+)", str(row[0]).strip(), re.I) if row else None
            if not match:
                continue
            block = normalized_block(rows[index:index + 6])
            no = "no" + str(int(match[1]))
            inspection = inspect_source(category, block, category_codes)
            errors = inspection["errors"]
            code, copy = inspection["code"], inspection["copy"]
            if numbers[str(row[0]).strip().lower()] != 1:
                errors.append("同一分頁的 NO 重複")
            if any(re.fullmatch(r"no\d+", r[0], re.I) for r in block[1:]):
                errors.append("商品六列區塊重疊")
            raw_date = block[1][0]
            date_match = re.fullmatch(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", raw_date)
            date = raw_date
            if date_match:
                try:
                    date = datetime(*map(int, date_match.groups())).date().isoformat()
                except ValueError:
                    errors.append("商品日期不合法")
            products.append({
                **inspection,
                "key": f"{category}:{index + 1}:{no}",
                "identity": f"{category}:{no}", "category": category,
                "row": index + 1, "no": no, "number": int(match[1]),
                "code": code, "name": block[0][1], "vendor": block[0][11],
                "date": date, "supplier_code": re.sub(r"^貨號\s*[:：]?\s*", "", block[4][1]),
                "source_hash": digest(block), "block": block,
                "copy": copy, "errors": errors,
            })
    identities = Counter(p["identity"] for p in products)
    for product in products:
        if identities[product["identity"]] > 1 and "同一分頁的 NO 重複" not in product["errors"]:
            product["errors"].append("同一分頁的 NO 重複")
    return products


def sequence_gaps(products):
    groups = defaultdict(set)
    for p in products:
        groups[p["category"]].add(p["number"])
    return [f"{category}：no{a + 1}" + (f"～no{b - 1}" if b > a + 2 else "")
            for category, nums in groups.items()
            for a, b in zip(sorted(nums), sorted(nums)[1:]) if b > a + 1]


def new_batch(name, target, products, actor, selected_keys=None, excluded_reason=""):
    if not name.strip() or not target.strip() or not actor.strip() or not products:
        raise DispatchError("請填寫批次名稱、目標聊天室、核對人並選擇商品")
    if len(products) > 150:
        raise DispatchError("每批最多 150 款，請縮小日期或供應商範圍")
    if len({p["identity"] for p in products}) != len(products):
        raise DispatchError("本批有重複 NO，請先修正來源表")
    chosen = set(selected_keys if selected_keys is not None else (p["key"] for p in products))
    if not chosen or not chosen <= {p["key"] for p in products}:
        raise DispatchError("選取清單已變動，請重新選擇")
    if len(chosen) < len(products) and not excluded_reason.strip():
        raise DispatchError("未選取商品需要填寫本批排除原因")
    items = []
    for order, p in enumerate(products, 1):
        source = deepcopy(p)
        items.append({"id": p["identity"], "source": source, "copy": p["copy"],
                      "images": [], "order": order, "review": None,
                      "excluded": p["key"] not in chosen,
                      "reason": excluded_reason.strip() if p["key"] not in chosen else "",
                      "image_receipts": [], "text_receipts": [], "uncertain": None,
                      "duplicate_note": ""})
    return {"schema": 1, "id": uuid.uuid4().hex, "name": name.strip(),
            "target": canonical_target(target), "actor": actor.strip(), "created_at": now(),
            "status": "draft", "approved_at": None, "approved_digest": None,
            "items": items, "observations": [], "reconciliation": None,
            "audit": [{"at": now(), "actor": actor.strip(), "action": "建立草稿"}]}


def content_digest(item):
    content = {k: item[k] for k in ("source", "copy", "images", "excluded", "reason")}
    if item.get("unit_confirmation"):
        content["unit_confirmation"] = item["unit_confirmation"]
    if item.get("cost_audit"):
        content["cost_audit"] = item["cost_audit"]
    return digest(content)


def batch_digest(batch):
    return digest({"target": batch["target"], "items": [
        {"id": i["id"], "order": i["order"], "content": content_digest(i)}
        for i in batch["items"]]})


def item_errors(item, *, require_review=True, require_source=True):
    if item["excluded"]:
        return [] if item["reason"].strip() else ["排除商品需填寫原因"]
    errors = list(item["source"]["errors"])
    from review_issues import issue_reason
    errors.extend(issue_reason(issue) for issue in item.get("known_issues", []) if issue.get("status") == "open")
    if item["source"].get("cost_audit_required"):
        from cost_audit import blockers
        errors.extend(blockers(item["source"], item.get("cost_audit"), require_source=require_source))
    if item["source"].get("unit_mode") == "legacy" and item["source"].get("copy") and not unit_confirmed(item):
        errors.append("待確認計價單位：裝箱單位不等於已確認的售價單位")
    if not item["images"]:
        errors.append("尚未加入商品圖片")
    if len(item["images"]) > 5:
        errors.append("每款最多 5 張圖片")
    text = item["copy"].strip()
    expected = item["source"]["copy"]
    if expected:
        codes = re.findall(r"BGD-[A-Z]+-\d+", text)
        if codes != [item["source"]["code"]]:
            errors.append("文案品號缺失、重複或與本款不一致")
        for prefix in ("售價", "裝箱"):
            required = [line for line in expected.splitlines() if line.startswith(prefix)]
            actual = [line for line in text.splitlines() if line.startswith(prefix)]
            if not required or actual != required:
                errors.append(f"{prefix}需與雲表一致；要調價或改單位請先修正報價表")
    else:
        errors.append("先處理來源資料，文案尚未能產生；不判定為品號錯誤")
    if re.search(r"進價|到手成本|預估成本|大陸運費|國際運費|內陸運費|外箱尺寸|木架|木框|計費重量|箱重|單個重量", text):
        errors.append("文案含內部成本、重量、外箱或木架資訊")
    if len(text) > 4500:
        errors.append("文案過長，請縮短至 4500 字以內")
    review = item.get("review")
    if require_review and (not review or review["digest"] != content_digest(item)):
        errors.append("圖片、文案與來源尚未逐款核對")
    return list(dict.fromkeys(errors))


def edit_item(batch, item_id, *, text, images, excluded, reason, reviewed, actor, duplicate_note="",
              confirmed_unit=None, unit_evidence="", cost_audit=None):
    result = deepcopy(batch)
    if result["status"] != "draft":
        raise DispatchError("已確認批次內容已鎖定，請建立新草稿後重新核對")
    if not actor.strip():
        raise DispatchError("請填寫核對人")
    item = next(i for i in result["items"] if i["id"] == item_id)
    item.update(copy=text.strip(), images=list(dict.fromkeys(images)), excluded=bool(excluded),
                reason=reason.strip(), duplicate_note=duplicate_note.strip(), review=None)
    if cost_audit is not None:
        item["cost_audit"] = deepcopy(cost_audit)
    if confirmed_unit is not None:
        if confirmed_unit:
            if confirmed_unit != item["source"].get("unit") or not unit_evidence.strip():
                raise DispatchError("請確認售價與裝箱採用相同單位並填寫確認依據；不同時先修正原報價表")
            item["unit_confirmation"] = {"unit": confirmed_unit, "source_hash": item["source"]["source_hash"],
                                          "actor": actor.strip(), "at": now(), "evidence": unit_evidence.strip()}
        else:
            item.pop("unit_confirmation", None)
    if reviewed and not excluded:
        item["review"] = {"digest": content_digest(item), "actor": actor.strip(), "at": now()}
        problems = item_errors(item)
        if problems:
            raise DispatchError("；".join(problems))
    if excluded and not reason.strip():
        raise DispatchError("請填寫暫緩／排除原因")
    result["audit"].append({"at": now(), "actor": actor.strip(), "action": "核對商品" if reviewed else "修改商品", "item": item_id})
    return result


def source_changes(batch, fresh_products):
    by_identity = defaultdict(list)
    for p in fresh_products:
        by_identity[p["identity"]].append(p)
    changes = []
    for item in batch["items"]:
        if item["excluded"]:
            continue
        source = item["source"]
        matches = by_identity[item["id"]]
        if len(matches) != 1:
            changes.append(f"{source['code'] or item['id']}：來源已刪除或 NO 重複")
        elif matches[0]["source_hash"] != source["source_hash"] or matches[0]["row"] != source["row"]:
            changes.append(f"{source['code'] or item['id']}：來源資料或列位置已變更")
    return changes


def prior_activity(batch, item, history):
    hits = []
    for old in history:
        if old["id"] == batch["id"] or not same_target(old["target"], batch["target"]):
            continue
        for previous in old["items"]:
            if previous["id"] != item["id"] or previous["excluded"]:
                continue
            if old["status"] != "draft":
                hits.append(f"{old['name']}（{item_status(previous)}）")
    return hits


def approve_batch(batch, fresh_products, history, actor, *, batch_review=False,
                  acknowledge_missing_source=False):
    result = deepcopy(batch)
    if result["status"] != "draft" or not actor.strip():
        raise DispatchError("批次狀態不允許確認，或缺少核對人")
    if not any(not i["excluded"] for i in result["items"]):
        raise DispatchError("本批沒有待發商品")
    errors = source_changes(result, fresh_products)
    for item in result["items"]:
        errors += [f"{item['source']['code'] or item['id']}：{e}" for e in item_errors(
            item, require_review=not batch_review, require_source=not batch_review)]
        if not item["excluded"] and prior_activity(result, item, history) and not item["duplicate_note"].strip():
            errors.append(f"{item['source']['code']}：同聊天室已有安排／發送紀錄，請填寫再次安排原因")
    missing_source = [i["id"] for i in result["items"] if not i["excluded"]
                      and i["source"].get("cost_audit_required")
                      and not (i.get("cost_audit") or {}).get("source_ready")]
    if batch_review and missing_source and not acknowledge_missing_source:
        errors.append("本批有缺廠商原文的商品，請先確認已知悉此限制；表內重算不代表原文正確")
    if errors:
        raise DispatchError("\n".join(errors))
    result.update(status="approved", approved_at=now(), approved_digest=batch_digest(result))
    if batch_review:
        # One explicit batch decision, never manufacture individual reviews or
        # change the original-evidence status to make the gate pass.
        result["batch_confirmation"] = {"actor": actor.strip(), "at": result["approved_at"],
                                         "digest": result["approved_digest"],
                                         "missing_source_ids": missing_source}
    result["audit"].append({"at": now(), "actor": actor.strip(), "action": "確認本批內容"})
    return result


def item_status(item):
    if item["excluded"]:
        return "已排除"
    if item.get("uncertain"):
        return "結果待確認"
    if len(item["image_receipts"]) > 1 or len(item["text_receipts"]) > 1:
        return "重複紀錄待處理"
    if item["image_receipts"] and item["text_receipts"]:
        return "已確認完成"
    if item["image_receipts"] or item["text_receipts"]:
        return "部分完成"
    return "待發"


def record_observation(batch, *, item_id, part, evidence, actor, target, action_id=None):
    """Record an operator's observation, never infer a LINE delivery receipt."""
    result = deepcopy(batch)
    if result["status"] not in ("approved", "in_progress") or batch_digest(result) != result["approved_digest"]:
        raise DispatchError("批次尚未確認、已完成或內容遭變更")
    if target.strip() != result["target"] or not actor.strip() or not evidence.strip():
        raise DispatchError("請確認正確聊天室，並填寫核對人與 LINE 紀錄時間／核對依據")
    if part not in ("image", "text", "uncertain", "resolve", "unexpected", "resolve_unexpected"):
        raise DispatchError("未知的核對動作")
    action_id = action_id or uuid.uuid4().hex
    if any(e["id"] == action_id for e in result["observations"]):
        return result
    item = next((i for i in result["items"] if i["id"] == item_id), None)
    if part not in ("unexpected", "resolve_unexpected") and (item is None or item["excluded"]):
        raise DispatchError("商品不在本批待發清單")
    event = {"id": action_id, "item": item_id, "part": part, "evidence": evidence.strip(),
             "actor": actor.strip(), "target": target.strip(), "at": now(), "method": "manual_visual"}
    if part in ("image", "text"):
        if item.get("uncertain"):
            raise DispatchError("先查明並解除結果待確認，再登記圖片或文案")
        receipts = item[part + "_receipts"]
        if receipts:
            raise DispatchError("此部分已有確認紀錄；不要重複登記或再次發送")
        receipts.append(event)
    elif part == "uncertain":
        item["uncertain"] = event
    elif part == "resolve":
        if not item.get("uncertain"):
            raise DispatchError("本款沒有待釐清的結果")
        item["uncertain"] = None
    elif part == "resolve_unexpected":
        problems = [e for e in result["observations"] if e["part"] == "unexpected" and e["id"] == item_id]
        resolved = [e for e in result["observations"] if e["part"] == "resolve_unexpected" and e["item"] == item_id]
        if not problems or resolved:
            raise DispatchError("找不到未處理的異常紀錄")
    result["observations"].append(event)
    result["status"] = "in_progress"
    result["reconciliation"] = None
    return result


def record_observation_batch(batch, *, item_ids, evidence, actor, target, action_id=None):
    """Fill only missing image/text observations for explicitly reviewed active items."""
    result = deepcopy(batch)
    requested = list(dict.fromkeys(item_ids))
    if not requested:
        raise DispatchError("沒有可補登的商品")
    active = {i["id"]: i for i in result["items"] if not i["excluded"]}
    if any(item_id not in active for item_id in requested):
        raise DispatchError("批次補登只能套用到本批未排除商品")
    base_id = action_id or uuid.uuid4().hex
    changed = []
    for item_id in requested:
        item = next(i for i in result["items"] if i["id"] == item_id)
        if item.get("uncertain"):
            raise DispatchError("有商品仍是結果待確認，請先逐款查明")
        for part in ("image", "text"):
            if not item[part + "_receipts"]:
                result = record_observation(
                    result, item_id=item_id, part=part, evidence=evidence,
                    actor=actor, target=target,
                    action_id=f"{base_id}:{item_id}:{part}",
                )
                changed.append(f"{item_id}:{part}")
    if not changed:
        raise DispatchError("所選商品的圖片與文案均已有核對紀錄")
    result["audit"].append({
        "at": now(), "actor": actor.strip(), "action": "事後批次補登已核對 LINE 圖文",
        "items": requested, "filled": changed, "evidence": evidence.strip(),
    })
    return result


def reconciliation(batch):
    return reconcile_batch(batch, item_status, batch_digest)


def finish_batch(batch, actor, evidence):
    report = reconciliation(batch)
    if not report["can_finish"] or not evidence.strip() or not actor.strip():
        raise DispatchError("尚有未完成或異常項目，或缺少逐款對帳依據")
    result = deepcopy(batch)
    result["status"] = "completed"
    result["reconciliation"] = {"at": now(), "actor": actor.strip(), "evidence": evidence.strip(), "report": report}
    return result
