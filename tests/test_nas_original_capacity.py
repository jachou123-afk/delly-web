"""NAS-only 32 MiB originals; synthetic bytes, fake Sheets/NAS, no live calls."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import struct
import zlib
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_manager import DispatchError
from dispatch_storage import (CloudDispatchStore, IMAGE_SHEET, MAX_IMAGE_BYTES,
    MAX_NAS_ORIGINAL_BYTES, MAX_IMAGE_PIXELS, MAX_PNG_PIXELS, MAX_JPEG_PIXELS,
    asset_bytes, nas_original_bytes, validate_image, validate_nas_original)
from image_thumbnails import THUMB_BYTES, display_bytes, make_thumbnail
from nas_dispatch_storage import NasDispatchStore
from product_images import checked_assets, checked_nas_originals
from quote_image_append import append_nas_image_repair
from quote_image_repair import prepare_image_repair
from quote_image_repair_batch import prepare_batch_repair, process_next_repair
from quote_image_repair_zip import MAX_ZIP_BYTES, MAX_TOTAL_BYTES, read_repair_zip
from quote_image_source_refresh import (prepare_source_refresh_attempt,
    refresh_binding_source, verify_source_refresh_attempt)
from test_nas_dispatch_storage import Network
from test_original_image_limits import png_of_size
from test_product_image_library import assignment, picture
from test_quote_image_append import fixture as append_fixture
from test_synology_image_store import config

MIB = 1024 * 1024


def pack(raws, identity="G正版:no1"):
    hashes = [hashlib.sha256(raw).hexdigest() for raw in raws]
    sheet, no = identity.split(":")
    value = {"schema": 1, "products": [{"sheet": sheet, "actual_no": no, "sha256": hashes}]}
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(value, ensure_ascii=False).encode())
        for digest, raw in zip(hashes, raws):
            archive.writestr("images/" + digest + ".png", raw)
    return output.getvalue()


def test_nas_exact_32mib_and_normal_20mib_policies_remain_separate():
    assert MAX_IMAGE_BYTES == 20 * MIB and MAX_NAS_ORIGINAL_BYTES == 32 * MIB
    assert (MAX_IMAGE_PIXELS, MAX_PNG_PIXELS, MAX_JPEG_PIXELS) == (25_000_000, 41_000_000, 64_000_000)
    assert MAX_ZIP_BYTES == 50 * MIB and MAX_TOTAL_BYTES == 64 * MIB
    raw = png_of_size(32 * MIB)
    clean = validate_nas_original(raw, "boundary.png")
    assert nas_original_bytes(clean) == raw and clean["sha256"] == hashlib.sha256(raw).hexdigest()
    assert checked_nas_originals([clean]) == [clean]
    for operation in (lambda: validate_image(raw, "boundary.png"), lambda: asset_bytes(clean),
                      lambda: checked_assets([clean]), lambda: CloudDispatchStore(FakeSpreadsheet()).put_asset(raw, "boundary.png")):
        with pytest.raises(DispatchError):
            operation()


def test_over_32mib_stops_before_image_decode_nas_or_sheet_mutation(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("over-capacity original must fail before image decode")
    monkeypatch.setattr(Image, "open", forbidden)
    network = Network()
    sheet = FakeSpreadsheet(product_rows([1]))
    before = deepcopy(sheet.sheets["G正版"].rows)
    store = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    with pytest.raises(DispatchError, match="32 MB"):
        store.put_asset(b"x" * (32 * MIB + 1), "over.png")
    assert not network.sessions and not network.objects
    assert list(sheet.sheets) == ["G正版"] and sheet.sheets["G正版"].rows == before


def test_zip_upload_fresh_readback_and_existing_only_preserve_product_and_order():
    raw = png_of_size(20 * MIB + 1)
    small = asset_bytes(picture(12))
    package = read_repair_zip(pack([small, raw]))
    assert [nas_original_bytes(package["assets"][h]) for h in package["products"][0]["sha256"]] == [small, raw]
    network = Network()
    sheet = FakeSpreadsheet(product_rows([1]))
    before = deepcopy(sheet.sheets["G正版"].rows)
    store = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    run = process_next_repair(store, package, prepare_batch_repair(store, package, mode="upload"), actor="synthetic", limit=1)
    assert run["items"][0]["status"] == "completed"
    assert sheet.sheets["G正版"].rows == before and IMAGE_SHEET not in sheet.sheets
    fresh = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    saved = fresh.product_image_bindings()["G正版:no1"]
    assert saved["assets"] == package["products"][0]["sha256"]
    assert nas_original_bytes(fresh.get_asset(saved["assets"][1])) == raw
    before_rows = {name: deepcopy(ws.rows) for name, ws in sheet.sheets.items()}
    network.sessions.clear()
    run = process_next_repair(fresh, package, prepare_batch_repair(fresh, package, mode="existing_only"), actor="synthetic", limit=1)
    assert run["items"][0]["status"] == "completed"
    assert {name: ws.rows for name, ws in sheet.sheets.items()} == before_rows
    assert not any(kw["data"]["api"] == "SYNO.FileStation.Upload" for session in network.sessions for _, kw in session.calls)
    path = next(path for path, data in network.objects.items() if data == raw)
    network.objects[path] = raw[:-1] + bytes([raw[-1] ^ 1])
    with pytest.raises(DispatchError, match="校驗碼"):
        fresh.get_asset(saved["assets"][1])


def test_append_then_source_refresh_readback_supports_nas_large_original():
    sheet, store, network, plan, old, _ = append_fixture()
    raw = png_of_size(20 * MIB + 1)
    new = validate_nas_original(raw, "supplement.png")
    before = deepcopy(sheet.sheets["G正版"].rows)
    result = append_nas_image_repair(store, plan, [new], actor="synthetic", confirmed_same_product=True)
    assert result["結果"] == "已追加" and network.uploads() == [raw]
    saved = store.product_image_bindings()[plan["source"]["identity"]]
    assert saved["assets"] == [a["sha256"] for a in [*old, new]]
    assert sheet.sheets["G正版"].rows == before
    sheet.sheets["G正版"].rows[1][0] = "2026/11/18"
    fresh_plan = prepare_image_repair(store, plan["source"]["identity"])
    attempt = prepare_source_refresh_attempt(fresh_plan, actor="synthetic", confirmed_same_product=True)
    network.sessions.clear()
    receipt = refresh_binding_source(store, attempt)
    verified = verify_source_refresh_attempt(store, attempt)
    assert receipt["receipt"]["sha256"] == saved["assets"] == verified["receipt"]["sha256"]
    assert not network.uploads()
    assert nas_original_bytes(store.get_asset(new["sha256"])) == raw


def test_large_existing_original_without_index_or_binding_is_adopted_without_upload():
    raw = png_of_size(20 * MIB + 1)
    package = read_repair_zip(pack([raw]))
    network = Network()
    sheet = FakeSpreadsheet(product_rows([1]))
    store = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    clean = next(iter(package["assets"].values()))
    nas = store._nas()
    network.objects[nas._path(nas._metadata(clean))] = raw
    before = deepcopy(sheet.sheets["G正版"].rows)
    run = process_next_repair(store, package, prepare_batch_repair(store, package, mode="existing_only"), actor="synthetic", limit=1)
    assert run["items"][0]["status"] == "completed"
    assert sheet.sheets["G正版"].rows == before
    assert store.product_image_bindings()["G正版:no1"]["assets"] == [clean["sha256"]]
    assert not any(kw["data"]["api"] == "SYNO.FileStation.Upload" for session in network.sessions for _, kw in session.calls)
    assert nas_original_bytes(store.get_asset(clean["sha256"])) == raw


def test_nas_preview_and_thumbnail_keep_raster_and_output_guards():
    out = BytesIO()
    with Image.new("RGB", (4525, 6033), "blue") as image:
        image.save(out, "PNG")
    base = out.getvalue()
    target = 20 * MIB + 1
    payload = b"\0" * (target - len(base) - 12)
    kind = b"npAD"
    raw = base[:-12] + struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload)) + base[-12:]
    assert len(raw) == target
    clean = validate_nas_original(raw, "large-preview.png")
    with pytest.raises(DispatchError, match="20 MB"):
        display_bytes(raw)
    preview = display_bytes(raw, nas_original=True)
    with Image.open(BytesIO(preview)) as image:
        assert max(image.size) <= 1600
    with pytest.raises(DispatchError):
        make_thumbnail(clean)
    thumbnail = make_thumbnail(clean, nas_original=True)
    assert len(asset_bytes(thumbnail)) <= THUMB_BYTES
    with Image.open(BytesIO(asset_bytes(thumbnail))) as image:
        assert max(image.size) <= 640
    assert nas_original_bytes(clean) == raw
    network = Network()
    sheet = FakeSpreadsheet(product_rows([1]))
    store = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    assert store.save_product_images([assignment(store.catalog()[0], [clean])], actor="synthetic", origin="test")[0]["結果"] == "已綁定"
    assert store.prepare_next_thumbnails() == [clean["sha256"]]
    assert store.get_thumbnails([clean["sha256"]])[clean["sha256"]]["mime"] == "image/webp"


@pytest.mark.parametrize("nas,limit", [(False, 20), (True, 32)])
def test_only_explicit_nas_uploader_uses_32mib(nas, limit):
    app = AppTest.from_string("from product_image_ui import render_quote_images\nrender_quote_images('synthetic', nas_originals=" + repr(nas) + ")").run()
    assert not app.exception
    assert app.get("file_uploader")[0].proto.max_upload_size_mb == limit


def test_stale_nas_binding_large_preview_remains_explicit_and_read_only():
    source = '''
import streamlit as st
from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_storage import validate_nas_original
from nas_dispatch_storage import NasDispatchStore
from product_image_ui import render_save_current_images
from test_nas_dispatch_storage import Network
from test_original_image_limits import png_of_size
from test_product_image_library import assignment
from test_synology_image_store import config
if "test_store" not in st.session_state:
    sheet = FakeSpreadsheet(product_rows([1]))
    network = Network()
    store = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    image = validate_nas_original(png_of_size(20*1024*1024+1), "synthetic.png")
    store.save_product_images([assignment(store.catalog()[0], [image])], actor="synthetic", origin="seed")
    sheet.sheets["G正版"].rows[0][1] = "same product, corrected name"
    st.session_state.update(test_store=store, test_network=network)
store = st.session_state["test_store"]
render_save_current_images(store, store.catalog()[0], [], "synthetic", "stale")
'''
    app = AppTest.from_string(source, default_timeout=20).run()
    assert not app.exception and not app.error
    assert any(c.label == "我已逐張核對：變更前綁定原圖仍是本款同一商品" for c in app.checkbox)
    assert next(b for b in app.button if b.label == "沿用舊圖並更新本商品配對").disabled
