"""Safe Sheets failure diagnostics using fake services and original batch guards."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

from gspread.exceptions import APIError
import pytest

from dispatch_manager import DispatchError
import quote_image_repair_batch as repair
from test_quote_image_repair_batch import setup, upload_count


PRIVATE = "synthetic-secret private-session https://private.example.test/path?token=secret /private/file"


class PoisonStringAPIError(APIError):
    def __str__(self):
        self.str_calls = getattr(self, "str_calls", 0) + 1
        raise AssertionError("diagnostics must not stringify a typed APIError")

    def __repr__(self):
        return "<PoisonStringAPIError>"


def api_error(status=429, code=429, *, poisoned=False):
    response = SimpleNamespace(status_code=status, text=PRIVATE)
    response.json = lambda: {"error": {"code": code, "message": PRIVATE,
                                      "status": PRIVATE, "errors": [{"message": PRIVATE}]}}
    result = (PoisonStringAPIError if poisoned else APIError)(response)
    # APIError's constructor has already parsed its documented numeric code.
    response.json = lambda: pytest.fail("diagnostics must not parse a remote body again")
    return result


def assert_no_private(value):
    import json
    serialized = json.dumps(value, ensure_ascii=False)
    for fragment in ("synthetic-secret", "private-session", "private.example.test", "/private", "token=secret"):
        assert fragment not in serialized


@pytest.mark.parametrize("status,code,expected", [
    (429, 429, {"http_status": 429, "api_code": 429}),
    (400, 400, {"http_status": 400, "api_code": 400}),
    (500, 500, {"http_status": 500, "api_code": 500}),
    (100, 0, {"http_status": 100, "api_code": 0}),
    (599, 999999, {"http_status": 599, "api_code": 999999}),
    (True, True, {}), ("429", "429", {}), (429.0, 429.0, {}),
    (99, -1, {}), (600, 1000000, {}), (None, None, {}),
    (429, "429 " + PRIVATE, {"http_status": 429}),
    (PRIVATE, 429, {"api_code": 429}),
])
def test_typed_api_error_diagnostic_reads_only_valid_numeric_metadata(status, code, expected):
    error = api_error(status, code, poisoned=True)
    result = repair.safe_service_diagnostic(error)
    assert result == {"kind": "google_sheets_api", "origin": "typed", **expected}
    assert getattr(error, "str_calls", 0) == 0
    assert_no_private(result)


@pytest.mark.parametrize("status", [429, True, "429", None])
def test_api_error_with_missing_response_is_safe(status):
    error = api_error(status, 429, poisoned=True)
    error.response = None
    assert repair.safe_service_diagnostic(error) == {
        "kind": "google_sheets_api", "origin": "typed", "api_code": 429}


def test_safe_user_message_for_typed_api_error_does_not_call_str_or_echo_remote_body():
    error = api_error(poisoned=True)
    result = repair.safe_repair_error(error)
    assert isinstance(result, str) and "429" in result
    assert "Sheets" in result or "Google 試算表" in result
    assert getattr(error, "str_calls", 0) == 0
    assert_no_private(result)


@pytest.mark.parametrize("chain_field", ["__cause__", "__context__"])
def test_typed_api_error_inside_exception_chain_preserves_only_native_numeric_metadata(chain_field):
    inner = api_error(poisoned=True)
    outer = DispatchError(PRIVATE)
    setattr(outer, chain_field, inner)
    result = repair.safe_service_diagnostic(outer)
    assert result == {"kind": "google_sheets_api", "origin": "typed", "http_status": 429, "api_code": 429}
    assert_no_private(result)
    message = repair.safe_repair_error(outer)
    assert "429" in message
    assert_no_private(message)


def test_exception_chain_cycle_without_native_metadata_does_not_stringify_or_hang():
    first, second = DispatchError(PRIVATE), RuntimeError(PRIVATE)
    first.__cause__, second.__context__ = second, first
    assert repair.safe_service_diagnostic(first) is None
    assert_no_private(repair.safe_repair_error(first))


@pytest.mark.parametrize("text", [
    "APIError: [429]: " + PRIVATE,
    "圖庫未完成：APIError: [429]: " + PRIVATE,
    "對應未完成或結果待確認：APIError: [429]: " + PRIVATE,
    "偽造前綴 圖庫未完成：APIError: [429]: " + PRIVATE,
    "\n圖庫未完成：APIError: [429]: " + PRIVATE,
    "圖庫未完成：APIError: [4290]: " + PRIVATE,
    "圖庫未完成：APIError: [099]: " + PRIVATE,
    "圖庫未完成：APIError: [600]: " + PRIVATE,
    "圖庫未完成：APIError: [999]: " + PRIVATE,
    "圖庫未完成：APIError: [true]: " + PRIVATE,
    "圖庫未完成：APIError: [429 ]: " + PRIVATE,
    "圖庫未完成：SomethingElse: [429]: " + PRIVATE,
    "對應未完成或結果待確認：偽造 APIError: [429]: " + PRIVATE,
])
def test_uncontrolled_or_malformed_apierror_text_is_never_classified_as_sheets(text):
    for error in (RuntimeError(text), DispatchError(text)):
        result = repair.safe_service_diagnostic(error)
        assert result is None or result.get("kind") != "google_sheets_api"
        assert_no_private(result)
        assert_no_private(repair.safe_repair_error(error))


def patched_phase(monkeypatch, phase, failed_identity, error, *, report_wrapper=False, report_diagnostic=None):
    name = {"prepare": "prepare_image_repair", "save": "save_image_repair",
            "readback": "_fresh_nas_readback"}[phase]
    original = getattr(repair, name)
    failed = []
    def call(store, plan_or_identity, *args, **kwargs):
        identity = plan_or_identity if phase == "prepare" else plan_or_identity["source"]["identity"]
        if identity == failed_identity:
            failed.append(identity)
            if report_wrapper:
                report = {"商品": identity, "結果": "待處理", "原因": error}
                if report_diagnostic is not None:
                    report["diagnostic"] = report_diagnostic
                return [report]
            raise error
        return original(store, plan_or_identity, *args, **kwargs)
    monkeypatch.setattr(repair, name, call)
    return failed


def assert_failure(item, phase, diagnostic):
    assert item["status"] == "failed" and item["receipt"] is None
    assert item["failure_phase"] == phase
    assert item["diagnostic"] == diagnostic
    assert datetime.fromisoformat(item["failure_at"]).tzinfo is not None
    assert_no_private(item["reason"])


def assert_halt(run, identity, phase, diagnostic):
    halt = run["halt"]
    assert isinstance(halt, dict)
    assert_no_private(halt)
    allowed = {"identity", "id", "phase", "at", "checked_at", "failure_at", "diagnostic", "reason"}
    assert set(halt) <= allowed
    if "reason" in halt:
        assert halt["reason"] == "服務回報 429；本批已立即停止，未自動重試"
    assert halt["diagnostic"] == diagnostic
    assert halt.get("identity", halt.get("id")) == identity
    assert halt["phase"] == phase
    stamp = halt.get("at", halt.get("checked_at", halt.get("failure_at")))
    assert datetime.fromisoformat(stamp).tzinfo is not None


@pytest.mark.parametrize("phase", ["prepare", "save", "readback"])
def test_429_halts_after_failed_item_update_and_preserves_prior_success_and_untouched_ready(monkeypatch, phase):
    _, store, network, package = setup(4)
    run = repair.prepare_batch_repair(store, package, mode="upload")
    run = repair.process_next_repair(store, package, run, actor="synthetic", limit=1)
    first_receipt = deepcopy(run["items"][0]["receipt"])
    target = run["items"][1]["identity"]
    failed = patched_phase(monkeypatch, phase, target, api_error(poisoned=True))
    checkpoints = []
    result = repair.process_next_repair(store, package, run, actor="synthetic", limit=10,
                                        on_update=lambda value: checkpoints.append(deepcopy(value)))
    diagnostic = {"kind": "google_sheets_api", "origin": "typed", "http_status": 429, "api_code": 429}
    assert [item["status"] for item in result["items"]] == ["completed", "failed", "ready", "ready"]
    assert [item["attempts"] for item in result["items"]] == [1, 1, 0, 0]
    assert result["items"][0]["receipt"] == first_receipt
    assert_failure(result["items"][1], phase, diagnostic)
    assert_halt(result, target, phase, diagnostic)
    assert failed == [target] and len(checkpoints) == 1
    assert checkpoints[-1] == result
    assert upload_count(network) == (2 if phase == "readback" else 1)
    assert_no_private(result)


def test_typed_429_diagnostic_in_formal_save_report_halts_without_using_reason_body(monkeypatch):
    _, store, network, package = setup(3)
    run = repair.prepare_batch_repair(store, package, mode="upload")
    target = run["items"][0]["identity"]
    diagnostic = {"kind": "google_sheets_api", "origin": "typed", "http_status": 429, "api_code": 429}
    failed = patched_phase(monkeypatch, "save", target, PRIVATE, report_wrapper=True, report_diagnostic=diagnostic)
    updates = []
    result = repair.process_next_repair(store, package, run, actor="synthetic", on_update=updates.append)
    assert [item["status"] for item in result["items"]] == ["failed", "ready", "ready"]
    assert [item["attempts"] for item in result["items"]] == [1, 0, 0]
    assert_failure(result["items"][0], "save", diagnostic)
    assert_halt(result, target, "save", diagnostic)
    assert failed == [target] and len(updates) == 1 and updates[-1] == result
    assert upload_count(network) == 0
    assert_no_private(result)


@pytest.mark.parametrize("prefix", ["圖庫未完成：", "對應未完成或結果待確認："])
def test_untyped_429_report_text_does_not_trigger_quota_halt(monkeypatch, prefix):
    _, store, network, package = setup(3)
    run = repair.prepare_batch_repair(store, package, mode="upload")
    target = run["items"][0]["identity"]
    patched_phase(monkeypatch, "save", target, f"{prefix}APIError: [429]: {PRIVATE}", report_wrapper=True)
    updates = []
    result = repair.process_next_repair(store, package, run, actor="synthetic", on_update=updates.append)
    assert [item["status"] for item in result["items"]] == ["failed", "completed", "completed"]
    assert_failure(result["items"][0], "save", None)
    assert result.get("halt") in (None, {})
    assert len(updates) == 3 and upload_count(network) == 2
    assert_no_private(result)


@pytest.mark.parametrize("diagnostic", [
    "429 " + PRIVATE,
    {"kind": "google_sheets_api", "origin": "typed", "http_status": "429"},
    {"kind": "google_sheets_api", "origin": "typed", "api_code": True},
    {"kind": "google_sheets_api", "origin": "typed", "http_status": 429.0},
    {"kind": "google_sheets_api", "origin": PRIVATE, "api_code": 429},
    {"kind": PRIVATE, "origin": "typed", "http_status": 429},
    {"kind": "google_sheets_api", "origin": "typed", "http_status": 429, "body": PRIVATE},
    {"kind": "google_sheets_api", "origin": "typed", "http_status": 999},
])
def test_invalid_or_unbounded_report_diagnostic_never_becomes_a_quota_halt(monkeypatch, diagnostic):
    _, store, _, package = setup(2)
    run = repair.prepare_batch_repair(store, package, mode="upload")
    target = run["items"][0]["identity"]
    patched_phase(monkeypatch, "save", target, PRIVATE, report_wrapper=True, report_diagnostic=diagnostic)
    result = repair.process_next_repair(store, package, run, actor="synthetic")
    assert [item["status"] for item in result["items"]] == ["failed", "completed"]
    assert_failure(result["items"][0], "save", None)
    assert result.get("halt") in (None, {})
    assert_no_private(result)


@pytest.mark.parametrize("catch_site", ["put_assets", "binding_append"])
def test_real_storage_catches_preserve_native_429_diagnostic_without_reason_text_matching(monkeypatch, catch_site):
    from product_images import PRODUCT_IMAGE_SHEET
    _, store, network, package = setup(2)
    error = api_error(poisoned=True)
    def quota(*args, **kwargs):
        raise error
    if catch_site == "put_assets":
        monkeypatch.setattr(store, "put_assets", quota)
    else:
        binding_sheet = store._sheet(PRODUCT_IMAGE_SHEET, create=True)
        monkeypatch.setattr(binding_sheet, "append_rows", quota)
    run = repair.prepare_batch_repair(store, package, mode="upload")
    updates = []
    result = repair.process_next_repair(store, package, run, actor="synthetic", on_update=updates.append)
    diagnostic = {"kind": "google_sheets_api", "origin": "typed", "http_status": 429, "api_code": 429}
    target = result["items"][0]["identity"]
    assert [item["status"] for item in result["items"]] == ["failed", "ready"]
    assert [item["attempts"] for item in result["items"]] == [1, 0]
    assert_failure(result["items"][0], "save", diagnostic)
    assert_halt(result, target, "save", diagnostic)
    assert len(updates) == 1 and updates[-1] == result
    assert upload_count(network) == (0 if catch_site == "put_assets" else 1)
    assert getattr(error, "str_calls", 0) == 0
    assert_no_private(result)


@pytest.mark.parametrize("catch_site", ["put_assets", "binding_append"])
def test_storage_pending_report_itself_has_no_remote_body_or_exception_stringification(monkeypatch, catch_site):
    from product_images import PRODUCT_IMAGE_SHEET
    from test_product_image_library import assignment
    _, store, _, package = setup(1)
    error = api_error(poisoned=True)
    def quota(*args, **kwargs):
        raise error
    if catch_site == "put_assets":
        monkeypatch.setattr(store, "put_assets", quota)
    else:
        binding_sheet = store._sheet(PRODUCT_IMAGE_SHEET, create=True)
        monkeypatch.setattr(binding_sheet, "append_rows", quota)
    source = store.catalog()[0]
    assets = [package["assets"][key] for key in package["products"][0]["sha256"]]
    report = store.save_product_images([assignment(source, assets)], actor="synthetic", origin="test")
    assert report[0]["結果"] == "待處理"
    assert report[0]["diagnostic"] == {"kind": "google_sheets_api", "origin": "typed",
                                         "http_status": 429, "api_code": 429}
    assert "429" in report[0]["原因"]
    assert getattr(error, "str_calls", 0) == 0
    assert_no_private(report)


@pytest.mark.parametrize("phase", ["prepare", "save", "readback"])
@pytest.mark.parametrize("code", [400, 500])
def test_non_429_sheets_error_is_isolated_and_remaining_items_continue(monkeypatch, phase, code):
    _, store, network, package = setup(3)
    run = repair.prepare_batch_repair(store, package, mode="upload")
    target = run["items"][1]["identity"]
    failed = patched_phase(monkeypatch, phase, target, api_error(code, code, poisoned=True))
    updates = []
    result = repair.process_next_repair(store, package, run, actor="synthetic", on_update=updates.append)
    diagnostic = {"kind": "google_sheets_api", "origin": "typed", "http_status": code, "api_code": code}
    assert [item["status"] for item in result["items"]] == ["completed", "failed", "completed"]
    assert [item["attempts"] for item in result["items"]] == [1, 1, 1]
    assert_failure(result["items"][1], phase, diagnostic)
    assert result.get("halt") in (None, {})
    assert failed == [target] and len(updates) == 3
    assert upload_count(network) == (3 if phase == "readback" else 2)
    assert_no_private(result)


@pytest.mark.parametrize("phase", ["prepare", "save", "readback"])
def test_unknown_error_still_has_phase_time_and_no_untrusted_diagnostic(monkeypatch, phase):
    _, store, _, package = setup(2)
    run = repair.prepare_batch_repair(store, package, mode="upload")
    target = run["items"][0]["identity"]
    patched_phase(monkeypatch, phase, target, RuntimeError(PRIVATE))
    result = repair.process_next_repair(store, package, run, actor="synthetic")
    assert_failure(result["items"][0], phase, None)
    assert result["items"][1]["status"] == "completed"
    assert result.get("halt") in (None, {})
    assert_no_private(result)


def test_receipt_export_keeps_failure_metadata_and_receipts_without_source_blocks_or_debug(monkeypatch):
    import json
    sheet, store, _, package = setup(3)
    sheet.sheets["G正版"].rows[0][1] = PRIVATE
    run = repair.prepare_batch_repair(store, package, mode="upload")
    target = run["items"][1]["identity"]
    patched_phase(monkeypatch, "save", target, api_error(poisoned=True))
    result = repair.process_next_repair(store, package, run, actor="synthetic")
    assert [item["status"] for item in result["items"]] == ["completed", "failed", "ready"]
    assert PRIVATE in json.dumps(result, ensure_ascii=False)  # Fixture actually contains private source text.
    result["debug"] = PRIVATE
    for item in result["items"]:
        item["debug"] = PRIVATE
    payload = repair.repair_receipt_payload(package, result)
    assert_no_private(payload)
    assert payload["receipts"] == [result["items"][0]["receipt"]]
    assert [item["status"] for item in payload["items"]] == ["completed", "failed", "ready"]
    failed = payload["items"][1]
    assert failed["failure_phase"] == "save"
    assert failed["failure_at"] == result["items"][1]["failure_at"]
    assert failed["diagnostic"] == result["items"][1]["diagnostic"]
    assert payload["halt"]["diagnostic"] == failed["diagnostic"]
    for item in payload["items"]:
        assert set(item) == {"identity", "sha256", "status", "attempts", "failure_phase", "failure_at", "diagnostic"}
        assert not ({"source", "block", "formulas", "values", "name", "price", "vendor", "debug"} & set(item))
