"""Versioned display-only derivatives. Never substitute these for originals."""
from io import BytesIO

from PIL import Image, ImageOps

from dispatch_manager import DispatchError
from dispatch_storage import asset_bytes, validate_image

THUMB_VERSION = 1
THUMB_SIDE = 640
THUMB_BYTES = 128 * 1024


def check_thumbnail(asset):
    raw = asset_bytes(asset)
    if len(raw) > THUMB_BYTES or asset["mime"] != "image/webp":
        raise DispatchError("縮圖格式或大小不符")
    with Image.open(BytesIO(raw)) as im:
        if max(im.size) > THUMB_SIDE or getattr(im, "is_animated", False):
            raise DispatchError("縮圖尺寸不符")
    return asset


def make_thumbnail(asset):
    clean = validate_image(asset_bytes(asset), asset["name"])
    with Image.open(BytesIO(asset_bytes(clean))) as opened:
        im = ImageOps.exif_transpose(opened).convert("RGBA")
        im.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", im.size, "white")
        canvas.paste(im, mask=im.getchannel("A"))
        for side, quality in ((640, 72), (640, 50), (480, 45), (320, 40)):
            canvas.thumbnail((side, side), Image.Resampling.LANCZOS)
            out = BytesIO()
            canvas.save(out, format="WEBP", quality=quality, method=4)
            if len(out.getvalue()) <= THUMB_BYTES:
                return check_thumbnail(validate_image(out.getvalue(), clean["name"]))
    raise DispatchError("縮圖無法在大小限制內建立；原檔不變")
