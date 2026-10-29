"""Original-only adoption: no real network, credentials, product or quote writes."""
from copy import deepcopy
import json

import pytest

from dispatch_manager import DispatchError
from dispatch_storage import CloudDispatchStore, asset_bytes, encode_record
from nas_dispatch_storage import LOCATION_SHEET
from nas_original_adoption import adopt_existing_nas_originals
from product_images import PRODUCT_IMAGE_SHEET, subject_key
from quote_image_repair import prepare_image_repair
from synology_image_store import SynologyImageStore
from test_existing_nas_image_repair import fixture, assert_no_upload
from test_quote_image_repair import rows
from test_synology_image_store import Response


@pytest.fixture(autouse=True)
def forbid_upload_and_legacy(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("physical adoption cannot upload or use legacy/display originals")
    monkeypatch.setattr(SynologyImageStore, "put_asset", forbidden)
    monkeypatch.setattr(SynologyImageStore, "put_thumbnail", forbidden)
    monkeypatch.setattr(CloudDispatchStore, "get_assets", forbidden)
    monkeypatch.setattr(CloudDispatchStore, "put_assets", forbidden)


def adopt(store, plan, assets, **kwargs):
    return adopt_existing_nas_originals(store, plan, assets, actor="合成核對", **kwargs)


def put_binding(store, plan, assets, *, actor="synthetic", parent=""):
    source = plan["source"]
    value = {"schema": 1, "identity": source["identity"], "subject": subject_key(source),
             "source_hash": source["source_hash"], "assets": [a["sha256"] for a in assets],
             "actor": actor, "origin": "synthetic", "at": "synthetic-time"}
    store._sheet(PRODUCT_IMAGE_SHEET, create=True).rows.extend(encode_record(source["identity"], value, parent))


def change_source(sheet, plan, field):
    if field == "source":
        sheet.sheets[plan["source"]["category"]].rows[plan["source"]["row"] - 1][1] = "different product"
    elif field == "formula":
        ws = sheet.sheets[plan["source"]["category"]]
        ws.formula_override = deepcopy(plan["formulas"])
        ws.formula_override[1][10] += "+0"
    elif field == "duplicate":
        ws = sheet.sheets[plan["source"]["category"]]
        ws.rows.extend(deepcopy(ws.rows[:6]))
    else:
        sheet.id = "another-synthetic-sheet"


@pytest.mark.parametrize("count", [1, 6, 10])
def test_new_adoption_reads_all_bytes_twice_and_preserves_product_and_other_tabs(count):
    sheet, store, network, assets, metadata, identity, plan = fixture(count)
    for title in ("_報價依據", "_發送批次", "_人工核對", "_LINE紀錄"):
        sheet.add_worksheet(title, rows=5, cols=8).rows = [["preserve existing records"]]
    before, physical = rows(sheet), dict(network.objects)
    phases = []
    report, binding = adopt(store, plan, assets, on_phase=phases.append)
    assert report[0]["結果"] == "已綁定"
    assert binding == store.product_image_bindings()[identity]
    assert binding["assets"] == [a["sha256"] for a in assets]
    assert binding["origin"] == "quote_image_repair_existing_nas"
    assert store.image_locations(binding["assets"]) == {m["sha256"]: m for m in metadata}
    assert {title: sheet.sheets[title].rows for title in before} == before
    assert set(sheet.sheets) - before.keys() == {LOCATION_SHEET, PRODUCT_IMAGE_SHEET}
    assert network.objects == physical
    assert len(network.sessions) == 2
    calls = [kw["data"] for s in network.sessions for _, kw in s.calls]
    downloads = [c for c in calls if c["api"] == "SYNO.FileStation.Download"]
    assert len(downloads) == count * 2
    expected = [store._nas()._path(m) for m in metadata] * 2
    assert [json.loads(c["path"])[0] for c in downloads] == expected
    assert phases == ["readback", "save", "readback"]
    assert_no_upload(network)


@pytest.mark.parametrize("indexed", [0, 1, 2])
@pytest.mark.parametrize("bound", [False, True])
def test_only_missing_indices_and_missing_binding_are_appended(indexed, bound):
    sheet, store, network, assets, metadata, identity, plan = fixture(2)
    if indexed:
        store._publish_locations(metadata[:indexed])
    if bound:
        put_binding(store, plan, assets)
    plan = prepare_image_repair(store, identity)
    old_bindings = deepcopy(store.product_image_bindings())
    before = rows(sheet)
    report, binding = adopt(store, plan, assets)
    assert report[0]["結果"] == ("已存在" if bound else "已綁定")
    if bound:
        assert store.product_image_bindings() == old_bindings
        assert sheet.sheets[PRODUCT_IMAGE_SHEET].rows == before[PRODUCT_IMAGE_SHEET]
    assert binding["assets"] == [a["sha256"] for a in assets]
    observed = store.image_locations(binding["assets"])
    assert observed == {m["sha256"]: m for m in metadata}
    assert_no_upload(network)


@pytest.mark.parametrize("order", ["reverse", "subset", "superset", "subject"])
def test_conflicting_binding_is_rejected_before_nas_and_index_writes(order):
    sheet, store, network, assets, _, identity, plan = fixture(2)
    selected = list(reversed(assets)) if order == "reverse" else assets[:1] if order == "subset" else assets
    if order == "superset":
        from test_quote_image_repair import picture
        selected = [*assets, picture(30)]
    put_binding(store, plan, selected)
    plan = prepare_image_repair(store, identity)
    if order == "subject":
        plan["binding"]["subject"] = "wrong subject"
    before = rows(sheet)
    with pytest.raises(DispatchError, match="順序|配對"):
        adopt(store, plan, assets)
    assert rows(sheet) == before and not network.sessions


@pytest.mark.parametrize("field,value", [("root", "/other"), ("library_id", "other"),
                                        ("size", 1), ("mime", "image/jpeg")])
def test_conflicting_location_is_rejected_before_read_or_write(field, value):
    sheet, store, network, assets, metadata, _, plan = fixture(2)
    ws = store._sheet(LOCATION_SHEET, create=True)
    ws.rows.extend(encode_record(metadata[0]["sha256"], {**metadata[0], field: value}))
    before = rows(sheet)
    with pytest.raises(DispatchError):
        adopt(store, plan, assets)
    assert rows(sheet) == before and not network.sessions


@pytest.mark.parametrize("failure", ["missing", "502", "badbyte"])
def test_first_original_read_failure_has_zero_index_or_binding_writes(failure):
    sheet, store, network, assets, metadata, _, plan = fixture(2)
    path = store._nas()._path(metadata[-1])
    if failure == "missing":
        del network.objects[path]
    elif failure == "badbyte":
        network.objects[path] = network.objects[path][:-1] + b"x"
    else:
        network.override = lambda p: Response({}, status=502) if p["api"] == "SYNO.FileStation.Download" else None
    before = rows(sheet)
    with pytest.raises(DispatchError):
        adopt(store, plan, assets)
    assert rows(sheet) == before
    assert_no_upload(network)


@pytest.mark.parametrize("change", ["source", "formula", "duplicate", "cloud", "binding", "location"])
def test_change_during_first_nas_read_stops_before_index_publication(change):
    sheet, store, network, assets, metadata, _, plan = fixture(2)
    changed = False
    def override(params):
        nonlocal changed
        if params["api"] != "SYNO.FileStation.Download" or changed:
            return None
        changed = True
        if change == "binding":
            put_binding(store, plan, assets)
        elif change == "location":
            ws = store._sheet(LOCATION_SHEET, create=True)
            ws.rows.extend(encode_record(metadata[0]["sha256"], metadata[0]))
        else:
            change_source(sheet, plan, change)
    network.override = override
    with pytest.raises(DispatchError):
        adopt(store, plan, assets)
    assert changed
    if change != "location":
        assert LOCATION_SHEET not in sheet.sheets
    if change != "binding":
        assert PRODUCT_IMAGE_SHEET not in sheet.sheets
    assert_no_upload(network)


@pytest.mark.parametrize("change", ["source", "formula", "binding"])
def test_change_during_location_write_prevents_binding_publication(change):
    sheet, store, network, assets, _, _, plan = fixture(2)
    ws = store._sheet(LOCATION_SHEET, create=True)
    def changing():
        if change == "binding":
            put_binding(store, plan, assets)
        else:
            change_source(sheet, plan, change)
    ws.before_append = changing
    with pytest.raises(DispatchError):
        adopt(store, plan, assets)
    if change != "binding":
        assert PRODUCT_IMAGE_SHEET not in sheet.sheets
    else:
        assert store.product_image_bindings()[plan["source"]["identity"]]["actor"] == "synthetic"
    assert_no_upload(network)


@pytest.mark.parametrize("target", [LOCATION_SHEET, PRODUCT_IMAGE_SHEET])
def test_uncertain_append_is_not_retried_and_explicit_retry_does_not_duplicate(target):
    sheet, store, network, assets, _, identity, plan = fixture(2)
    ws = store._sheet(target, create=True)
    ws.fail_append_after_write = True
    with pytest.raises(DispatchError, match="結果待確認"):
        adopt(store, plan, assets)
    length = len(ws.rows)
    assert len(network.sessions) == 1
    if target == LOCATION_SHEET:
        assert PRODUCT_IMAGE_SHEET not in sheet.sheets
    ws.fail_append_after_write = False
    current = prepare_image_repair(store, identity)
    report, _ = adopt(store, current, assets)
    assert report[0]["結果"] in {"已存在", "已綁定"}
    assert len(ws.rows) == length
    assert_no_upload(network)


@pytest.mark.parametrize("change", ["source", "formula", "binding", "location", "missing", "502", "badbyte"])
def test_final_fresh_original_or_state_failure_cannot_claim_completion(change):
    sheet, store, network, assets, metadata, identity, plan = fixture(2)
    changed = False
    def override(params):
        nonlocal changed
        if params["api"] != "SYNO.FileStation.Download" or len(network.sessions) != 2:
            return None
        if changed:
            return None
        changed = True
        if change == "502":
            return Response({}, status=502)
        if change in {"missing", "badbyte"}:
            path = store._nas()._path(metadata[0])
            if change == "missing":
                del network.objects[path]
            else:
                network.objects[path] = network.objects[path][:-1] + b"x"
        elif change == "location":
            sheet.sheets[LOCATION_SHEET].rows = sheet.sheets[LOCATION_SHEET].rows[:1]
        elif change == "binding":
            current = store.product_image_bindings()[identity]
            put_binding(store, plan, assets, actor="another writer", parent=current["_revision"])
        else:
            change_source(sheet, plan, change)
    network.override = override
    with pytest.raises(DispatchError):
        adopt(store, plan, assets)
    assert changed
    assert_no_upload(network)


def test_tampered_snapshot_and_unconfigured_store_do_not_read_nas():
    sheet, store, network, assets, _, _, plan = fixture(2)
    before = rows(sheet)
    bad = deepcopy(plan)
    bad["formulas"][1][10] += "+0"
    with pytest.raises(DispatchError):
        adopt(store, bad, assets)
    from nas_dispatch_storage import NasDispatchStore
    with pytest.raises(DispatchError):
        adopt(NasDispatchStore(sheet), plan, assets)
    assert rows(sheet) == before and not network.sessions


@pytest.mark.parametrize("target", [LOCATION_SHEET, PRODUCT_IMAGE_SHEET])
def test_batch_adoption_write_429_stops_chunk_without_retry_or_private_error_text(monkeypatch, target):
    import quote_image_repair_batch as batch
    from test_quote_image_repair_batch import setup, upload_count
    from test_quote_image_repair_diagnostics import api_error, assert_no_private
    sheet, store, network, package = setup(2)
    for asset in package["assets"].values():
        nas = store._nas()
        network.objects[nas._path(nas._metadata(asset))] = asset_bytes(asset)
    ws = store._sheet(target, create=True)
    error, calls = api_error(poisoned=True), []
    def failed_append(*args, **kwargs):
        calls.append(True)
        raise error
    monkeypatch.setattr(ws, "append_rows", failed_append)
    run = batch.prepare_batch_repair(store, package, mode="existing_only")
    result = batch.process_next_repair(store, package, run, actor="synthetic", limit=2)
    assert len(calls) == 1
    assert [item["status"] for item in result["items"]] == ["failed", "ready"]
    assert [item["attempts"] for item in result["items"]] == [1, 0]
    assert result["items"][0]["failure_phase"] == "save"
    assert result["halt"]["diagnostic"] == {"kind": "google_sheets_api", "origin": "typed",
                                            "http_status": 429, "api_code": 429}
    assert getattr(error, "str_calls", 0) == 0
    assert len(network.sessions) == 1 and upload_count(network) == 0
    assert_no_private(batch.repair_receipt_payload(package, result))


def test_adoption_rejects_eleven_unique_originals_before_nas_and_writes():
    sheet, store, network, assets, _, _, plan = fixture(11)
    before = rows(sheet)
    with pytest.raises(DispatchError, match="1～10"):
        adopt(store, plan, assets)
    assert rows(sheet) == before and not network.sessions
