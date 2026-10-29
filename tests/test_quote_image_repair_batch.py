"""Formal single-item NAS paths with synthetic originals and Sheets only."""
from copy import deepcopy
import json

import pytest

from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_manager import DispatchError
from dispatch_storage import CloudDispatchStore, asset_bytes
from nas_dispatch_storage import NasDispatchStore
from quote_image_repair_batch import (prepare_batch_repair, process_next_repair,
                                      retry_failed_repair, safe_repair_error)
from quote_image_repair_zip import read_repair_zip
from synology_image_store import NasDiagnosticError
from test_nas_dispatch_storage import Network
from test_product_image_library import assignment
from test_quote_image_repair_zip import image, pack, sha
from test_synology_image_store import Response, config


def setup(count=3):
    products = [{"sheet": "G正版", "actual_no": f"no{n + 1}", "sha256": [sha(image(n))]}
                for n in range(count)]
    package = read_repair_zip(pack({"schema": 1, "products": products},
                                   [(f"images/{sha(image(n))}.png", image(n)) for n in range(count)]))
    sheet = FakeSpreadsheet(product_rows(range(1, count + 1)))
    for name in ("_報價依據", "_發送批次", "_人工核對", "_LINE紀錄"):
        sheet.add_worksheet(name, rows=2, cols=8).rows = [["preserve content"]]
    network = Network()
    store = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    return sheet, store, network, package


def upload_count(network):
    return sum(kw["data"]["api"] == "SYNO.FileStation.Upload"
               for session in network.sessions for _, kw in session.calls)


def test_bounded_ordered_save_and_receipts_skip_success_on_next_submission():
    sheet, store, network, package = setup(12)
    before = {name: deepcopy(ws.rows) for name, ws in sheet.sheets.items()}
    run = prepare_batch_repair(store, package, mode="upload")
    assert not network.sessions and not network.objects
    assert {name: ws.rows for name, ws in sheet.sheets.items()} == before
    updates = []
    run = process_next_repair(store, package, run, actor="synthetic", limit=10, on_update=updates.append)
    assert [item["status"] for item in run["items"]] == ["completed"] * 10 + ["ready"] * 2
    assert len(updates) == 10 and upload_count(network) == 10
    assert all(item["receipt"]["binding_revision"] for item in run["items"][:10])
    first_receipt = deepcopy(run["items"][0]["receipt"])
    run = process_next_repair(store, package, run, actor="synthetic", limit=10)
    assert all(item["status"] == "completed" for item in run["items"])
    assert run["items"][0]["receipt"] == first_receipt and upload_count(network) == 12
    count = len(network.sessions)
    assert process_next_repair(store, package, run, actor="synthetic") == run
    assert len(network.sessions) == count
    assert {name: sheet.sheets[name].rows for name in before} == before
    reopened = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    for item in run["items"]:
        for expected_sha in item["sha256"]:
            assert asset_bytes(reopened.get_asset(expected_sha)) == asset_bytes(package["assets"][expected_sha])


def test_existing_only_verifies_all_bytes_without_upload_and_keeps_mode_on_failure():
    sheet, store, network, package = setup(2)
    for asset in package["assets"].values():
        nas = store._nas()
        network.objects[nas._path(nas._metadata(asset))] = asset_bytes(asset)
    run = prepare_batch_repair(store, package, mode="existing_only")
    run = process_next_repair(store, package, run, actor="synthetic")
    assert all(item["status"] == "completed" for item in run["items"])
    assert upload_count(network) == 0
    assert len(store.product_image_bindings()) == 2


def test_502_is_isolated_and_not_missing_or_automatic_upload_or_retry():
    _, store, network, package = setup(2)
    for asset in package["assets"].values():
        nas = store._nas()
        network.objects[nas._path(nas._metadata(asset))] = asset_bytes(asset)
    failed_sha = package["products"][0]["sha256"][0]
    original = store._nas_factory
    failures = [True]

    def factory(settings):
        nas = original(settings)
        session_factory = nas._session_factory
        def session():
            value = session_factory()
            value.override = lambda p: Response({}, status=502) if (failures[0]
                and p["api"] == "SYNO.FileStation.Download" and failed_sha in str(p.get("path"))) else None
            return value
        nas._session_factory = session
        return nas

    store._nas_factory = factory
    run = process_next_repair(store, package, prepare_batch_repair(store, package, mode="existing_only"), actor="synthetic")
    assert [i["status"] for i in run["items"]] == ["failed", "completed"]
    assert "502" in run["items"][0]["reason"] and "missing" not in run["items"][0]["reason"]
    assert upload_count(network) == 0
    count = len(network.sessions)
    assert process_next_repair(store, package, run, actor="synthetic") == run
    assert len(network.sessions) == count
    failures[0] = False
    retry = retry_failed_repair(package, run)
    assert retry["mode"] == "existing_only" and retry["items"][1]["receipt"] == run["items"][1]["receipt"]
    done = process_next_repair(store, package, retry, actor="synthetic")
    assert all(i["status"] == "completed" for i in done["items"]) and upload_count(network) == 0


