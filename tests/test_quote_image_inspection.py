"""Read-only inspection contracts, using synthetic Sheet/NAS fixtures only.

Run with this draft directory, the implementation draft and repo/tests on
PYTHONPATH. This file never creates real NAS, browser or cloud connections.
"""
from copy import deepcopy
from datetime import datetime
import json

import pytest
import requests

from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_manager import DispatchError
from dispatch_storage import CloudDispatchStore, asset_bytes
from nas_dispatch_storage import NasDispatchStore
from product_images import subject_key
from quote_image_repair import prepare_image_repair
from quote_image_inspection import inspect_nas_files, read_bound_originals
from synology_image_store import SynologyImageStore
from test_nas_dispatch_storage import Network
from test_product_image_library import assignment, picture
from test_synology_image_store import Response, Session, config


SENSITIVE = "synthetic-secret private-session /private/path?password=secret"


def rows(sheet):
    return {title: deepcopy(ws.rows) for title, ws in sheet.sheets.items()}


def all_calls(network):
    return [kwargs["data"] for session in network.sessions for _, kwargs in session.calls]


class InspectionNetwork(Network):
    """Actual adapter with fake HTTP. Only exact-path size getinfo is added."""
    def __init__(self):
        super().__init__()
        self.answers = {}
        self.before_stat = None
        self.before_root = None
        self.stat_paths = []

    def factory(self, settings):
        def session_factory():
            session = Session()
            session.objects = self.objects

            def override(params):
                assert params["api"] != "SYNO.FileStation.Upload", "inspection must never upload"
                if params["api"] != "SYNO.FileStation.List" or params["method"] != "getinfo":
                    return None
                additional = json.loads(params["additional"])
                if additional == ["perm"]:
                    if self.before_root:
                        self.before_root()
                    return None
                assert additional == ["size"]
                paths = json.loads(params["path"])
                assert len(paths) == 1
                path = paths[0]
                self.stat_paths.append(path)
                if self.before_stat:
                    self.before_stat(path)
                if path in self.answers:
                    answer = self.answers[path]
                    return answer(params) if callable(answer) else answer
                if path not in self.objects:
                    return Response({"success": False, "error": {"code": 408, "message": SENSITIVE}})
                return Response({"success": True, "data": {"files": [
                    {"path": path, "isdir": False, "additional": {"size": len(self.objects[path])}}]}})

            session.override = override
            self.sessions.append(session)
            return session
        return SynologyImageStore(settings, session_factory)


def fixture(count=2, *, bound=False, nas=True):
    sheet = FakeSpreadsheet(product_rows((1, 2)))
    for name in ("_報價依據", "_發送批次", "_人工核對", "_LINE紀錄"):
        sheet.add_worksheet(name, rows=5, cols=8).rows = [["preserve existing content"]]
    assets = [picture(number) for number in range(count)]
    legacy = CloudDispatchStore(sheet)
    if bound:
        assert legacy.save_product_images([assignment(legacy.catalog()[0], assets)],
            actor="合成核對", origin="test")[0]["結果"] == "已綁定"
    network = InspectionNetwork()
    store = NasDispatchStore(sheet, config(), nas_factory=network.factory) if nas else legacy
    if nas:
        metadata = [store._nas()._metadata(asset) for asset in assets]
        for asset, meta in zip(assets, metadata):
            network.objects[store._nas()._path(meta)] = asset_bytes(asset)
        if bound:
            # Fixture publication only; inspection must preserve all these rows.
            store._publish_locations(metadata)
    else:
        metadata = []
    identity = store.catalog()[0]["identity"]
    plan = prepare_image_repair(store, identity)
    return sheet, store, network, assets, metadata, identity, plan


def guard_writes(monkeypatch, sheet, store):
    def forbidden(*args, **kwargs):
        raise AssertionError("read-only inspection attempted a Sheet/image write")
    for ws in sheet.sheets.values():
        monkeypatch.setattr(ws, "update", forbidden)
        monkeypatch.setattr(ws, "append_rows", forbidden)
    monkeypatch.setattr(sheet, "add_worksheet", forbidden)
    for name in ("put_asset", "put_assets", "save_product_images", "_publish_locations"):
        if hasattr(store, name):
            monkeypatch.setattr(store, name, forbidden)
    monkeypatch.setattr(SynologyImageStore, "put_asset", forbidden)
    monkeypatch.setattr(SynologyImageStore, "put_thumbnail", forbidden)


