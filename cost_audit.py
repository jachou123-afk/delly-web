"""Read-only Decimal cost verifier, independent of the quote formula builder.

Rules are intentionally explicit and versioned. No formula evaluation, source
parsing model, current sidebar settings, or writes are used in this module.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
import hashlib
import json
import re
import unicodedata
from supplier_names import normalize_vendor

RULE_VERSION = "cost-check-v1"
UNITS = {"個", "盒", "套", "瓶", "罐", "包", "袋"}
INPUT_LABELS = {"price": "進價（RMB／計價單位）", "qty": "每箱數量", "unit": "計價／裝箱單位",
                "carton_kg": "整箱毛重（kg）", "unit_g": "單位重量（g）",
                "dom_rate": "內陸費率（RMB/kg）", "intl_rate": "國際費率（RMB/kg）",
                "ex_rate": "匯率（TWD/RMB）"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def block(rows):
    result = [[str(v).strip() for v in list(row)[:12]] for row in list(rows)[:6]]
    result += [[] for _ in range(6 - len(result))]
    return [row + [""] * (12 - len(row)) for row in result]


def number(value):
    raw = unicodedata.normalize("NFKC", str(value).strip())
    if isinstance(value, bool) or len(raw) > 40 or not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", raw):
        raise ValueError("缺少有效非負數字")
    result = Decimal(raw.replace(",", ""))
    if not result.is_finite():
        raise ValueError("數字不合法")
    return result


def fmt(value):
    return format(value, "f").rstrip("0").rstrip(".") if "." in format(value, "f") else str(value)


def free_shipping(vendor):
    return normalize_vendor(vendor) == "v多品村"


def calculate(inputs, vendor):
    """Independently implement the existing, not newly invented, cost policy."""
    values = {key: number(inputs.get(key, "")) for key in INPUT_LABELS if key != "unit"}
    if inputs.get("unit") not in UNITS:
        raise ValueError("計價／裝箱單位未確認")
    price, qty, carton, unit = (values[k] for k in ("price", "qty", "carton_kg", "unit_g"))
    if price <= 0 or qty <= 0 or qty != qty.to_integral_value() or values["ex_rate"] <= 0:
        raise ValueError("進價、整數裝箱量、匯率必須大於零")
    candidates = [w for w in (carton * 1000 / qty, unit) if w > 0]
    if not candidates:
        raise ValueError("沒有原始重量，不能由計費重量倒推")
    if len(candidates) == 2 and (max(candidates) - min(candidates)) / max(candidates) >= Decimal("0.2"):
        raise ValueError("整箱換算重量與單重差異達 20%，需先確認來源")
    basis = max(candidates)
    weight = (basis * Decimal("1.05")).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    domestic = Decimal(0) if free_shipping(vendor) else (weight / 1000 * values["dom_rate"]).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    international = (weight / 1000 * values["intl_rate"]).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    cost = ((price + domestic + international) * values["ex_rate"]).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    quote = (cost / Decimal("0.9")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    sale = quote.to_integral_value(rounding=ROUND_CEILING)
    weight_basis = f"{fmt(carton)} kg ÷ {fmt(qty)} × 1000" if carton > 0 else f"{fmt(unit)} g"
    if carton > 0 and unit > 0:
        weight_basis = f"取較大值（{weight_basis}，{fmt(unit)} g）"
    return {
        "weight": (weight, f"{weight_basis} × 1.05 → 向上取 2 位"),
        "domestic": (domestic, "多品村：沿用廣州包郵規則 = 0" if free_shipping(vendor) else f"{fmt(weight)} ÷ 1000 × {fmt(values['dom_rate'])} → 向上取 2 位"),
        "international": (international, f"{fmt(weight)} ÷ 1000 × {fmt(values['intl_rate'])} → 向上取 2 位"),
        "cost": (cost, f"（{fmt(price)} + {fmt(domestic)} + {fmt(international)}）× {fmt(values['ex_rate'])} → 四捨五入 1 位"),
        "quote": (quote, f"{fmt(cost)} ÷ 0.9 → 四捨五入 1 位（毛利率 10%，非加價 10%）"),
        "sale": (sale, f"{fmt(quote)} → 無條件進位整數"),
    }


def legacy_inputs(source, formulas):
    """Recover only explicit source notes/formula literals, never reverse costs.

    This is a convenience proposal, not evidence that historical inputs were
    correct. Unknown syntax stays blank. No supplier prose is re-parsed here.
    """
    saved = block(source.get("block", []))
    formulas = block(formulas)
    result = dict.fromkeys(INPUT_LABELS, "")
    result["price"] = saved[1][6]
    carton = re.fullmatch(r"裝箱\s*(\d+)\s*([^\s/]+)\s*/箱", saved[2][1])
    if carton:
        result.update(qty=carton[1], unit=carton[2])
    note = unicodedata.normalize("NFKC", saved[3][1])
    unit = re.fullmatch(r"單個重量\s*(\d+(?:\.\d+)?)\s*g", note, re.I)
    carton = re.fullmatch(r"整箱毛重\s*(\d+(?:\.\d+)?)\s*KG", note, re.I)
    both = re.fullmatch(r"整箱毛重\s*(\d+(?:\.\d+)?)KG[/／]單個重量\s*(\d+(?:\.\d+)?)g", note, re.I)
    if unit:
        result.update(unit_g=unit[1], carton_kg="0")
    elif carton:
        result.update(carton_kg=carton[1], unit_g="0")
    elif both:
        result.update(carton_kg=both[1], unit_g=both[2])
    for key, column in (("dom_rate", 8), ("intl_rate", 9)):
        rates = re.findall(r"ROUNDUP\(\(?H\$?\d+/1000\)?\*(\d+(?:\.\d+)?),2\)", formulas[1][column].upper().replace(" ", ""))
        if len(rates) == 1:
            result[key] = rates[0]
    rates = re.findall(r"ROUND\(\(G\$?\d+\+I\$?\d+\+J\$?\d+\)\*(\d+(?:\.\d+)?),1\)", formulas[1][10].upper().replace(" ", ""))
    if len(rates) == 1:
        result["ex_rate"] = rates[0]
    if free_shipping(source.get("vendor", "")):
        result["dom_rate"] = "0"
    return result


def make_evidence(source, formulas, raw_source, inputs, *, notes, origin, parsed=None):
    if not raw_source.strip() or not notes.strip():
        raise ValueError("請保留廠商完整原文並填寫核對依據／費用處理說明")
    if len(raw_source) > 50000 or len(notes) > 5000:
        raise ValueError("原文或說明過長，請縮小至本款完整內容")
    clean = {key: str(inputs.get(key, "")).strip() for key in INPUT_LABELS}
    calculate(clean, source["vendor"])
    return {"schema": 1, "rule": RULE_VERSION, "identity": source["identity"],
            "source_hash": source["source_hash"], "formula_hash": fingerprint(block(formulas)),
            "raw_source": raw_source, "inputs": clean, "notes": notes.strip(),
            "origin": origin, "parsed": deepcopy(parsed or {}),
            "product": {key: source.get(key, "") for key in ("category", "no", "code", "supplier_code", "name", "vendor")}}


def audit(source, formulas=None, evidence=None):
    saved = block(source.get("block", []))
    formulas = block(formulas or [])
    result = {"rule": RULE_VERSION, "source_hash": source["source_hash"],
              "formula_hash": fingerprint(formulas), "evidence_hash": fingerprint(evidence) if evidence else "",
              "source_ready": False, "math_pass": False, "errors": [], "rows": [], "input_rows": [],
              "raw_source": "", "notes": "", "inputs": legacy_inputs(source, formulas), "origin": "legacy",
              "saved_evidence": deepcopy(evidence) if evidence else None}
    if evidence:
        if (evidence.get("schema") != 1 or evidence.get("rule") != RULE_VERSION
                or evidence.get("identity") != source["identity"]
                or evidence.get("source_hash") != source["source_hash"]
                or evidence.get("formula_hash") != result["formula_hash"]):
            result["errors"].append("保存的依據與這版原表／公式不同，需重新核對並補存依據")
        else:
            result.update(inputs=evidence["inputs"], raw_source=evidence["raw_source"],
                          notes=evidence["notes"], origin=evidence["origin"])
            result["source_ready"] = bool(result["raw_source"].strip() and result["notes"].strip())
    label = "當次保存／人工補登，仍須對照原文" if result["source_ready"] else "原表／公式字面值候選，未對照廠商原文"
    parsed = evidence.get("parsed", {}) if result["source_ready"] else {}
    aliases = {"carton_kg": "weight", "unit_g": "unit_weight_g", "unit": "qty_unit"}
    result["input_rows"] = [{"項目": title, "最初擷取值": str(parsed.get(aliases.get(key, key), "未保存")) if key not in {"dom_rate", "intl_rate", "ex_rate"} else "操作者當次設定",
                            "驗算使用值": result["inputs"].get(key) or "未保存／無法辨識", "依據": label}
                            for key, title in INPUT_LABELS.items()]
    # Do not accept a formula that happens to show the same result while reading
    # another product, another sheet, or an external source. Never execute it.
    row_number = source.get("row", 1) + 1
    allowed_functions = {"IFERROR", "IF", "OR", "NOT", "ISNUMBER", "ROUND", "ROUNDUP", "MAX"}
    for col in (2, 7, 8, 9, 10):
        formula = formulas[1][col].upper()
        if not formula.startswith("="):
            result["errors"].append(f"原表 {chr(65 + col)} 欄沒有可核對的公式")
            continue
        refs = re.findall(r"(?<![A-Z])\$?([A-Z]{1,3})\$?(\d+)", formula)
        functions = set(re.findall(r"([A-Z_][A-Z_0-9.]*)\s*\(", formula))
        if ("!" in formula or ":" in formula or "[" in formula
                or functions - allowed_functions
                or any(int(r) != row_number or c not in "GHIJK" for c, r in refs)):
            result["errors"].append(f"原表 {chr(65 + col)} 欄含跨商品引用或未支援公式，需人工核對")
    # Check price and carton against source explicitly; consistent calculations
    # alone cannot detect a stale or differently-unitized evidence record.
    try:
        calculated = calculate(result["inputs"], source["vendor"])
        if number(saved[1][6]) != number(result["inputs"]["price"]):
            result["errors"].append("進價與核對依據不同")
        required_carton = f"裝箱 {fmt(number(result['inputs']['qty']))}{result['inputs']['unit']}/箱"
        if re.sub(r"\s+", "", saved[2][1]) != re.sub(r"\s+", "", required_carton):
            result["errors"].append("裝箱數量／單位與核對依據不同")
        if source.get("unit") != result["inputs"]["unit"]:
            result["errors"].append("計價單位與核對依據不同")
    except (ValueError, InvalidOperation, KeyError) as exc:
        calculated = {}
        result["errors"].append(f"完整驗算待補資料：{exc}")
    specs = [("weight", "計費重量（g）", 7), ("domestic", "內陸運費（RMB）", 8),
             ("international", "國際運費（RMB）", 9), ("cost", "到手成本（TWD）", 10),
             ("quote", "10% 報價（TWD）", 2), ("sale", "廣告售價（TWD）", None)]
    for key, title, column in specs:
        actual = source.get("price", "") if column is None else saved[1][column]
        amount, formula = calculated.get(key, (None, "缺少原始參數，不由結果倒推"))
        difference = None
        if amount is not None:
            try:
                difference = number(actual) - amount
            except (ValueError, InvalidOperation):
                pass
        result["rows"].append({"項目": title, "原表／原售價": actual or "缺資料", "獨立算式": formula,
                               "重算結果": fmt(amount) if amount is not None else "無法驗算",
                               "差額（原表−重算）": fmt(difference) if difference is not None else "—",
                               "結果": "一致" if difference == 0 else ("有差異" if difference is not None else "待補資料")})
        if difference != 0 and amount is not None:
            result["errors"].append(f"{title}與獨立驗算不一致或原表缺值")
    result["math_pass"] = bool(calculated and not result["errors"])
    return result


def evidence_status(report):
    """Storage status is deliberately not a human-review or arithmetic result."""
    if report is None:
        return "尚未確認（讀取未完成）"
    if report.get("source_ready"):
        return "原文已保存，待核對"
    if report.get("saved_evidence"):
        return "已保存舊版，需重新核對"
    return "缺廠商原文"


def blockers(source, report, *, require_source=True):
    if not report or report.get("rule") != RULE_VERSION or report.get("source_hash") != source["source_hash"]:
        return ["成本尚未獨立驗算，請開啟本款成本核對"]
    issues = list(report.get("errors", []))
    if not report.get("math_pass"):
        issues.append("成本驗算尚未通過")
    if require_source and not report.get("source_ready"):
        issues.append("缺少本款廠商原文／當次參數依據，不能視為來源已核對")
    return list(dict.fromkeys(issues))
