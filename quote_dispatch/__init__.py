"""Shared rules and stages for quote-to-LINE dispatch."""

from .config import (
    RuleConfigError,
    advertising_target,
    canonical_target,
    get_common_rules,
    load_rules,
    normalize_vendor,
    same_target,
    supplier_rule,
    vendor_filter_label,
    vendor_options,
)

__all__ = [
    "RuleConfigError",
    "advertising_target",
    "canonical_target",
    "get_common_rules",
    "load_rules",
    "normalize_vendor",
    "same_target",
    "supplier_rule",
    "vendor_filter_label",
    "vendor_options",
]
