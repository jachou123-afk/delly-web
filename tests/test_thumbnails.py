from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image

from dispatch_manager import DispatchError
from dispatch_storage import asset_bytes, encode_record, validate_image
from image_thumbnails import make_thumbnail, check_thumbnail, THUMB_BYTES
from nas_dispatch_storage import NasDispatchStore
from thumbnail_storage import THUMB_SHEET
from test_nas_dispatch_storage import seeded
from test_product_image_library import picture
from test_synology_image_store import config


@pytest.mark.parametrize("mode,size", [("RGBA", (2000, 1000)), ("RGB", (500, 1200)), ("P", (100, 50))])
def test_derivative_is_small_oriented_not_upscaled_and_original_unchanged(mode, size):
    im = Image.new(mode, size)
    out = BytesIO()
    im.save(out, format="PNG")
    original = validate_image(out.getvalue(), "original.png")
    before = deepcopy(original)
    thumb = make_thumbnail(original)
    assert original == before and len(asset_bytes(thumb)) <= THUMB_BYTES
    assert thumb["mime"] == "image/webp" and thumb["sha256"] != original["sha256"]
    with Image.open(BytesIO(asset_bytes(thumb))) as small:
        assert max(small.size) <= 640
        assert small.width <= size[0] and small.height <= size[1]
        assert abs(small.width / small.height - size[0] / size[1]) < 0.01
        if mode == "RGBA":
            assert small.convert("RGB").getpixel((0, 0)) == (255, 255, 255)


def test_exif_orientation_applied_and_metadata_not_copied():
    im = Image.new("RGB", (1200, 600), "red")
    exif = Image.Exif()
    exif[274] = 6
    out = BytesIO()
    im.save(out, format="JPEG", exif=exif)
    thumb = make_thumbnail(validate_image(out.getvalue(), "rotated.jpg"))
    with Image.open(BytesIO(asset_bytes(thumb))) as small:
        assert small.size == (320, 640) and not small.getexif()


def test_noisy_derivative_respects_byte_limit():
    im = Image.effect_noise((1800, 1800), 100).convert("RGB")
    out = BytesIO()
    im.save(out, format="JPEG", quality=65)
    original = validate_image(out.getvalue(), "noise.jpg")
    assert len(asset_bytes(make_thumbnail(original))) <= THUMB_BYTES


def test_check_rejects_nonthumbnail_or_oversized_dimensions():
    with pytest.raises(DispatchError):
        check_thumbnail(picture())
    im = Image.new("RGB", (641, 10))
    out = BytesIO()
    im.save(out, format="WEBP")
    with pytest.raises(DispatchError):
        check_thumbnail(validate_image(out.getvalue(), "oversized.webp"))


def test_63_derivatives_seven_groups_keep_originals_quotes_bindings_and_reviews():
    sheet, store, network = seeded(63, 69)
    while store.migration_status()["pending"]:
        store.migrate_next_images()
    before = {k: deepcopy(ws.rows) for k, ws in sheet.sheets.items()}
    original_objects = dict(network.objects)
    groups = []
    while store.thumbnail_status()["pending"]:
        groups.append(store.prepare_next_thumbnails())
    assert list(map(len, groups)) == [10] * 6 + [3]
    assert before == {k: sheet.sheets[k].rows for k in before}
    assert original_objects == {k: network.objects[k] for k in original_objects}
    assert set(sheet.sheets) - set(before) == {THUMB_SHEET}
    assert len(sheet.sheets[THUMB_SHEET].rows) == 64
    assert store.thumbnail_status()["ready"] == 63
    count = len(network.sessions)
    assert store.prepare_next_thumbnails() == [] and len(network.sessions) == count
    # A fresh store reads only derivatives; original read routes are forbidden.
    reopened = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    reopened.get_assets = lambda *a: pytest.fail("original preview download")
    for group in groups:
        thumbs = reopened.get_thumbnails(group)
        assert set(thumbs) == set(group)
        for thumb in thumbs.values():
            check_thumbnail(thumb)
        calls = network.sessions[-1].calls
        download_paths = [c[1]["data"]["path"] for c in calls if c[1]["data"]["api"] == "SYNO.FileStation.Download"]
        assert download_paths and all("/thumbnails/v1/" in p for p in download_paths)
    assert before == {k: sheet.sheets[k].rows for k in before}
    assert all(s.closed for s in network.sessions)


def test_thumbnail_miss_generates_readonly_preview_without_setup():
    sheet, store, network = seeded(2)
    before = {k: deepcopy(ws.rows) for k, ws in sheet.sheets.items()}
    ids = store.thumbnail_status()["pending"]
    thumbs = store.get_thumbnails(ids)
    assert set(thumbs) == set(ids)
    for thumb in thumbs.values():
        check_thumbnail(thumb)
    assert not network.sessions and before == {k: ws.rows for k, ws in sheet.sheets.items()}


