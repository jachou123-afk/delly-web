from copy import deepcopy
import json

import pytest

from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_manager import DispatchError, new_batch
from dispatch_storage import CloudDispatchStore, IMAGE_SHEET, asset_bytes, encode_record
from nas_dispatch_storage import LOCATION_SHEET, NasDispatchStore, configured_store
from product_images import PRODUCT_IMAGE_SHEET
from synology_image_store import SynologyImageStore
from test_product_image_library import picture, assignment
from test_synology_image_store import Session, config


class Network:
    def __init__(self):
        self.objects, self.sessions = {}, []
        self.fail_upload = False

    def factory(self, settings):
        def session():
            result = Session()
            result.objects = self.objects
            result.fail_upload_after_write = self.fail_upload
            self.sessions.append(result)
            return result
        return SynologyImageStore(settings, session)


def seeded(count=3, total=None):
    sheet = FakeSpreadsheet(product_rows(range(1, (total or count) + 1)))
    legacy = CloudDispatchStore(sheet)
    products = legacy.catalog()
    legacy.save_product_images([assignment(p, [picture(n)]) for n, p in enumerate(products[:count])],
                               actor="synthetic", origin="test")
    legacy.save_batch(new_batch("synthetic", "no LINE", products, "test"))
    network = Network()
    store = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    return sheet, store, network


def test_63_originals_69_products_migrate_in_seven_batches_no_other_writes():
    sheet, store, network = seeded(63, 69)
    before = {name: deepcopy(ws.rows) for name, ws in sheet.sheets.items()}
    refs_before = store.product_image_refs(store.catalog())
    sizes, moved = [], []
    while store.migration_status()["pending"]:
        group = store.migrate_next_images()
        sizes.append(len(group))
        moved.extend(group)
    assert sizes == [10] * 6 + [3]
    assert len(set(moved)) == 63 and len(network.objects) == 63
    assert len(sheet.sheets[LOCATION_SHEET].rows) == 64
    assert set(sheet.sheets) - set(before) == {LOCATION_SHEET}
    assert before == {name: sheet.sheets[name].rows for name in before}
    assert store.product_image_refs(store.catalog()) == refs_before
    assert sum(bool(v) for v in refs_before["images"].values()) == 63
    checked = [row for offset in range(0, 63, 10)
               for row in store.verify_migrated_images(moved[offset:offset + 10])]
    assert len(checked) == 63
    assert sum(r["bytes"] for r in checked) == sum(len(v) for v in network.objects.values())
    assert all(s.closed for s in network.sessions)
    assert all(not item["review"] for item in store.list_batches()[0]["items"])
    count = len(network.sessions)
    assert store.migrate_next_images() == [] and len(network.sessions) == count


def test_migrated_reads_never_load_legacy_payload_even_after_reopen():
    sheet, store, network = seeded(2)
    ids = store.migrate_next_images()
    sheet.sheets[IMAGE_SHEET].batch_get = lambda *a, **k: pytest.fail("legacy payload read")
    reopened = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    assert set(reopened.get_assets(ids)) == set(ids)
    assert reopened.migration_status()["pending"] == []


def test_mixed_reads_use_legacy_only_for_unmapped_ids():
    sheet, store, network = seeded(2)
    ids = store.migration_status()["eligible"]
    original = CloudDispatchStore(sheet).get_asset(ids[0])
    store.put_assets([original])
    legacy_ws = sheet.sheets[IMAGE_SHEET]
    legacy_ws.read_ranges.clear()
    assert set(store.get_assets(ids)) == set(ids)
    old_index = legacy_ws.get("A2:B")
    migrated_row = next(n for n, row in enumerate(old_index, 2) if row[1] == ids[0])
    assert f"A{migrated_row}:H{migrated_row}" not in legacy_ws.read_ranges
    assert len(network.objects) == 1


def test_new_originals_and_bindings_use_nas_without_legacy_payload_sheet():
    sheet = FakeSpreadsheet(product_rows([1]))
    network = Network()
    store = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    source = store.catalog()[0]
    before = deepcopy(sheet.sheets["G正版"].rows)
    result = store.save_product_images([assignment(source, [picture()])], actor="test", origin="test")
    assert result[0]["結果"] == "已綁定"
    assert IMAGE_SHEET not in sheet.sheets
    assert {LOCATION_SHEET, PRODUCT_IMAGE_SHEET} <= set(sheet.sheets)
    assert sheet.sheets["G正版"].rows == before
    assert asset_bytes(store.get_asset(picture()["sha256"])) == asset_bytes(picture())
    rows = deepcopy(sheet.sheets[LOCATION_SHEET].rows)
    assert store.put_asset(asset_bytes(picture()), "another-name.png") == picture()["sha256"]
    assert sheet.sheets[LOCATION_SHEET].rows == rows and len(network.objects) == 1
    # New NAS-only images are not misclassified as old originals to migrate.
    assert not store.migration_status()["eligible"]


@pytest.mark.parametrize("missing", [True, False])
def test_no_secret_or_invalid_secret_cannot_fall_back_for_migrated_images(missing):
    sheet, store, _ = seeded(1)
    identity = store.migrate_next_images()[0]
    broken = NasDispatchStore(sheet, config_error=not missing)
    with pytest.raises(DispatchError, match="不會退回舊圖"):
        broken.get_asset(identity)
    before = {k: deepcopy(ws.rows) for k, ws in sheet.sheets.items()}
    with pytest.raises(DispatchError):
        broken.put_assets([picture()])
    assert before == {k: ws.rows for k, ws in sheet.sheets.items()}


