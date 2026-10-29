"""Bounded, memory-only original-image packages for formal quote image repair.

The manifest selects real saved identities and ordered original SHA-256 values;
it contains no source conversations, prices, credentials or arbitrary paths.
"""
import hashlib
from io import BytesIO
import json
import re
import unicodedata
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile
from zlib import error as ZlibError

from dispatch_manager import DispatchError, digest
from dispatch_storage import MAX_IMAGE_BYTES, asset_bytes, validate_image
from image_limits import MAX_PRODUCT_ORIGINALS

MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 1001
MAX_PRODUCTS = 250
MAX_MANIFEST_BYTES = 256 * 1024
SHA = re.compile(r"[0-9a-f]{64}")
IMAGE_PATH = re.compile(r"images/([0-9a-f]{64})\.(jpg|jpeg|png|webp)")
MIME_EXTENSIONS = {"image/jpeg": {"jpg", "jpeg"}, "image/png": {"png"}, "image/webp": {"webp"}}


def _fail():
    raise DispatchError("批次補圖清單格式不符；只接受分頁、實際 NO 與原圖 SHA-256")


def _products(manifest):
    if (not isinstance(manifest, dict) or set(manifest) != {"schema", "products"}
            or type(manifest["schema"]) is not int or manifest["schema"] != 1
            or not isinstance(manifest["products"], list)
            or not 1 <= len(manifest["products"]) <= MAX_PRODUCTS):
        _fail()
    result, identities = [], set()
    for product in manifest["products"]:
        if not isinstance(product, dict) or set(product) != {"sheet", "actual_no", "sha256"}:
            _fail()
        sheet, actual_no, hashes = product["sheet"], product["actual_no"], product["sha256"]
        if (not isinstance(sheet, str) or not 1 <= len(sheet) <= 80 or sheet != sheet.strip()
                or ":" in sheet or any(unicodedata.category(c).startswith("C") for c in sheet)
                or not isinstance(actual_no, str) or not re.fullmatch(r"no[1-9][0-9]{0,11}", actual_no)
                or not isinstance(hashes, list) or not 1 <= len(hashes) <= MAX_PRODUCT_ORIGINALS
                or any(not isinstance(value, str) or not SHA.fullmatch(value) for value in hashes)
                or len(set(hashes)) != len(hashes)):
            _fail()
        identity = sheet + ":" + actual_no
        if identity in identities:
            raise DispatchError("批次補圖清單含重複商品；每款實際 NO 只能出現一次")
        identities.add(identity)
        result.append({"sheet": sheet, "actual_no": actual_no, "identity": identity, "sha256": list(hashes)})
    return result


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise DispatchError("批次補圖清單含重複 JSON 欄位")
        value[key] = item
    return value


def _read_member(archive, entry, limit):
    if type(entry.file_size) is not int or not 0 < entry.file_size <= limit:
        raise DispatchError("批次原圖包解壓後超過大小限制或含空檔案")
    with archive.open(entry) as member:
        raw = member.read(limit + 1)
    if len(raw) != entry.file_size or len(raw) > limit:
        raise DispatchError("批次原圖包檔案大小不符，停止使用")
    return raw


def package_digest(package):
    """Rehash normalized product order, filenames, MIME and every original byte."""
    if (not isinstance(package, dict)
            or set(package) not in ({"schema", "package_sha256", "products", "assets"},
                                    {"schema", "package_sha256", "products", "assets", "digest"})
            or type(package.get("schema")) is not int or package["schema"] != 1
            or not isinstance(package.get("package_sha256"), str)
            or not SHA.fullmatch(package["package_sha256"])
            or not isinstance(package.get("products"), list) or not isinstance(package.get("assets"), dict)):
        _fail()
    specs = []
    for product in package["products"]:
        if not isinstance(product, dict) or set(product) != {"sheet", "actual_no", "identity", "sha256"}:
            _fail()
        specs.append({key: product[key] for key in ("sheet", "actual_no", "sha256")})
    products = _products({"schema": 1, "products": specs})
    if products != package["products"]:
        _fail()
    expected = {sha for product in products for sha in product["sha256"]}
    if set(package["assets"]) != expected:
        raise DispatchError("批次原圖包與清單的圖片集合不符")
    evidence, total = [], 0
    for sha in sorted(expected):
        asset = package["assets"][sha]
        if (not isinstance(asset, dict) or set(asset) != {"sha256", "name", "mime", "data"}
                or asset["sha256"] != sha or not isinstance(asset["name"], str)
                or not isinstance(asset["mime"], str)):
            _fail()
        match = IMAGE_PATH.fullmatch("images/" + asset["name"])
        if (not match or match[1] != sha
                or match[2] not in MIME_EXTENSIONS.get(asset["mime"], set())):
            _fail()
        raw = asset_bytes(asset)
        total += len(raw)
        if not raw or total > MAX_TOTAL_BYTES:
            raise DispatchError("批次原圖包解壓後超過大小限制或含空檔案")
        evidence.append({"sha256": hashlib.sha256(raw).hexdigest(), "name": asset["name"],
                         "mime": asset["mime"], "bytes": len(raw)})
    return digest({"schema": 1, "package_sha256": package["package_sha256"],
                   "products": products, "assets": evidence})


