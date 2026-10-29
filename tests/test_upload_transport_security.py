"""Independent transport diagnostics security tests; no network or real secrets."""
import json
import socket
import ssl
from http.client import RemoteDisconnected

import pytest
import requests
from urllib3.exceptions import (ConnectTimeoutError, NameResolutionError,
                                NewConnectionError, ProtocolError, ReadTimeoutError)

from product_image_storage import (checked_image_diagnostic, image_error_diagnostic,
                                   image_error_summary)
from synology_image_store import NasDiagnosticError, safe_transport_cause


PRIVATE = "synthetic-private https://sensitive.example.test/?sid=private"


def assert_private_absent(value):
    text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    assert "synthetic-private" not in text
    assert "sensitive.example.test" not in text
    assert "sid=private" not in text


@pytest.mark.parametrize("error,expected", [
    (socket.timeout(PRIVATE), "socket_timeout"),
    (ConnectionResetError(PRIVATE), "connection_reset"),
    (ConnectionAbortedError(PRIVATE), "connection_aborted"),
    (BrokenPipeError(PRIVATE), "broken_pipe"),
    (RemoteDisconnected(PRIVATE), "remote_disconnected"),
    (socket.gaierror(11001, PRIVATE), "dns_error"),
    (ssl.SSLError(PRIVATE), "tls_error"),
    (ProtocolError(PRIVATE), "protocol_error"),
    (requests.ConnectTimeout(PRIVATE), "connect_timeout"),
    (requests.ReadTimeout(PRIVATE), "read_timeout"),
    (ConnectTimeoutError(PRIVATE), "connect_timeout"),
    (ReadTimeoutError(None, PRIVATE, PRIVATE), "read_timeout"),
    (requests.ConnectionError(PRIVATE), "connection_error"),
    (requests.Timeout(PRIVATE), "timeout"),
    (requests.RequestException(PRIVATE), "request_error"),
    (RuntimeError(PRIVATE), "unknown"),
], ids=["socket-timeout", "reset", "aborted", "broken-pipe", "remote-disconnected",
        "dns", "tls", "protocol", "requests-connect", "requests-read",
        "urllib3-connect", "urllib3-read", "connection", "timeout", "request", "unknown"])
def test_only_fixed_native_type_classification_is_returned(error, expected):
    assert safe_transport_cause(error) == expected


def test_socket_timeout_nested_in_protocol_args_is_found_without_parsing_text():
    inner = socket.timeout(PRIVATE)
    middle = ProtocolError(PRIVATE, inner)
    outer = requests.ConnectionError(middle)
    assert safe_transport_cause(outer) == "socket_timeout"
    assert safe_transport_cause(requests.ConnectionError("TimeoutError: " + PRIVATE)) == "connection_error"


def test_new_connection_failure_is_not_inferred_to_be_connect_timeout():
    # urllib3 makes NewConnectionError a ConnectTimeoutError subclass even for
    # refused/generic connection failures; Requests deliberately excludes it.
    error = NewConnectionError(None, PRIVATE)
    assert safe_transport_cause(error) == "connection_error"
    dns = NameResolutionError("sensitive.example.test", None, socket.gaierror(11001, PRIVATE))
    assert safe_transport_cause(dns) == "dns_error"


@pytest.mark.parametrize("chain", ["cause", "context", "suppressed_context"])
def test_native_cause_survives_safe_exception_links(chain):
    inner = ConnectionResetError(PRIVATE)
    outer = requests.ConnectionError(PRIVATE)
    if chain == "cause":
        outer.__cause__ = inner
    else:
        outer.__context__ = inner
        if chain == "suppressed_context":
            outer.__suppress_context__ = True
    assert safe_transport_cause(outer) == "connection_reset"


def test_unknown_objects_and_args_containers_are_not_traversed_or_rendered():
    touched = []
    class PoisonedContainer:
        @property
        def __class__(self):
            touched.append("class")
            raise AssertionError("unknown __class__ attribute was touched")
        def __iter__(self):
            touched.append("iter")
            raise AssertionError("arbitrary args container was iterated")
        def __str__(self):
            touched.append("str")
            raise AssertionError("untrusted value was rendered")
        def __repr__(self):
            touched.append("repr")
            raise AssertionError("untrusted value was rendered")
    container = PoisonedContainer()
    assert safe_transport_cause(container) == "unknown"
    assert safe_transport_cause(RuntimeError(container)) == "unknown"
    assert safe_transport_cause(RuntimeError([socket.timeout(PRIVATE)])) == "unknown"
    assert safe_transport_cause(RuntimeError({"cause": socket.timeout(PRIVATE)})) == "unknown"
    assert touched == []


def test_native_descriptors_bypass_poisoned_exception_properties_and_strings():
    touched = []
    class PoisonedConnection(requests.ConnectionError):
        @property
        def args(self):
            touched.append("args")
            raise AssertionError("overridden args property was touched")
        @property
        def __cause__(self):
            touched.append("cause")
            raise AssertionError("overridden cause property was touched")
        @property
        def __context__(self):
            touched.append("context")
            raise AssertionError("overridden context property was touched")
        def __str__(self):
            touched.append("str")
            raise AssertionError("untrusted exception string was rendered")
        def __repr__(self):
            touched.append("repr")
            raise AssertionError("untrusted exception repr was rendered")
    error = PoisonedConnection.__new__(PoisonedConnection)
    BaseException.__init__(error, ProtocolError(PRIVATE, socket.timeout(PRIVATE)))
    assert safe_transport_cause(error) == "socket_timeout"
    assert touched == []