def assert_no_transfer(network, *, downloads=False):
    calls = all_calls(network)
    assert not any(call["api"] == "SYNO.FileStation.Upload" for call in calls)
    if not downloads:
        assert not any(call["api"] == "SYNO.FileStation.Download" for call in calls)
    for session in network.sessions:
        assert session.closed and session.trust_env is False
        assert all(kwargs["allow_redirects"] is False and kwargs["verify"] is True
                   and not kwargs.get("files") for _, kwargs in session.calls)


def assert_common_receipt(result, identity, plan):
    assert result["identity"] == identity
    assert result["snapshot_digest"] == plan["snapshot_digest"]
    assert datetime.fromisoformat(result["checked_at"]).tzinfo is not None


@pytest.mark.parametrize("count", [1, 6, 10])
@pytest.mark.parametrize("bound", [False, True])
def test_stat_queries_each_exact_original_after_root_check_without_reading_bytes(monkeypatch, count, bound):
    sheet, store, network, assets, metadata, identity, plan = fixture(count, bound=bound)
    before, physical = rows(sheet), dict(network.objects)
    guard_writes(monkeypatch, sheet, store)
    result = inspect_nas_files(store, plan, assets)
    assert_common_receipt(result, identity, plan)
    assert set(result) == {"identity", "checked_at", "snapshot_digest", "images"}
    assert len(result["images"]) == count
    assert network.stat_paths == [store._nas()._path(meta) for meta in metadata]
    for row, asset in zip(result["images"], assets):
        assert set(row) == {"sha256", "name", "expected_size", "exists", "size", "size_matches", "status", "reason"}
        assert row["sha256"] == asset["sha256"] and row["name"] == asset["name"]
        assert row["expected_size"] == row["size"] == len(asset_bytes(asset))
        assert row["exists"] is True and row["size_matches"] is True and row["status"] == "exists"
        assert isinstance(row["reason"], str)
    list_calls = [call for call in all_calls(network) if call["api"] == "SYNO.FileStation.List"]
    assert json.loads(list_calls[0]["additional"]) == ["perm"]
    assert all(json.loads(call["additional"]) == ["size"] for call in list_calls[1:])
    assert rows(sheet) == before and network.objects == physical
    assert SENSITIVE not in json.dumps(result) and config().root not in json.dumps(result, ensure_ascii=False)
    assert_no_transfer(network)


def test_stat_distinguishes_size_mismatch_missing_and_http_error_and_continues(monkeypatch):
    sheet, store, network, assets, metadata, identity, plan = fixture(4)
    paths = [store._nas()._path(meta) for meta in metadata]
    network.objects[paths[0]] += b"x"
    del network.objects[paths[1]]
    network.answers[paths[2]] = Response({"success": False, "error": {"code": 408, "message": SENSITIVE}}, status=502)
    before = rows(sheet)
    guard_writes(monkeypatch, sheet, store)
    result = inspect_nas_files(store, plan, assets)
    assert_common_receipt(result, identity, plan)
    mismatch, missing, error, passed = result["images"]
    assert mismatch["exists"] is True and mismatch["size_matches"] is False and mismatch["status"] == "exists"
    assert mismatch["size"] == mismatch["expected_size"] + 1
    assert missing["status"] == "missing" and missing["exists"] is False
    assert missing["size"] is None and missing["size_matches"] is False
    assert error["status"] == "error" and error["exists"] is None and error["size"] is None
    assert error["size_matches"] is False and "502" in error["reason"]
    assert passed["status"] == "exists" and passed["size_matches"] is True
    assert network.stat_paths == paths and rows(sheet) == before
    assert "synthetic-secret" not in json.dumps(result) and "private-session" not in json.dumps(result)
    assert_no_transfer(network)