def read_repair_zip(data):
    """Read only manifest.json and its exact SHA-addressed original image set."""
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_ZIP_BYTES:
        raise DispatchError("請提供不超過 50 MB 的批次商品原圖 ZIP")
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_ENTRIES:
                raise DispatchError("批次原圖包檔案數超過限制")
            names, image_entries, manifest_entry, total = set(), [], None, 0
            for entry in entries:
                name = entry.filename
                # Exact relative ASCII filenames also reject traversal, aliases,
                # separators, drives, controls, NUL truncation and directories.
                if (entry.orig_filename != name or name in names or entry.is_dir()
                        or entry.flag_bits & 1 or entry.compress_type not in (ZIP_STORED, ZIP_DEFLATED)
                        or (entry.external_attr >> 16) & 0o170000 not in (0, 0o100000)
                        or (name != "manifest.json" and not IMAGE_PATH.fullmatch(name))):
                    raise DispatchError("批次原圖包含不支援的路徑、重複檔案、連結、壓縮或加密格式")
                names.add(name)
                limit = MAX_MANIFEST_BYTES if name == "manifest.json" else MAX_IMAGE_BYTES
                if not 0 < entry.file_size <= limit:
                    raise DispatchError("批次原圖包解壓後超過大小限制或含空檔案")
                total += entry.file_size
                if total > MAX_TOTAL_BYTES:
                    raise DispatchError("批次原圖包解壓後超過總大小限制")
                if name == "manifest.json":
                    manifest_entry = entry
                else:
                    image_entries.append(entry)
            if manifest_entry is None:
                raise DispatchError("批次原圖包缺少根目錄 manifest.json")
            raw_manifest = _read_member(archive, manifest_entry, MAX_MANIFEST_BYTES)
            manifest = json.loads(raw_manifest.decode("utf-8"), object_pairs_hook=_unique_object)
            products = _products(manifest)
            expected = {sha for product in products for sha in product["sha256"]}
            assets = {}
            for entry in image_entries:
                match = IMAGE_PATH.fullmatch(entry.filename)
                sha, extension = match.groups()
                if sha in assets:
                    raise DispatchError("批次原圖包含重複 SHA 原圖，每張實體檔只能保存一次")
                if sha not in expected:
                    raise DispatchError("批次原圖包含清單未引用的圖片")
                raw = _read_member(archive, entry, MAX_IMAGE_BYTES)
                if hashlib.sha256(raw).hexdigest() != sha:
                    raise DispatchError("批次原圖檔名與原始位元組 SHA-256 不符")
                asset = validate_image(raw, entry.filename.split("/")[1])
                if extension not in MIME_EXTENSIONS.get(asset["mime"], set()):
                    raise DispatchError("批次原圖副檔名與實際圖片格式不符")
                assets[sha] = asset
    except DispatchError:
        raise
    except (BadZipFile, OSError, ValueError, RuntimeError, NotImplementedError, EOFError,
            ZlibError, UnicodeError, TypeError, OverflowError):
        raise DispatchError("批次原圖 ZIP 或清單損壞，停止使用") from None
    if set(assets) != expected:
        raise DispatchError("批次原圖包缺少清單指定的原圖")
    package = {"schema": 1, "package_sha256": hashlib.sha256(data).hexdigest(),
               "products": products, "assets": assets}
    package["digest"] = package_digest(package)
    return package
