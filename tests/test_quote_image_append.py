"""Synthetic Sheet and NAS only; never touches live service or private originals."""
from copy import deepcopy
import json

import pytest

from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_manager import DispatchError
from dispatch_storage import asset_bytes, encode_record
from nas_dispatch_storage import NasDispatchStore, LOCATION_SHEET
from product_images import PRODUCT_IMAGE_SHEET
from quote_image_repair import prepare_image_repair
from quote_image_append import append_nas_image_repair, ORIGIN
from synology_image_store import SynologyImageStore
from test_product_image_library import picture, assignment
from test_synology_image_store import Session, Response, config


class Network:
    def __init__(self):
        self.objects, self.sessions = {}, []
        self.on_stat = self.on_download = self.on_upload = None
        self.fail_upload_after_write = False

    def factory(self, settings):
        def make():
            session = Session()
            session.objects = self.objects
            session.fail_upload_after_write = self.fail_upload_after_write
            self.sessions.append(session)

            def override(params):
                api = params["api"]
                if api == "SYNO.FileStation.Upload" and self.on_upload:
                    return self.on_upload(params)
                if api == "SYNO.FileStation.Download" and self.on_download:
                    answer = self.on_download(params)
                    if answer is not None:
                        return answer
                if api != "SYNO.FileStation.List" or json.loads(params["additional"]) != ["size"]:
                    return None
                path = json.loads(params["path"])[0]
                if self.on_stat:
                    answer = self.on_stat(path)
                    if answer is not None:
                        return answer
                if path not in self.objects:
                    return Response({"success": False, "error": {"code": 408}})
                return Response({"success": True, "data": {"files": [{
                    "path": path, "isdir": False, "additional": {"size": len(self.objects[path])}}]}})
            session.override = override
            return session
        return SynologyImageStore(settings, make)

    def uploads(self):
        return [kwargs["files"]["file"][1] for session in self.sessions
                for _, kwargs in session.calls
                if kwargs["data"]["api"] == "SYNO.FileStation.Upload"]


def fixture(count=2):
    sheet = FakeSpreadsheet(product_rows((1650, 1651)))
    for name in ("_報價依據", "_發送批次", "_人工核對", "_LINE紀錄"):
        sheet.add_worksheet(name, rows=5, cols=8).rows = [["preserve content"]]
    net = Network()
    store = NasDispatchStore(sheet, config(), nas_factory=net.factory)
    old = [picture(i) for i in range(count)]
    source = store.catalog()[0]
    assert store.save_product_images([assignment(source, old)], actor="synthetic", origin="seed")[0]["結果"] == "已綁定"
    plan = prepare_image_repair(store, source["identity"])
    net.sessions.clear()
    return sheet, store, net, plan, old, picture(90)


def rows(sheet):
    return {name: deepcopy(ws.rows) for name, ws in sheet.sheets.items()}


def append(store, plan, new, **kw):
    return append_nas_image_repair(store, plan, [new], actor="synthetic", confirmed_same_product=True, **kw)


def test_append_preserves_old_main_order_and_only_uploads_new_original():
    sheet, store, net, plan, old, new = fixture()
    before, old_objects = rows(sheet), dict(net.objects)
    result = append(store, plan, new)
    assert result["結果"] == "已追加"
    ids = [a["sha256"] for a in [*old, new]]
    saved = store.product_image_bindings()[plan["source"]["identity"]]
    assert saved["assets"] == ids
    assert saved["append_parent"] == plan["binding"]["_revision"]
    assert saved["origin"] == ORIGIN
    assert net.uploads() == [asset_bytes(new)]
    assert all(net.objects[path] == raw for path, raw in old_objects.items())
    assert {k: rows(sheet)[k] for k in before if k not in {LOCATION_SHEET, PRODUCT_IMAGE_SHEET}} == {
        k: v for k, v in before.items() if k not in {LOCATION_SHEET, PRODUCT_IMAGE_SHEET}}
    assert result["receipt"]["sha256"] == ids
    assert result["receipt"]["main_sha256"] == old[0]["sha256"]
    assert result["receipt"]["appended_sha256"] == [new["sha256"]]
    assert len(net.sessions) >= 5  # Several independent original-read connections.


def test_physical_existing_new_original_without_index_is_adopted_without_upload():
    _, store, net, plan, _, new = fixture()
    meta = store._nas()._metadata(new)
    net.objects[store._nas()._path(meta)] = asset_bytes(new)
    assert not store.image_locations([new["sha256"]])
    assert append(store, plan, new)["結果"] == "已追加"
    assert net.uploads() == []
    assert new["sha256"] in store.image_locations([new["sha256"]])


