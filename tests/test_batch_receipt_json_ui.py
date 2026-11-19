"""Receipt JSON stays visible without browser downloads or new storage calls."""
import json
from types import SimpleNamespace

from gspread.exceptions import APIError
from streamlit.testing.v1 import AppTest

from quote_image_repair_batch import prepare_batch_repair, repair_receipt_payload
from test_quote_image_repair_batch import setup
from test_quote_image_repair_batch_ui import APP, enable, load_zip, confirm, widget


def start():
    app = AppTest.from_string(APP, default_timeout=20).run()
    assert not app.exception
    return app


def test_json_expander_is_dom_readable_and_contains_only_receipts_and_safe_scope():
    app = confirm(load_zip(enable(start())))
    widget(app, "number_input", "本次最多處理款數").set_value(1).run()
    widget(app, "button", "處理下一批").click().run()
    assert not app.exception
    code = next(block for block in app.code if block.proto.language == "json")
    payload = json.loads(code.value)
    assert len(payload["receipts"]) == 1
    assert payload["items"][0]["status"] == "completed"
    assert payload["items"][1]["status"] == "ready"
    assert "source" not in payload and "values" not in payload
    assert "synthetic-secret" not in code.value and "進價" not in code.value
    calls = app.session_state["test_store_calls"]
    app.run()
    assert app.session_state["test_store_calls"] == calls
    assert len(app.code) == 1


def test_429_failure_json_phase_and_halt_remain_visible_with_next_items_ready():
    app = confirm(load_zip(enable(start())))
    store = app.session_state["test_store"]
    error = APIError(SimpleNamespace(status_code=429, json=lambda: {"error": {"code": 429, "message": "secret https://private.test"}}))
    def stop(assets):
        raise error
    store.put_assets = stop
    widget(app, "button", "處理下一批").click().run()
    assert not app.exception and not app.success
    assert any("429" in warning.value for warning in app.warning)
    payload = json.loads(app.code[0].value)
    assert payload["items"][0]["failure_phase"] == "save"
    assert payload["items"][0]["diagnostic"]["http_status"] == 429
    assert [item["status"] for item in payload["items"]] == ["failed", "ready", "ready"]
    assert [item["attempts"] for item in payload["items"]] == [1, 0, 0]
    assert payload["halt"]["phase"] == "save"
    assert "secret" not in app.code[0].value and "private.test" not in app.code[0].value


def test_payload_drops_unapproved_diagnostic_body_fields_and_old_unknown_phase():
    _, store, _, package = setup(1)
    run = prepare_batch_repair(store, package, mode="upload")
    run["items"][0].update(status="failed", failure_phase="secret", diagnostic={
        "kind": "google_sheets_api", "origin": "typed", "api_code": 429, "body": "secret"})
    payload = repair_receipt_payload(package, run)
    assert payload["items"][0]["failure_phase"] is None
    assert payload["items"][0]["diagnostic"] is None
    assert "secret" not in json.dumps(payload)


def test_quota_metric_method_and_redacted_message_survive_real_storage_to_receipt_ui():
    from test_google_quota_diagnostic import quota_error
    app = confirm(load_zip(enable(start())))
    store = app.session_state["test_store"]
    error = quota_error(method="POST", metric="write_requests", limit="WriteRequestsPerMinutePerUser")
    def stop(assets):
        raise error
    store.put_assets = stop
    widget(app, "button", "處理下一批").click().run()
    assert not app.exception
    payload_text = app.code[0].value
    payload = json.loads(payload_text)
    diagnostic = payload["items"][0]["diagnostic"]
    assert diagnostic["request_method"] == "POST"
    assert diagnostic["quota_metric"] == "sheets.googleapis.com/write_requests"
    assert diagnostic["quota_limit"] == "WriteRequestsPerMinutePerUser"
    assert "[REDACTED]" in diagnostic["google_message"]
    assert payload["halt"]["diagnostic"] == diagnostic
    assert [i["status"] for i in payload["items"]] == ["failed", "ready", "ready"]
    assert [i["attempts"] for i in payload["items"]] == [1, 0, 0]
    assert all(text not in payload_text for text in ("private-project", "private-sheet", "secret-token", "private@example.test"))
