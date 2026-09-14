"""Read embedded source images in memory; never follow external image URLs."""
from collections import Counter
from io import BytesIO
import posixpath
import re
import unicodedata
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from dispatch_storage import validate_image

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "d": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _archive(data):
    if not data or len(data) > 50 * 1024 * 1024:
        raise ValueError("圖片包／Excel 檔需在 50 MB 內")
    try:
        archive = ZipFile(BytesIO(data))
    except BadZipFile as exc:
        raise ValueError("檔案不是有效 ZIP／Excel") from exc
    infos = archive.infolist()
    if (len(infos) > 20000 or len({i.filename for i in infos}) != len(infos)
            or sum(i.file_size for i in infos) > 250 * 1024 * 1024
            or any(i.file_size > 20 * 1024 * 1024 or i.flag_bits & 1 for i in infos)):
        archive.close()
        raise ValueError("壓縮檔太大、包含加密或重複項目，停止讀取")
    return archive


def _xml(archive, path):
    raw = archive.read(path)
    markup = raw.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in markup or b"<!ENTITY" in markup:
        raise ValueError("不支援含外部實體的 Excel")
    return ET.fromstring(raw)


def _rels(archive, path):
    rel_path = posixpath.join(posixpath.dirname(path), "_rels", posixpath.basename(path) + ".rels")
    if rel_path not in archive.namelist():
        return {}
    result = {}
    for rel in _xml(archive, rel_path).findall(f"{{{REL_NS}}}Relationship"):
        if rel.get("TargetMode") == "External":
            continue
        target = rel.get("Target", "")
        target = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                                   else posixpath.join(posixpath.dirname(path), target))
        if target.startswith("../") or ":" in target or "\\" in target:
            continue
        result[rel.get("Id")] = target
    return result


def _cell_text(cell, strings):
    if cell is None:
        return ""
    value = cell.findtext("s:v", "", NS)
    if cell.get("t") == "s":
        return strings[int(value)]
    if cell.get("t") == "inlineStr":
        return "".join(cell.itertext())
    return value


def extract_sheet_images(data, products):
    """Only propose drawings anchored inside a matching NO/name six-row block."""
    result = {p["identity"]: [] for p in products}
    warnings = []
    with _archive(data) as archive:
        workbook = _xml(archive, "xl/workbook.xml")
        workbook_rels = _rels(archive, "xl/workbook.xml")
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(si.itertext()) for si in _xml(archive, "xl/sharedStrings.xml")]
        for sheet in workbook.findall("s:sheets/s:sheet", NS):
            title = sheet.get("name")
            selected = [p for p in products if p["category"] == title]
            if not selected:
                continue
            path = workbook_rels.get(sheet.get(f"{{{NS['r']}}}id"))
            if not path:
                continue
            root = _xml(archive, path)
            cells = {c.get("r"): c for c in root.findall("s:sheetData/s:row/s:c", NS)}
            selected = [p for p in selected
                        if _cell_text(cells.get(f"A{p['row']}"), strings).strip().lower() == p["no"].lower()
                        and _cell_text(cells.get(f"B{p['row']}"), strings).strip() == p["name"]]
            worksheet_rels = _rels(archive, path)
            for drawing in root.findall("s:drawing", NS):
                drawing_path = worksheet_rels.get(drawing.get(f"{{{NS['r']}}}id"))
                if not drawing_path:
                    continue
                drawing_rels = _rels(archive, drawing_path)
                for anchor in _xml(archive, drawing_path):
                    row = anchor.findtext("d:from/d:row", None, NS)
                    col = anchor.findtext("d:from/d:col", None, NS)
                    if row is None or col is None:
                        continue
                    row, col = int(row) + 1, int(col) + 1
                    # Original product image area is M:T. Never take icons in another area.
                    matches = [p for p in selected if p["row"] <= row < p["row"] + 6 and 13 <= col <= 20]
                    if len(matches) != 1:
                        continue
                    for blip in anchor.findall(".//a:blip", NS):
                        media = drawing_rels.get(blip.get(f"{{{NS['r']}}}embed"))
                        if not media:
                            continue
                        try:
                            asset = validate_image(archive.read(media), posixpath.basename(media))
                            asset["reference"] = f"{title} 第 {row} 列原圖（依錨點提出，仍須目視核對）"
                            result[matches[0]["identity"]].append(asset)
                        except (ValueError, KeyError) as exc:
                            warnings.append(f"{matches[0]['code'] or matches[0]['no']}：{exc}")
    return {"images": result, "warnings": warnings}


def _filename_key(value):
    value = unicodedata.normalize("NFKC", str(value)).upper().strip()
    return re.sub(r"^貨號\s*[:：]?\s*", "", value)


def match_image_pack(data, products):
    """Exact supplier-code/BGD filename matches only; ambiguous names stay unpaired."""
    keys = {p["identity"]: {_filename_key(p["code"]), _filename_key(p.get("supplier_code", ""))} - {""}
            for p in products}
    counts = Counter(key for values in keys.values() for key in values)
    result = {p["identity"]: [] for p in products}
    warnings = []
    with _archive(data) as archive:
        for info in archive.infolist():
            if info.is_dir() or posixpath.splitext(info.filename)[1].lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                continue
            stem = _filename_key(posixpath.splitext(posixpath.basename(info.filename))[0])
            matches = [identity for identity, values in keys.items() if stem in values and counts[stem] == 1]
            if len(matches) != 1:
                warnings.append(f"{posixpath.basename(info.filename)}：檔名無唯一對應貨號，未自動配對")
                continue
            try:
                asset = validate_image(archive.read(info), posixpath.basename(info.filename))
                asset["reference"] = f"圖片包：{asset['name']}（貨號完全相符，仍須目視核對）"
                result[matches[0]].append(asset)
            except ValueError as exc:
                warnings.append(f"{posixpath.basename(info.filename)}：{exc}")
    return {"images": result, "warnings": warnings}
