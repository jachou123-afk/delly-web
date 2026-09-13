"""Build fail-closed LINE advertising copy from a saved Google Sheets block.

This module intentionally does not alter purchasing or costing data.  It only
controls which already-saved fields are exposed in outbound advertising copy.
"""

from decimal import Decimal, InvalidOperation, ROUND_CEILING
import math
import re
import unicodedata


CATEGORY_CODES = {
    "G正版": "G",
    "S生活用品": "S",
}

LEAD_TIME_LINE = "交貨2-3週"

_NO_PATTERN = re.compile(r"(?:NO)?\s*(\d+)", re.IGNORECASE)
_UNIT_PATTERN = re.compile(r"^[^\s/：:]+$")
_PRICE_UNITS = {"個", "盒", "套", "瓶", "罐", "包", "袋"}
_INTERNAL_PREFIX = re.compile(r"^計價單位\s*[：:]")
_OUTER_BOX_PREFIX = re.compile(r"^(?:外箱尺寸|外箱規格|外箱规格|外箱)\s*[：:]?")
_WEIGHT_PREFIX = re.compile(
    r"^(?:整箱毛重|整箱重量|箱重|毛重|淨重|净重|單個重量|单个重量|"
    r"每個重量|每个重量|單件重量|单件重量|每件重量|單重|单重|重量)\s*[：:]?"
)
_WOOD_PATTERN = re.compile(r"(?:木架|木框)")
_CARTON_PREFIX = re.compile(r"^裝箱\s*")
_LICENSE_PATTERN = re.compile(r"正版(?:授權|授权)")
_PRODUCT_SIZE_PREFIX = re.compile(r"^(?:(?:產品|产品)\s*)?尺寸\s*[：:]?")
_PACKAGING_SIZE_PREFIX = re.compile(
    r"^(彩盒尺寸|包裝尺寸|包装尺寸|端盒尺寸)\s*[：:]?\s*(.+)$"
)
_PACKAGING_SIZE_PRIORITY = {
    "彩盒尺寸": 0,
    "端盒尺寸": 1,
    "包裝尺寸": 2,
    "包装尺寸": 2,
}
_TRAILING_PRIVATE_FIELD = re.compile(
    r"\s+(?=(?:外箱尺寸|外箱規格|外箱规格|外箱|整箱毛重|整箱重量|箱重|毛重|"
    r"淨重|净重|單個重量|单个重量|每個重量|每个重量|單件重量|单件重量|"
    r"每件重量|單重|单重|重量|木架|木框)\s*[：:]?)"
)


def _clean_text(value):
    return str(value or "").replace("\r", "").strip()