def test_unconfigured_installations_keep_legacy_behavior_without_creating_index():
    sheet = FakeSpreadsheet()
    store = NasDispatchStore(sheet)
    identity = store.put_asset(asset_bytes(picture()), "same.png")
    assert store.get_asset(identity)["sha256"] == identity
    assert LOCATION_SHEET not in sheet.sheets


@pytest.mark.parametrize("bad", [{}, {"password": "synthetic-secret"}, {"root": "/"}])
def test_config_error_masked_and_blocks_new_writes_but_not_quote_catalog(bad):
    sheet = FakeSpreadsheet()
    store = configured_store(sheet, lambda: bad)
    assert store.catalog()
    with pytest.raises(DispatchError) as error:
        store.put_assets([picture()])
    assert "synthetic-secret" not in str(error.value)
    assert set(sheet.sheets) == {"G正版"}


@pytest.mark.parametrize("kind", ["missing", "corrupt", "wrong_library", "wrong_root"])
def test_nas_failure_never_falls_back_or_overwrites(kind):
    sheet, store, network = seeded(1)
    identity = store.migrate_next_images()[0]
    before = {k: deepcopy(ws.rows) for k, ws in sheet.sheets.items()}
    path = next(iter(network.objects))
    if kind == "missing":
        network.objects.pop(path)
    elif kind == "corrupt":
        network.objects[path] = b"x" * len(network.objects[path])
    else:
        store.nas_config = config(**({"library_id": "different-library"} if kind == "wrong_library"
                                     else {"root": "/different-root"}))
    objects_before = dict(network.objects)
    with pytest.raises(DispatchError):
        store.get_asset(identity)
    with pytest.raises(DispatchError):
        store.put_assets([picture()])
    assert objects_before == network.objects
    assert before == {k: ws.rows for k, ws in sheet.sheets.items()}


@pytest.mark.parametrize("change", [{"data": "synthetic-secret"}, {"schema": True},
    {"size": 0}, {"sha256": "wrong"}, {"root": "/homes"}, {"mime": []}])
def test_invalid_pointer_rejected_without_touching_nas(change):
    sheet, store, network = seeded(1)
    identity = store.migrate_next_images()[0]
    value = store.image_locations([identity])[identity]
    sheet.sheets[LOCATION_SHEET].rows[1:] = encode_record(identity, {**value, **change})
    count = len(network.sessions)
    with pytest.raises(DispatchError):
        store.get_asset(identity)
    assert len(network.sessions) == count


def test_duplicate_identical_location_is_safe_but_conflict_is_not():
    sheet, store, _ = seeded(1)
    identity = store.migrate_next_images()[0]
    value = store.image_locations([identity])[identity]
    ws = sheet.sheets[LOCATION_SHEET]
    ws.rows.extend(encode_record(identity, {**value, "name": "other.png"}))
    assert store.get_asset(identity)
    ws.rows.extend(encode_record(identity, {**value, "library_id": "another-library"}))
    with pytest.raises(DispatchError, match="衝突|不同圖庫"):
        store.get_asset(identity)


def test_oversized_filename_cannot_bloat_location_sheet():
    sheet, store, network = seeded(1)
    with pytest.raises(DispatchError, match="檔名過長"):
        store.put_assets([{**picture(), "name": "a" * 513}])
    assert not network.sessions and LOCATION_SHEET not in sheet.sheets


def test_upload_failure_leaves_no_pointer_or_changes_to_original_data():
    sheet, store, network = seeded(1)
    before = {k: deepcopy(ws.rows) for k, ws in sheet.sheets.items()}
    network.fail_upload = True
    with pytest.raises(DispatchError, match="待確認"):
        store.migrate_next_images()
    assert before == {k: ws.rows for k, ws in sheet.sheets.items()}
    assert len(network.objects) == 1 and LOCATION_SHEET not in sheet.sheets
    network.fail_upload = False
    assert len(store.migrate_next_images()) == 1 and len(network.objects) == 1


def test_index_append_timeout_is_recovered_by_readback_not_duplicate_write():
    sheet, store, network = seeded(1)
    ws = store._sheet(LOCATION_SHEET, create=True)
    ws.fail_append_after_write = True
    with pytest.raises(DispatchError, match="索引儲存結果待確認"):
        store.migrate_next_images()
    assert len(store.migration_status()["migrated"]) == 1
    before = deepcopy(ws.rows)
    assert store.migrate_next_images() == []
    assert ws.rows == before and len(network.objects) == 1
    assert len(store.verify_migrated_images(store.migration_status()["migrated"])) == 1


@pytest.mark.parametrize("ids", [[], [picture(i)["sha256"] for i in range(11)], [picture(99)["sha256"]]])
def test_verification_rejects_empty_oversized_or_unmigrated_scope(ids):
    _, store, network = seeded(1)
    with pytest.raises(DispatchError):
        store.verify_migrated_images(ids)
    assert not network.sessions


def test_location_rows_contain_only_small_metadata_no_originals_or_credentials():
    sheet, store, _ = seeded(2)
    store.migrate_next_images()
    text = json.dumps(sheet.sheets[LOCATION_SHEET].rows, ensure_ascii=False)
    assert len(text) < 2000
    assert all(word not in text for word in ("synthetic-secret", "image_test", "nas.example.test", '"data"'))
