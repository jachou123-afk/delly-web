"""Quota context survives storage/receipts; private remote content never does."""
from copy import deepcopy
import json
from types import SimpleNamespace

from gspread.exceptions import APIError
import pytest

from dispatch_manager import DispatchError
from google_quota_diagnostic import REDACTED_MESSAGE, checked_fields, quota_error_fields
from product_image_storage import checked_image_diagnostic, image_error_diagnostic, image_error_summary
from quote_image_repair_batch import ImageStorageFailure, safe_service_diagnostic


def quota_error(*, method="GET", metric="read_requests", limit="ReadRequestsPerMinutePerUser", message=None):
    kind = "Write" if metric == "write_requests" else "Read"
    error = {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": message or (
        f"Quota exceeded for quota metric '{kind} requests' and limit '{kind} requests per minute per user' "
        "of service 'sheets.googleapis.com' for consumer 'project_number:private-project-123'."),
        "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "RATE_LIMIT_EXCEEDED",
            "domain": "googleapis.com", "metadata": {"quota_metric": "sheets.googleapis.com/" + metric,
                "quota_limit": limit, "consumer": "projects/private-project-123",
                "credential": "secret-token", "email": "private@example.test"}}]}
    response = SimpleNamespace(status_code=429, request=SimpleNamespace(method=method,
        url="https://sheets.googleapis.com/v4/spreadsheets/private-sheet?key=secret-token"),
        json=lambda: {"error": deepcopy(error)})
    exc = APIError(response)
    # The APIError already parsed JSON once. Diagnostics must not parse it again.
    response.json = lambda: pytest.fail("must read APIError.error, not the response body again")
    return exc


def test_native_get_quota_fields_and_consumer_redaction_without_request_url_or_credentials():
    diagnostic = image_error_diagnostic(quota_error())
    assert diagnostic["request_method"] == "GET"
    assert diagnostic["quota_metric"] == "sheets.googleapis.com/read_requests"
    assert diagnostic["quota_limit"] == "ReadRequestsPerMinutePerUser"
    assert diagnostic["quota_reason"] == "RATE_LIMIT_EXCEEDED"
    assert "consumer '[REDACTED]'" in diagnostic["google_message"]
    assert checked_image_diagnostic(diagnostic) == diagnostic
    encoded = json.dumps(diagnostic)
    assert all(text not in encoded for text in ("private-project", "private-sheet", "secret-token", "private@example.test"))
    assert "方法 GET" in image_error_summary(diagnostic)
    assert "ReadRequestsPerMinutePerUser" in image_error_summary(diagnostic)


def test_post_write_quota_survives_cause_chain_and_compact_storage_failure():
    cause = quota_error(method="POST", metric="write_requests", limit="WriteRequestsPerMinutePerUser")
    wrapped = DispatchError("safe storage message")
    wrapped.__cause__ = cause
    diagnostic = image_error_diagnostic(wrapped)
    assert diagnostic["request_method"] == "POST" and diagnostic["quota_metric"].endswith("write_requests")
    assert safe_service_diagnostic(ImageStorageFailure(diagnostic)) == diagnostic


@pytest.mark.parametrize("message", [
    "secret-token https://private.example/path?token=abc private@example.test",
    "raw private supplier name and product quotation 12.2 RMB",
    "Quota exceeded for quota metric 'secret-token' and limit 'private-data' of service 'sheets.googleapis.com' for consumer 'x'.",
    "Quota exceeded\nprivate response body", "x" * 3000,
])
def test_unrecognized_messages_are_replaced_not_heuristically_leaked(message):
    diagnostic = image_error_diagnostic(quota_error(message=message))
    assert diagnostic["google_message"] == REDACTED_MESSAGE
    assert message not in json.dumps(diagnostic)
    assert diagnostic["request_method"] == "GET"


@pytest.mark.parametrize("field,value", [
    ("request_method", "https://private.example"), ("quota_metric", "secret-token"),
    ("quota_limit", "PrivateProject123"), ("quota_reason", "secret-token"),
    ("google_message", "private@example.test"), ("quota_limit", 60),
])
def test_receipt_validator_rejects_private_or_malformed_extra_values(field, value):
    assert checked_fields({field: value}) is None
    assert checked_image_diagnostic({"kind": "google_sheets_api", "origin": "typed", "api_code": 429, field: value}) is None


def test_quota_metadata_is_not_allowed_on_nas_or_non_429_failures():
    fields = {"request_method": "GET", "quota_metric": "sheets.googleapis.com/read_requests"}
    assert checked_image_diagnostic({"kind": "nas", "origin": "typed", "http_status": 429, **fields}) is None
    assert checked_image_diagnostic({"kind": "google_sheets_api", "origin": "typed", "http_status": 403, **fields}) is None
    error = quota_error()
    error.code, error.response.status_code = 403, 403
    assert quota_error_fields(error) == {}


def test_conflicting_errorinfo_is_left_unclassified_and_unknown_fields_are_not_exported():
    exc = quota_error()
    other = deepcopy(exc.error["details"][0])
    other["metadata"]["quota_metric"] = "sheets.googleapis.com/write_requests"
    exc.error["details"].append(other)
    diagnostic = image_error_diagnostic(exc)
    assert "quota_metric" not in diagnostic and "quota_limit" not in diagnostic
    assert diagnostic["request_method"] == "GET"
    assert "consumer" not in diagnostic


def test_unusable_old_payload_keeps_minimal_safe_diagnostic():
    exc = APIError(SimpleNamespace(status_code=429,
        json=lambda: {"error": {"code": 429, "message": "private secret body"}}))
    assert image_error_diagnostic(exc) == {"kind": "google_sheets_api", "origin": "typed", "http_status": 429, "api_code": 429}


@pytest.mark.parametrize("catch_site", ["location_append", "binding_append"])
def test_actual_save_catches_keep_write_quota_context_and_stop_without_retry(monkeypatch, catch_site):
    from nas_dispatch_storage import LOCATION_SHEET
    from product_images import PRODUCT_IMAGE_SHEET
    from quote_image_repair_batch import prepare_batch_repair, process_next_repair
    from test_quote_image_repair_batch import setup, upload_count
    _, store, network, package = setup(2)
    target = store._sheet(LOCATION_SHEET if catch_site == "location_append" else PRODUCT_IMAGE_SHEET, create=True)
    error = quota_error(method="POST", metric="write_requests", limit="WriteRequestsPerMinutePerUser")
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise error
    monkeypatch.setattr(target, "append_rows", fail)
    run = process_next_repair(store, package, prepare_batch_repair(store, package, mode="upload"), actor="synthetic")
    diagnostic = run["halt"]["diagnostic"]
    assert diagnostic["request_method"] == "POST" and diagnostic["quota_metric"].endswith("write_requests")
    assert diagnostic["quota_limit"] == "WriteRequestsPerMinutePerUser"
    assert calls == [1] and upload_count(network) == 1
    assert [item["status"] for item in run["items"]] == ["failed", "ready"]
    assert [item["attempts"] for item in run["items"]] == [1, 0]
    assert "private-project" not in json.dumps(diagnostic)
