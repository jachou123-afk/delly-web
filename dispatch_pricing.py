"""Batch-only advertising prices; never mutate quotation/source evidence."""
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING, localcontext

from line_ad_copy import _sheet_number, _validated_unit

LEGACY = "sheet_10"
TIERED = "cost_tier_13_18_v1"
POLICY_LABELS = {
    LEGACY: "沿用原表 10% 報價",
    TIERED: "成本滿 40 元採 13%；未滿 40 元採 18%",
}


def validate_policy(policy):
    if not isinstance(policy, str) or policy not in POLICY_LABELS:
        raise ValueError("不支援的批次定價規則，請重新選擇")
    return policy


def tier_price(raw_cost):
    """Decimal input and direct ceiling; no rounded quote or binary float."""
    cost = _sheet_number(raw_cost, "分級定價成本")
    margin = 13 if cost >= Decimal("40") else 18
    divisor = Decimal("0.87") if margin == 13 else Decimal("0.82")
    with localcontext() as context:
        context.prec = max(28, len(cost.as_tuple().digits) + abs(cost.as_tuple().exponent) + 12)
        price = int((cost / divisor).to_integral_value(rounding=ROUND_CEILING))
        if not Decimal(price - 1) * divisor < cost <= Decimal(price) * divisor:
            raise ValueError("分級售價進位驗證失敗")
    return cost, margin, price


def pricing_snapshot(source):
    try:
        raw_cost = source["block"][1][10]
        source_hash = source["source_hash"]
    except (KeyError, IndexError, TypeError):
        raise ValueError("分級定價缺少保存的來源成本／版本") from None
    if not isinstance(source_hash, str) or not source_hash:
        raise ValueError("分級定價缺少保存的來源版本")
    cost, margin, price = tier_price(raw_cost)
    return {"policy": TIERED, "source_hash": source_hash, "cost": str(cost),
            "margin_percent": margin, "price": price,
            "unit": _validated_unit(source.get("unit", ""))}


def pricing_errors(item):
    if "pricing" not in item:
        return []
    try:
        saved = item["pricing"]
        if not isinstance(saved, dict) or saved.get("policy") != TIERED:
            raise ValueError("商品的批次定價規則無效")
        # Exact shape prevents a stale/hand-edited override from becoming authority.
        expected = pricing_snapshot(item["source"])
        if (saved != expected or set(saved) != set(expected)
                or any(type(saved[k]) is not type(v) for k, v in expected.items())):
            raise ValueError("分級售價與來源成本／版本不一致，請重新套用規則並驗算")
    except (ValueError, TypeError, KeyError) as exc:
        return [str(exc)]
    return []


def batch_pricing_errors(batch):
    try:
        policy = validate_policy(batch.get("pricing_policy", LEGACY))
    except ValueError as exc:
        return [str(exc)]
    issues = []
    for item in batch["items"]:
        if item["excluded"]:
            continue
        has_pricing = "pricing" in item
        if (policy == TIERED) != has_pricing:
            issues.append(f"{item['source'].get('code') or item['id']}：商品與本批定價規則不同，請重新套用")
        issues.extend(pricing_errors(item))
    return list(dict.fromkeys(issues))


def price_line(item):
    errors = pricing_errors(item)
    if errors:
        raise ValueError("；".join(errors))
    if "pricing" in item:
        record = item["pricing"]
        return f"售價{record['price']}元/{record['unit']}"
    lines = [line for line in item["source"].get("copy", "").splitlines() if line.startswith("售價")]
    if len(lines) != 1:
        raise ValueError("來源售價尚未產生或不唯一")
    return lines[0]


def replace_price(text, replacement):
    lines = text.splitlines()
    positions = [n for n, line in enumerate(lines) if line.startswith("售價")]
    if len(positions) != 1:
        raise ValueError("文案售價缺失或不唯一，請先處理文案")
    lines[positions[0]] = replacement
    return "\n".join(lines)


def expected_copy(item):
    original = item["source"].get("copy", "")
    return replace_price(original, price_line(item)) if "pricing" in item else original


def apply_item_pricing(item, policy):
    """Return a draft item with only its price line and pricing record changed."""
    validate_policy(policy)
    result = deepcopy(item)
    if policy == TIERED:
        result["pricing"] = pricing_snapshot(result["source"])
    else:
        result.pop("pricing", None)
    result["copy"] = replace_price(result["copy"], price_line(result))
    result["review"] = None
    return result


def pricing_check(item, report):
    """Compare the batch price with independently audited source cost."""
    errors = pricing_errors(item)
    saved = item.get("pricing") or {}
    computed = {}
    if not errors:
        if not report or not report.get("math_pass") or report.get("source_hash") != item["source"]["source_hash"]:
            errors.append("分級售價須先完成本款來源成本驗算")
        else:
            rows = [row for row in report.get("rows", []) if row.get("項目") == "到手成本（TWD）"]
            try:
                if len(rows) != 1 or rows[0].get("結果") != "一致":
                    raise ValueError("分級售價缺少一致的獨立重算成本")
                cost, margin, price = tier_price(rows[0]["重算結果"])
                computed = {"cost": str(cost), "margin_percent": margin, "price": price}
                if (cost != _sheet_number(saved.get("cost"), "保存成本")
                        or margin != saved.get("margin_percent") or price != saved.get("price")):
                    raise ValueError("分級售價與獨立重算成本不一致")
                actual = [line for line in item["copy"].splitlines() if line.startswith("售價")]
                if actual != [price_line(item)]:
                    raise ValueError("文案售價與本批分級定價不一致")
            except (ValueError, KeyError, TypeError) as exc:
                errors.append(str(exc))
    return {"policy": TIERED, **computed, "errors": errors, "pass": not errors}
