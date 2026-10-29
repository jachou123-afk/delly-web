"""Exact-path metadata only against fake transport; no real NAS or file writes."""
from copy import deepcopy
import json

import pytest
import requests

from dispatch_manager import DispatchError
from synology_image_store import NasDiagnosticError, SynologyImageStore
from test_synology_image_store import Response, Session, config, original


def prepared(response=None):
    session = Session()
    store = SynologyImageStore(config(), lambda: session)
    metadata = store._metadata(original())
    path = store._path(metadata)
    good = {"success": True, "data": {"files": [
        {"path": path, "isdir": False, "additional": {"size": metadata["size"]}}]}}

    def override(params):
        if params["api"] == "SYNO.FileStation.List" and params["method"] == "getinfo":
            assert json.loads(params["path"]) == [path]
            assert json.loads(params["additional"]) == ["size"]
            return response(params) if callable(response) else response or Response(good)
        return None
    session.override = override
    return store, session, metadata, path, good


def calls(session):
    return [kwargs["data"] for _, kwargs in session.calls]


def assert_read_only(session):
    assert not any(item["api"] in {"SYNO.FileStation.Download", "SYNO.FileStation.Upload"}
                   for item in calls(session))
    assert not session.objects
    assert all(kwargs["allow_redirects"] is False and kwargs["verify"] is True
               and not kwargs.get("files") for _, kwargs in session.calls)


def test_exact_path_size_query_returns_no_paths_and_never_loads_original_bytes():
    store, session, metadata, _, _ = prepared()
    with store:
        result = store.stat_asset(metadata)
        assert result == {"sha256": metadata["sha256"], "exists": True,
                          "size": metadata["size"], "expected_size": metadata["size"], "size_matches": True}
        assert store._root_checked is False
    assert sum(item["method"] == "getinfo" for item in calls(session)) == 1
    assert sum(item["method"] == "login" for item in calls(session)) == 1
    assert sum(item["method"] == "logout" for item in calls(session)) == 1
    assert "path" not in result and "root" not in result and "name" not in result
    assert session.closed and session.trust_env is False
    assert_read_only(session)


def test_existing_file_with_wrong_size_is_diagnostic_only_not_missing_or_completed():
    store, session, metadata, _, good = prepared()
    good["data"]["files"][0]["additional"]["size"] += 1
    session.override = lambda p: Response(good) if p["api"] == "SYNO.FileStation.List" else None
    with store:
        result = store.stat_asset(metadata)
    assert result["exists"] is True and result["size_matches"] is False
    assert result["size"] == result["expected_size"] + 1
    assert_read_only(session)


def test_only_api_408_means_missing_original():
    response = Response({"success": False, "error": {"code": 408, "message": "private-path synthetic-secret"}})
    store, session, metadata, _, _ = prepared(response)
    with store:
        result = store.stat_asset(metadata)
    assert result == {"sha256": metadata["sha256"], "exists": False, "size": None,
                      "expected_size": metadata["size"], "size_matches": False}
    assert "private" not in json.dumps(result) and "secret" not in json.dumps(result)
    assert sum(item["method"] == "getinfo" for item in calls(session)) == 1
    assert_read_only(session)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 408, 429, 500, 502, 503])
def test_http_status_even_408_never_becomes_missing(status):
    store, session, metadata, _, _ = prepared(Response(
        {"success": False, "error": {"code": 408, "message": "private synthetic-secret"}}, status=status))
    with store:
        with pytest.raises(NasDiagnosticError) as error:
            store.stat_asset(metadata)
        assert error.value.kind == "http" and error.value.http_status == status
        assert error.value.api_code is None
        assert "private" not in str(error.value) and "synthetic-secret" not in str(error.value)
    assert_read_only(session)


@pytest.mark.parametrize("code", [400, 401, 402, 403, 407, 409, 410, 1000, "408", True, None])
def test_other_api_errors_and_non_integer_408_never_become_missing(code):
    store, session, metadata, _, _ = prepared(Response(
        {"success": False, "error": {"code": code, "message": "private synthetic-secret"}}))
    with store:
        with pytest.raises(NasDiagnosticError) as error:
            store.stat_asset(metadata)
        assert error.value.kind == "api"
        assert "private" not in str(error.value) and "synthetic-secret" not in str(error.value)
    assert_read_only(session)


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("files"), lambda d: d.update(files=[]), lambda d: d.update(files={}),
    lambda d: d["files"].append(deepcopy(d["files"][0])),
    lambda d: d["files"].__setitem__(0, "private-path"),
    lambda d: d["files"][0].update(path="/private/other.png"),
    lambda d: d["files"][0].update(isdir=True), lambda d: d["files"][0].update(isdir=0),
    lambda d: d["files"][0].pop("isdir"), lambda d: d["files"][0].pop("additional"),
    lambda d: d["files"][0].update(additional=[]),
    lambda d: d["files"][0]["additional"].pop("size"),
    lambda d: d["files"][0]["additional"].update(size=0),
    lambda d: d["files"][0]["additional"].update(size=-1),
    lambda d: d["files"][0]["additional"].update(size=True),
    lambda d: d["files"][0]["additional"].update(size="100"),
    lambda d: d["files"][0]["additional"].update(size=1.0),
])
def test_malformed_or_ambiguous_metadata_is_an_error_not_missing(mutate):
    store, session, metadata, _, good = prepared()
    mutate(good["data"])
    session.override = lambda p: Response(good) if p["api"] == "SYNO.FileStation.List" else None
    with store:
        with pytest.raises(NasDiagnosticError) as error:
            store.stat_asset(metadata)
        assert error.value.kind == "response_shape"
        assert "private" not in str(error.value)
    assert_read_only(session)


@pytest.mark.parametrize("change", [
    {"root": "/other"}, {"library_id": "other-library"}, {"sha256": "../private"},
    {"storage": "remote-url"}, {"schema": 2}, {"size": 0}, {"size": True},
    {"mime": "text/html"}, {"name": None},
])
def test_same_original_metadata_guards_reject_before_stat_request(change):
    store, session, metadata, _, _ = prepared()
    with store:
        before = len(session.calls)
        with pytest.raises(DispatchError, match="索引不符"):
            store.stat_asset({**metadata, **change})
        assert len(session.calls) == before
    assert_read_only(session)


@pytest.mark.parametrize("failure", ["redirect", "tls", "timeout", "connection", "invalid_json"])
def test_transport_failure_is_not_missing_and_does_not_trigger_write_or_retry(failure):
    def response(params):
        if failure == "redirect":
            result = Response({}, status=307)
            result.headers["Location"] = "https://user:synthetic-secret@private.test/path"
            return result
        if failure == "invalid_json":
            return Response(b"private-path synthetic-secret", mime="text/html")
        error = {"tls": requests.exceptions.SSLError, "timeout": requests.exceptions.ReadTimeout,
                 "connection": requests.exceptions.ConnectionError}[failure]
        raise error("private-path synthetic-secret")
    store, session, metadata, _, _ = prepared(response)
    with store:
        with pytest.raises(NasDiagnosticError) as error:
            store.stat_asset(metadata)
        assert "private" not in str(error.value) and "synthetic-secret" not in str(error.value)
    assert sum(item["method"] == "getinfo" for item in calls(session)) == 1
    assert_read_only(session)
