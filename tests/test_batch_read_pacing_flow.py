"""Local read-budget stops propagate through formal storage without API retries."""
from copy import deepcopy
import json

import pytest
from streamlit.testing.v1 import AppTest

from batch_read_pacing import ReadBudgetWaitExceeded, WAIT_LIMIT_ERROR
from dispatch_manager import DispatchError
from product_image_storage import checked_image_diagnostic
import quote_image_repair_batch as repair
from test_quote_image_repair_batch import setup, upload_count
from test_quote_image_repair_batch_ui import APP, enable, load_zip, confirm, widget


@pytest.mark.parametrize("phase", ["prepare", "save", "readback"])
def test_local_wait_limit_stops_at_current_phase_and_leaves_remaining_ready(monkeypatch, phase):
    _, store, network, package = setup(3)
    run = repair.prepare_batch_repair(store, package, mode="upload")
    function = {"prepare": "prepare_image_repair", "save": "save_image_repair", "readback": "_fresh_nas_readback"}[phase]
    def stop(*args, **kwargs):
        raise ReadBudgetWaitExceeded()
    monkeypatch.setattr(repair, function, stop)
    updates = []
    result = repair.process_next_repair(store, package, run, actor="synthetic", on_update=updates.append)
    assert [i["status"] for i in result["items"]] == ["failed", "ready", "ready"]
    assert [i["attempts"] for i in result["items"]] == [1, 0, 0]
    assert result["items"][0]["failure_phase"] == phase
    assert result["items"][0]["reason"] == WAIT_LIMIT_ERROR
    assert result["halt"]["diagnostic"] == {"kind": "read_budget_wait", "origin": "local"}
    assert len(updates) == 1 and updates[0]["halt"] == result["halt"]
    payload = repair.repair_receipt_payload(package, result)
    assert payload["halt"]["reason"] == WAIT_LIMIT_ERROR
    assert "http_status" not in payload["halt"]["diagnostic"] and "api_code" not in payload["halt"]["diagnostic"]
    if phase != "readback":
        assert upload_count(network) == 0


def test_real_storage_pending_preserves_local_limit_without_writes_or_text_guess(monkeypatch):
    sheet, store, network, package = setup(2)
    run = repair.prepare_batch_repair(store, package, mode="upload")
    before = {name: deepcopy(ws.rows) for name, ws in sheet.sheets.items()}
    def stop(assets):
        raise ReadBudgetWaitExceeded()
    monkeypatch.setattr(store, "put_assets", stop)
    result = repair.process_next_repair(store, package, run, actor="synthetic")
    assert result["items"][0]["diagnostic"] == {"kind": "read_budget_wait", "origin": "local"}
    assert result["items"][0]["failure_phase"] == "save" and result["items"][1]["status"] == "ready"
    assert {name: ws.rows for name, ws in sheet.sheets.items()} == before
    assert upload_count(network) == 0
    assert repair.safe_service_diagnostic(DispatchError(WAIT_LIMIT_ERROR)) is None


def test_existing_success_receipt_is_not_retried_when_later_read_budget_stops(monkeypatch):
    _, store, network, package = setup(3)
    run = repair.process_next_repair(store, package, repair.prepare_batch_repair(store, package, mode="upload"), actor="synthetic", limit=1)
    first = deepcopy(run["items"][0]["receipt"])
    original = repair.prepare_image_repair
    def stop_second(store, identity):
        if identity == "G正版:no2":
            raise ReadBudgetWaitExceeded()
        return original(store, identity)
    monkeypatch.setattr(repair, "prepare_image_repair", stop_second)
    result = repair.process_next_repair(store, package, run, actor="synthetic")
    assert [i["status"] for i in result["items"]] == ["completed", "failed", "ready"]
    assert result["items"][0]["receipt"] == first and upload_count(network) == 1


@pytest.mark.parametrize("value", [
    {"kind": "read_budget_wait", "origin": "typed"},
    {"kind": "read_budget_wait", "origin": "local", "http_status": 429},
    {"kind": "read_budget_wait", "origin": "local", "body": "private"},
])
def test_local_wait_diagnostic_cannot_smuggle_remote_codes_or_body(value):
    assert checked_image_diagnostic(value) is None


def start():
    app = AppTest.from_string(APP, default_timeout=20).run()
    assert not app.exception
    return app


def test_ui_preflight_is_unpaced_and_only_next_batch_builds_paced_store():
    app = confirm(load_zip(enable(start())))
    assert "test_pacing_calls" not in app.session_state
    app.session_state["test_pacing_wait"] = True
    widget(app, "number_input", "本次最多處理款數").set_value(1).run()
    widget(app, "button", "處理下一批").click().run()
    assert not app.exception and app.session_state["test_pacing_calls"] == 1
    assert app.session_state["quote_image_repair_batch_run"]["items"][0]["status"] == "completed"


def test_ui_local_wait_limit_is_visible_as_local_stop_not_remote_429():
    app = confirm(load_zip(enable(start())))
    def stop(assets):
        raise ReadBudgetWaitExceeded()
    app.session_state["test_store"].put_assets = stop
    widget(app, "button", "處理下一批").click().run()
    assert not app.exception
    assert any("本地讀取等待達上限" in warning.value for warning in app.warning)
    assert not any("429" in warning.value for warning in app.warning)
    payload = json.loads(app.code[0].value)
    assert payload["items"][0]["diagnostic"] == {"kind": "read_budget_wait", "origin": "local"}
    assert [i["status"] for i in payload["items"]] == ["failed", "ready", "ready"]
