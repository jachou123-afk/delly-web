"""Fixed upload timeout and allowlisted diagnostics; synthetic transport only."""
import json

import pytest
import requests
from urllib3.exceptions import ProtocolError

import synology_image_store as module
from synology_image_store import NasDiagnosticError, SynologyImageStore, TIMEOUT, UPLOAD_TIMEOUT
from product_image_storage import checked_image_diagnostic, image_error_diagnostic, image_error_summary
from test_synology_image_store import Response, config, original, Session
from dispatch_storage import asset_bytes


PRIVATE = "synthetic-secret synthetic-sid https://private.example.test/?token=private"


class DirectSession:
    def __init__(self, *, response=None, error=None):
        self.response, self.error, self.calls = response, error, []
    def post(self, url, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class BrokenBody(Response):
    def __init__(self, error):
        super().__init__(b"", mime="application/octet-stream")
        self.error = error
    def iter_content(self, chunk_size):
        yield b"one chunk"
        raise self.error


def direct(session):
    store = SynologyImageStore(config())
    store._session = session
    return store


def clock(monkeypatch, start=10, end=11.25):
    moments = iter([start, end])
    monkeypatch.setattr(module, "monotonic", lambda: next(moments))


@pytest.mark.parametrize("files,expected", [(None, (8, 30)), ({"file": ("fixed.png", b"fixed", "image/png")}, (60, 60))])
def test_only_explicit_multipart_uses_upload_timeout_and_all_security_flags_stay(files, expected):
    response = Response({"success": True})
    session = DirectSession(response=response)
    assert direct(session)._request("entry.cgi", {"api": "synthetic"}, files=files)[0] == response.raw
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["timeout"] == expected
    assert call["verify"] is True and call["allow_redirects"] is False and call["stream"] is True
    assert call["files"] is files and response.closed
    assert TIMEOUT == (8, 30) and UPLOAD_TIMEOUT == (60, 60)


@pytest.mark.parametrize("error,kind,cause", [
    (requests.ConnectTimeout(PRIVATE), "connect_timeout", "connect_timeout"),
    (requests.ReadTimeout(PRIVATE), "read_timeout", "read_timeout"),
    (requests.Timeout(PRIVATE), "timeout", "timeout"),
    (requests.exceptions.SSLError(PRIVATE), "tls", "tls_error"),
    (requests.ConnectionError(PRIVATE), "connection", "connection_error"),
    (requests.RequestException(PRIVATE), "request", "request_error"),
    (requests.ConnectionError(ProtocolError(PRIVATE, TimeoutError(PRIVATE))), "connection", "socket_timeout"),
])
def test_request_failure_has_phase_elapsed_native_cause_and_exactly_one_attempt(monkeypatch, error, kind, cause):
    clock(monkeypatch)
    session = DirectSession(error=error)
    with pytest.raises(NasDiagnosticError) as caught:
        direct(session)._request("entry.cgi", {}, files={"file": ("fixed.png", b"fixed", "image/png")})
    observed = caught.value
    assert observed.kind == kind
    assert observed.transport_phase == "request_or_headers" and observed.elapsed_ms == 1250
    assert observed.transport_cause == cause
    assert len(session.calls) == 1 and session.calls[0]["timeout"] == (60, 60)
    diagnostic = image_error_diagnostic(observed)
    assert diagnostic == {"kind": "nas", "origin": "typed", "nas_kind": kind,
                          "transport_phase": "request_or_headers", "elapsed_ms": 1250,
                          "transport_cause": cause}
    text = json.dumps([diagnostic, observed.safe_summary(), image_error_summary(diagnostic)], ensure_ascii=False)
    assert all(part not in text for part in ("synthetic-secret", "synthetic-sid", "private.example", "token="))


@pytest.mark.parametrize("files", [None, {"file": ("fixed.png", b"fixed", "image/png")}])
def test_streamed_response_connection_error_is_distinguished_from_request_failure(monkeypatch, files):
    clock(monkeypatch)
    response = BrokenBody(requests.ConnectionError(ProtocolError(PRIVATE, TimeoutError(PRIVATE))))
    session = DirectSession(response=response)
    with pytest.raises(NasDiagnosticError) as caught:
        direct(session)._request("entry.cgi", {}, files=files)
    error = caught.value
    assert error.kind == "connection" and error.transport_phase == "response_body"
    assert error.elapsed_ms == 1250 and error.transport_cause == "socket_timeout"
    assert response.closed and len(session.calls) == 1


@pytest.mark.parametrize("end,expected", [(9, 0), (10, 0), (10.001, 0), (11.25, 1250), (4000, 3600000),
                                        (1e308, 3600000), (10 ** 1000, 3600000),
                                        (float("nan"), None), (float("inf"), None)])
def test_elapsed_time_is_bounded_without_remote_values(monkeypatch, end, expected):
    clock(monkeypatch, end=end)
    with pytest.raises(NasDiagnosticError) as caught:
        direct(DirectSession(error=requests.ConnectionError(PRIVATE)))._request("entry.cgi", {})
    assert caught.value.elapsed_ms == expected
    diagnostic = image_error_diagnostic(caught.value)
    if expected is None:
        assert "elapsed_ms" not in diagnostic
    else:
        assert type(diagnostic["elapsed_ms"]) is int and 0 <= diagnostic["elapsed_ms"] <= 3600000


@pytest.mark.parametrize("response,kind,phase", [
    (Response({}, status=502), "http", "request_or_headers"),
    (Response(b"x" * 70000, length=70000), "response_size", "request_or_headers"),
    (Response(b"x" * 70000), "response_size", "response_body"),
])
def test_http_and_size_failures_keep_bounded_diagnostics_and_close_response(monkeypatch, response, kind, phase):
    clock(monkeypatch)
    session = DirectSession(response=response)
    with pytest.raises(NasDiagnosticError) as caught:
        direct(session)._request("entry.cgi", {})
    assert caught.value.kind == kind and caught.value.transport_phase == phase
    assert caught.value.elapsed_ms == 1250 and caught.value.transport_cause is None
    assert response.closed and len(session.calls) == 1


def test_normal_original_roundtrip_still_checks_all_original_bytes_and_no_retry():
    session = Session()
    with SynologyImageStore(config(), lambda: session) as store:
        asset = original()
        metadata = store.put_asset(asset)
        assert asset_bytes(store.get_asset(metadata)) == asset_bytes(asset)
        uploads = [kwargs for _, kwargs in session.calls if kwargs["data"]["api"] == "SYNO.FileStation.Upload"]
        assert len(uploads) == 1 and uploads[0]["timeout"] == (60, 60)
        assert all(kwargs["timeout"] == (8, 30) for _, kwargs in session.calls
                   if kwargs["data"]["api"] != "SYNO.FileStation.Upload")
        session.objects[store._path(metadata)] = b"x" * metadata["size"]
        from dispatch_manager import DispatchError
        with pytest.raises(DispatchError, match="校驗碼"):
            store.get_asset(metadata)


def test_batch_receipt_and_json_keep_only_safe_transport_fields(monkeypatch):
    import quote_image_repair_batch as batch
    from test_quote_image_repair_batch import setup
    _, store, _, package = setup(1)
    error = NasDiagnosticError(PRIVATE, kind="connection", transport_phase="request_or_headers",
                               elapsed_ms=60000, transport_cause="socket_timeout")
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(store, "put_assets", fail)
    result = batch.process_next_repair(store, package, batch.prepare_batch_repair(store, package, mode="upload"), actor="synthetic")
    expected = {"kind": "nas", "origin": "typed", "nas_kind": "connection",
                "transport_phase": "request_or_headers", "elapsed_ms": 60000,
                "transport_cause": "socket_timeout"}
    assert result["items"][0]["diagnostic"] == expected
    payload = batch.repair_receipt_payload(package, result)
    assert payload["items"][0]["diagnostic"] == expected
    assert "private.example" not in json.dumps(payload)


def test_real_batch_ui_json_preserves_safe_phase_time_cause_without_remote_text(monkeypatch):
    from test_quote_image_repair_batch_ui import confirm, enable, load_zip, start, widget
    app = confirm(load_zip(enable(start())))
    error = NasDiagnosticError(PRIVATE, kind="connection", transport_phase="response_body",
                               elapsed_ms=60001, transport_cause="read_timeout")
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(app.session_state["test_store"], "put_assets", fail)
    widget(app, "button", "處理下一批").click().run()
    assert not app.exception
    payload = [json.loads(block.value) for block in app.code if block.language == "json"][-1]
    assert all(item["diagnostic"]["transport_phase"] == "response_body"
               and item["diagnostic"]["elapsed_ms"] == 60001
               and item["diagnostic"]["transport_cause"] == "read_timeout" for item in payload["items"])
    assert "60001" in str(app.dataframe[0].value)
    assert "private.example" not in json.dumps(payload)


def test_actual_nas_pending_upload_wrapper_keeps_transport_metadata_without_index_or_retry():
    import quote_image_repair_batch as batch
    from test_quote_image_repair_batch import setup, upload_count
    from product_images import PRODUCT_IMAGE_SHEET
    from nas_dispatch_storage import LOCATION_SHEET
    sheet, store, network, package = setup(1)
    network.fail_upload = True
    run = batch.prepare_batch_repair(store, package, mode="upload")
    result = batch.process_next_repair(store, package, run, actor="synthetic")
    item = result["items"][0]
    assert item["status"] == "failed" and item["receipt"] is None
    assert item["diagnostic"]["nas_kind"] == "timeout"
    assert item["diagnostic"]["transport_phase"] == "request_or_headers"
    assert item["diagnostic"]["transport_cause"] == "timeout"
    assert type(item["diagnostic"]["elapsed_ms"]) is int
    assert upload_count(network) == 1
    assert LOCATION_SHEET not in sheet.sheets and PRODUCT_IMAGE_SHEET not in sheet.sheets
    serialized = json.dumps(batch.repair_receipt_payload(package, result))
    assert "synthetic-secret" not in serialized and "synthetic-sid" not in serialized
