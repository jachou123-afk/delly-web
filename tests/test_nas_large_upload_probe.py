"""Fixed large probe with in-memory stores only; never accesses NAS or Secrets."""
from copy import deepcopy
from io import BytesIO
import hashlib
import json

from PIL import Image
import pytest

from dispatch_storage import asset_bytes, validate_image
import nas_large_upload_probe as probe
from synology_image_store import NasDiagnosticError, SynologyImageStore


PRIVATE = "synthetic-private https://sensitive.example.test/?sid=private"


@pytest.mark.parametrize("policy", [None, (8, 30), [60, 60], (True, 60), (60,)])
def test_unloaded_upload_policy_stops_before_settings_or_any_store_io(monkeypatch, policy):
    monkeypatch.setattr(probe, "UPLOAD_TIMEOUT", policy)
    def forbidden(*args, **kwargs):
        pytest.fail("unloaded client policy must not read Secrets or connect")
    result = probe.run_large_upload_probe(forbidden, forbidden, confirmed=True)
    assert result["status"] == "failed" and result["client_ready"] is False
    assert result["multipart_timeout_s"] is None and result["upload_attempted"] is False


def test_unloaded_upload_policy_ui_warns_without_triggering_settings_or_controls(monkeypatch):
    import builtins
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(probe, "UPLOAD_TIMEOUT", None)
    def forbidden():
        pytest.fail("unloaded client must not read Secrets")
    monkeypatch.setattr(builtins, "_large_policy_test_reader", forbidden, raising=False)
    app = AppTest.from_string("import builtins\nfrom nas_large_upload_probe import render_large_upload_probe\n"
                             "render_large_upload_probe(builtins._large_policy_test_reader)").run()
    assert not app.exception and not app.button and not app.checkbox
    assert any("尚未載入" in warning.value for warning in app.warning)


def section():
    return dict(base_url="https://nas.example.test", root="/合成診斷專用圖庫",
                library_id="synthetic-library-v1", account="probe_test",
                password="synthetic-secret")


class NoStringNasError(NasDiagnosticError):
    calls = 0
    def __str__(self):
        type(self).calls += 1
        raise AssertionError("remote exception body must not be rendered")
    def __repr__(self):
        return "<NoStringNasError>"


class Factory:
    def __init__(self, *, existing=False):
        self.objects = {}
        if existing:
            self.objects[probe.PNG_SHA256] = probe._fixed_png_bytes()
        self.events = []
        self.sessions = 0
        self.error_at = None
        self.stat_override = None
        self.bad_put_metadata = False
        self.bad_readback = False
        self.fail_after_write = False

    def fail(self, point):
        if self.error_at == point:
            raise NoStringNasError(PRIVATE, kind="connection",
                                   transport_phase="request_or_headers", elapsed_ms=123,
                                   transport_cause="connection_reset")

    def __call__(self, settings):
        self.sessions += 1
        number = self.sessions
        owner = self
        class Store:
            def __enter__(self):
                owner.events.append((number, "connect"))
                owner.fail("connect" + str(number))
                return self
            def __exit__(self, *args):
                owner.events.append((number, "close"))
            def _metadata(self, asset):
                return SynologyImageStore(settings)._metadata(asset)
            def check_root(self):
                owner.events.append((number, "root"))
                owner.fail("root" + str(number))
            def stat_asset(self, metadata):
                owner.events.append((number, "stat"))
                owner.fail("stat")
                if owner.stat_override is not None:
                    return owner.stat_override
                exists = metadata["sha256"] in owner.objects
                return {"sha256": metadata["sha256"], "exists": exists,
                        "size": len(owner.objects[metadata["sha256"]]) if exists else None,
                        "expected_size": metadata["size"], "size_matches": exists}
            def put_asset(self, asset):
                owner.events.append((number, "upload"))
                owner.fail("upload")
                owner.objects[asset["sha256"]] = asset_bytes(asset)
                if owner.fail_after_write:
                    raise NoStringNasError(PRIVATE, kind="connection",
                                           transport_phase="request_or_headers", elapsed_ms=456,
                                           transport_cause="socket_timeout")
                metadata = self._metadata(asset)
                return {**metadata, "size": metadata["size"] + 1} if owner.bad_put_metadata else metadata
            def get_asset(self, metadata):
                owner.events.append((number, "readback"))
                owner.fail("readback" + str(number))
                if owner.bad_readback and number == 2:
                    output = BytesIO()
                    Image.new("RGB", (8, 8)).save(output, format="PNG")
                    return validate_image(output.getvalue(), "synthetic-wrong.png")
                return validate_image(owner.objects[metadata["sha256"]], probe.FILENAME)
            def __getattr__(self, name):
                raise AssertionError("unexpected store operation: " + name)
        return Store()


