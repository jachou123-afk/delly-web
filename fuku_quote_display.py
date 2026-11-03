"""Compact FuKu display only; source text, notes and cost inputs stay intact."""
import re
import unicodedata


def normalize_weight_line(line):
    """Ignore list bullets only before a weight label; preserve numeric signs."""
    line = unicodedata.normalize("NFKC", str(line)).strip()
    return re.sub(r"^[-·•]\s*(?=(?:鐵盒)?重量\s*[:：])", "", line)


def compact_specs(unit, product_size, color_size, outer_size, notes):
    # Confirmation/audit prose belongs to saved evidence, never the display.
    source = re.split(r"人工確認依據[：:]|富酷重量僅保留原文", str(notes), maxsplit=1)[0]
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    display = [f"計價單位：{unit}"]
    confirmation = str(notes).split("人工確認依據：", 1)[-1] if "人工確認依據：" in str(notes) else ""
    content = re.search(r"（含([^）\n]+)）", confirmation)
    if content and len(content[1]) <= 24:
        display.append(f"內容：{content[1]}")
    for label, value in (("尺寸", product_size), ("彩盒", color_size), ("外箱", outer_size)):
        if value:
            display.append(f"{label}：{value}")
    tin = next((re.sub(r"^鐵盒尺寸\s*[:：]\s*", "鐵盒：", line)
                for line in lines if re.match(r"^鐵盒尺寸\s*[:：]", line)), "")
    package_size = next((re.sub(r"^包裝尺寸\s*[:：]\s*", "包裝尺寸：", line)
                         for line in lines if re.match(r"^包裝尺寸\s*[:：]", line)), "")
    if tin or package_size:
        display.append(tin or package_size)
    packaging = next((re.sub(r"^包裝(?:描述)?\s*[:：]\s*", "", line)
                      for line in lines if re.match(r"^包裝(?:描述)?\s*[:：]", line)), "")
    if not packaging:
        packaging = next((re.sub(r"^典藏", "", line) for line in lines
                          if "鐵盒" in line and "珍珠棉" in line), "")
    if packaging:
        display.append("包裝：" + packaging.replace("+", "＋"))
    cautions = [line for line in lines if re.search(r"控價|混裝|隨機|電池|插腳|另加|木架|木框|不含|不包", line)]
    display.extend(cautions)
    if re.search(r"USB\s*充電", source, re.I):
        display.append("特色：USB充電")
    display = list(dict.fromkeys(display))
    # No arbitrary truncation of numeric specifications. Overflow stays in the
    # full evidence and is explicitly signposted instead of silently clipped.
    if len(display) > 6:
        display = display[:5] + ["其餘規格與注意事項：詳見完整原文"]
    return "\n".join(display)


def unassigned_weight_note(notes):
    source = re.split(r"人工確認依據[：:]|富酷重量僅保留原文", str(notes), maxsplit=1)[0]
    pattern = r"^(?:鐵盒)?重量\s*[:：]\s*(.+)$"
    values = [m[1].strip() for line in source.splitlines()
              if (m := re.match(pattern, normalize_weight_line(line)))]
    if not values:
        return "重量 保留原文備註（未指定計重基準）"
    return "重量 " + "；".join(dict.fromkeys(values)) + "（原文，未指定淨重／毛重）"


def layout_requests(sheet_id, start_row, block_count=1):
    result = [{"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
        "properties": {"pixelSize": 340}, "fields": "pixelSize"}}]
    for block in range(block_count):
        base = start_row - 1 + block * 6
        for offset, height in enumerate((26, 120, 22, 44, 22, 22)):
            result.append({"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": base + offset, "endIndex": base + offset + 1},
                "properties": {"pixelSize": height}, "fields": "pixelSize"}})
        result.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": base, "endRowIndex": base + 6, "startColumnIndex": 1, "endColumnIndex": 2},
            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}},
            "fields": "userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment"}})
    return result


def confirmed_unassigned_weight_display(raw_source, notes, display):
    """Accept only the explicit FuKu confirmation, with every original weight retained."""
    marker = "富酷重量僅保留原文；計重欄留空。確認依據："
    if marker not in notes or not notes.split(marker, 1)[1].strip():
        return False
    normalize = lambda text: re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    raw_lines = [normalize_weight_line(line) for line in str(raw_source).splitlines()
                 if re.match(r"^(?:鐵盒)?重量\s*[:：]", normalize_weight_line(line))]
    dual = r"^(?:鐵盒)?重量\s*[:：]\s*\d+(?:\.\d+)?\s*(?:kg|g)\s*[/／]\s*\d+(?:\.\d+)?\s*(?:kg|g)\s*$"
    if not raw_lines or not all(re.fullmatch(dual, line, re.I) for line in raw_lines):
        return False
    if not all(normalize(line) in normalize(notes) for line in raw_lines):
        return False
    expected = unassigned_weight_note(notes)
    return expected != "重量 保留原文備註（未指定計重基準）" and display in {
        expected, "重量 保留原文備註（未指定計重基準）"}
