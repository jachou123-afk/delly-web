"""Strict, read-only loader for shared and supplier-specific dispatch rules.

The ``.yaml`` files use JSON syntax, which is valid YAML 1.2.  Keeping the
subset deliberately small lets Streamlit load the rules with Python's standard
library and avoids a second configuration parser becoming a deployment risk.
"""

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re


RULES_DIR = Path(__file__).with_name("rules")


class RuleConfigError(ValueError):
    """The checked-in rules are incomplete, ambiguous, or malformed."""


@dataclass(frozen=True)
class CommonRules:
    schema_version: int
    vendor_prefix: str
    default_vendors: tuple[str, ...]
    vendor_aliases: dict[str, str]
    lead_time_line: str
    price_units: frozenset[str]
    blocked_terms: tuple[str, ...]
    advertising_target: str
    target_aliases: dict[str, str]


@dataclass(frozen=True)
class SupplierRules:
    canonical_name: str
    aliases: tuple[str, ...]
    domestic_shipping: str
    domestic_shipping_note: str
    parser_profile: str


@dataclass(frozen=True)
class RuleRegistry:
    common: CommonRules
    suppliers: dict[str, SupplierRules]
    supplier_aliases: dict[str, str]


def _read_mapping(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuleConfigError(f"無法讀取規則 {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuleConfigError(f"規則 {path.name} 必須是物件")
    return value


def _required(mapping, key, expected, source):
    value = mapping.get(key)
    if not isinstance(value, expected):
        raise RuleConfigError(f"{source} 缺少或寫錯 {key}")
    return value


def _string_tuple(value, source):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RuleConfigError(f"{source} 必須是文字清單")
    return tuple(value)


def _load_common(rules_dir):
    raw = _read_mapping(rules_dir / "common.yaml")
    advertising = _required(raw, "advertising", dict, "common.yaml")
    targets = _required(raw, "targets", dict, "common.yaml")
    aliases = _required(raw, "vendor_aliases", dict, "common.yaml")
    target_aliases = _required(targets, "aliases", dict, "common.yaml targets")
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in aliases.items()):
        raise RuleConfigError("common.yaml vendor_aliases 必須是文字對文字")
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in target_aliases.items()):
        raise RuleConfigError("common.yaml targets.aliases 必須是文字對文字")
    schema_version = _required(raw, "schema_version", int, "common.yaml")
    if schema_version != 1:
        raise RuleConfigError(f"不支援的規則版本: {schema_version}")
    prefix = _required(raw, "vendor_prefix", str, "common.yaml").strip()
    lead_time = _required(advertising, "lead_time_line", str, "common.yaml advertising").strip()
    target = _required(targets, "advertising", str, "common.yaml targets").strip()
    default_vendors = _string_tuple(raw.get("default_vendors"), "common.yaml default_vendors")
    units = _string_tuple(advertising.get("price_units"), "common.yaml advertising.price_units")
    blocked = _string_tuple(advertising.get("blocked_terms"), "common.yaml advertising.blocked_terms")
    if not prefix or not lead_time or not target or not units:
        raise RuleConfigError("common.yaml 的前綴、交期、群組或售價單位不可為空")
    return CommonRules(
        schema_version=schema_version,
        vendor_prefix=prefix,
        default_vendors=default_vendors,
        vendor_aliases=dict(aliases),
        lead_time_line=lead_time,
        price_units=frozenset(units),
        blocked_terms=blocked,
        advertising_target=target,
        target_aliases=dict(target_aliases),
    )


def _load_supplier(path):
    raw = _read_mapping(path)
    pricing = _required(raw, "pricing", dict, path.name)
    parser = _required(raw, "parser", dict, path.name)
    canonical = _required(raw, "canonical_name", str, path.name).strip()
    aliases = _string_tuple(raw.get("aliases"), f"{path.name} aliases")
    shipping = _required(pricing, "domestic_shipping", str, path.name).strip()
    note = _required(pricing, "domestic_shipping_note", str, path.name).strip()
    profile = _required(parser, "profile", str, path.name).strip()
    if not canonical.startswith("v") or shipping not in {"free", "calculated"} or not profile:
        raise RuleConfigError(f"{path.name} 的廠商名稱、內陸運費或解析設定無效")
    if shipping == "free" and not note:
        raise RuleConfigError(f"{path.name} 設為包郵時必須寫明備註")
    if shipping == "calculated" and note:
        raise RuleConfigError(f"{path.name} 非包郵廠商不可帶包郵備註")
    return SupplierRules(canonical, aliases, shipping, note, profile)


def _load_rules(rules_dir):
    rules_dir = Path(rules_dir)
    common = _load_common(rules_dir)
    suppliers = {}
    supplier_aliases = {}
    paths = sorted((rules_dir / "suppliers").glob("*.yaml"))
    if not paths:
        raise RuleConfigError("沒有任何廠商規則")
    for path in paths:
        rule = _load_supplier(path)
        if rule.canonical_name in suppliers:
            raise RuleConfigError(f"廠商規則重複: {rule.canonical_name}")
        suppliers[rule.canonical_name] = rule
        bare = rule.canonical_name[1:]
        for alias in (bare, *rule.aliases):
            alias = alias.strip()
            previous = supplier_aliases.get(alias)
            if previous and previous != rule.canonical_name:
                raise RuleConfigError(f"廠商別名重複: {alias}")
            supplier_aliases[alias] = rule.canonical_name
    for alias, bare in common.vendor_aliases.items():
        canonical = supplier_aliases.get(bare, common.vendor_prefix + bare)
        previous = supplier_aliases.get(alias)
        if previous and previous != canonical:
            raise RuleConfigError(f"共同廠商別名衝突: {alias}")
        supplier_aliases[alias] = canonical
    missing = [name for name in common.default_vendors if name and name not in suppliers]
    if missing:
        raise RuleConfigError("預設廠商缺少獨立規則: " + "、".join(missing))
    return RuleRegistry(common, suppliers, supplier_aliases)


@lru_cache(maxsize=1)
def load_rules():
    return _load_rules(RULES_DIR)


def get_common_rules():
    return load_rules().common


def normalize_vendor(value):
    """Use one lowercase v and only exact, operator-confirmed aliases."""
    common = get_common_rules()
    text = str(value or "").strip().strip("\u200b\ufeff").strip()
    if not text or text in {"v", "V", "ｖ", "Ｖ"}:
        return ""
    if re.match(r"(?i)https?://|^[=+@\d]", text) or text in {"價格調降"}:
        return text
    text = re.sub(r"^(?:[vｖ]|[VＶ](?=[\s\-－–—\u3400-\u9fff]))[\s\-－–—]*", "", text).strip()
    if not text:
        return ""
    return load_rules().supplier_aliases.get(text, common.vendor_prefix + text)


def supplier_rule(value):
    canonical = normalize_vendor(value)
    configured = load_rules().suppliers.get(canonical)
    if configured:
        return configured
    return SupplierRules(canonical, (), "calculated", "", "common")


def vendor_options(existing=""):
    canonical = normalize_vendor(existing)
    options = list(get_common_rules().default_vendors)
    if canonical and canonical not in options:
        options.append(canonical)
    return options, canonical


def vendor_filter_label(value):
    return normalize_vendor(value) or "（未填廠商）"


def advertising_target():
    return get_common_rules().advertising_target


def canonical_target(name):
    text = str(name or "").strip()
    return get_common_rules().target_aliases.get(text, text)


def same_target(left, right):
    left = str(left or "").strip()
    right = str(right or "").strip()
    return bool(left and right) and canonical_target(left) == canonical_target(right)