@pytest.mark.parametrize("failure", ["http408", "http404", "api400", "api_string408", "redirect", "tls", "timeout", "badshape"])
def test_stat_never_labels_other_errors_as_missing_or_advises_upload(monkeypatch, failure):
    sheet, store, network, assets, metadata, _, plan = fixture(1)
    path = store._nas()._path(metadata[0])
    if failure.startswith("http"):
        answer = Response({"success": False, "error": {"code": 408, "message": SENSITIVE}}, status=int(failure[4:]))
    elif failure.startswith("api"):
        code = 400 if failure == "api400" else "408"
        answer = Response({"success": False, "error": {"code": code, "message": SENSITIVE}})
    elif failure == "redirect":
        answer = Response({}, status=307)
        answer.headers["Location"] = "https://user:synthetic-secret@private.test/path"
    elif failure == "badshape":
        answer = Response({"success": True, "data": {"files": [{"path": "/private/other", "isdir": True}]}})
    else:
        error_type = requests.exceptions.SSLError if failure == "tls" else requests.exceptions.ReadTimeout
        def answer(params):
            raise error_type(SENSITIVE)
    network.answers[path] = answer
    before = rows(sheet)
    guard_writes(monkeypatch, sheet, store)
    result = inspect_nas_files(store, plan, assets)
    row = result["images"][0]
    assert row["status"] == "error" and row["exists"] is None and row["size"] is None
    assert row["size_matches"] is False
    assert "synthetic-secret" not in row["reason"] and "/private" not in row["reason"]
    assert "upload" not in row["reason"].lower() and "請上傳" not in row["reason"]
    assert rows(sheet) == before
    assert_no_transfer(network)


@pytest.mark.parametrize("mode", ["legacy", "missing_config", "invalid_config"])
def test_stat_requires_ready_nas_without_legacy_fallback(monkeypatch, mode):
    sheet, store, network, assets, _, _, plan = fixture(1, nas=mode != "legacy")
    if mode == "missing_config":
        store.nas_config = None
    elif mode == "invalid_config":
        store.nas_config_error = True
    before = rows(sheet)
    guard_writes(monkeypatch, sheet, store)
    with pytest.raises(DispatchError):
        inspect_nas_files(store, plan, assets)
    assert rows(sheet) == before and not network.sessions


@pytest.mark.parametrize("count", [0, 11])
def test_stat_invalid_original_count_stops_before_network(monkeypatch, count):
    sheet, store, network, _, _, _, plan = fixture(1)
    before = rows(sheet)
    guard_writes(monkeypatch, sheet, store)
    with pytest.raises(DispatchError):
        inspect_nas_files(store, plan, [picture(number) for number in range(count)])
    assert rows(sheet) == before and not network.sessions


def mutate_source(sheet, plan, kind):
    ws = sheet.sheets[plan["source"]["category"]]
    if kind == "source":
        ws.rows[plan["source"]["row"] - 1][1] = "不同商品來源"
    elif kind == "formula":
        ws.formula_override = deepcopy(plan["formulas"])
        ws.formula_override[1][10] += "+0"
    else:
        raise AssertionError(kind)


@pytest.mark.parametrize("kind", ["source", "formula"])
@pytest.mark.parametrize("when", ["before", "during"])
def test_stat_rechecks_product_values_and_formulas_before_and_after_network(monkeypatch, kind, when):
    sheet, store, network, assets, _, _, plan = fixture(2)
    guard_writes(monkeypatch, sheet, store)
    if when == "before":
        mutate_source(sheet, plan, kind)
    else:
        network.before_stat = lambda path: mutate_source(sheet, plan, kind)
    with pytest.raises(DispatchError):
        inspect_nas_files(store, plan, assets)
    if when == "before":
        assert not network.sessions
    assert_no_transfer(network)


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("when", ["before", "during"])
def test_stat_preserves_exact_plan_binding_revision_before_and_after_network(monkeypatch, bound, when):
    sheet, store, network, assets, _, identity, plan = fixture(2, bound=bound)
    original = deepcopy(store.product_image_bindings())
    changed = deepcopy(original)
    if bound:
        changed[identity]["_revision"] = "different revision"
    else:
        changed[identity] = {"identity": identity, "subject": subject_key(plan["source"]),
                             "assets": [asset["sha256"] for asset in assets], "_revision": "new binding"}
    state = {"changed": when == "before"}
    monkeypatch.setattr(store, "product_image_bindings", lambda: deepcopy(changed if state["changed"] else original))
    network.before_stat = lambda path: state.update(changed=True)
    before = rows(sheet)
    guard_writes(monkeypatch, sheet, store)
    with pytest.raises(DispatchError):
        inspect_nas_files(store, plan, assets)
    assert rows(sheet) == before
    if when == "before":
        assert not network.sessions
    assert_no_transfer(network)


