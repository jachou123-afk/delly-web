"""Allowlisted NAS failure subtype survives storage, batch and UI safely."""
import json

import pytest

from dispatch_manager import DispatchError
from product_images import PRODUCT_IMAGE_SHEET
from product_image_storage import checked_image_diagnostic, image_error_diagnostic, image_error_summary
import quote_image_repair_batch as repair
from synology_image_store import NasDiagnosticError
from test_product_image_library import assignment
from test_quote_image_repair_batch import setup


PRIVATE = "synthetic-secret private-session https://private.example.test/path?token=secret /private/file"


class NoStringNasError(NasDiagnosticError):
    def __str__(self):
        self.str_calls = getattr(self, "str_calls", 0) + 1
        raise AssertionError("NAS diagnostic must not stringify remote content")

    def __repr__(self):
        return "<NoStringNasError>"


def no_private(value):
    text = json.dumps(value, ensure_ascii=False)
    assert all(fragment not in text for fragment in
               ("synthetic-secret", "private-session", "private.example.test", "token=secret", "/private"))


@pytest.mark.parametrize("kind", list(NasDiagnosticError.LABELS))
def test_every_allowlisted_nas_subtype_is_preserved_with_fixed_label_and_codes(kind):
    error = NoStringNasError(PRIVATE, kind=kind, http_status=502, api_code=408)
    expected = {"kind": "nas", "origin": "typed", "nas_kind": kind,
                "http_status": 502, "api_code": 408}
    assert image_error_diagnostic(error) == expected
    assert checked_image_diagnostic(expected) == expected
    message = image_error_summary(expected)
    assert NasDiagnosticError.LABELS[kind] in message
    assert "HTTP 502" in message and "API 代碼 408" in message
    assert getattr(error, "str_calls", 0) == 0
    no_private(message)


def test_legal_unknown_is_fixed_and_does_not_echo_original_unknown_kind():
    error = NoStringNasError(PRIVATE, kind="unknown private subtype")
    diagnostic = image_error_diagnostic(error)
    assert diagnostic == {"kind": "nas", "origin": "typed", "nas_kind": "unknown"}
    message = image_error_summary(diagnostic)
    assert message and ("未分類" in message or "NAS 回應失敗" in message)
    assert "unknown private subtype" not in message
    no_private(message)


@pytest.mark.parametrize("value", [PRIVATE, "API 回應結構不完整", "", None, True, 1, [], {}, ["response_shape"]])
def test_invalid_or_unhashable_subtype_is_rejected_without_reflecting_value(value):
    diagnostic = {"kind": "nas", "origin": "typed", "nas_kind": value}
    assert checked_image_diagnostic(diagnostic) is None
    no_private(image_error_summary(diagnostic))


@pytest.mark.parametrize("kind,origin", [("google_sheets_api", "typed"), ("http", "typed"),
    ("network_timeout", "typed"), ("network_connection", "typed"), ("read_budget_wait", "local")])
def test_non_nas_categories_cannot_smuggle_nas_subtype(kind, origin):
    assert checked_image_diagnostic({"kind": kind, "origin": origin, "nas_kind": "response_shape"}) is None


@pytest.mark.parametrize("value", [
    {"kind": "nas", "origin": "local", "nas_kind": "response_shape"},
    {"kind": "nas", "origin": "typed", "nas_kind": "response_shape", "body": PRIVATE},
    {"kind": "nas", "origin": "typed", "nas_kind": "response_shape", "url": PRIVATE},
    {"kind": "nas", "origin": "typed", "nas_kind": "response_shape", "http_status": True},
    {"kind": "nas", "origin": "typed", "nas_kind": "response_shape", "http_status": "502"},
    {"kind": "nas", "origin": "typed", "nas_kind": "response_shape", "http_status": 600},
    {"kind": "nas", "origin": "typed", "nas_kind": "response_shape", "api_code": -1},
    {"kind": "nas", "origin": "typed", "nas_kind": "response_shape", "api_code": 1000000},
    {"kind": "nas", "origin": "typed", "nas_kind": "response_shape", "api_code": 408.0},
])
def test_subtype_does_not_weaken_origin_extra_field_or_bounded_code_guards(value):
    assert checked_image_diagnostic(value) is None
    no_private(image_error_summary(value))


def test_old_nas_diagnostic_without_subtype_stays_valid_and_does_not_guess_one():
    old = {"kind": "nas", "origin": "typed", "http_status": 502, "api_code": 408}
    assert checked_image_diagnostic(old) == old
    assert "nas_kind" not in checked_image_diagnostic(old)
    assert image_error_summary(old) == "NAS 回應失敗；HTTP 502；API 代碼 408"


