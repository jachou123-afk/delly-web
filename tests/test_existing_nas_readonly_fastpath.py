"""Existing NAS originals fast path: real guards with fake Sheets/transport."""
from copy import deepcopy
import json

import pytest

from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_manager import DispatchError
from dispatch_storage import CloudDispatchStore, asset_bytes
from nas_dispatch_storage import LOCATION_SHEET, NasDispatchStore
from product_images import PRODUCT_IMAGE_SHEET
from quote_image_repair import prepare_image_repair
import quote_image_repair_batch as repair
from quote_image_repair_zip import read_repair_zip
from synology_image_store import SynologyImageStore
from test_nas_dispatch_storage import Network
from test_product_image_library import assignment
from test_quote_image_repair_zip import image, pack, sha
from test_synology_image_store import Response, config


class ReadNetwork(Network):
    def __init__(self):
        super().__init__()
        self.on_download = None
        self.download_error = None

    def factory(self, settings):
        nas = super().factory(settings)
        original = nas._session_factory
        def session():
            result = original()
            def override(params):
                assert params["api"] != "SYNO.FileStation.Upload", "fast verifier attempted upload"
                if params["api"] == "SYNO.FileStation.Download":
                    if self.on_download:
                        self.on_download(json.loads(params["path"])[0])
                    return self.download_error
                return None
            result.override = override
            return result
        nas._session_factory = session
        return nas


def fixture(count=2):
    raws = [image(n) for n in range(count)]
    package = read_repair_zip(pack({"schema": 1, "products": [{"sheet": "G正版", "actual_no": "no1",
        "sha256": [sha(raw) for raw in raws]}]}, [(f"images/{sha(raw)}.png", raw) for raw in raws]))
    assets = [package["assets"][key] for key in package["products"][0]["sha256"]]
    sheet = FakeSpreadsheet(product_rows((1, 2)))
    for title in ("_報價依據", "_發送批次", "_人工核對", "_LINE紀錄"):
        sheet.add_worksheet(title, rows=5, cols=8).rows = [["preserve existing rows"]]
    legacy = CloudDispatchStore(sheet)
    source = legacy.catalog()[0]
    assert legacy.save_product_images([assignment(source, assets)], actor="synthetic", origin="test")[0]["結果"] == "已綁定"
    network = ReadNetwork()
    store = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    metadata = [store._nas()._metadata(asset) for asset in assets]
    for asset, meta in zip(assets, metadata):
        network.objects[store._nas()._path(meta)] = asset_bytes(asset)
    store._publish_locations(metadata)
    return sheet, store, network, package, assets, metadata


def rows(sheet):
    return {title: deepcopy(ws.rows) for title, ws in sheet.sheets.items()}


def calls(network):
    return [kw["data"] for session in network.sessions for _, kw in session.calls]


def forbid_writes(monkeypatch, sheet, store, *, forbid_adoption=True):
    def forbidden(*args, **kwargs):
        raise AssertionError("complete-existing path must remain purely read-only")
    for ws in sheet.sheets.values():
        monkeypatch.setattr(ws, "update", forbidden)
        monkeypatch.setattr(ws, "append_rows", forbidden)
    monkeypatch.setattr(sheet, "add_worksheet", forbidden)
    for method in ("save_product_images", "put_asset", "put_assets", "_publish_locations", "get_assets", "get_display_asset"):
        monkeypatch.setattr(store, method, forbidden)
    monkeypatch.setattr(SynologyImageStore, "put_asset", forbidden)
    monkeypatch.setattr(SynologyImageStore, "put_thumbnail", forbidden)
    monkeypatch.setattr(repair, "save_image_repair", forbidden)
    monkeypatch.setattr(repair, "_fresh_nas_readback", forbidden)
    if forbid_adoption:
        monkeypatch.setattr(repair, "save_existing_nas_image_repair", forbidden)


def assert_read_only(network, before):
    assert network.objects == before
    assert not any(call["api"] == "SYNO.FileStation.Upload" for call in calls(network))
    for session in network.sessions:
        assert session.closed and session.trust_env is False
        assert all(kw["verify"] is True and kw["allow_redirects"] is False and not kw.get("files")
                   for _, kw in session.calls)