def test_migrated_originals_without_derivatives_are_readable_without_cloud_writes():
    sheet, store, network = seeded(2)
    ids = store.migrate_next_images()
    before = {k: deepcopy(ws.rows) for k, ws in sheet.sheets.items()}
    objects = dict(network.objects)
    thumbs = store.get_thumbnails(ids)
    assert set(thumbs) == set(ids)
    for thumb in thumbs.values():
        check_thumbnail(thumb)
    assert before == {k: ws.rows for k, ws in sheet.sheets.items()}
    assert objects == network.objects


@pytest.mark.parametrize("kind", ["missing", "corrupt", "secret_missing", "secret_invalid", "wrong_library"])
def test_thumbnail_failure_is_closed_and_never_changes_originals(kind):
    sheet, store, network = seeded(1)
    identity = store.prepare_next_thumbnails()[0]
    before = {k: deepcopy(ws.rows) for k, ws in sheet.sheets.items()}
    if kind == "missing":
        network.objects.clear()
    elif kind == "corrupt":
        path = next(iter(network.objects))
        network.objects[path] = b"wrong"
    elif kind == "secret_missing":
        store.nas_config = None
    elif kind == "secret_invalid":
        store.nas_config_error = True
    else:
        store.nas_config = config(library_id="another-library")
    objects = dict(network.objects)
    store.get_assets = lambda *a: pytest.fail("failed thumbnail fell back")
    with pytest.raises(DispatchError):
        store.get_thumbnails([identity])
    assert objects == network.objects and before == {k: ws.rows for k, ws in sheet.sheets.items()}


@pytest.mark.parametrize("change", ["version", "source", "extra", "too_big", "root", "conflict"])
def test_bad_thumbnail_index_rejected_before_authentication(change):
    sheet, store, network = seeded(1)
    identity = store.prepare_next_thumbnails()[0]
    meta = store.thumbnail_locations([identity])[identity]
    record = {"version": 1, "source_sha256": identity, "image": dict(meta)}
    if change == "version":
        record["version"] = True
    elif change == "source":
        record["source_sha256"] = picture(30)["sha256"]
    elif change == "extra":
        record["image"]["data"] = "not allowed"
    elif change == "too_big":
        record["image"]["size"] = THUMB_BYTES + 1
    elif change == "root":
        record["image"]["root"] = "/homes"
    else:
        record["image"]["sha256"] = "f" * 64
    sheet.sheets[THUMB_SHEET].rows.extend(encode_record(identity, record))
    count = len(network.sessions)
    with pytest.raises(DispatchError):
        store.get_thumbnails([identity])
    assert len(network.sessions) == count


def test_upload_timeout_keeps_originals_and_does_not_publish_thumbnail():
    sheet, store, network = seeded(1)
    before = {k: deepcopy(ws.rows) for k, ws in sheet.sheets.items()}
    network.fail_upload = True
    with pytest.raises(DispatchError):
        store.prepare_next_thumbnails()
    assert before == {k: ws.rows for k, ws in sheet.sheets.items()}
    network.fail_upload = False
    assert len(store.prepare_next_thumbnails()) == 1
    assert len(network.objects) == 1


def test_index_write_timeout_recovery_does_not_duplicate_rows():
    sheet, store, network = seeded(1)
    ws = store._sheet(THUMB_SHEET, create=True)
    ws.fail_append_after_write = True
    with pytest.raises(DispatchError, match="待確認"):
        store.prepare_next_thumbnails()
    rows, objects = deepcopy(ws.rows), dict(network.objects)
    assert store.prepare_next_thumbnails() == []
    assert rows == ws.rows and objects == network.objects


def test_legacy_installation_has_bounded_readonly_thumbnail_fallback():
    sheet, _, _ = seeded(2)
    store = NasDispatchStore(sheet)
    before = {k: deepcopy(ws.rows) for k, ws in sheet.sheets.items()}
    ids = store.thumbnail_status()["pending"]
    assert len(store.get_thumbnails(ids)) == 2
    assert before == {k: ws.rows for k, ws in sheet.sheets.items()}
    with pytest.raises(DispatchError):
        store.prepare_next_thumbnails()
    with pytest.raises(DispatchError, match="最多"):
        store.get_thumbnails([picture(n)["sha256"] for n in range(31)])


def test_saved_batch_choice_without_library_binding_can_get_a_thumbnail():
    sheet, store, _ = seeded(1)
    extra = picture(44)
    identity = store.put_asset(asset_bytes(extra), extra["name"])
    batch = store.list_batches()[0]
    batch["items"][0]["images"] = [identity]
    store.save_batch(batch, expected_revision=batch["_revision"])
    assert identity in store.thumbnail_status()["pending"]
    store.prepare_next_thumbnails()
    assert identity in store.get_thumbnails([identity])
