"""Prepare catalog draft templates with the existing delly-web LINE formatter."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys


CATALOG_ASSIGNMENT = "window.catalogProducts = "
PRODUCT_ID = re.compile(r"p-[0-9a-f]{24}")
PRODUCT_CODE = re.compile(r"BGD-([A-Z]{1,8})-([0-9]+)")
CATALOG_TABS = {
    "G正版": "G",
    "W玩具": "W",
    "S生活用品": "S",
    "W娃娃": "WA",
    "D吊飾": "D",
    "F方小姐": "F",
    "富酷": "FK",
}
CATALOG_NAME_MARKER = re.compile(r"^【(正版・帶雷標|正版|帶雷標)】\s*")
LASER_TEXT = re.compile(r"(?:雷[射]?|鐳射|镭射)(?:標|标)")
SPEC_LABELS = {
    "商品尺寸": "尺寸",
    "產品尺寸": "尺寸",
    "單盒彩盒尺寸": "彩盒尺寸",
    "單品包裝尺寸": "包裝尺寸",
    "外包裝尺寸": "包裝尺寸",
}


def load_catalog(path):
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text.startswith(CATALOG_ASSIGNMENT) or not text.endswith(";"):
        raise ValueError("目錄資料格式不符")
    products = json.loads(text[len(CATALOG_ASSIGNMENT):-1])
    if not isinstance(products, list):
        raise ValueError("目錄商品需要清單")
    return products


def catalog_details(specs, laser):
    if not isinstance(specs, list):
        raise ValueError("商品規格需要清單")
    details = []
    for spec in specs:
        if not isinstance(spec, dict):
            raise ValueError("商品規格缺少標籤或內容")
        label = spec.get("label")
        value = spec.get("value")
        if not isinstance(label, str) or not isinstance(value, str) or not label.strip() or not value.strip():
            raise ValueError("商品規格缺少標籤或內容")
        label, value = label.strip(), value.strip()
        if "\n" in label or "\r" in label or "\n" in value or "\r" in value:
            raise ValueError("目錄規格需要單行文字")
        label = SPEC_LABELS.get(label, label)
        separator = " " if label.endswith("尺寸") else ":"
        details.append(f"{label}{separator}{value}")
    if laser and not any(LASER_TEXT.search(line) for line in details):
        details.insert(0, "帶鐳射標")
    return details


def build_template(product, formatter, categories):
    code = product.get("code")
    unit = product.get("unit")
    pack = product.get("pack")
    name = product.get("name")
    match = PRODUCT_CODE.fullmatch(code) if isinstance(code, str) else None
    if not match or match[1] not in categories:
        raise ValueError("商品編號無法對應分頁")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("商品名稱缺失")
    if not isinstance(unit, str) or type(pack) is not int or pack < 1:
        raise ValueError("計價單位或箱入數缺失")
    marker = CATALOG_NAME_MARKER.match(name)
    license_label = marker[1] if marker else ""
    if marker:
        name = name[marker.end():]
    licensed = license_label.startswith("正版")
    details = catalog_details(product.get("specs", []), "帶雷標" in license_label)
    if licensed:
        details.insert(0, "正版授權")

    copy = formatter.build_line_ad_copy(
        name=name,
        category_name=categories[match[1]],
        no_value=match[2],
        quote_10="1",
        unit=unit,
        details=details,
        carton_text=f"裝箱 {pack}{unit}/箱",
        category_codes=CATALOG_TABS,
    ).splitlines()
    price_line = f"售價1元/{unit}"
    if copy.count(price_line) != 1 or copy[-2:] != [price_line, formatter.LEAD_TIME_LINE]:
        raise ValueError("正式文案格式缺少預期的售價和交期")
    if (copy[:1] == ["正版授權"]) != licensed:
        raise ValueError("正式文案與目錄的正版標籤不符")
    if code != copy[2 if licensed else 1]:
        raise ValueError("正式文案品號與目錄不同")
    return {"before": copy[:-2], "after": copy[-1:]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("delly_web_checkout", type=Path, help="Verified delly-web checkout")
    parser.add_argument("catalog_data", type=Path, help="The catalog-data.js input")
    parser.add_argument("output", type=Path, help="Destination JSON file")
    args = parser.parse_args()
    checkout = args.delly_web_checkout.resolve(strict=True)
    if not (checkout / "line_ad_copy.py").is_file():
        parser.error("delly-web checkout 缺少 line_ad_copy.py")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(checkout))
    import line_ad_copy as formatter

    if Path(formatter.__file__).resolve().parent != checkout:
        raise RuntimeError("未載入指定的 delly-web 文案程式")
    categories = {code: name for name, code in formatter.validate_codes(CATALOG_TABS).items()}

    products = load_catalog(args.catalog_data)
    templates = {}
    seen = set()
    omitted = Counter()
    for product in products:
        if not isinstance(product, dict):
            omitted["商品資料格式錯誤"] += 1
            continue
        product_id = product.get("id")
        if not isinstance(product_id, str) or not PRODUCT_ID.fullmatch(product_id):
            omitted["商品識別碼錯誤"] += 1
            continue
        if product_id in seen:
            raise ValueError("目錄有重複的商品識別碼")
        seen.add(product_id)
        try:
            templates[product_id] = build_template(product, formatter, categories)
        except ValueError as exc:
            omitted[str(exc)] += 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(templates, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"products": len(products), "templates": len(templates), "omitted": omitted}, ensure_ascii=False))


if __name__ == "__main__":
    main()
