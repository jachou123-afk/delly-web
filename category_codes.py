"""One authoritative category-code registry; never infer from the first letter."""
import re

DEFAULT_CATEGORY_CODES = {"G正版": "G", "S生活用品": "S"}
CATEGORY_SHEET = "_分類代碼"
QUOTE_CATEGORIES = ("G正版", "W玩具", "S生活用品", "W娃娃", "D吊飾")


def validate_codes(mapping):
    if not isinstance(mapping, dict) or len(mapping) > 100:
        raise ValueError("分類代碼表格式不正確")
    result = {}
    for category, code in mapping.items():
        if not isinstance(category, str) or not category.strip() or category.startswith("_") or category != category.strip():
            raise ValueError("分類名稱需與商品分頁完全相同，且不可使用內部分頁")
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z]{1,8}", code):
            raise ValueError("廣告分類代碼需為 1～8 個大寫英文字母")
        if code in result.values():
            raise ValueError(f"分類代碼 {code} 重複，可能產生相同廣告品號")
        result[category] = code
    for category, code in DEFAULT_CATEGORY_CODES.items():
        if result.get(category) != code:
            raise ValueError(f"既有分類 {category} 的代碼 {code} 不可改動")
    return result


def add_code(mapping, category, code):
    current = validate_codes(mapping)
    category, code = category.strip(), code.strip().upper()
    if category in current and current[category] != code:
        raise ValueError("已有分類代碼不可直接更改，需另外安排歷史品號遷移")
    return validate_codes({**current, category: code})