@pytest.mark.parametrize("status,code", [(100, 0), (599, 999999)])
def test_numeric_boundaries_remain_accepted(status, code):
    diagnostic = {"kind": "nas", "origin": "typed", "nas_kind": "response_shape",
                  "http_status": status, "api_code": code}
    assert checked_image_diagnostic(diagnostic) == diagnostic


@pytest.mark.parametrize("chain", ["direct", "cause", "context", "from_none"])
def test_native_subtype_survives_wrapper_chains_including_suppressed_context(chain):
    error = NoStringNasError(PRIVATE, kind="response_shape", http_status=502)
    if chain == "direct":
        outer = error
    elif chain == "from_none":
        try:
            try:
                raise error
            except NasDiagnosticError:
                raise DispatchError(PRIVATE) from None
        except DispatchError as caught:
            outer = caught
        assert outer.__suppress_context__ is True
    else:
        outer = DispatchError(PRIVATE)
        setattr(outer, "__cause__" if chain == "cause" else "__context__", error)
    diagnostic = image_error_diagnostic(outer)
    assert diagnostic == {"kind": "nas", "origin": "typed", "nas_kind": "response_shape", "http_status": 502}
    assert NasDiagnosticError.LABELS["response_shape"] in image_error_summary(diagnostic)
    assert getattr(error, "str_calls", 0) == 0
    no_private(diagnostic)


@pytest.mark.parametrize("site", ["put_assets", "binding_append"])
def test_both_storage_catches_preserve_subtype_without_stringifying_exception(monkeypatch, site):
    _, store, _, package = setup(1)
    error = NoStringNasError(PRIVATE, kind="response_shape", http_status=502)
    def failing(*args, **kwargs):
        raise error
    if site == "put_assets":
        monkeypatch.setattr(store, "put_assets", failing)
    else:
        monkeypatch.setattr(store._sheet(PRODUCT_IMAGE_SHEET, create=True), "append_rows", failing)
    source = store.catalog()[0]
    assets = [package["assets"][key] for key in package["products"][0]["sha256"]]
    report = store.save_product_images([assignment(source, assets)], actor="synthetic", origin="test")
    assert report[0]["結果"] == "待處理"
    assert report[0]["diagnostic"] == {"kind": "nas", "origin": "typed", "nas_kind": "response_shape", "http_status": 502}
    assert NasDiagnosticError.LABELS["response_shape"] in report[0]["原因"]
    assert "HTTP 502" in report[0]["原因"] and getattr(error, "str_calls", 0) == 0
    no_private(report)


def test_batch_item_and_export_preserve_subtype_from_real_storage_catch(monkeypatch):
    _, store, _, package = setup(1)
    error = NoStringNasError(PRIVATE, kind="response_shape")
    def failing(*args, **kwargs):
        raise error
    monkeypatch.setattr(store, "put_assets", failing)
    run = repair.prepare_batch_repair(store, package, mode="upload")
    result = repair.process_next_repair(store, package, run, actor="synthetic")
    item = result["items"][0]
    assert item["status"] == "failed" and item["failure_phase"] == "save"
    assert item["diagnostic"] == {"kind": "nas", "origin": "typed", "nas_kind": "response_shape"}
    assert NasDiagnosticError.LABELS["response_shape"] in item["reason"]
    payload = repair.repair_receipt_payload(package, result)
    assert payload["items"][0]["diagnostic"] == item["diagnostic"]
    assert getattr(error, "str_calls", 0) == 0
    no_private(payload)


def test_actual_batch_ui_code_json_and_visible_reason_retain_fixed_subtype(monkeypatch):
    from test_quote_image_repair_batch_ui import confirm, enable, load_zip, start, widget
    app = confirm(load_zip(enable(start())))
    error = NoStringNasError(PRIVATE, kind="response_shape")
    def failing(*args, **kwargs):
        raise error
    monkeypatch.setattr(app.session_state["test_store"], "put_assets", failing)
    widget(app, "button", "處理下一批").click().run()
    assert not app.exception
    run = app.session_state["quote_image_repair_batch_run"]
    assert all(item["status"] == "failed" and item["diagnostic"]["nas_kind"] == "response_shape" for item in run["items"])
    code_payloads = [json.loads(block.value) for block in app.code if block.language == "json"]
    assert code_payloads and all(item["diagnostic"]["nas_kind"] == "response_shape" for item in code_payloads[-1]["items"])
    assert NasDiagnosticError.LABELS["response_shape"] in str(app.dataframe[0].value)
    assert getattr(error, "str_calls", 0) == 0
    no_private(code_payloads)
