from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from streamlit.testing.v1 import AppTest

import nas_connection_check as probe
from dispatch_manager import DispatchError
from synology_image_store import NasDiagnosticError, SynologyImageStore
from test_synology_image_store import Session, Response, config


def section():
    return vars(config()).copy()


def test_real_adapter_probe_uses_two_sessions_and_one_fixed_verified_object():
    sessions, objects = [], {}

    def store_factory(settings):
        session = Session()
        session.objects = objects
        sessions.append(session)
        return SynologyImageStore(settings, lambda: session)

    result = probe.run_probe(section, store_factory)
    assert result["status"] == "passed"
    assert result["passed"] == list(probe.STEPS)
    assert 0 < result["bytes"] < 1024
    assert result["sha256"] == probe.test_asset()["sha256"]
    assert len(sessions) == 2 and all(s.closed for s in sessions)
    calls = [kw["data"] for s in sessions for _, kw in s.calls]
    assert sum(p["method"] == "login" for p in calls) == 2
    assert sum(p["method"] == "logout" for p in calls) == 2
    assert sum(p["api"] == "SYNO.FileStation.Upload" for p in calls) == 1
    assert all(p["method"] != "list_share" for p in calls)
    assert len(objects) == 1
    # A new deployment safely verifies the same path, without accumulating files.
    assert probe.run_probe(section, store_factory)["status"] == "passed"
    assert len(objects) == 1
    assert "synthetic-secret" not in json.dumps(result)
    assert result["configured_origin"] == "https://nas.example.test"
    assert "測試商品圖庫" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("changes", [
    {"root": "/homes"}, {"base_url": "http://nas.example.test"},
    {"base_url": "https://synthetic-secret@nas.example.test"},
    {"account": "admin"}, {"password": ""}, {"password": None},
    {"password": "這裡換成剛設定的NAS專用密碼"},
    {"password": "請在Streamlit這裡填入剛設定的NAS專用密碼"},
    {"library_id": "short"},
])
def test_bad_config_never_reaches_network(changes):
    calls = []
    result = probe.run_probe(lambda: {**section(), **changes}, lambda c: calls.append(c))
    assert result["status"] == "failed" and result["failed_step"] == "settings"
    assert not calls
    assert "synthetic-secret" not in json.dumps(result)


def test_missing_config_no_network_and_only_reads_nas_fields():
    assert probe.run_probe(lambda: None, lambda c: pytest.fail("network"))["status"] == "unconfigured"
    accesses = []

    class OnlyNamedFields:
        def __getitem__(self, key):
            accesses.append(key)
            return section()[key]

    probe.run_probe(lambda: OnlyNamedFields(), lambda c: (_ for _ in ()).throw(RuntimeError("stop")))
    assert accesses == list(probe.FIELDS)


@pytest.mark.parametrize("error", [KeyError("password"), ValueError("synthetic-secret parse error"),
                                        DispatchError("synthetic-secret unexpected detail")])
def test_reader_errors_redacted_and_do_not_break_quote_app(error):
    def reader():
        raise error
    result = probe.run_probe(reader, lambda c: pytest.fail("network"))
    assert result["status"] == "failed" and result["failed_step"] == "settings"
    assert "synthetic-secret" not in json.dumps(result)


def test_failed_login_once_never_writes_or_reconnects():
    session = Session()
    session.override = lambda p: Response({"success": False, "error": {"code": 400,
        "message": "synthetic-secret"}}) if p["method"] == "login" else None
    checker = probe.OncePerProcess()
    factory = lambda c: SynologyImageStore(c, lambda: session)
    first = checker.get(section, factory)
    for _ in range(4):
        assert checker.get(section, factory) == first
    assert first["failed_step"] == "connect"
    assert first["reason"] == "NAS API 回報失敗；API 代碼 400"
    calls = [kw["data"] for _, kw in session.calls]
    assert sum(p["method"] == "login" for p in calls) == 1
    assert not session.objects
    assert session.closed


@pytest.mark.parametrize("details,expected", [
    ({"kind": "read_timeout"}, "等待回應／讀取逾時"),
    ({"kind": "connect_timeout"}, "連線逾時"),
    ({"kind": "redirect", "http_status": 307}, "入口轉向；HTTP 307"),
    ({"kind": "http", "http_status": 502}, "HTTP 回應失敗；HTTP 502"),
    ({"kind": "api", "api_code": 123456}, "NAS API 回報失敗；API 代碼 123456"),
    ({"kind": "untrusted secret", "http_status": 900, "api_code": "secret"}, "未分類錯誤"),
])
def test_typed_diagnostic_receipt_preserves_only_safe_category_and_bounded_codes(details, expected):
    error = NasDiagnosticError("synthetic-secret https://private.test/path?sid=private", **details)
    assert probe._reason(error) == expected
    result = probe.run_probe(lambda: (_ for _ in ()).throw(error), lambda c: pytest.fail("network"))
    assert result["reason"] == expected and result["failed_step"] == "settings"
    assert "secret" not in json.dumps(result) and "private" not in json.dumps(result)


