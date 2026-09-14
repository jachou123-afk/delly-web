"""Product-bound image references, separate from human review and send state."""
from collections import Counter
import unicodedata

from dispatch_manager import DispatchError, digest
from supplier_names import normalize_vendor

PRODUCT_IMAGE_SHEET = "_商品圖片對應"


def subject_key(source):
    """Price/date/row changes need not erase a photo; a different product must."""
    fields = [source["category"], source["no"], source["name"],
              source.get("supplier_code", ""), normalize_vendor(source.get("vendor", ""))]
    return digest([unicodedata.normalize("NFKC", str(v)).strip() for v in fields])


def checked_assets(assets):
    from dispatch_storage import asset_bytes, validate_image
    checked = {}
    for asset in assets:
        clean = validate_image(asset_bytes(asset), asset["name"])
        checked[clean["sha256"]] = clean
    if not 1 <= len(checked) <= 5:
        raise DispatchError("每款需有 1～5 張不同的有效原圖")
    return list(checked.values())


def source_problem(source, fresh):
    matches = [p for p in fresh if p["identity"] == source["identity"]]
    if len(matches) != 1:
        return "商品已刪除或 NO 重複，未綁定圖片"
    if matches[0]["source_hash"] != source["source_hash"] or matches[0]["row"] != source["row"]:
        return "商品資料已變動，請重新載入並核對圖片"
    if subject_key(matches[0]) != subject_key(source):
        return "商品身分不一致，未綁定圖片"
    return ""


def unique_proposals(products, proposals, bindings):
    """Only one distinct candidate and no different saved set can auto-bind."""
    counts = Counter(p["identity"] for p in products)
    plan, rows = [], []
    for source in products:
        identity = source["identity"]
        images = {a["sha256"]: a for a in proposals.get(identity, [])}
        saved = bindings.get(identity)
        reason = ""
        if counts[identity] != 1:
            reason = "NO 重複，無法唯一配對"
        elif not images:
            reason = "未找到圖片"
        elif len(images) != 1:
            reason = "有多張候選，請逐款選擇要採用的圖片"
        elif saved and (saved["subject"] != subject_key(source) or saved["assets"] != list(images)):
            reason = "已有不同圖片／商品身分變動，請逐款確認替換"
        else:
            plan.append({"source": source, "assets": list(images.values()),
                         "expected_revision": saved.get("_revision", "") if saved else ""})
        rows.append({"品號": source.get("code") or identity, "商品": source["name"],
                     "圖片數": len(images), "配對結果": reason or "唯一配對，可保存"})
    return plan, rows