@pytest.mark.parametrize("count", [1, 6, 10])
def test_complete_existing_group_is_verified_once_without_adoption_or_any_write(monkeypatch, count):
    sheet, store, network, package, assets, metadata = fixture(count)
    run = repair.prepare_batch_repair(store, package, mode="existing_only")
    before, physical = rows(sheet), dict(network.objects)
    prepare_calls, real_prepare = [], repair.prepare_image_repair
    def preparing(*args, **kwargs):
        prepare_calls.append(True)
        return real_prepare(*args, **kwargs)
    monkeypatch.setattr(repair, "prepare_image_repair", preparing)
    forbid_writes(monkeypatch, sheet, store)
    updates = []
    result = repair.process_next_repair(store, package, run, actor="synthetic", on_update=updates.append)
    item = result["items"][0]
    assert item["status"] == "completed" and item["attempts"] == 1
    assert item["receipt"]["result"] == "已存在" and item["receipt"]["mode"] == "existing_only"
    assert item["receipt"]["sha256"] == [asset["sha256"] for asset in assets]
    assert item["receipt"]["binding_revision"] == store.product_image_bindings()[item["identity"]]["_revision"]
    assert len(prepare_calls) == 2  # Existing process plan, then final source/formula/binding check.
    assert len(updates) == 1 and updates[-1] == result
    downloads = [call for call in calls(network) if call["api"] == "SYNO.FileStation.Download"]
    assert len(downloads) == count
    assert [json.loads(call["path"])[0] for call in downloads] == [store._nas()._path(meta) for meta in metadata]
    list_calls = [call for call in calls(network) if call["api"] == "SYNO.FileStation.List"]
    assert list_calls and all(json.loads(call["path"]) == [store.nas_config.root] for call in list_calls)
    assert rows(sheet) == before
    assert_read_only(network, physical)
    sessions_before = len(network.sessions)
    assert repair.process_next_repair(store, package, result, actor="synthetic") == result
    assert len(network.sessions) == sessions_before


@pytest.mark.parametrize("missing", ["binding", "all_locations", "one_location"])
def test_incomplete_eligibility_uses_only_original_adoption_entry_and_keeps_mode(monkeypatch, missing):
    sheet, store, network, package, assets, _ = fixture(2)
    if missing == "binding":
        sheet.sheets[PRODUCT_IMAGE_SHEET].rows = sheet.sheets[PRODUCT_IMAGE_SHEET].rows[:1]
    elif missing == "all_locations":
        sheet.sheets[LOCATION_SHEET].rows = sheet.sheets[LOCATION_SHEET].rows[:1]
    else:
        excluded = assets[-1]["sha256"]
        sheet.sheets[LOCATION_SHEET].rows = [row for row in sheet.sheets[LOCATION_SHEET].rows if len(row) < 2 or row[1] != excluded]
    plan = prepare_image_repair(store, package["products"][0]["identity"])
    assert repair._verify_complete_existing_nas(store, plan, assets) is None
    assert not network.sessions
    run = repair.prepare_batch_repair(store, package, mode="existing_only")
    adopted = []
    forbid_writes(monkeypatch, sheet, store, forbid_adoption=False)
    def adoption(*args, **kwargs):
        adopted.append((args[1]["source"]["identity"], [asset["sha256"] for asset in args[2]]))
        return [{"結果": "待處理", "原因": "synthetic adoption not executed"}]
    monkeypatch.setattr(repair, "save_existing_nas_image_repair", adoption)
    result = repair.process_next_repair(store, package, run, actor="synthetic")
    assert adopted == [(package["products"][0]["identity"], [asset["sha256"] for asset in assets])]
    assert result["mode"] == "existing_only" and result["items"][0]["status"] == "failed"
    assert not network.sessions


def test_misordered_binding_cannot_fast_verify_or_silently_change_main_image(monkeypatch):
    sheet, store, network, package, assets, _ = fixture(2)
    bindings = deepcopy(store.product_image_bindings())
    identity = package["products"][0]["identity"]
    bindings[identity]["assets"].reverse()
    monkeypatch.setattr(store, "product_image_bindings", lambda: deepcopy(bindings))
    plan = prepare_image_repair(store, identity)
    before = rows(sheet)
    forbid_writes(monkeypatch, sheet, store)
    try:
        assert repair._verify_complete_existing_nas(store, plan, assets) is None
    except DispatchError:
        pass
    run = repair.prepare_batch_repair(store, package, mode="existing_only")
    assert run["items"][0]["status"] == "blocked"
    assert repair.process_next_repair(store, package, run, actor="synthetic") == run
    assert rows(sheet) == before and not network.sessions


@pytest.mark.parametrize("change", ["size", "mime", "root", "library_id", "sha256"])
def test_contradictory_location_metadata_raises_without_adoption_or_nas(monkeypatch, change):
    sheet, store, network, package, assets, _ = fixture(2)
    locations = deepcopy(store.image_locations([asset["sha256"] for asset in assets]))
    first = locations[assets[0]["sha256"]]
    first[change] = {"size": first["size"] + 1, "mime": "image/jpeg", "root": "/other",
                     "library_id": "other-library", "sha256": "0" * 64}[change]
    monkeypatch.setattr(store, "image_locations", lambda ids: deepcopy(locations))
    plan = prepare_image_repair(store, package["products"][0]["identity"])
    before = rows(sheet)
    forbid_writes(monkeypatch, sheet, store)
    with pytest.raises(DispatchError):
        repair._verify_complete_existing_nas(store, plan, assets)
    assert rows(sheet) == before and not network.sessions