@pytest.mark.parametrize("nas", [False, True])
@pytest.mark.parametrize("count", [1, 6, 10])
def test_read_bound_originals_returns_complete_ordered_verified_group_read_only(monkeypatch, nas, count):
    sheet, store, network, assets, _, identity, plan = fixture(count, bound=True, nas=nas)
    expected_order = [asset["sha256"] for asset in assets]
    original_get = store.get_assets
    calls = []
    def unordered_get(ids):
        calls.append(list(ids))
        result = original_get(ids)
        return dict(reversed(list(result.items())))
    monkeypatch.setattr(store, "get_assets", unordered_get)
    monkeypatch.setattr(store, "get_display_asset", lambda *a: pytest.fail("must not read display fallback"), raising=False)
    before, physical = rows(sheet), dict(network.objects)
    guard_writes(monkeypatch, sheet, store)
    result = read_bound_originals(store, plan)
    assert_common_receipt(result, identity, plan)
    assert set(result) == {"identity", "checked_at", "snapshot_digest", "binding_revision", "sha256", "assets"}
    assert result["binding_revision"] == plan["binding"]["_revision"]
    assert result["sha256"] == expected_order
    assert [asset["sha256"] for asset in result["assets"]] == expected_order
    assert [asset_bytes(asset) for asset in result["assets"]] == [asset_bytes(asset) for asset in assets]
    assert all(set(asset) == {"sha256", "name", "mime", "data"} for asset in result["assets"])
    assert calls == [expected_order]
    assert rows(sheet) == before and network.objects == physical
    assert_no_transfer(network, downloads=True)


def test_read_with_no_binding_stops_without_image_or_network_reads(monkeypatch):
    sheet, store, network, _, _, _, plan = fixture(1)
    monkeypatch.setattr(store, "get_assets", lambda *a: pytest.fail("unbound image read"))
    before = rows(sheet)
    guard_writes(monkeypatch, sheet, store)
    with pytest.raises(DispatchError):
        read_bound_originals(store, plan)
    assert rows(sheet) == before and not network.sessions


@pytest.mark.parametrize("failure", ["missing", "corrupt", "wrong_sha", "wrong_mime", "display_fallback", "extra"])
def test_read_rejects_any_incomplete_or_invalid_original_group_without_partial_success(monkeypatch, failure):
    sheet, store, network, _, _, _, plan = fixture(2, bound=True, nas=False)
    real_get = store.get_assets
    def broken(ids):
        result = real_get(ids)
        key = list(ids)[-1]
        if failure == "missing":
            del result[key]
        elif failure == "corrupt":
            result[key]["data"] = "not valid base64"
        elif failure == "wrong_sha":
            result[key] = picture(3)
        elif failure == "wrong_mime":
            result[key]["mime"] = "image/jpeg"
        elif failure == "display_fallback":
            result[key]["_cloud_backup"] = True
        else:
            result[picture(3)["sha256"]] = picture(3)
        return result
    monkeypatch.setattr(store, "get_assets", broken)
    before = rows(sheet)
    guard_writes(monkeypatch, sheet, store)
    with pytest.raises(DispatchError):
        read_bound_originals(store, plan)
    assert rows(sheet) == before and not network.sessions


