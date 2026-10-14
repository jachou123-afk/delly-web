"""Separate complete one-line quotation fields without losing source clauses.

The caller retains the untouched raw source. Unknown or additional clauses are
not removed: they stay with the conservative general parser for review.
"""
import re


_NUMBER = r"\d+(?:\.\d+)?"
_UNIT = r"個|隻|只|盒|套|雙|件|瓶|罐|包|袋"
_SIZE = rf"{_NUMBER}(?:\s*[-~～]\s*{_NUMBER})?(?:\s*[*xX×]\s*{_NUMBER})*\s*(?:cm|mm|公分|毫米)"
_FIELD = re.compile(
    rf"(?P<size_label>產品尺寸|單個尺寸|彩盒尺寸|外箱尺寸|外箱規格)\s*:?\s*(?P<size>{_SIZE})"
    rf"|(?P<qty_label>裝箱數量|裝箱量|裝箱數|裝箱|每箱數量|一箱)\s*:?\s*(?P<qty>\d+)\s*(?P<qty_unit>{_UNIT})"
    rf"|(?:一|每|單)\s*(?P<price_unit>{_UNIT})\s*(?P<price_label>包郵|價格|價)\s*:?\s*(?P<price>{_NUMBER})\s*元"
    r"|(?P<pack>獨立(?:袋子|袋裝|OPP袋))",
    re.I,
)


def expand_inline_quote(text):
    """Accept only a whole, uniquely labeled field tail after a product name.

    Order and punctuation may vary. Unknown clauses, duplicate fields, multiple
    products and incomplete quotations stay untouched for the normal safeguards.
    Product descriptors such as '2寸' stay in the name, never become box sizes.
    """
    bag = expand_inline_bag_quote(text)
    if bag != text:
        return bag
    if len(text.strip().splitlines()) != 1:
        return text
    first = _FIELD.search(text)
    if not first:
        return text
    name = text[:first.start()].strip(" \t。；;，,")
    if not name or re.search(r"產品尺寸|單個尺寸|彩盒尺寸|外箱尺寸|外箱規格|裝箱|每箱數量|一箱\s*\d", name):
        return text
    remaining = text[first.start():]
    lines, seen = [name], set()
    while remaining:
        match = _FIELD.match(remaining)
        if not match:
            return text
        if match['size_label']:
            key = {'單個尺寸': '產品尺寸', '外箱規格': '外箱尺寸'}.get(match['size_label'], match['size_label'])
            line = f"{key}:{match['size'].strip()}"
        elif match['qty_label']:
            key, line = 'qty', f"裝箱:{match['qty']}{match['qty_unit']}"
        elif match['price_unit']:
            key = 'price'
            delivery = '（包郵）' if match['price_label'] == '包郵' else ''
            line = f"單{match['price_unit']}價格:{match['price']}元{delivery}"
        else:
            key, line = 'pack', f"包裝:{match['pack']}"
        if key in seen:
            return text
        seen.add(key)
        lines.append(line)
        remaining = remaining[match.end():].lstrip(" \t。；;，,")
    if not {'qty', 'price'} <= seen:
        return text
    return '\n'.join(lines)


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