@pytest.mark.parametrize("confirmed", [False, None, 1, "yes"])
def test_same_product_explicit_confirmation_is_mandatory(confirmed):
    sheet, store, net, plan, _, new = fixture()
    before = rows(sheet)
    with pytest.raises(DispatchError, match="先核對"):
        append_nas_image_repair(store, plan, [new], actor="synthetic", confirmed_same_product=confirmed)
    assert rows(sheet) == before and not net.uploads()


@pytest.mark.parametrize("count", [0, 2])
def test_exact_one_new_image_only(count):
    sheet, store, net, plan, _, new = fixture()
    before = rows(sheet)
    with pytest.raises(DispatchError, match="每次只追加一張"):
        append_nas_image_repair(store, plan, [new, picture(91)][:count], actor="synthetic", confirmed_same_product=True)
    assert rows(sheet) == before and not net.uploads()


def test_existing_sha_is_not_reuploaded_or_reordered():
    sheet, store, net, plan, old, _ = fixture()
    before = rows(sheet)
    with pytest.raises(DispatchError, match="已在既有圖組"):
        append(store, plan, old[1])
    assert rows(sheet) == before and not net.uploads()


@pytest.mark.parametrize("damage", ["missing_binding", "subject", "source_hash", "max10"])
def test_invalid_existing_plan_binding_is_rejected(damage):
    sheet, store, net, plan, _, new = fixture(count=10 if damage == "max10" else 2)
    before = rows(sheet)
    if damage == "missing_binding":
        plan["binding"] = None
    elif damage != "max10":
        plan["binding"][damage] = "different"
    with pytest.raises(DispatchError, match="完整 NAS 綁定"):
        append(store, plan, new)
    assert rows(sheet) == before and not net.uploads()


@pytest.mark.parametrize("change", ["name", "price", "formula", "row", "no", "duplicate"])
def test_changed_source_or_formula_before_append_never_writes(change):
    sheet, store, net, plan, _, new = fixture()
    ws = sheet.sheets["G正版"]
    if change == "name": ws.rows[0][1] = "different product"
    elif change == "price": ws.rows[1][6] = "999"
    elif change == "formula":
        ws.formula_override = deepcopy(plan["formulas"])
        ws.formula_override[1][10] += "+0"
    elif change == "row": ws.rows.insert(0, [])
    elif change == "no": ws.rows[0][0] = "no999"
    else: ws.rows += deepcopy(ws.rows[:6])
    before = rows(sheet)
    with pytest.raises(DispatchError): append(store, plan, new)
    assert rows(sheet) == before and not net.uploads()


def test_old_original_missing_or_corrupt_cannot_fall_back_or_upload_new():
    for corrupt in (False, True):
        sheet, store, net, plan, old, new = fixture()
        path = store._nas()._path(store._nas()._metadata(old[0]))
        if corrupt: net.objects[path] = b"broken"
        else: del net.objects[path]
        before = rows(sheet)
        with pytest.raises(DispatchError): append(store, plan, new)
        assert rows(sheet) == before and not net.uploads()


def test_old_original_missing_index_stops_before_network_and_upload():
    sheet, store, net, plan, _, new = fixture()
    sheet.sheets[LOCATION_SHEET].rows = sheet.sheets[LOCATION_SHEET].rows[:1]
    before = rows(sheet)
    with pytest.raises(DispatchError, match="完整 NAS 位置"):
        append(store, plan, new)
    assert rows(sheet) == before and not net.sessions and not net.uploads()


@pytest.mark.parametrize("failure", ["http502", "denied", "malformed", "wrong_size"])
def test_new_stat_error_is_never_turned_into_missing_upload(failure):
    sheet, store, net, plan, _, new = fixture()
    def stat(path):
        if failure == "http502": return Response(b"private", status=502)
        if failure == "denied": return Response({"success": False, "error": {"code": 407}})
        if failure == "malformed": return Response({"success": True, "data": {"files": []}})
        return Response({"success": True, "data": {"files": [{"path": path, "isdir": False, "additional": {"size": 1}}]}})
    net.on_stat = stat
    before = rows(sheet)
    with pytest.raises(DispatchError): append(store, plan, new)
    assert rows(sheet) == before and not net.uploads()


