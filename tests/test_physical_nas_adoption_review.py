"""Independent adoption review: synthetic bytes, Sheets and NAS transport only."""
from copy import deepcopy
import json

import pytest

from dispatch_manager import DispatchError
from dispatch_storage import CloudDispatchStore, asset_bytes, encode_record
from nas_dispatch_storage import LOCATION_SHEET
from nas_original_adoption import adopt_existing_nas_originals
from product_images import PRODUCT_IMAGE_SHEET, subject_key
from quote_image_repair import prepare_image_repair
import quote_image_repair_batch as batch
from synology_image_store import SynologyImageStore
from test_existing_nas_image_repair import fixture, assert_no_upload
from test_quote_image_repair import picture, rows
from quote_image_repair_zip import read_repair_zip
from test_quote_image_repair_zip import pack
from test_synology_image_store import Response


@pytest.fixture(autouse=True)
def never_upload_or_use_legacy(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("adoption cannot upload or use legacy originals")
    monkeypatch.setattr(SynologyImageStore, "put_asset", forbidden)
    monkeypatch.setattr(SynologyImageStore, "put_thumbnail", forbidden)
    monkeypatch.setattr(CloudDispatchStore, "get_assets", forbidden)
    monkeypatch.setattr(CloudDispatchStore, "put_assets", forbidden)


def seed_binding(store, source, assets, *, previous="", subject=None, actor="existing"):
    value = {"schema": 1, "identity": source["identity"],
             "subject": subject if subject is not None else subject_key(source),
             "source_hash": source["source_hash"],
             "assets": [asset["sha256"] for asset in assets],
             "actor": actor, "origin": "synthetic", "at": "2026-10-29T00:00:00Z"}
    store._sheet(PRODUCT_IMAGE_SHEET, create=True).rows.extend(
        encode_record(source["identity"], value, previous))
    return store.product_image_bindings()[source["identity"]]


def adopt(store, plan, assets, **kwargs):
    return adopt_existing_nas_originals(store, plan, assets, actor="獨立合成核對", **kwargs)


def downloads(network):
    return [kw["data"] for session in network.sessions for _, kw in session.calls
            if kw["data"]["api"] == "SYNO.FileStation.Download"]


@pytest.mark.parametrize("change", ["order", "different", "subset", "superset", "subject"])
def test_existing_binding_conflict_is_rejected_before_any_nas_or_index_write(change):
    sheet, store, network, assets, _, identity, plan = fixture(2)
    selected, subject = list(assets), None
    if change == "order":
        selected.reverse()
    elif change == "different":
        selected[1] = picture(200)
    elif change == "subset":
        selected = selected[:1]
    elif change == "superset":
        selected.append(picture(201))
    else:
        subject = "different synthetic product"
    seed_binding(store, plan["source"], selected, subject=subject)
    plan = prepare_image_repair(store, identity)
    before = rows(sheet)
    with pytest.raises(DispatchError, match="順序或商品配對"):
        adopt(store, plan, assets)
    assert rows(sheet) == before
    assert network.sessions == []


def test_existing_index_filename_is_preserved_while_missing_metadata_is_added():
    sheet, store, network, assets, metadata, identity, _ = fixture(2)
    old = {**metadata[0], "name": "old-synthetic-display-name.png"}
    store._publish_locations([old])
    plan = prepare_image_repair(store, identity)
    physical = deepcopy(network.objects)
    report, binding = adopt(store, plan, assets)
    assert report[0]["結果"] == "已綁定"
    assert binding["assets"] == [a["sha256"] for a in assets]
    observed = store.image_locations(binding["assets"])
    assert observed[assets[0]["sha256"]] == old
    assert observed[assets[1]["sha256"]] == metadata[1]
    assert network.objects == physical
    assert len(downloads(network)) == 4
    assert_no_upload(network)


@pytest.mark.parametrize("had_binding", [False, True])
def test_binding_revision_change_during_first_nas_read_prevents_index_publication(had_binding):
    sheet, store, network, assets, _, identity, plan = fixture(2)
    original = seed_binding(store, plan["source"], assets) if had_binding else None
    plan = prepare_image_repair(store, identity)
    changed = False
    def override(params):
        nonlocal changed
        if params["api"] == "SYNO.FileStation.Download" and not changed:
            changed = True
            seed_binding(store, plan["source"], assets,
                         previous=original["_revision"] if original else "",
                         actor="another writer")
        return None
    network.override = override
    with pytest.raises(DispatchError, match="圖片版本"):
        adopt(store, plan, assets)
    assert changed and LOCATION_SHEET not in sheet.sheets
    assert_no_upload(network)


@pytest.mark.parametrize("change", ["formula", "source", "binding", "index"])
def test_change_during_location_publication_is_detected_before_binding_append(monkeypatch, change):
    sheet, store, network, assets, metadata, identity, plan = fixture(2)
    publish = store._publish_locations
    def changing(values):
        publish(values)
        if change == "formula":
            ws = sheet.sheets[plan["source"]["category"]]
            ws.formula_override = deepcopy(plan["formulas"])
            ws.formula_override[1][10] += "+0"
        elif change == "source":
            sheet.sheets[plan["source"]["category"]].rows[0][1] = "changed source"
        elif change == "binding":
            seed_binding(store, plan["source"], assets, actor="another writer")
        else:
            ws = store._sheet(LOCATION_SHEET)
            ws.rows.extend(encode_record(metadata[0]["sha256"],
                                        {**metadata[0], "size": metadata[0]["size"] + 1}))
    monkeypatch.setattr(store, "_publish_locations", changing)
    with pytest.raises(DispatchError):
        adopt(store, plan, assets)
    if change != "binding":
        assert PRODUCT_IMAGE_SHEET not in sheet.sheets
    else:
        binding = store.product_image_bindings()[identity]
        assert binding["actor"] == "another writer"
    assert len(downloads(network)) == len(assets)
    assert_no_upload(network)


@pytest.mark.parametrize("target", [LOCATION_SHEET, PRODUCT_IMAGE_SHEET])
def test_unknown_write_never_retries_and_explicit_resume_does_not_duplicate(target):
    sheet, store, network, assets, _, identity, plan = fixture(2)
    ws = store._sheet(target, create=True)
    ws.fail_append_after_write = True
    append_calls = []
    append = ws.append_rows
    def counted(*args, **kwargs):
        append_calls.append(1)
        return append(*args, **kwargs)
    ws.append_rows = counted
    with pytest.raises(DispatchError, match="結果待確認"):
        adopt(store, plan, assets)
    assert len(append_calls) == 1
    old_rows = deepcopy(ws.rows)
    ws.fail_append_after_write = False
    plan = prepare_image_repair(store, identity)
    report, binding = adopt(store, plan, assets)
    assert report[0]["結果"] in {"已綁定", "已存在"}
    assert ws.rows == old_rows
    assert len(append_calls) == 1
    assert binding["assets"] == [a["sha256"] for a in assets]
    assert_no_upload(network)


@pytest.mark.parametrize("failure", ["http502", "missing", "hash", "formula", "binding", "index"])
def test_second_nas_read_or_concurrent_change_cannot_return_success(failure):
    sheet, store, network, assets, metadata, identity, plan = fixture(2)
    count = 0
    def override(params):
        nonlocal count
        if params["api"] != "SYNO.FileStation.Download":
            return None
        count += 1
        if count != len(assets) + 1:
            return None
        path = json.loads(params["path"])[0]
        if failure == "http502":
            return Response({}, status=502)
        if failure == "missing":
            del network.objects[path]
        elif failure == "hash":
            raw = network.objects[path]
            network.objects[path] = raw[:-1] + bytes([raw[-1] ^ 1])
        elif failure == "formula":
            ws = sheet.sheets[plan["source"]["category"]]
            ws.formula_override = deepcopy(plan["formulas"])
            ws.formula_override[1][10] += "+0"
        elif failure == "binding":
            current = store.product_image_bindings()[identity]
            seed_binding(store, plan["source"], assets,
                         previous=current["_revision"], actor="another writer")
        else:
            ws = store._sheet(LOCATION_SHEET)
            ws.rows = ws.rows[:1]
        return None
    network.override = override
    with pytest.raises(DispatchError):
        adopt(store, plan, assets)
    assert count >= len(assets) + 1
    assert PRODUCT_IMAGE_SHEET in sheet.sheets
    assert_no_upload(network)


def test_batch_incomplete_existing_uses_new_helper_and_exactly_two_nas_reads(monkeypatch):
    sheet, store, network, assets, _, identity, _ = fixture(2)
    category, no = identity.split(":", 1)
    package = read_repair_zip(pack({"schema": 1, "products": [{
        "sheet": category, "actual_no": no, "sha256": [a["sha256"] for a in assets]}]},
        [(f"images/{a['sha256']}.png", asset_bytes(a)) for a in assets]))
    def forbidden(*args, **kwargs):
        raise AssertionError("low-read adoption used legacy/old saver/duplicate readback")
    monkeypatch.setattr(CloudDispatchStore, "get_assets", forbidden)
    monkeypatch.setattr(CloudDispatchStore, "put_assets", forbidden)
    for name in ("get_assets", "get_display_asset", "save_product_images", "put_assets"):
        monkeypatch.setattr(store, name, forbidden)
    monkeypatch.setattr(batch, "save_existing_nas_image_repair", forbidden)
    monkeypatch.setattr(batch, "_fresh_nas_readback", forbidden)
    run = batch.prepare_batch_repair(store, package, mode="existing_only")
    before = rows(sheet)
    run = batch.process_next_repair(store, package, run, actor="independent batch")
    assert run["items"][0]["status"] == "completed"
    assert run["items"][0]["receipt"]["sha256"] == [a["sha256"] for a in assets]
    assert len(downloads(network)) == 2 * len(assets)
    assert len(network.sessions) == 2
    assert {name: sheet.sheets[name].rows for name in before} == before
    observed = rows(sheet)
    run = batch.process_next_repair(store, package, run, actor="independent batch")
    assert rows(sheet) == observed and len(network.sessions) == 2
    assert_no_upload(network)