def assert_safe(receipt):
    output = json.dumps(receipt, ensure_ascii=False, allow_nan=False)
    assert PRIVATE not in output and "sensitive.example.test" not in output
    assert "synthetic-secret" not in output and "probe_test" not in output
    assert "/合成診斷專用圖庫" not in output
    assert "data" not in receipt and "metadata" not in receipt


def test_fixture_is_a_fixed_valid_1460_square_uncompressed_png_below_seven_mib():
    asset = probe.large_test_asset()
    raw = asset_bytes(asset)
    assert len(raw) == 6_396_813 < 7 * 1024 * 1024
    assert hashlib.sha256(raw).hexdigest() == probe.PNG_SHA256
    assert asset["name"] == probe.FILENAME and asset["mime"] == "image/png"
    with Image.open(BytesIO(raw)) as image:
        assert image.size == (1460, 1460) and image.mode == "RGB"
        assert image.getpixel((1459, 1459)) == (0, 0, 0)


@pytest.mark.parametrize("confirmed", [False, None, 0, 1, "true", [], {}])
def test_only_explicit_boolean_true_reads_settings_or_opens_a_session(confirmed):
    def forbidden(*args):
        raise AssertionError("unconfirmed probe performed IO")
    receipt = probe.run_large_upload_probe(forbidden, forbidden, confirmed=confirmed)
    assert receipt["status"] == "not_confirmed" and receipt["result"] == "not_run"
    assert receipt["upload_attempted"] is False and receipt["passed"] == []
    assert_safe(receipt)


def test_missing_settings_do_not_generate_fixture_or_open_nas(monkeypatch):
    monkeypatch.setattr(probe, "large_test_asset", lambda: pytest.fail("fixture generated"))
    receipt = probe.run_large_upload_probe(lambda: None, lambda c: pytest.fail("network"), confirmed=True)
    assert receipt["status"] == "unconfigured" and receipt["upload_attempted"] is False


def test_settings_reader_accesses_only_five_fields_without_iterating_private_container():
    accessed = []
    class OnlyFields:
        def __getitem__(self, field):
            accessed.append(field)
            return section()[field]
        def __iter__(self):
            raise AssertionError("all Secrets were iterated")
    factory = Factory()
    result = probe.run_large_upload_probe(OnlyFields, factory, confirmed=True)
    assert result["result"] == "upload_verified"
    assert accessed == list(probe.FIELDS)
    assert_safe(result)


def test_missing_fixed_object_attempts_upload_once_and_reads_it_in_a_new_session():
    factory = Factory()
    result = probe.run_large_upload_probe(section, factory, confirmed=True)
    assert result["status"] == "passed" and result["result"] == "upload_verified"
    assert result["upload_attempted"] is True
    assert factory.sessions == 2
    assert factory.events == [(1, "connect"), (1, "root"), (1, "stat"),
                              (1, "upload"), (1, "close"), (2, "connect"),
                              (2, "root"), (2, "readback"), (2, "close")]
    assert list(factory.objects) == [probe.PNG_SHA256]
    assert_safe(result)


def test_existing_object_is_read_only_and_cannot_claim_current_upload_acceptance():
    factory = Factory(existing=True)
    before = deepcopy(factory.objects)
    result = probe.run_large_upload_probe(section, factory, confirmed=True)
    assert result["status"] == "passed" and result["result"] == "upload_not_tested"
    assert result["upload_attempted"] is False
    assert factory.objects == before and factory.sessions == 2
    assert not any(event == "upload" for _, event in factory.events)
    assert sum(event == "readback" for _, event in factory.events) == 2
    assert_safe(result)


