"""Synthetic pre-existing originals only; never uses real NAS or credentials."""
from copy import deepcopy
import json

import pytest
import requests

from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_manager import DispatchError
from dispatch_storage import CloudDispatchStore, asset_bytes, encode_record
from nas_dispatch_storage import LOCATION_SHEET, NasDispatchStore
from nas_existing_originals import ExistingNasOriginalStore
from product_images import PRODUCT_IMAGE_SHEET
from quote_image_repair import prepare_image_repair, save_existing_nas_image_repair
from synology_image_store import SynologyImageStore
from test_nas_dispatch_storage import Network
from test_quote_image_repair import picture, rows
from test_synology_image_store import Response, Session, config


class ReadOnlyNetwork(Network):
    def __init__(self):
        super().__init__()
        self.override = None

    def factory(self, settings):
        def session():
            result = Session()
            result.objects = self.objects

            def override(params):
                assert params["api"] != "SYNO.FileStation.Upload", "read-only mode attempted upload"
                return self.override(params) if self.override else None

            result.override = override
            self.sessions.append(result)
            return result
        return SynologyImageStore(settings, session)


def fixture(count=1):
    sheet = FakeSpreadsheet(product_rows((1, 2)))
    network = ReadOnlyNetwork()
    store = NasDispatchStore(sheet, config(), nas_factory=network.factory)
    assets = [picture(n) for n in range(count)]
    metadata = [store._nas()._metadata(a) for a in assets]
    for asset, item in zip(assets, metadata):
        network.objects[store._nas()._path(item)] = asset_bytes(asset)
    identity = store.catalog()[0]["identity"]
    plan = prepare_image_repair(store, identity)
    return sheet, store, network, assets, metadata, identity, plan


def save(store, plan, assets):
    return save_existing_nas_image_repair(store, plan, assets, actor="合成核對")


def assert_no_upload(network):
    calls = [kwargs for session in network.sessions for _, kwargs in session.calls]
    assert not any(kwargs.get("files") for kwargs in calls)
    assert not any(kwargs["data"]["api"] == "SYNO.FileStation.Upload" for kwargs in calls)