def test_typed_transport_probe_diagnostic_does_not_stringify_exception():
    class NoStringNasError(NasDiagnosticError):
        def __str__(self):
            raise AssertionError("typed NAS diagnostic must not stringify remote values")
    error = NoStringNasError("synthetic-private", kind="connection",
                             transport_phase="response_body", elapsed_ms=60000,
                             transport_cause="read_timeout")
    result = probe._reason(error)
    assert "60000" in result and "讀取回應內容" in result and "底層讀取逾時" in result
    assert "synthetic-private" not in result


@pytest.mark.parametrize("location,expected", [
    ("https://target.example.test:5001/private?sid=secret#fragment", "https://target.example.test:5001"),
    ("https://user:secret@target.example.test/private", ""),
    ("http://target.example.test/private", ""),
    ("/private?sid=secret", ""),
    ("https://target.example.test/private\nsecret", ""),
    ("https://target.example.test/" + "x" * 2048, ""),
])
def test_probe_receipt_exposes_only_configured_and_safe_redirect_origins(location, expected):
    session = Session()
    response = Response({}, status=307)
    response.headers["Location"] = location
    session.override = lambda p: response
    result = probe.run_probe(section, lambda c: SynologyImageStore(c, lambda: session))
    assert result["configured_origin"] == "https://nas.example.test"
    assert result.get("redirect_origin", "") == expected
    assert result["status"] == "failed" and result["failed_step"] == "connect"
    assert len(session.calls) == 1 and not session.objects
    assert "secret" not in json.dumps(result) and "private" not in json.dumps(result)


def test_probe_ui_shows_origins_without_surfacing_url_remainders(monkeypatch):
    receipt = {"status": "failed", "passed": ["settings"], "checked_at": "2026-01-01T00:00:00+00:00",
               "failed_step": "connect", "reason": "入口轉向；HTTP 307",
               "configured_origin": "https://nas.example.test",
               "redirect_origin": "https://target.example.test:5001"}
    monkeypatch.setattr(probe, "run_probe", lambda *a: receipt)
    monkeypatch.setattr(probe, "_deployment_check", probe.OncePerProcess())
    app = AppTest.from_string("from nas_connection_check import render_nas_connection_check\n"
                             "render_nas_connection_check()").run()
    assert not app.exception
    assert any("目前設定入口" in c.value and receipt["configured_origin"] in c.value for c in app.caption)
    assert any("未跟隨" in c.value and receipt["redirect_origin"] in c.value for c in app.caption)


@pytest.mark.parametrize("failed_step", ["root", "upload", "reconnect", "readback"])
def test_every_failure_stops_at_exact_stage(failed_step):
    events, sessions = [], []

    class Store:
        def __enter__(self):
            phase = "connect" if not sessions else "reconnect"
            sessions.append(self)
            events.append(phase)
            if phase == failed_step:
                raise RuntimeError("synthetic-secret")
            return self

        def __exit__(self, *args):
            events.append("closed")

        def check_root(self):
            events.append("root")
            if failed_step == "root":
                raise RuntimeError("synthetic-secret")

        def put_asset(self, asset):
            events.append("upload")
            if failed_step == "upload":
                raise RuntimeError("synthetic-secret")
            return {}

        def get_asset(self, metadata):
            events.append("readback")
            if failed_step == "readback":
                raise RuntimeError("synthetic-secret")
            return probe.test_asset()

    result = probe.run_probe(section, lambda c: Store())
    assert result["status"] == "failed" and result["failed_step"] == failed_step
    assert "synthetic-secret" not in json.dumps(result)
    if failed_step == "root":
        assert "upload" not in events
    if failed_step in {"root", "upload"}:
        assert "reconnect" not in events


def test_concurrent_viewers_cannot_repeat_probe_or_mutate_receipt(monkeypatch):
    calls = []
    monkeypatch.setattr(probe, "run_probe", lambda *a: calls.append(True) or
                        {"status": "failed", "passed": ["settings"]})
    checker = probe.OncePerProcess()
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: checker.get(section), range(48)))
    assert len(calls) == 1
    results[0]["passed"].clear()
    assert checker.get(section)["passed"] == ["settings"]


@pytest.mark.parametrize("status", ["passed", "failed", "unconfigured"])
def test_tiny_probe_keeps_once_receipt_and_large_probe_is_separate_disabled_opt_in(monkeypatch, status):
    receipt = {"status": status, "passed": ["settings"] if status != "unconfigured" else [],
               "checked_at": "2026-01-01T00:00:00+00:00", "bytes": 77,
               "failed_step": "connect", "reason": "NAS API 錯誤碼 400"}
    calls = []
    monkeypatch.setattr(probe, "run_probe", lambda *a: calls.append(True) or receipt)
    monkeypatch.setattr(probe, "_deployment_check", probe.OncePerProcess())
    app = AppTest.from_string("from nas_connection_check import render_nas_connection_check\n"
                             "render_nas_connection_check()", default_timeout=15).run()
    assert not app.exception and not app.text_input
    assert len(app.button) == 1 and app.button[0].label == "執行一次大型上傳診斷"
    assert app.button[0].disabled and not app.checkbox[0].value
    assert len(app.expander) == 1
    assert {"passed": len(app.success), "failed": len(app.warning),
            "unconfigured": len(app.info)}[status] == 1
    app.run()
    assert len(calls) == 1