def test_uncertain_binding_write_explicit_resubmit_is_readonly():
    sheet, store, net, plan, _, new = fixture()
    ws = sheet.sheets[PRODUCT_IMAGE_SHEET]
    ws.fail_append_after_write = True
    with pytest.raises(DispatchError, match="結果待確認"):
        append(store, plan, new)
    before, uploads = rows(sheet), list(net.uploads())
    ws.fail_append_after_write = False
    result = append(store, plan, new)
    assert result["結果"] == "已追加"
    assert rows(sheet) == before and net.uploads() == uploads


def test_uncertain_file_upload_explicit_resubmit_adopts_without_second_upload():
    sheet, store, net, plan, _, new = fixture()
    before_binding = deepcopy(sheet.sheets[PRODUCT_IMAGE_SHEET].rows)
    net.fail_upload_after_write = True
    with pytest.raises(DispatchError, match="保存結果待確認"):
        append(store, plan, new)
    assert sheet.sheets[PRODUCT_IMAGE_SHEET].rows == before_binding
    assert not store.image_locations([new["sha256"]])
    first_uploads = list(net.uploads())
    net.fail_upload_after_write = False
    assert append(store, plan, new)["結果"] == "已追加"
    assert net.uploads() == first_uploads


def test_conflicting_sibling_binding_during_final_write_is_not_success():
    sheet, store, _, plan, _, new = fixture()
    binding = plan["binding"]
    sibling = {k:v for k,v in binding.items() if k != "_revision"}
    sibling["actor"] = "concurrent writer"
    ws = sheet.sheets[PRODUCT_IMAGE_SHEET]
    ws.before_append = lambda: ws.rows.extend(encode_record(plan["source"]["identity"], sibling, binding["_revision"]))
    with pytest.raises(DispatchError, match="同時修改衝突"):
        append(store, plan, new)


def test_new_original_corruption_after_upload_prevents_location_and_binding_publish():
    sheet, store, net, plan, _, new = fixture()
    before_binding = deepcopy(sheet.sheets[PRODUCT_IMAGE_SHEET].rows)
    target = store._nas()._path(store._nas()._metadata(new))
    def corrupt(params):
        path = json.loads(params["path"])[0]
        if path == target and path in net.objects:
            net.objects[path] = b"damaged"
    net.on_download = corrupt
    with pytest.raises(DispatchError): append(store, plan, new)
    assert sheet.sheets[PRODUCT_IMAGE_SHEET].rows == before_binding
    assert not store.image_locations([new["sha256"]])


def test_source_changes_after_new_file_write_no_binding_is_published():
    sheet, store, net, plan, _, new = fixture()
    before_binding = deepcopy(sheet.sheets[PRODUCT_IMAGE_SHEET].rows)
    def change(params):
        sheet.sheets["G正版"].rows[0][1] = "changed during upload"
    net.on_upload = change
    with pytest.raises(DispatchError): append(store, plan, new)
    assert sheet.sheets[PRODUCT_IMAGE_SHEET].rows == before_binding
    assert not store.image_locations([new["sha256"]])


def test_concurrent_binding_writer_stops_instead_of_merging():
    sheet, store, net, plan, _, new = fixture()
    source, binding = plan["source"], plan["binding"]
    value = {k: v for k, v in binding.items() if k != "_revision"}
    value["actor"] = "other writer"
    sheet.sheets[PRODUCT_IMAGE_SHEET].rows += encode_record(source["identity"], value, binding["_revision"])
    before = rows(sheet)
    with pytest.raises(DispatchError, match="版本已變更"):
        append(store, plan, new)
    assert rows(sheet) == before and not net.uploads()


def test_source_changes_in_binding_write_cannot_report_success():
    sheet, store, _, plan, _, new = fixture()
    sheet.sheets[PRODUCT_IMAGE_SHEET].before_append = lambda: sheet.sheets["G正版"].rows[0].__setitem__(1, "changed in final append")
    with pytest.raises(DispatchError): append(store, plan, new)


def test_exact_target_from_another_parent_is_not_adopted():
    sheet, store, net, plan, old, new = fixture()
    append(store, plan, new)
    before = rows(sheet)
    plan["binding"]["_revision"] = "f" * 32
    with pytest.raises(DispatchError, match="版本已變更"):
        append(store, plan, new)
    assert rows(sheet) == before


def test_unready_or_legacy_store_is_not_supported():
    from dispatch_storage import CloudDispatchStore
    sheet, _, _, plan, _, new = fixture()
    for store in (CloudDispatchStore(sheet), NasDispatchStore(sheet), NasDispatchStore(sheet, config(), config_error=True)):
        with pytest.raises(DispatchError, match="只支援"):
            append(store, plan, new)