def _remove_ad_labels(value):
    """Remove labels that are handled structurally in the LINE layout."""
    text = str(value or "").replace("新品", "")
    text = _LICENSE_PATTERN.sub("", text)
    text = re.sub(r"^[\s#＃|｜/／、,，;；:：-]+", "", text)
    text = re.sub(r"[\s#＃|｜/／、,，;；:：-]+$", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _dimension_signature(value):
    """Normalize only the dimension value so equivalent package sizes dedupe."""
    normalized = unicodedata.normalize("NFKC", value).lower()
    normalized = normalized.replace("×", "*").replace("x", "*")
    return re.sub(r"\s+", "", normalized).strip("，,；;")


def build_bgd_code(category_name, no_value):
    """Return a strict BGD code without guessing unsupported sheet mappings."""
    category = _clean_text(category_name)
    if category not in CATEGORY_CODES:
        raise ValueError(f"尚未設定分頁代號：{category or '空白'}")

    no_text = unicodedata.normalize("NFKC", _clean_text(no_value))
    match = _NO_PATTERN.fullmatch(no_text)
    if not match:
        raise ValueError("NO 必須是 no 加純數字，例如 no221")
    return f"BGD-{CATEGORY_CODES[category]}-{match.group(1)}"


def ceil_ad_price(quote_10):
    """Round a valid 10% quote upward to the integer shown to customers."""
    if isinstance(quote_10, bool):
        raise ValueError("10%報價不是有效數字")
    if isinstance(quote_10, float) and not math.isfinite(quote_10):
        raise ValueError("10%報價不是有效有限值")
    raw = _clean_text(quote_10).replace(",", "")
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ValueError("10%報價不是有效數字") from None
    if not value.is_finite() or value <= 0:
        raise ValueError("10%報價缺失或不是正數")
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def _ad_detail_lines(details):
    """Filter internal-only fields while preserving customer-facing details."""
    if isinstance(details, str):
        source_lines = details.splitlines()
    else:
        source_lines = []
        for item in details or ():
            source_lines.extend(_clean_text(item).splitlines())

    candidates = []
    for source_line in source_lines:
        line = source_line.strip()
        if not line:
            continue
        normalized = unicodedata.normalize("NFKC", line)
        if (
            _INTERNAL_PREFIX.match(normalized)
            or _OUTER_BOX_PREFIX.match(normalized)
            or _WEIGHT_PREFIX.match(normalized)
            or _WOOD_PATTERN.search(normalized)
            or _CARTON_PREFIX.match(normalized)
        ):
            continue

        # Preserve a customer-facing clause that precedes an internal field on
        # the same line, e.g. "6個圖案混 單個重量 68g".
        private_suffix = _TRAILING_PRIVATE_FIELD.search(normalized)
        if private_suffix:
            line = line[:private_suffix.start()].rstrip(" ，,；;")

        line = _remove_ad_labels(line)
        if line:
            candidates.append(line)

    packaging_dimensions = {}
    for index, line in enumerate(candidates):
        match = _PACKAGING_SIZE_PREFIX.match(unicodedata.normalize("NFKC", line))
        if not match:
            continue
        signature = _dimension_signature(match.group(2))
        if not signature:
            continue
        choice = (_PACKAGING_SIZE_PRIORITY[match.group(1)], index, line)
        current = packaging_dimensions.get(signature)
        if current is None or choice[:2] < current[:2]:
            packaging_dimensions[signature] = choice

    # Packaging methods such as "包裝:12個/opp袋" never enter this branch.
    # Only an explicit packaging-dimension field suppresses the generic product
    # dimension.  Distinct package levels remain visible; identical dimension
    # values appear once, with 彩盒尺寸 preferred when present.
    has_packaging_dimension = bool(packaging_dimensions)
    emitted_dimensions = set()
    result = []
    seen = set()
    for line in candidates:
        normalized = unicodedata.normalize("NFKC", line)
        if has_packaging_dimension and _PRODUCT_SIZE_PREFIX.match(normalized):
            continue

        package_match = _PACKAGING_SIZE_PREFIX.match(normalized)
        if package_match:
            signature = _dimension_signature(package_match.group(2))
            if not signature or signature in emitted_dimensions:
                continue
            line = packaging_dimensions[signature][2]
            emitted_dimensions.add(signature)

        if line not in seen:
            seen.add(line)
            result.append(line)
    return result


def _validated_unit(unit):
    value = unicodedata.normalize("NFKC", _clean_text(unit))
    if value not in _PRICE_UNITS or not _UNIT_PATTERN.fullmatch(value):
        raise ValueError("缺少或無法確認計價單位")
    return value


def _carton_line_and_unit(carton_text):
    """Validate the required carton line and remove any trailing weight field."""
    lines = [line.strip() for line in _clean_text(carton_text).splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("裝箱資訊缺失或格式不明")
    line = lines[0]
    normalized = unicodedata.normalize("NFKC", line)
    private_suffix = _TRAILING_PRIVATE_FIELD.search(normalized)
    if private_suffix:
        line = line[:private_suffix.start()].rstrip(" ，,；;")
        normalized = unicodedata.normalize("NFKC", line)
    match = re.fullmatch(r"裝箱\s*([0-9]+)\s*([^\s/]+)\s*/\s*箱", normalized)
    if not match:
        raise ValueError("裝箱資訊必須是『裝箱 數量單位/箱』")
    return f"裝箱 {match.group(1)}{match.group(2)}/箱", _validated_unit(match.group(2))


def build_line_ad_copy(
    *,
    name,
    category_name,
    no_value,
    quote_10,
    unit,
    details,
    carton_text,
):
    """Build one complete customer-facing message, failing closed on gaps."""
    raw_name = re.sub(r"\s+", " ", _clean_text(name))
    raw_details = details if isinstance(details, str) else "\n".join(
        _clean_text(item) for item in (details or ())
    )
    is_licensed = bool(
        _LICENSE_PATTERN.search(raw_name)
        or _LICENSE_PATTERN.search(raw_details)
    )
    product_name = _remove_ad_labels(raw_name)
    if not product_name:
        raise ValueError("商品名稱不可空白")

    price_unit = _validated_unit(unit)
    carton_line, carton_unit = _carton_line_and_unit(carton_text)
    if carton_unit != price_unit:
        raise ValueError("裝箱單位與計價單位不一致，停止產生廣告")

    lines = [
        *(["正版授權"] if is_licensed else []),
        product_name,
        build_bgd_code(category_name, no_value),
        *_ad_detail_lines(details),
        carton_line,
        f"售價{ceil_ad_price(quote_10)}元/{price_unit}",
        LEAD_TIME_LINE,
    ]
    return "\n".join(lines)


def build_line_ad_copy_from_sheet_block(category_name, rows):
    """Build advertising copy from one displayed 6x12 Google Sheets block."""
    block = [list(row or []) + [""] * (12 - len(row or [])) for row in list(rows or [])[:6]]
    while len(block) < 6:
        block.append([""] * 12)

    info_text = _clean_text(block[1][1])
    unit_matches = re.findall(
        r"^計價單位\s*[：:]\s*([^\s/]+)\s*$",
        unicodedata.normalize("NFKC", info_text),
        re.MULTILINE,
    )
    if len(set(unit_matches)) != 1:
        raise ValueError("雲表中的計價單位缺失或不唯一")

    return build_line_ad_copy(
        name=block[0][1],
        category_name=category_name,
        no_value=block[0][0],
        quote_10=block[1][2],
        unit=unit_matches[0],
        details=info_text,
        carton_text=block[2][1],
    )
