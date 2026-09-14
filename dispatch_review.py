"""Inspectable saved-source evidence and human-confirmed legacy unit support.

Never changes a quote worksheet or assumes a carton unit is a pricing unit.
"""
from copy import deepcopy
import re
import unicodedata

from line_ad_copy import (
    LEAD_TIME_LINE, _carton_line_and_unit, _validated_unit, build_bgd_code,
    build_line_ad_copy, ceil_ad_price, validate_sheet_pricing,
)


def inspect_source(category, rows, category_codes=None):
    block = [[str(v).strip() for v in list(row)[:12]] for row in list(rows)[:6]]
    block += [[] for _ in range(6 - len(block))]
    block = [row + [""] * (12 - len(row)) for row in block]
    errors = []
    code = ""
    try:
        code = build_bgd_code(category, block[0][0], category_codes)
    except ValueError as exc:
        errors.append(str(exc))
    try:
        validate_sheet_pricing(block)
    except ValueError as exc:
        errors.append(str(exc))
    details = unicodedata.normalize("NFKC", block[1][1])
    unit_lines = [line.strip() for line in details.splitlines()
                  if re.match(r"^計價單位\s*[：:]", line.strip())]
    unit_values = []
    for line in unit_lines:
        match = re.fullmatch(r"計價單位\s*[：:]\s*([^\s/]+)\s*", line)
        if match:
            unit_values.append(match[1])
    unit = ""
    unit_mode = "explicit" if unit_lines else "legacy"
    if unit_lines:
        if len(unit_values) != len(unit_lines) or len(set(unit_values)) != 1:
            errors.append("計價單位欄位互相矛盾或格式不明，請先修正原報價表")
        else:
            try:
                unit = _validated_unit(unit_values[0])
            except ValueError as exc:
                errors.append(str(exc))
    carton, carton_unit = "", ""
    try:
        carton, carton_unit = _carton_line_and_unit(block[2][1])
    except ValueError as exc:
        errors.append(str(exc))
    if unit and carton_unit and unit != carton_unit:
        errors.append("計價單位與裝箱單位不同，需先換算並修正報價表；不直接套用")
    candidate = unit if unit_mode == "explicit" else carton_unit
    copy = ""
    if not errors and candidate:
        try:
            copy = build_line_ad_copy(name=block[0][1], category_name=category,
                                      no_value=block[0][0], quote_10=block[1][2], unit=candidate,
                                      details=block[1][1], carton_text=block[2][1], category_codes=category_codes)
        except ValueError as exc:
            errors.append(str(exc))
    return {"review_version": 2, "block": block, "code": code, "copy": copy,
            "errors": errors, "quote_10": block[1][2],
            "price": str(ceil_ad_price(block[1][2])) if copy else "",
            "details": block[1][1], "carton": block[2][1], "carton_line": carton,
            "unit": candidate, "unit_mode": unit_mode, "unit_raw": "\n".join(unit_lines),
            "unit_evidence": "\n".join(unit_lines) if unit_lines else block[2][1],
            "lead_time": LEAD_TIME_LINE}


def unit_confirmed(item):
    source = item["source"]
    if source.get("unit_mode") != "legacy":
        return True
    confirmation = item.get("unit_confirmation") or {}
    return bool(source.get("unit") and confirmation.get("unit") == source["unit"]
                and confirmation.get("source_hash") == source["source_hash"]
                and confirmation.get("actor") and confirmation.get("evidence"))


def hydrate_draft(batch, fresh):
    """Upgrade same-source draft evidence in memory only. Never touches approved data."""
    result = deepcopy(batch)
    if result["status"] != "draft":
        return result
    for item in result["items"]:
        old = item["source"]
        matches = [p for p in fresh if p["identity"] == item["id"]]
        if len(matches) != 1:
            continue
        current = matches[0]
        if current["source_hash"] != old["source_hash"] or current["row"] != old["row"]:
            continue
        if item["copy"] == old.get("copy", ""):
            item["copy"] = current["copy"]
        item["source"] = deepcopy(current)
    return result


def comparison_rows(item, text=None):
    source = item["source"]
    text = item["copy"] if text is None else text
    lines = text.splitlines()
    def prefixed(prefix):
        return "\n".join(line for line in lines if line.startswith(prefix)) or "尚未產生"
    codes = re.findall(r"BGD-[A-Z]+-\d+", text)
    source_code = source["code"] or source["no"]
    expected_price = f"售價{source.get('price', '')}元/{source.get('unit', '')}"
    unit_note = ("原表明示，仍需目視核對" if source.get("unit") else "原表單位待修正") if source.get("unit_mode") == "explicit" else (
        "已人工確認" if unit_confirmed(item) else "僅裝箱單位，須確認計價是否相同")
    return [
        {"核對項目": "品號", "報價表／依據": source_code, "準備發出的內容": "、".join(codes) or "尚未產生",
         "檢查結果": "一致" if codes == [source["code"]] else "待處理"},
        {"核對項目": "品名", "報價表／依據": source["name"],
         "準備發出的內容": next((line for line in lines if line and line != "正版授權"), "尚未產生"),
         "檢查結果": "人工確認款式／規格"},
        {"核對項目": "售價", "報價表／依據": f"10% 報價 {source.get('quote_10', '未讀取')} → 無條件進位 {source.get('price', '待確認')}",
         "準備發出的內容": prefixed("售價"), "檢查結果": "一致" if prefixed("售價") == expected_price and source.get("price") else "待處理"},
        {"核對項目": "計價單位", "報價表／依據": source.get("unit_evidence") or "未讀取",
         "準備發出的內容": f"每{source.get('unit') or '？'}計價", "檢查結果": unit_note},
        {"核對項目": "裝箱", "報價表／依據": source.get("carton", "未讀取"),
         "準備發出的內容": prefixed("裝箱"), "檢查結果": "一致" if prefixed("裝箱") == source.get("carton_line") else "待處理"},
        {"核對項目": "交期", "報價表／依據": "現行廣告預設，非供應商原文的保證", "準備發出的內容": prefixed("交貨"),
         "檢查結果": "請對照廠商最新交期"},
    ]
