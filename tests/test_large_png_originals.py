"""Bounded large PNG support keeps NAS originals exact and UI copies small."""
import hashlib
from io import BytesIO
import pytest
from PIL import Image, ImageOps
from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_manager import DispatchError
from dispatch_storage import asset_bytes, validate_image
from image_thumbnails import display_bytes, make_thumbnail
from nas_dispatch_storage import NasDispatchStore
from test_nas_dispatch_storage import Network
from test_product_image_library import assignment
from test_synology_image_store import config

@pytest.fixture(scope="module")
def large_png():
    out=BytesIO()
    with Image.new("RGBA",(5225,6467),(40,80,120,128)) as im:
        im.save(out,"PNG")
    return out.getvalue()

def test_large_png_exact_nas_roundtrip_and_fresh_read(large_png):
    original=validate_image(large_png,"large.png")
    network=Network(); sheet=FakeSpreadsheet(product_rows([1]))
    store=NasDispatchStore(sheet,config(),nas_factory=network.factory)
    result=store.save_product_images([assignment(store.catalog()[0],[original])],actor="synthetic",origin="test")
    assert result[0]["結果"]=="已綁定"
    fresh=NasDispatchStore(sheet,config(),nas_factory=network.factory)
    assert asset_bytes(fresh.get_asset(original["sha256"]))==large_png
    assert original["sha256"]==hashlib.sha256(large_png).hexdigest()
    assert list(network.objects.values())==[large_png]

def test_large_png_preview_shrinks_before_exif_and_color_copies(large_png,monkeypatch):
    transpose=ImageOps.exif_transpose
    def bounded(im,*args,**kwargs):
        assert max(im.size)<=1600
        return transpose(im,*args,**kwargs)
    monkeypatch.setattr(ImageOps,"exif_transpose",bounded)
    preview=display_bytes(large_png)
    with Image.open(BytesIO(preview)) as im:
        assert max(im.size)<=1600 and im.mode=="RGBA"
        assert im.getpixel((0,0))[3]==128
    original=validate_image(large_png,"large.png")
    thumbnail=make_thumbnail(original)
    with Image.open(BytesIO(asset_bytes(thumbnail))) as im:
        assert max(im.size)<=640
    assert asset_bytes(original)==large_png

def test_large_png_hard_limit_is_checked_before_preview_decode():
    out=BytesIO()
    with Image.new("L",(8001,5000)) as im: im.save(out,"PNG")
    raw=out.getvalue()
    with pytest.raises(DispatchError,match="4000 萬像素"): validate_image(raw,"large.png")
    with pytest.raises(DispatchError,match="4000 萬像素"): display_bytes(raw)
