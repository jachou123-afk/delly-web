"""Synthetic NAS transport only: no credentials, real NAS or network access."""
import hashlib
import json

import pytest
import requests

from dispatch_manager import DispatchError
from dispatch_storage import asset_bytes, validate_image
from dispatch_fakes import image_data
from synology_image_store import API_VERSIONS, NasConfig, SynologyImageStore


def config(**changes):
    values = dict(base_url="https://nas.example.test", root="/測試商品圖庫",
                  library_id="test-library-v1", account="image_test", password="synthetic-secret")
    values.update(changes)
    return NasConfig(**values)


class Response:
    def __init__(self, data, status=200, mime="application/json", length=None):
        self.raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.status_code = status
        self.headers = {"Content-Type": mime}
        if length is not None:
            self.headers["Content-Length"] = str(length)
        self.closed = False

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.raw), chunk_size):
            yield self.raw[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class Session:
    def __init__(self):
        self.calls, self.objects, self.responses = [], {}, []
        self.closed = False
        self.trust_env = True
        self.override = None
        self.fail_upload_after_write = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        assert kwargs["verify"] is True
        assert kwargs["allow_redirects"] is False
        assert kwargs["stream"] is True
        assert kwargs["timeout"] == (8, 30)
        params = kwargs["data"]
        response = self.override(params) if self.override else None
        if response is None:
            api, method = params["api"], params["method"]
            if api == "SYNO.API.Info":
                response = Response({"success": True, "data": {
                    api: {"path": "entry.cgi", "minVersion": 1, "maxVersion": max(version, 3),
                          **({"requestFormat": "JSON"} if api != "SYNO.API.Auth" else {})}
                    for api, version in API_VERSIONS.items()
                }})
            elif api == "SYNO.API.Auth":
                if method == "login":
                    assert params["passwd"] == "synthetic-secret"
                    response = Response({"success": True, "data": {"sid": "synthetic-sid"}})
                else:
                    assert params["_sid"] == "synthetic-sid"
                    response = Response({"success": True})
            elif api == "SYNO.FileStation.List":
                response = Response({"success": True, "data": {"files": [
                    {"path": json.loads(params["path"])[0], "isdir": True}
                ]}})
            elif api == "SYNO.FileStation.Upload":
                filename, raw, mime = kwargs["files"]["file"]
                assert params["overwrite"] == "false"
                path = params["path"] + "/" + filename
                self.objects.setdefault(path, raw)
                if self.fail_upload_after_write:
                    raise requests.Timeout("synthetic-secret synthetic-sid in unsafe transport detail")
                response = Response({"success": True})
            else:
                assert api == "SYNO.FileStation.Download"
                path = json.loads(params["path"])[0]
                response = Response(self.objects[path], mime="application/octet-stream") if path in self.objects else Response({"success": False}, status=404)
        self.responses.append(response)
        return response

    def close(self):
        self.closed = True


def original():
    return validate_image(image_data(), "商品原圖.png")


def test_round_trip_original_is_lossless_and_metadata_contains_no_bytes_or_credentials():
    session = Session()
    with SynologyImageStore(config(), lambda: session) as store:
        assert store.check_root()["readable"] is True
        metadata = store.put_asset(original())
        assert asset_bytes(store.get_asset(metadata)) == image_data()
        assert metadata["sha256"] == hashlib.sha256(image_data()).hexdigest()
        assert metadata["size"] == len(image_data())
        assert "data" not in metadata and "password" not in metadata
        assert set(session.objects) == {f"/測試商品圖庫/originals/{metadata['sha256'][:2]}/{metadata['sha256']}.png"}
    assert session.closed and session.trust_env is False
    assert all(r.closed for r in session.responses)
    assert session.calls[-1][1]["data"]["method"] == "logout"
    assert all("synthetic-secret" not in url and "synthetic-sid" not in url for url, _ in session.calls)
    assert "synthetic-secret" not in repr(config())


@pytest.mark.parametrize("url", ["http://nas.example.test", "https://u:p@nas.example.test",
    "https://nas.example.test/?token=x", "https://nas.example.test/#x",
    "https://nas.example.test/path", "https://nas.example.test:bad", "https://nas.example.test\\evil"])
def test_unsafe_endpoint_rejected_without_network(url):
    with pytest.raises(DispatchError):
        config(base_url=url)


@pytest.mark.parametrize("root", ["/", "/home", "/homes", "/圖片區", "/a/../b", "/a/b", "/%2e%2e", "relative", "/x\\y"])
def test_broad_or_traversing_storage_roots_rejected(root):
    with pytest.raises(DispatchError):
        config(root=root)


def test_duplicate_upload_is_skip_and_never_changes_original_bytes():
    session = Session()
    with SynologyImageStore(config(), lambda: session) as store:
        first = store.put_asset(original())
        before = dict(session.objects)
        assert store.put_asset(original()) == first
        assert session.objects == before


@pytest.mark.parametrize("change", [
    {"root": "/another-library"}, {"library_id": "other-library"},
    {"sha256": "../../elsewhere"}, {"size": 0}, {"size": True},
    {"storage": "remote-url"}, {"mime": "text/html"}, {"mime": []},
])
def test_metadata_cannot_redirect_storage_or_read_other_files(change):
    session = Session()
    with SynologyImageStore(config(), lambda: session) as store:
        metadata = store._metadata(original())
        before = len(session.calls)
        with pytest.raises(DispatchError, match="索引不符"):
            store.get_asset({**metadata, **change})
        assert len(session.calls) == before


def test_corrupted_existing_object_is_not_overwritten_or_published():
    session = Session()
    with SynologyImageStore(config(), lambda: session) as store:
        metadata = store._metadata(original())
        path = store._path(metadata)
        session.objects[path] = b"x" * metadata["size"]
        with pytest.raises(DispatchError, match="校驗碼不符"):
            store.put_asset(original())
        assert session.objects[path] == b"x" * metadata["size"]


def test_uncertain_upload_does_not_retry_and_can_be_safely_verified_later():
    session = Session()
    with SynologyImageStore(config(), lambda: session) as store:
        session.fail_upload_after_write = True
        with pytest.raises(DispatchError, match="結果待確認") as error:
            store.put_asset(original())
        assert "synthetic-secret" not in str(error.value)
        assert sum(k["data"]["api"] == "SYNO.FileStation.Upload" for _, k in session.calls) == 1
        session.fail_upload_after_write = False
        assert asset_bytes(store.get_asset(store.put_asset(original()))) == image_data()


@pytest.mark.parametrize("failure", [
    Response({}, status=302), Response(b"<html>login page</html>", mime="text/html"),
    Response({"success": False, "error": {"code": 403, "message": "synthetic-secret"}}),
])
def test_failed_discovery_does_not_send_credentials_or_follow_redirect(failure):
    session = Session()
    session.override = lambda p: failure
    with pytest.raises(DispatchError) as error:
        with SynologyImageStore(config(), lambda: session):
            pass
    assert "synthetic-secret" not in str(error.value)
    assert len(session.calls) == 1
    assert "passwd" not in session.calls[0][1]["data"]
    assert session.closed and failure.closed


def test_bad_api_path_blocks_login():
    session = Session()
    session.override = lambda p: Response({"success": True, "data": {
        api: {"path": "../../redirect.cgi", "minVersion": 1, "maxVersion": 7}
        for api in API_VERSIONS
    }})
    with pytest.raises(DispatchError, match="未傳送登入資料"):
        with SynologyImageStore(config(), lambda: session):
            pass
    assert len(session.calls) == 1


def test_login_failure_is_not_retried_and_error_body_is_redacted():
    session = Session()
    session.override = lambda p: Response({"success": False, "error": {
        "code": 400, "message": "synthetic-secret"
    }}) if p["method"] == "login" else None
    with pytest.raises(DispatchError, match="400") as error:
        with SynologyImageStore(config(), lambda: session):
            pass
    assert "synthetic-secret" not in str(error.value)
    assert len(session.calls) == 2 and session.closed


@pytest.mark.parametrize("declared", [None, 999999999])
def test_oversized_response_is_bounded_with_or_without_content_length(declared):
    session = Session()
    session.override = lambda p: Response(b"x" * 70000, length=declared)
    with pytest.raises(DispatchError, match="超過允許大小"):
        with SynologyImageStore(config(), lambda: session):
            pass
    assert session.responses[0].closed


def test_wrong_root_readback_is_not_accepted():
    session = Session()
    session.override = lambda p: Response({"success": True, "data": {"files": [
        {"path": "/another", "isdir": True}
    ]}}) if p["api"] == "SYNO.FileStation.List" else None
    with SynologyImageStore(config(), lambda: session) as store:
        with pytest.raises(DispatchError, match="找不到指定"):
            store.check_root()


def test_missing_root_stops_before_any_image_upload():
    session = Session()
    session.override = lambda p: Response({"success": True, "data": {"files": []}}) if p["api"] == "SYNO.FileStation.List" else None
    with SynologyImageStore(config(), lambda: session) as store:
        with pytest.raises(DispatchError, match="找不到指定"):
            store.put_asset(original())
    assert not session.objects
    assert not any(k["data"]["api"] == "SYNO.FileStation.Upload" for _, k in session.calls)


@pytest.mark.parametrize("field,value", [("account", "admin"), ("account", ""), ("password", ""),
    ("library_id", ""), ("library_id", None), ("root", None), ("base_url", None)])
def test_incomplete_or_admin_configuration_is_rejected(field, value):
    with pytest.raises(DispatchError):
        config(**{field: value})


def test_network_details_never_leak_in_error_and_discovery_does_not_login():
    session = Session()
    def fail(params):
        raise requests.exceptions.SSLError("synthetic-secret in underlying transport")
    session.override = fail
    with pytest.raises(DispatchError, match="憑證") as error:
        with SynologyImageStore(config(), lambda: session):
            pass
    assert "synthetic-secret" not in str(error.value)
    assert len(session.calls) == 1 and session.closed
