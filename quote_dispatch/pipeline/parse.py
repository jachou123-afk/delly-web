"""Supplier routing without supplier-specific guesses."""

from quote_dispatch.config import normalize_vendor, supplier_rule


def parser_context(vendor):
    """Return inspectable routing metadata before parsing supplier prose."""
    rule = supplier_rule(vendor)
    return {"vendor": normalize_vendor(vendor), "profile": rule.parser_profile}


def parse_supplier_text(text, vendor, common_parser):
    """Use the common parser unless a checked-in profile is implemented."""
    context = parser_context(vendor)
    if context["profile"] != "common":
        raise ValueError(f"尚未實作廠商解析規則: {context['profile']}")
    return common_parser(text)
