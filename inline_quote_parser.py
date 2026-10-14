"""Recognize a complete, unambiguous one-line bagged-product quotation.

The caller retains the untouched raw source. Unknown or additional clauses are
not removed: they stay with the conservative general parser for review.
"""
import re


def expand_inline_bag_quote(text):
    match = re.fullmatch(
        r"\s*(?P<name>[^\n。；;]+?)[。；;]\s*"
        r"(?P<pack>獨立(?:袋子|袋裝|OPP袋))\s+"
        r"裝箱\s*(?P<qty>\d+)\s*(?P<qty_unit>個|隻|只|盒|套|雙|件)\s+"
        r"(?:一|每)(?P<price_unit>個|隻|只|盒|套|雙|件)\s*包郵\s*"
        r"(?P<price>\d+(?:\.\d+)?)\s*元[。]?\s*",
        text, re.I,
    )
    if not match:
        return text
    return (f"{match['name'].strip()}\n包裝:{match['pack']}（原文包郵）\n"
            f"裝箱:{match['qty']}{match['qty_unit']}\n"
            f"單{match['price_unit']}價格:{match['price']}元")