@pytest.mark.parametrize("change", ["price", "formula", "source"])
def test_source_change_does_not_retarget_or_change_quote_and_other_products_continue(change):
    sheet, store, network, package = setup(2)
    run = prepare_batch_repair(store, package, mode="upload")
    if change == "price":
        sheet.sheets["G正版"].rows[1][6] = "99.9"
    elif change == "source":
        sheet.sheets["G正版"].rows[0][1] = "different product"
    else:
        # Formula snapshot is captured at execution, then must stay unchanged during save.
        original = store.put_assets
        def changing(assets):
            value = original(assets)
            sheet.sheets["G正版"].formula_override = [[""] * 12 for _ in range(12)]
            return value
        store.put_assets = changing
    before = deepcopy(sheet.sheets["G正版"].rows)
    result = process_next_repair(store, package, run, actor="synthetic")
    assert result["items"][0]["status"] == "failed"
    assert sheet.sheets["G正版"].rows == before
    if change != "formula":
        assert result["items"][1]["status"] == "completed" and upload_count(network) == 1


def test_missing_duplicate_and_different_binding_are_blocked_without_nas_access():
    sheet, store, network, package = setup(3)
    source = store.catalog()[0]
    other = package["assets"][package["products"][1]["sha256"][0]]
    store.save_product_images([assignment(source, [other])], actor="synthetic", origin="test")
    sheet.sheets["G正版"].rows[12:18] = deepcopy(sheet.sheets["G正版"].rows[6:12])
    network.sessions.clear()
    run = prepare_batch_repair(store, package, mode="upload")
    assert [i["status"] for i in run["items"]] == ["blocked"] * 3
    assert process_next_repair(store, package, run, actor="synthetic") == run
    assert not network.sessions


@pytest.mark.parametrize("change", ["package", "scope", "receipt", "limit"])
def test_changed_package_scope_or_receipt_and_excess_chunk_never_write(change):
    _, store, network, package = setup(1)
    run = prepare_batch_repair(store, package, mode="upload")
    if change == "package":
        package["products"][0]["actual_no"] = "no9"
    elif change == "scope":
        run["mode"] = "existing_only"
    elif change == "receipt":
        run["items"][0].update(status="completed", receipt={})
    with pytest.raises(DispatchError):
        process_next_repair(store, package, run, actor="synthetic", limit=11 if change == "limit" else 10)
    assert not network.sessions and not network.objects


def test_interrupted_ui_keeps_per_item_receipt_and_resume_does_not_repeat_upload():
    _, store, network, package = setup(2)
    run = prepare_batch_repair(store, package, mode="upload")
    checkpoint = []
    def stop_after_one(updated):
        checkpoint.append(updated)
        raise RuntimeError("synthetic interruption")
    with pytest.raises(RuntimeError):
        process_next_repair(store, package, run, actor="synthetic", on_update=stop_after_one)
    assert checkpoint[0]["items"][0]["status"] == "completed" and upload_count(network) == 1
    done = process_next_repair(store, package, checkpoint[0], actor="synthetic")
    assert all(i["status"] == "completed" for i in done["items"]) and upload_count(network) == 2


def test_legacy_binding_is_not_misreported_as_nas_complete():
    sheet, store, network, package = setup(1)
    legacy = CloudDispatchStore(sheet)
    legacy.save_product_images([assignment(legacy.catalog()[0], list(package["assets"].values()))], actor="synthetic", origin="test")
    run = process_next_repair(store, package, prepare_batch_repair(store, package, mode="upload"), actor="synthetic")
    assert run["items"][0]["status"] == "failed" and not run["items"][0]["receipt"]
    assert "NAS 原圖位置" in run["items"][0]["reason"] and upload_count(network) == 0


def test_unconfigured_nas_and_non_nas_store_fail_closed():
    _, store, network, package = setup(1)
    for invalid in (CloudDispatchStore(store.spreadsheet), NasDispatchStore(store.spreadsheet)):
        with pytest.raises(DispatchError, match="已就緒"):
            prepare_batch_repair(invalid, package, mode="upload")
    assert not network.sessions


def test_arbitrary_exception_is_redacted_but_safe_nas_codes_remain():
    assert "secret" not in safe_repair_error(RuntimeError("secret https://private.test"))
    assert safe_repair_error(NasDiagnosticError("secret", kind="http", http_status=502)) == "HTTP 回應失敗；HTTP 502"
    assert "502" in safe_repair_error(DispatchError("圖庫 NAS HTTP 502 secret"))


@pytest.mark.parametrize("mode", ["upload", "existing_only"])
def test_502_has_no_unverified_index_binding_or_automatic_retry(mode):
    sheet, store, network, package = setup(1)
    before = {name: deepcopy(ws.rows) for name, ws in sheet.sheets.items()}
    original = store._nas_factory
    def factory(settings):
        nas = original(settings)
        session_factory = nas._session_factory
        def session():
            value = session_factory()
            value.override = lambda p: Response({}, status=502) if p["api"] == "SYNO.FileStation.Download" else None
            return value
        nas._session_factory = session
        return nas
    store._nas_factory = factory
    run = process_next_repair(store, package, prepare_batch_repair(store, package, mode=mode), actor="synthetic")
    assert run["items"][0]["status"] == "failed" and "502" in run["items"][0]["reason"]
    assert {name: ws.rows for name, ws in sheet.sheets.items()} == before
    assert upload_count(network) == (1 if mode == "upload" else 0)
    count = len(network.sessions)
    assert process_next_repair(store, package, run, actor="synthetic") == run
    assert len(network.sessions) == count