def test_read_rejects_stale_subject_even_if_plan_and_current_binding_match(monkeypatch):
    sheet, store, network, _, _, identity, plan = fixture(2, bound=True, nas=False)
    binding = deepcopy(store.product_image_bindings())
    binding[identity]["subject"] = "stale product subject"
    plan["binding"] = deepcopy(binding[identity])
    monkeypatch.setattr(store, "product_image_bindings", lambda: deepcopy(binding))
    monkeypatch.setattr(store, "get_assets", lambda *a: pytest.fail("must reject stale binding before image read"))
    before = rows(sheet)
    guard_writes(monkeypatch, sheet, store)
    with pytest.raises(DispatchError):
        read_bound_originals(store, plan)
    assert rows(sheet) == before and not network.sessions


def test_nas_read_failure_never_uses_available_legacy_copy_or_display_fallback(monkeypatch):
    sheet, store, network, assets, metadata, _, plan = fixture(2, bound=True)
    del network.objects[store._nas()._path(metadata[1])]
    # Fixture retains identical legacy bytes, which display fallback could find.
    assert asset_bytes(CloudDispatchStore(sheet).get_asset(assets[1]["sha256"])) == asset_bytes(assets[1])
    monkeypatch.setattr(store, "get_display_asset", lambda *a: pytest.fail("display fallback"))
    before = rows(sheet)
    guard_writes(monkeypatch, sheet, store)
    with pytest.raises(DispatchError):
        read_bound_originals(store, plan)
    assert rows(sheet) == before
    assert_no_transfer(network, downloads=True)


@pytest.mark.parametrize("kind", ["source", "formula"])
@pytest.mark.parametrize("when", ["before", "during"])
def test_read_rechecks_source_and_formula_before_and_after_original_byte_read(monkeypatch, kind, when):
    sheet, store, network, _, _, _, plan = fixture(2, bound=True, nas=False)
    real_get, read_calls = store.get_assets, []
    def reading(ids):
        read_calls.append(list(ids))
        result = real_get(ids)
        mutate_source(sheet, plan, kind)
        return result
    monkeypatch.setattr(store, "get_assets", reading)
    if when == "before":
        mutate_source(sheet, plan, kind)
    guard_writes(monkeypatch, sheet, store)
    with pytest.raises(DispatchError):
        read_bound_originals(store, plan)
    assert bool(read_calls) is (when == "during")
    assert not network.sessions


@pytest.mark.parametrize("change", ["revision", "subject", "order", "actor"])
@pytest.mark.parametrize("when", ["before", "during"])
def test_read_requires_entire_binding_unchanged_before_and_after_read(monkeypatch, change, when):
    sheet, store, network, _, _, identity, plan = fixture(2, bound=True, nas=False)
    original = deepcopy(store.product_image_bindings())
    changed = deepcopy(original)
    if change == "revision":
        changed[identity]["_revision"] = "changed revision"
    elif change == "subject":
        changed[identity]["subject"] = "different subject"
    elif change == "order":
        changed[identity]["assets"].reverse()
    else:
        changed[identity]["actor"] = "another actor"
    state = {"changed": when == "before"}
    monkeypatch.setattr(store, "product_image_bindings", lambda: deepcopy(changed if state["changed"] else original))
    real_get, read_calls = store.get_assets, []
    def reading(ids):
        read_calls.append(list(ids))
        result = real_get(ids)
        state["changed"] = True
        return result
    monkeypatch.setattr(store, "get_assets", reading)
    before = rows(sheet)
    guard_writes(monkeypatch, sheet, store)
    with pytest.raises(DispatchError):
        read_bound_originals(store, plan)
    assert bool(read_calls) is (when == "during")
    assert rows(sheet) == before and not network.sessions


@pytest.mark.parametrize("operation", ["stat", "read"])
def test_plan_source_snapshot_tampering_stops_without_network(monkeypatch, operation):
    sheet, store, network, assets, _, _, plan = fixture(2, bound=True)
    plan["formulas"][1][10] += "+0"
    before = rows(sheet)
    guard_writes(monkeypatch, sheet, store)
    with pytest.raises(DispatchError):
        if operation == "stat":
            inspect_nas_files(store, plan, assets)
        else:
            read_bound_originals(store, plan)
    assert rows(sheet) == before and not network.sessions
