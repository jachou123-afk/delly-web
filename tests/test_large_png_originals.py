"""Bounded large PNG support keeps NAS originals exact and UI copies small."""
import base64
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
    with Image.new("RGBA",(5492,7372),(40,80,120,128)) as im:
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

def test_large_png_preview_shrinks_before_application_exif_conversion(large_png,monkeypatch):
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

@pytest.mark.parametrize("size", [(5492, 7372), (8200, 5000)])
def test_png_pixel_boundary_accepts_original_bytes(size):
    output = BytesIO()
    with Image.new("L", size, 120) as image:
        image.save(output, "PNG")
    raw = output.getvalue()
    original = validate_image(raw, "boundary.png")
    assert asset_bytes(original) == raw
    assert original["sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.fixture(scope="module")
def over_limit_png():
    out=BytesIO()
    with Image.new("L",(8201,5000)) as im: im.save(out,"PNG")
    return out.getvalue()


def test_large_png_hard_limit_is_checked_before_preview_decode(over_limit_png, monkeypatch):
    def forbidden_decode(*args, **kwargs):
        pytest.fail("An over-limit PNG must be rejected before raster decoding")
    monkeypatch.setattr(Image.Image, "load", forbidden_decode)
    with pytest.raises(DispatchError,match="4100 萬像素"): validate_image(over_limit_png,"large.png")
    with pytest.raises(DispatchError,match="4100 萬像素"): display_bytes(over_limit_png)


def test_over_limit_png_is_rejected_before_nas_io(over_limit_png):
    asset = {"name": "over-limit.png", "mime": "image/png",
             "sha256": hashlib.sha256(over_limit_png).hexdigest(),
             "data": base64.b64encode(over_limit_png).decode("ascii")}
    network = Network()
    sheet = FakeSpreadsheet(product_rows([1]))
    store = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    with pytest.raises(DispatchError, match="4100 萬像素"):
        store.put_assets([asset])
    assert not network.sessions and not network.objects


def test_large_png_repair_zip_retains_original_bytes(large_png):
    from quote_image_repair_zip import read_repair_zip
    from test_quote_image_repair_zip import manifest, pack, sha
    value = manifest(products=[{"sheet": "富酷", "actual_no": "no1",
                                "sha256": [sha(large_png)]}])
    package = read_repair_zip(pack(value, [("images/" + sha(large_png) + ".png", large_png)]))
    assert asset_bytes(package["assets"][sha(large_png)]) == large_png


def test_large_png_append_uploads_only_new_original(large_png):
    from quote_image_append import append_nas_image_repair
    from test_quote_image_append import fixture
    _, store, network, plan, old, _ = fixture()
    original = validate_image(large_png, "large-supplement.png")
    old_objects = dict(network.objects)
    result = append_nas_image_repair(store, plan, [original], actor="synthetic",
                                     confirmed_same_product=True)
    assert network.uploads() == [large_png]
    assert result["receipt"]["sha256"] == [a["sha256"] for a in old] + [original["sha256"]]
    assert result["receipt"]["main_sha256"] == old[0]["sha256"]
    assert result["receipt"]["appended_sha256"] == [original["sha256"]]
    assert all(network.objects[path] == raw for path, raw in old_objects.items())


def test_png_pixel_extension_preserves_byte_and_pillow_limits(monkeypatch):
    import dispatch_storage
    assert dispatch_storage.MAX_PNG_PIXELS == 41_000_000
    assert dispatch_storage.MAX_IMAGE_BYTES == 20 * 1024 * 1024
    assert dispatch_storage.MAX_JPEG_PIXELS == 64_000_000
    assert dispatch_storage.MAX_IMAGE_PIXELS == 25_000_000
    with pytest.raises(DispatchError, match="20 MB"):
        validate_image(b"x" * (dispatch_storage.MAX_IMAGE_BYTES + 1), "large.png")
    output = BytesIO()
    with Image.new("RGB", (20, 20)) as image:
        image.save(output, "PNG")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(DispatchError, match="圖片損壞或格式不支援"):
        validate_image(output.getvalue(), "bomb.png")
    assert Image.MAX_IMAGE_PIXELS == 100
