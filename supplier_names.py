"""Canonical supplier labels; aliases explicitly confirmed by the operator.

Only supplier fields should call this helper. Never normalize raw supplier
messages, quote snapshots, evidence, or historical dispatch records in place.
"""
import re


ALIASES = {
    "非凡": "菲凡",
    "卡樂星": "優娜卡樂星",
    "多品": "多品村",
    "阿汤": "阿湯",
}
DEFAULT_VENDORS = ("", "v菲凡", "v多品村", "v優娜卡樂星")


def normalize_vendor(value):
    """Use one lowercase v, no prefix separator; preserve the business name.

Do not case-fold, fuzzy-match, translate, or strip internal spaces/hyphens.
An uppercase ASCII V attached to an English name may be part of that name.
"""
    text = str(value or "").strip().strip("\u200b\ufeff").strip()
    if not text or text in {"v", "V", "ｖ", "Ｖ"}:
        return ""
    # URL/formula/price-note pollution in old L cells is not a supplier name.
    if re.match(r"(?i)https?://|^[=+@\d]", text) or text in {"價格調降"}:
        return text
    text = re.sub(r"^(?:[vｖ]|[VＶ](?=[\s\-－–—\u3400-\u9fff]))[\s\-－–—]*", "", text).strip()
    if not text:
        return ""
    return "v" + ALIASES.get(text, text)


def vendor_options(existing=""):
    canonical = normalize_vendor(existing)
    options = list(DEFAULT_VENDORS)
    if canonical and canonical not in options:
        options.append(canonical)
    return options, canonical


def vendor_filter_label(value):
    return normalize_vendor(value) or "（未填廠商）"
