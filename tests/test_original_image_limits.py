"""Original-byte limits use synthetic PNGs, Sheets and NAS; no real files or network."""
import base64
from copy import deepcopy
import hashlib
from io import BytesIO
import struct
import zlib

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from dispatch_fakes import FakeSpreadsheet, image_data, product_rows
from dispatch_manager import DispatchError
from dispatch_storage import (CloudDispatchStore, IMAGE_SHEET, MAX_IMAGE_BYTES,
                              asset_bytes, validate_image)
from nas_dispatch_storage import NasDispatchStore
from product_images import checked_assets
from test_nas_dispatch_storage import Network
from test_product_image_library import assignment, picture
from test_synology_image_store import config


def png_of_size(size):
    """Add a valid private ancillary PNG chunk, without changing image pixels."""
    original = image_data()
    payload = b"\0" * (size - len(original) - 12)
    kind = b"npAD"
    chunk = (struct.pack(">I", len(payload)) + kind + payload
             + struct.pack(">I", zlib.crc32(kind + payload)))
    result = original[:-12] + chunk + original[-12:]
    assert len(result) == size
    return result


@pytest.mark.parametrize("size", [3 * 1024 * 1024, 13 * 1024 * 1024, 20 * 1024 * 1024])
def test_large_original_nas_binding_roundtrip_preserves_exact_bytes_and_sha(size):
    raw = png_of_size(size)
    original = validate_image(raw, "synthetic-original.png")
    assert original["sha256"] == hashlib.sha256(raw).hexdigest()
    sheet = FakeSpreadsheet(product_rows([1]))
    before = deepcopy(sheet.sheets["G正版"].rows)
    network = Network()
    store = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    source = store.catalog()[0]
    result = store.save_product_images([assignment(source, [original])], actor="synthetic", origin="test")
    assert result[0]["結果"] == "已綁定"
    assert sheet.sheets["G正版"].rows == before and IMAGE_SHEET not in sheet.sheets
    reopened = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    binding = reopened.product_image_bindings()[source["identity"]]
    assert binding["assets"] == [original["sha256"]]
    assert asset_bytes(reopened.get_asset(original["sha256"])) == raw
    assert list(network.objects.values()) == [raw]


def test_larger_than_20mb_or_empty_is_rejected_without_nas_or_sheet_writes():
    sheet = FakeSpreadsheet(product_rows([1]))
    network = Network()
    store = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    before = deepcopy(sheet.sheets["G正版"].rows)
    for raw in (b"", b"x" * (MAX_IMAGE_BYTES + 1)):
        with pytest.raises(DispatchError, match="20 MB"):
            store.put_asset(raw, "synthetic.png")
    assert set(sheet.sheets) == {"G正版"}
    assert sheet.sheets["G正版"].rows == before
    assert not network.objects and not network.sessions


def test_nas_read_still_checks_size_and_hash_for_large_original():
    raw = png_of_size(3 * 1024 * 1024)
    network = Network()
    store = NasDispatchStore(FakeSpreadsheet(), config(), nas_factory=network.factory)
    identity = store.put_asset(raw, "synthetic.png")
    path = next(iter(network.objects))
    network.objects[path] = raw[:-1] + bytes([raw[-1] ^ 1])
    with pytest.raises(DispatchError, match="校驗碼"):
        store.get_asset(identity)
    forged = {"data": base64.b64encode(b"x" * (MAX_IMAGE_BYTES + 1)).decode(),
              "sha256": hashlib.sha256(b"x" * (MAX_IMAGE_BYTES + 1)).hexdigest()}
    with pytest.raises(DispatchError, match="校驗失敗"):
        asset_bytes(forged)


@pytest.mark.parametrize("bulk", [False, True])
def test_legacy_sheet_payload_limit_remains_2mb_with_clear_nas_requirement(bulk):
    raw = png_of_size(2 * 1024 * 1024 + 1)
    sheet = FakeSpreadsheet()
    store = CloudDispatchStore(sheet)
    before = set(sheet.sheets)
    with pytest.raises(DispatchError, match="需使用 NAS"):
        if bulk:
            store.put_assets([validate_image(raw, "synthetic.png")])
        else:
            store.put_asset(raw, "synthetic.png")
    assert set(sheet.sheets) == before and IMAGE_SHEET not in sheet.sheets


def test_format_animation_pixels_and_five_originals_are_still_guarded():
    output = BytesIO()
    Image.new("RGB", (20, 20)).save(output, "GIF")
    with pytest.raises(DispatchError, match="JPG"):
        validate_image(output.getvalue(), "synthetic.gif")
    output = BytesIO()
    Image.new("RGB", (20, 20), "red").save(
        output, "PNG", save_all=True, append_images=[Image.new("RGB", (20, 20), "blue")])
    with pytest.raises(DispatchError, match="靜態"):
        validate_image(output.getvalue(), "synthetic.png")
    output = BytesIO()
    Image.new("RGB", (5001, 5000)).save(output, "PNG")
    with pytest.raises(DispatchError, match="2500 萬像素"):
        validate_image(output.getvalue(), "synthetic.png")
    assert len(checked_assets([picture(n) for n in range(5)])) == 5
    with pytest.raises(DispatchError, match="1～5"):
        checked_assets([picture(n) for n in range(6)])


def test_quote_original_upload_is_20mb_but_advertisement_upload_stays_2mb():
    app = AppTest.from_string(
        'from product_image_ui import render_quote_images\nrender_quote_images("synthetic")').run()
    assert not app.exception
    upload = app.get("file_uploader")[0].proto
    assert upload.max_upload_size_mb == 20 and upload.multiple_files
    assert list(upload.type) == [".jpg", ".jpeg", ".png", ".webp"]
    assert any("20 MB" in item.value and "NAS" in item.value for item in app.caption)
    from test_dispatch_ui import app_source
    adverts = AppTest.from_string(app_source(ready=True), default_timeout=20).run()
    assert not adverts.exception
    advertisement = next(item.proto for item in adverts.get("file_uploader")
                         if item.proto.label == "加入商品圖片")
    assert advertisement.max_upload_size_mb == 2
