"""Batch diagnostics are bounded stat-only requests against synthetic NAS."""
from copy import deepcopy
import json

import pytest

from dispatch_manager import DispatchError
from quote_image_repair_batch import (prepare_batch_repair, process_next_repair,
                                      inspect_batch_nas_files)
from test_quote_image_repair_batch import setup, upload_count
from test_quote_image_append import Network
from test_synology_image_store import Response


def fixture(count=13):
    sheet, store, _, package = setup(count)
    network = Network()
    store._nas_factory = network.factory
    run = prepare_batch_repair(store, package, mode="upload")
    run = process_next_repair(store, package, run, actor="synthetic", limit=2)
    network.fail_upload_after_write = True
    run = process_next_repair(store, package, run, actor="synthetic", limit=1)
    network.fail_upload_after_write = False
    assert [item["status"] for item in run["items"][:3]] == ["completed", "completed", "failed"]
    network.sessions.clear()
    return sheet, store, network, package, run


def test_stat_skips_completed_and_never_changes_mode_run_objects_or_sheets():
    sheet, store, network, package, run = fixture()
    before = {name: deepcopy(ws.rows) for name, ws in sheet.sheets.items()}
    saved_run, objects = deepcopy(run), deepcopy(network.objects)
    report = inspect_batch_nas_files(store, package, run)
    assert report["mode"] == "stat_only" and report["repair_mode"] == "upload"
    assert report["unfinished_count"] == 11 and len(report["items"]) == 10
    assert [r["identity"] for r in report["items"]] == [i["identity"] for i in run["items"][2:12]]
    assert report["items"][0]["images"][0]["exists"] is True
    assert report["items"][1]["images"][0]["exists"] is False
    assert "API 408" in report["items"][1]["images"][0]["reason"]
    second = inspect_batch_nas_files(store, package, run, offset=10)
    assert [r["identity"] for r in second["items"]] == [run["items"][12]["identity"]]
    assert run == saved_run and network.objects == objects
    assert {name: ws.rows for name, ws in sheet.sheets.items()} == before
    assert upload_count(network) == 0
    assert not any(kw["data"]["api"] == "SYNO.FileStation.Download" for session in network.sessions for _, kw in session.calls)
    for item in report["items"]:
        assert item["checked_at"]
        for image in item["images"]:
            assert len(image["sha256"]) == 64 and image["expected_size"] > 0
    json.dumps(report, ensure_ascii=False)


@pytest.mark.parametrize("offset,limit", [(-1, 1), (True, 1), (11, 1), (0, 0), (0, 11), (0, True)])
def test_invalid_offsets_and_batch_sizes_stop_before_nas(offset, limit):
    _, store, network, package, run = fixture()
    before = deepcopy(run)
    with pytest.raises(DispatchError, match="最多 10 款"):
        inspect_batch_nas_files(store, package, run, offset=offset, limit=limit)
    assert not network.sessions and run == before


def test_http502_remains_error_and_is_never_missing_or_upload_advice():
    _, store, network, package, run = fixture(4)
    network.on_stat = lambda path: Response({}, status=502)
    before = deepcopy(run)
    report = inspect_batch_nas_files(store, package, run)
    assert all(r["images"][0]["exists"] is None and r["images"][0]["status"] == "error" for r in report["items"])
    assert all("502" in r["images"][0]["reason"] for r in report["items"])
    assert run == before and upload_count(network) == 0


def test_changed_source_is_not_inspected_or_retargeted():
    sheet, store, network, package, run = fixture(4)
    sheet.sheets["G正版"].rows[13][6] = "999"
    report = inspect_batch_nas_files(store, package, run, limit=1)
    assert not report["items"][0]["images"] and report["items"][0]["reason"]
    assert not network.sessions


def test_nas429_halts_diagnostics_without_changing_original_run():
    _, store, network, package, run = fixture(4)
    calls = []
    def limited(path):
        calls.append(path)
        return Response({}, status=429)
    network.on_stat = limited
    before = deepcopy(run)
    report = inspect_batch_nas_files(store, package, run)
    assert len(calls) == len(report["items"]) == 1
    assert report["halt"]["diagnostic"]["http_status"] == 429
    assert run == before and upload_count(network) == 0


def test_local_read_budget_halts_before_any_next_item(monkeypatch):
    from batch_read_pacing import ReadBudgetWaitExceeded
    import quote_image_repair_batch as repair
    _, store, network, package, run = fixture(4)
    calls = []
    def waiting(*args, **kwargs):
        calls.append(args)
        raise ReadBudgetWaitExceeded()
    monkeypatch.setattr(repair, "prepare_image_repair", waiting)
    before = deepcopy(run)
    report = inspect_batch_nas_files(store, package, run)
    assert len(calls) == len(report["items"]) == 1
    assert report["halt"]["diagnostic"]["kind"] == "read_budget_wait"
    assert run == before and not network.sessions


def test_stat_ui_requires_click_and_does_not_process_pending_items():
    from test_quote_image_repair_batch_ui import start, enable, load_zip, widget
    app = load_zip(enable(start()), mode="只核對既有 NAS 原圖（不重新上傳）")
    before = deepcopy(app.session_state["quote_image_repair_batch_run"])
    assert not app.session_state["test_network"].sessions
    widget(app, "button", "只查本包未完成款 NAS 檔案資訊").click().run()
    assert not app.exception
    assert app.session_state["quote_image_repair_batch_run"] == before
    assert app.session_state["quote_image_repair_batch_run"]["mode"] == "existing_only"
    assert upload_count(app.session_state["test_network"]) == 0
    assert len(app.session_state["quote_image_repair_batch_stat_report"]["items"]) == 3
    assert any(b.proto.label == "下載本包唯讀檔案資訊 JSON" for b in app.get("download_button"))