@pytest.fixture(autouse=True)
def forbid_upload_and_legacy(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("existing-only mode cannot upload or read legacy images")
    monkeypatch.setattr(SynologyImageStore, "put_asset", forbidden)
    monkeypatch.setattr(CloudDispatchStore, "get_assets", forbidden)
    monkeypatch.setattr(CloudDispatchStore, "put_assets", forbidden)


def test_adopts_physical_originals_only_after_verification_and_preserves_other_data():
    sheet, store, network, assets, metadata, identity, plan = fixture(2)
    for name in ("_報價依據", "_發送批次", "_人工核對", "_LINE紀錄"):
        sheet.add_worksheet(name, rows=5, cols=8).rows = [["preserve existing content"]]
    before, physical = rows(sheet), dict(network.objects)
    result = save(store, plan, assets)
    assert result[0]["結果"] == "已綁定"
    assert {name: sheet.sheets[name].rows for name in before} == before
    assert set(sheet.sheets) - before.keys() == {LOCATION_SHEET, PRODUCT_IMAGE_SHEET}
    binding = store.product_image_bindings()[identity]
    assert binding["assets"] == [a["sha256"] for a in assets]
    assert binding["origin"] == "quote_image_repair_existing_nas"
    assert store.image_locations(binding["assets"]) == {m["sha256"]: m for m in metadata}
    assert network.objects == physical
    assert_no_upload(network)
    before = rows(sheet)
    assert save(store, plan, list(reversed(assets)))[0]["結果"] == "已存在"
    assert rows(sheet) == before
    assert store.product_image_bindings()[identity]["assets"] == binding["assets"]
    assert_no_upload(network)


@pytest.mark.parametrize("failure", ["missing", "403", "500", "api", "redirect", "tls",
                                     "timeout", "connection", "size", "hash", "mime"])
def test_any_failed_original_stops_before_all_index_and_binding_writes(failure):
    sheet, store, network, assets, metadata, _, plan = fixture(2)
    path = store._nas()._path(metadata[1])
    if failure == "missing":
        del network.objects[path]
    elif failure == "size":
        network.objects[path] += b"broken"
    elif failure == "hash":
        raw = network.objects[path]
        network.objects[path] = raw[:-1] + bytes([raw[-1] ^ 1])
    else:
        def override(params):
            if params["api"] != "SYNO.FileStation.Download" or json.loads(params["path"])[0] != path:
                return None
            if failure in {"403", "500"}:
                return Response({}, status=int(failure))
            if failure == "api":
                return Response({"success": False, "error": {"code": 408}})
            if failure == "redirect":
                response = Response({}, status=302)
                response.headers["Location"] = "https://sensitive.example.test/?sid=private"
                return response
            if failure == "mime":
                return Response(network.objects[path], mime="text/html")
            error = {"tls": requests.exceptions.SSLError, "timeout": requests.ReadTimeout,
                     "connection": requests.ConnectionError}[failure]
            raise error("https://sensitive.example.test/?sid=private")
        network.override = override
    before = rows(sheet)
    with pytest.raises(DispatchError) as caught:
        save(store, plan, assets)
    assert "sensitive.example" not in str(caught.value) and "sid=private" not in str(caught.value)
    assert rows(sheet) == before
    assert_no_upload(network)


@pytest.mark.parametrize("field,value", [("root", "/另一圖庫"), ("library_id", "other-library"),
                                       ("mime", "image/jpeg"), ("size", 1)])
def test_conflicting_existing_location_is_never_overwritten(field, value):
    sheet, store, network, assets, metadata, _, plan = fixture()
    ws = store._sheet(LOCATION_SHEET, create=True)
    ws.rows.extend(encode_record(metadata[0]["sha256"], {**metadata[0], field: value}))
    before = rows(sheet)
    with pytest.raises(DispatchError):
        save(store, plan, assets)
    assert rows(sheet) == before
    assert not network.sessions


@pytest.mark.parametrize("change", ["source", "formula", "duplicate", "revision"])
def test_change_during_download_stops_before_index_publication(change):
    sheet, store, network, assets, _, identity, plan = fixture()
    changed = False

    def override(params):
        nonlocal changed
        if params["api"] != "SYNO.FileStation.Download" or changed:
            return None
        changed = True
        if change == "source":
            sheet.sheets["G正版"].rows[0][1] = "已換商品"
        elif change == "formula":
            sheet.sheets["G正版"].formula_override = deepcopy(plan["formulas"])
            sheet.sheets["G正版"].formula_override[1][10] += "+0"
        elif change == "duplicate":
            sheet.sheets["G正版"].rows += deepcopy(sheet.sheets["G正版"].rows[:6])
        else:
            from product_images import subject_key
            record = {"schema": 1, "identity": identity, "subject": subject_key(plan["source"]),
                      "source_hash": plan["source"]["source_hash"], "assets": [assets[0]["sha256"]],
                      "actor": "other", "origin": "test", "updated_at": "2026-01-01T00:00:00Z"}
            store._sheet(PRODUCT_IMAGE_SHEET, create=True).rows.extend(encode_record(identity, record))
        return None

    network.override = override
    with pytest.raises(DispatchError):
        save(store, plan, assets)
    assert LOCATION_SHEET not in sheet.sheets
    assert_no_upload(network)


def test_existing_different_binding_blocks_without_reading_or_writing_originals():
    sheet, store, network, assets, _, _, plan = fixture()
    save(store, plan, assets)
    before, sessions = rows(sheet), len(network.sessions)
    with pytest.raises(DispatchError, match="不會替換"):
        save(store, plan, [picture(10)])
    assert rows(sheet) == before and len(network.sessions) == sessions


def test_unknown_location_write_explicit_retry_is_idempotent():
    sheet, store, network, assets, _, _, plan = fixture()
    ws = store._sheet(LOCATION_SHEET, create=True)
    ws.fail_append_after_write = True
    with pytest.raises(DispatchError, match="結果待確認"):
        save(store, plan, assets)
    assert PRODUCT_IMAGE_SHEET not in sheet.sheets
    length = len(ws.rows)
    ws.fail_append_after_write = False
    assert save(store, plan, assets)[0]["結果"] == "已綁定"
    assert len(ws.rows) == length
    assert_no_upload(network)


def test_final_original_readback_failure_cannot_claim_success():
    sheet, store, network, assets, _, _, plan = fixture()
    ws = store._sheet(PRODUCT_IMAGE_SHEET, create=True)
    ws.before_append = lambda: network.objects.clear()
    with pytest.raises(DispatchError):
        save(store, plan, assets)
    assert_no_upload(network)


def test_legacy_or_unconfigured_store_does_not_fall_back():
    sheet, store, network, assets, _, _, plan = fixture()
    before = rows(sheet)
    for unavailable in (CloudDispatchStore(sheet), NasDispatchStore(sheet),
                        NasDispatchStore(sheet, config(), config_error=True)):
        with pytest.raises(DispatchError, match="NAS 商品圖庫"):
            save(unavailable, plan, assets)
    assert rows(sheet) == before and not network.sessions


def test_adapter_rejects_unselected_assets_and_never_uses_display_backup():
    _, store, network, assets, _, _, _ = fixture()
    adapter = ExistingNasOriginalStore(store, assets, before_publish=lambda: None)
    other = picture(20)
    for operation in (lambda: adapter.get_assets([other["sha256"]]),
                      lambda: adapter.put_assets([other]),
                      lambda: adapter.get_display_asset(assets[0]["sha256"])):
        with pytest.raises(DispatchError):
            operation()
    assert not network.sessions


def test_same_content_in_another_cloud_sheet_is_rejected():
    sheet, store, network, assets, _, _, plan = fixture()
    sheet.id = "another-synthetic-spreadsheet"
    before = rows(sheet)
    with pytest.raises(DispatchError, match="雲表來源已變更"):
        save(store, plan, assets)
    assert rows(sheet) == before and not network.sessions


@pytest.mark.parametrize("change", ["index", "binding", "formula"])
def test_final_readback_detects_mid_read_changes_without_false_success(change):
    sheet, store, network, assets, _, identity, plan = fixture()
    changed = False

    def override(params):
        nonlocal changed
        if (params["api"] != "SYNO.FileStation.Download" or changed
                or PRODUCT_IMAGE_SHEET not in sheet.sheets):
            return None
        changed = True
        if change == "index":
            sheet.sheets[LOCATION_SHEET].rows = sheet.sheets[LOCATION_SHEET].rows[:1]
        elif change == "binding":
            binding = store.product_image_bindings()[identity]
            value = {k: v for k, v in binding.items() if k != "_revision"}
            value["actor"] = "another operator"
            sheet.sheets[PRODUCT_IMAGE_SHEET].rows.extend(encode_record(identity, value, binding["_revision"]))
        else:
            sheet.sheets["G正版"].formula_override = deepcopy(plan["formulas"])
            sheet.sheets["G正版"].formula_override[1][10] += "+0"
        return None

    network.override = override
    with pytest.raises(DispatchError):
        save(store, plan, assets)
    assert changed
    assert_no_upload(network)