def test_real_adapter_sends_one_upload_api_then_existing_probe_sends_none():
    from test_synology_image_store import Response, Session
    sessions, objects = [], {}
    def factory(settings):
        def new_session():
            session = Session()
            session.objects = objects
            def override(params):
                if params["api"] != "SYNO.FileStation.List":
                    return None
                path = json.loads(params["path"])[0]
                if path == settings.root:
                    return None
                if path not in objects:
                    return Response({"success": False, "error": {"code": 408}})
                return Response({"success": True, "data": {"files": [{
                    "path": path, "isdir": False, "additional": {"size": len(objects[path])}}]}})
            session.override = override
            sessions.append(session)
            return session
        return SynologyImageStore(settings, new_session)
    first = probe.run_large_upload_probe(section, factory, confirmed=True)
    assert first["result"] == "upload_verified" and first["upload_attempted"] is True
    calls = [kw for session in sessions for _, kw in session.calls]
    uploads = [kw for kw in calls if kw["data"]["api"] == "SYNO.FileStation.Upload"]
    assert len(uploads) == 1
    assert uploads[0]["timeout"] == (60, 60)
    assert len(uploads[0]["files"]["file"][1]) == probe.PNG_BYTES
    assert len(sessions) == 2 and len(objects) == 1
    second = probe.run_large_upload_probe(section, factory, confirmed=True)
    assert second["result"] == "upload_not_tested" and second["upload_attempted"] is False
    calls = [kw for session in sessions for _, kw in session.calls]
    assert sum(kw["data"]["api"] == "SYNO.FileStation.Upload" for kw in calls) == 1
    assert len(sessions) == 4
    assert all(session.closed and session.trust_env is False for session in sessions)
    assert all(response.closed for session in sessions for response in session.responses)
    assert_safe(first)
    assert_safe(second)


@pytest.mark.parametrize("stat", [
    {}, [], {"exists": False},
    {"sha256": probe.PNG_SHA256, "exists": 0, "size": None,
     "expected_size": probe.PNG_BYTES, "size_matches": False},
    {"sha256": probe.PNG_SHA256, "exists": False, "size": 0,
     "expected_size": probe.PNG_BYTES, "size_matches": False},
    {"sha256": probe.PNG_SHA256, "exists": False, "size": None,
     "expected_size": True, "size_matches": False},
    {"sha256": probe.PNG_SHA256, "exists": True, "size": probe.PNG_BYTES + 1,
     "expected_size": probe.PNG_BYTES, "size_matches": False},
    {"sha256": probe.PNG_SHA256, "exists": False, "size": None,
     "expected_size": probe.PNG_BYTES, "size_matches": False, "remote": PRIVATE},
])
def test_unknown_or_inconsistent_stat_never_authorizes_upload(stat):
    factory = Factory()
    factory.stat_override = stat
    result = probe.run_large_upload_probe(section, factory, confirmed=True)
    assert result["status"] == "failed" and result["upload_attempted"] is False
    assert result["failed_step"] == "stat"
    assert factory.sessions == 1 and factory.objects == {}
    assert not any(event == "upload" for _, event in factory.events)
    assert_safe(result)


@pytest.mark.parametrize("failure", ["connect1", "root1", "stat", "upload", "connect2", "root2", "readback2"])
def test_transport_failures_have_only_typed_diagnostics_and_never_retry(failure):
    factory = Factory()
    factory.error_at = failure
    NoStringNasError.calls = 0
    result = probe.run_large_upload_probe(section, factory, confirmed=True)
    assert result["status"] == "failed" and result["result"] == "failed"
    assert NoStringNasError.calls == 0
    assert result["diagnostic"]["nas_kind"] == "connection"
    assert result["diagnostic"]["transport_cause"] == "connection_reset"
    assert sum(event == "upload" for _, event in factory.events) <= 1
    assert factory.sessions <= 2
    assert_safe(result)