def test_exception_cycle_and_more_than_sixteen_nodes_are_bounded():
    error = RuntimeError(PRIVATE)
    error.__cause__ = error
    assert safe_transport_cause(error) == "unknown"
    current = socket.timeout(PRIVATE)
    for _ in range(20):
        current = RuntimeError(PRIVATE, current)
    assert safe_transport_cause(current) == "unknown"


def test_constructor_and_storage_export_never_stringify_original_exception():
    class NoStringNasError(NasDiagnosticError):
        calls = 0
        def __str__(self):
            type(self).calls += 1
            raise AssertionError("original NAS exception must not be rendered")
        def __repr__(self):
            return "<NoStringNasError>"
    error = NoStringNasError(PRIVATE, kind="connection",
                             transport_phase="request_or_headers", elapsed_ms=123,
                             transport_cause="socket_timeout")
    diagnostic = image_error_diagnostic(error)
    assert diagnostic == {"kind": "nas", "origin": "typed", "nas_kind": "connection",
                          "transport_phase": "request_or_headers", "elapsed_ms": 123,
                          "transport_cause": "socket_timeout"}
    assert checked_image_diagnostic(diagnostic) == diagnostic
    summary = image_error_summary(diagnostic)
    assert NasDiagnosticError.TRANSPORT_PHASE_LABELS["request_or_headers"] in summary
    assert NasDiagnosticError.TRANSPORT_CAUSE_LABELS["socket_timeout"] in summary
    assert NoStringNasError.calls == 0
    assert_private_absent({"diagnostic": diagnostic, "summary": summary})


@pytest.mark.parametrize("field,value", [
    ("transport_phase", "upload_write_guessed"),
    ("transport_phase", []),
    ("transport_phase", {}),
    ("transport_cause", "custom " + PRIVATE),
    ("transport_cause", []),
    ("transport_cause", {}),
    ("elapsed_ms", True),
    ("elapsed_ms", False),
    ("elapsed_ms", -1),
    ("elapsed_ms", 3600001),
    ("elapsed_ms", "123"),
    ("elapsed_ms", 1.0),
    ("elapsed_ms", float("nan")),
    ("elapsed_ms", float("inf")),
    ("elapsed_ms", []),
    ("elapsed_ms", {}),
])
def test_constructor_invalid_transport_fields_become_none_and_raw_json_is_rejected(field, value):
    error = NasDiagnosticError(PRIVATE, kind="connection", **{field: value})
    assert getattr(error, field) is None
    diagnostic = {"kind": "nas", "origin": "typed", "nas_kind": "connection", field: value}
    assert checked_image_diagnostic(diagnostic) is None
    cleaned = image_error_diagnostic(error)
    assert field not in cleaned
    assert_private_absent(cleaned)


@pytest.mark.parametrize("elapsed", [0, 3600000])
def test_elapsed_boundaries_are_real_integers_and_strict_json_safe(elapsed):
    error = NasDiagnosticError(PRIVATE, kind="connection",
                             transport_phase="response_body", elapsed_ms=elapsed,
                             transport_cause="unknown")
    value = image_error_diagnostic(error)
    assert value["elapsed_ms"] == elapsed
    assert type(value["elapsed_ms"]) is int
    assert checked_image_diagnostic(value) == value
    assert_private_absent(value)


@pytest.mark.parametrize("kind,origin", [
    ("google_sheets_api", "typed"), ("http", "typed"),
    ("network_timeout", "typed"), ("network_connection", "typed"),
    ("read_budget_wait", "local"),
])
@pytest.mark.parametrize("field,value", [
    ("transport_phase", "request_or_headers"), ("elapsed_ms", 1),
    ("transport_cause", "socket_timeout"),
])
def test_non_nas_categories_cannot_smuggle_any_transport_metadata(kind, origin, field, value):
    assert checked_image_diagnostic({"kind": kind, "origin": origin, field: value}) is None


def test_unknown_extra_remote_fields_are_never_exported_as_safe_transport_json():
    value = {"kind": "nas", "origin": "typed", "nas_kind": "connection",
             "transport_phase": "request_or_headers", "elapsed_ms": 1,
             "transport_cause": "socket_timeout", "remote_body": PRIVATE}
    assert checked_image_diagnostic(value) is None


def test_older_urllib3_without_name_resolution_subclass_keeps_safe_diagnostics(monkeypatch):
    import synology_image_store as module
    monkeypatch.delattr(module.urllib3_errors, "NameResolutionError", raising=False)
    assert module.safe_transport_cause(NewConnectionError(None, PRIVATE)) == "connection_error"
    wrapped = requests.ConnectionError(ProtocolError(PRIVATE, socket.gaierror(11001, PRIVATE)))
    assert module.safe_transport_cause(wrapped) == "dns_error"