@pytest.mark.parametrize("failure", ["http502", "missing", "badbyte"])
def test_existing_eligible_original_failure_never_becomes_adoption_or_display_fallback(monkeypatch, failure):
    sheet, store, network, package, assets, metadata = fixture(2)
    path = store._nas()._path(metadata[0])
    if failure == "http502":
        network.download_error = Response({}, status=502)
    elif failure == "missing":
        del network.objects[path]
    else:
        network.objects[path] = network.objects[path][:-1] + b"x"
    run = repair.prepare_batch_repair(store, package, mode="existing_only")
    before, physical = rows(sheet), dict(network.objects)
    forbid_writes(monkeypatch, sheet, store)
    result = repair.process_next_repair(store, package, run, actor="synthetic")
    item = result["items"][0]
    assert item["status"] == "failed" and item["receipt"] is None
    assert item["attempts"] == 1 and result["mode"] == "existing_only"
    if failure == "http502":
        assert "502" in item["reason"] and "missing" not in item["reason"].lower()
    assert rows(sheet) == before
    assert_read_only(network, physical)
    previous_sessions = len(network.sessions)
    assert repair.process_next_repair(store, package, result, actor="synthetic") == result
    assert len(network.sessions) == previous_sessions


def source_mutation(sheet, plan, field):
    ws = sheet.sheets[plan["source"]["category"]]
    if field == "source":
        ws.rows[plan["source"]["row"] - 1][1] = "different product"
    elif field == "formula":
        ws.formula_override = deepcopy(plan["formulas"])
        ws.formula_override[1][10] += "+0"


@pytest.mark.parametrize("change", ["source", "formula", "binding", "location_changed", "location_removed"])
def test_mutations_during_nas_read_fail_the_entire_group_without_adoption(monkeypatch, change):
    sheet, store, network, package, assets, _ = fixture(2)
    identity = package["products"][0]["identity"]
    plan = prepare_image_repair(store, identity)
    bindings = deepcopy(store.product_image_bindings())
    locations = deepcopy(store.image_locations([asset["sha256"] for asset in assets]))
    state = {"changed": False}
    changed_bindings, changed_locations = deepcopy(bindings), deepcopy(locations)
    changed_bindings[identity]["_revision"] = "different revision"
    if change == "location_removed":
        changed_locations.pop(assets[-1]["sha256"])
    else:
        changed_locations[assets[-1]["sha256"]]["name"] = "different index filename.png"
    monkeypatch.setattr(store, "product_image_bindings", lambda: deepcopy(changed_bindings if state["changed"] and change == "binding" else bindings))
    monkeypatch.setattr(store, "image_locations", lambda ids: deepcopy(changed_locations if state["changed"] and change.startswith("location") else locations))
    def changing(path):
        state["changed"] = True
        if change in {"source", "formula"}:
            source_mutation(sheet, plan, change)
    network.on_download = changing
    run = repair.prepare_batch_repair(store, package, mode="existing_only")
    physical = dict(network.objects)
    forbid_writes(monkeypatch, sheet, store)
    result = repair.process_next_repair(store, package, run, actor="synthetic")
    assert result["items"][0]["status"] == "failed" and result["items"][0]["receipt"] is None
    assert state["changed"]
    assert_read_only(network, physical)


@pytest.mark.parametrize("change", ["source", "formula", "binding"])
def test_mutations_inside_final_prepare_are_detected(monkeypatch, change):
    sheet, store, network, package, _, _ = fixture(2)
    identity = package["products"][0]["identity"]
    original_plan = prepare_image_repair(store, identity)
    bindings = deepcopy(store.product_image_bindings())
    changed = deepcopy(bindings)
    changed[identity]["_revision"] = "revision during final prepare"
    state = {"calls": 0, "binding_changed": False}
    monkeypatch.setattr(store, "product_image_bindings", lambda: deepcopy(changed if state["binding_changed"] else bindings))
    real_prepare = repair.prepare_image_repair
    def preparing(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 2:
            if change == "binding":
                state["binding_changed"] = True
            else:
                source_mutation(sheet, original_plan, change)
        return real_prepare(*args, **kwargs)
    monkeypatch.setattr(repair, "prepare_image_repair", preparing)
    run = repair.prepare_batch_repair(store, package, mode="existing_only")
    physical = dict(network.objects)
    forbid_writes(monkeypatch, sheet, store)
    result = repair.process_next_repair(store, package, run, actor="synthetic")
    assert state["calls"] == 2
    assert result["items"][0]["status"] == "failed" and result["items"][0]["receipt"] is None
    assert_read_only(network, physical)


def test_upload_mode_keeps_original_save_entry_even_when_binding_and_locations_are_complete(monkeypatch):
    _, store, network, package, _, _ = fixture(2)
    run = repair.prepare_batch_repair(store, package, mode="upload")
    saved = []
    def save(*args, **kwargs):
        saved.append(args[1]["source"]["identity"])
        return [{"結果": "待處理", "原因": "synthetic original entry not executed"}]
    monkeypatch.setattr(repair, "save_image_repair", save)
    monkeypatch.setattr(repair, "_verify_complete_existing_nas", lambda *a: pytest.fail("upload cannot use existing-only fast path"))
    result = repair.process_next_repair(store, package, run, actor="synthetic")
    assert saved == [package["products"][0]["identity"]]
    assert result["mode"] == "upload" and result["items"][0]["status"] == "failed"
    assert not network.sessions
