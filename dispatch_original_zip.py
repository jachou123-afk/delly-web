"""Session-only original images, matched by approved SHA-256, never filenames.

No files are extracted, resized, persisted or rebound to products.
"""
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile
from zlib import error as ZlibError

from dispatch_manager import DispatchError
from dispatch_storage import MAX_IMAGE_BYTES, validate_image

MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 500


def original_zip_reader(data, expected_ids):
    """Accept exactly the selected originals, retaining their original bytes."""
    expected = set(expected_ids)
    if not expected or not data or len(data) > MAX_ZIP_BYTES:
        raise DispatchError("請提供本輪完整原圖 ZIP，檔案不得超過 50 MB")
    assets = {}
    total = 0
    names = set()
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES:
                raise DispatchError("原圖包檔案數過多，請只放本輪原圖")
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (path.is_absolute() or ".." in path.parts or "\\" in entry.filename or ":" in entry.filename
                        or entry.filename in names or entry.flag_bits & 1
                        or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                    raise DispatchError("原圖包含不支援的路徑、重複檔名、連結或加密檔案")
                names.add(entry.filename)
                if entry.is_dir():
                    continue
                if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                    raise DispatchError("原圖包只能包含 JPG、PNG、WebP 圖片")
                total += entry.file_size
                if entry.file_size > MAX_IMAGE_BYTES or total > MAX_TOTAL_BYTES:
                    raise DispatchError("原圖包解壓後超過圖片大小上限，請減少本輪商品")
                with archive.open(entry) as member:
                    raw = member.read(MAX_IMAGE_BYTES + 1)
                asset = validate_image(raw, path.name)
                identity = asset["sha256"]
                if identity not in expected:
                    raise DispatchError("原圖包含未匹配本輪已核准圖片的檔案；不按檔名替換圖片")
                if identity in assets:
                    raise DispatchError("原圖包含重複圖片，請每張原圖只放一次")
                assets[identity] = asset
    except DispatchError:
        raise
    except (BadZipFile, OSError, ValueError, RuntimeError, NotImplementedError, EOFError, ZlibError) as exc:
        raise DispatchError("原圖 ZIP 損壞或格式不支援") from exc
    if set(assets) != expected:
        raise DispatchError(f"原圖包缺少本輪 {len(expected - set(assets))} 張已核准原圖")

    def read_asset(identity):
        if identity not in assets:
            raise DispatchError("原圖不在本輪已核准清單")
        return assets[identity]

    return read_asset