def test_http_502_or_even_unhandled_api408_stat_exception_is_not_missing(monkeypatch):
    for error in [NoStringNasError(PRIVATE, kind="http", http_status=502),
                  NoStringNasError(PRIVATE, kind="api", api_code=408)]:
        factory = Factory()
        original = factory.fail
        def fail(point):
            if point == "stat":
                raise error
            original(point)
        monkeypatch.setattr(factory, "fail", fail)
        result = probe.run_large_upload_probe(section, factory, confirmed=True)
        assert result["status"] == "failed" and result["upload_attempted"] is False
        assert not any(event == "upload" for _, event in factory.events)
        assert_safe(result)


def test_upload_result_unknown_is_not_retried_and_later_explicit_call_only_reads_existing():
    factory = Factory()
    factory.fail_after_write = True
    first = probe.run_large_upload_probe(section, factory, confirmed=True)
    assert first["status"] == "failed" and first["upload_attempted"] is True
    assert factory.sessions == 1
    assert sum(event == "upload" for _, event in factory.events) == 1
    factory.fail_after_write = False
    second = probe.run_large_upload_probe(section, factory, confirmed=True)
    assert second["result"] == "upload_not_tested" and second["upload_attempted"] is False
    assert sum(event == "upload" for _, event in factory.events) == 1
    assert_safe(first)
    assert_safe(second)


@pytest.mark.parametrize("change", ["returned_metadata", "fresh_readback"])
def test_bad_metadata_or_new_session_bytes_cannot_report_upload_verified(change):
    factory = Factory()
    factory.bad_put_metadata = change == "returned_metadata"
    factory.bad_readback = change == "fresh_readback"
    result = probe.run_large_upload_probe(section, factory, confirmed=True)
    assert result["status"] == "failed" and result["result"] == "failed"
    assert result["upload_attempted"] is True
    assert sum(event == "upload" for _, event in factory.events) == 1
    assert_safe(result)


def test_safe_receipt_rejects_untrusted_values_and_remote_fields():
    value = {"status": [PRIVATE], "result": {"private": PRIVATE}, "checked_at": PRIVATE,
             "upload_attempted": "true", "passed": [PRIVATE, "stat", {"secret": PRIVATE}],
             "reason": PRIVATE, "metadata": {"path": PRIVATE},
             "diagnostic": {"kind": [], "origin": "typed", "remote": PRIVATE}}
    result = probe._safe_receipt(value)
    assert result["status"] == "failed" and result["upload_attempted"] is False
    assert result["passed"] == ["stat"]
    assert_safe(result)


@pytest.mark.parametrize("existing", [False, True])
def test_ui_requires_checkbox_and_button_and_saves_only_safe_json_once(monkeypatch, existing):
    from streamlit.testing.v1 import AppTest
    calls = []
    def run(*args, **kwargs):
        calls.append(kwargs)
        return {"status": "passed", "result": "upload_not_tested" if existing else "upload_verified",
                "upload_attempted": not existing, "passed": ["readback"],
                "reason": PRIVATE, "remote_body": PRIVATE, "metadata": {"root": PRIVATE}}
    monkeypatch.setattr(probe, "run_large_upload_probe", run)
    app = AppTest.from_string("""
import nas_large_upload_probe as probe
def forbidden():
    raise AssertionError('opening UI read Secrets')
probe.render_large_upload_probe(forbidden)
""").run()
    assert not app.exception and calls == []
    assert app.button[0].disabled
    app.checkbox[0].check().run()
    assert calls == [] and not app.button[0].disabled
    app.button[0].click().run()
    assert not app.exception and calls == [{"confirmed": True}]
    assert app.button[0].disabled
    stored = app.session_state[probe._JSON_STATE_KEY]
    assert type(stored) is str and json.loads(stored)["result"] == (
        "upload_not_tested" if existing else "upload_verified")
    assert_safe(json.loads(stored))
    assert_safe(json.loads(app.code[0].value))
    assert bool(app.warning) is existing
    assert bool(app.success) is not existing
    app.run()
    assert calls == [{"confirmed": True}]
