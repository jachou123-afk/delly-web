"""Large JPEG originals remain exact; UI decoding is bounded separately."""
import hashlib
from io import BytesIO

import pytest
from PIL import Image

from dispatch_fakes import FakeSpreadsheet, image_data, product_rows
from dispatch_manager import DispatchError
from dispatch_storage import asset_bytes, validate_image
from image_thumbnails import display_bytes, make_thumbnail
from nas_dispatch_storage import NasDispatchStore
from test_nas_dispatch_storage import Network
from test_product_image_library import assignment
from test_synology_image_store import config


@pytest.fixture(scope="module")
def large_jpeg():
    out = BytesIO()
    with Image.new("L", (6534, 7795), 120) as im:
        im.save(out, "JPEG")
    return out.getvalue()


def test_large_jpeg_nas_roundtrip_preserves_original(large_jpeg):
    original = validate_image(large_jpeg, "large-original.jpg")
    network = Network()
    store = NasDispatchStore(FakeSpreadsheet(product_rows([1])), config(), nas_factory=network.factory)
    product = store.catalog()[0]
    result = store.save_product_images([assignment(product, [original])], actor="synthetic", origin="test")
    assert result[0]["結果"] == "已綁定"
    assert asset_bytes(store.get_asset(original["sha256"])) == large_jpeg
    assert original["sha256"] == hashlib.sha256(large_jpeg).hexdigest()
    assert list(network.objects.values()) == [large_jpeg]


def test_large_jpeg_preview_and_thumbnail_downsample_before_decode(large_jpeg, monkeypatch):
    original = validate_image(large_jpeg, "large-original.jpg")
    real_load = Image.Image.load
    def bounded_load(self, *args, **kwargs):
        assert self.width * self.height <= 25_000_000
        return real_load(self, *args, **kwargs)
    monkeypatch.setattr(Image.Image, "load", bounded_load)
    preview = display_bytes(large_jpeg)
    with Image.open(BytesIO(preview)) as im:
        assert max(im.size) <= 1600
        assert abs(im.width / im.height - 6534 / 7795) < .002
    thumbnail = make_thumbnail(original)
    with Image.open(BytesIO(asset_bytes(thumbnail))) as im:
        assert max(im.size) <= 640
    assert asset_bytes(original) == large_jpeg
    assert preview != large_jpeg


def test_normal_images_are_not_reencoded():
    raw = image_data()
    assert display_bytes(raw) is raw


def test_large_jpeg_hard_limit_remains():
    out = BytesIO()
    with Image.new("L", (8001, 8000)) as im:
        im.save(out, "JPEG")
    with pytest.raises(DispatchError, match="6400 萬像素"):
        validate_image(out.getvalue(), "over-limit.jpg")
    with pytest.raises(DispatchError, match="6400 萬像素"):
        display_bytes(out.getvalue())
