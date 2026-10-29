"""Fake clocks and sessions only; no waiting in real time or external services."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import inspect
from threading import Lock
from types import SimpleNamespace

from gspread.exceptions import APIError
from gspread.http_client import BackOffHTTPClient, HTTPClient
from gspread.spreadsheet import Spreadsheet
import pytest
import requests

from dispatch_manager import DispatchError
from nas_dispatch_storage import NasDispatchStore
import batch_read_pacing as pacing
from test_synology_image_store import config


class Clock:
    def __init__(self):
        self.current = 0.0
        self.sleeps = []
        self.lock = Lock()

    def now(self):
        with self.lock:
            return self.current

    def sleep(self, seconds):
        assert type(seconds) in (int, float) and 0 < seconds <= 1
        with self.lock:
            self.sleeps.append(seconds)
            self.current += seconds

    def advance(self, seconds):
        with self.lock:
            self.current += seconds


class Session:
    def __init__(self, clock=None):
        self.calls = []
        self.clock = clock
        self.status = 200
        self.error = None
        self.properties = {"title": "synthetic-book", "locale": "zh_TW"}
        self.lock = Lock()

    def request(self, **kwargs):
        with self.lock:
            self.calls.append({**kwargs, "at": self.clock.now() if self.clock else None})
        if self.error:
            raise self.error
        body = ({"properties": deepcopy(self.properties), "sheets": []} if self.status == 200 else
                {"error": {"code": self.status, "message": "private synthetic-secret"}})
        return SimpleNamespace(ok=self.status == 200, status_code=self.status, json=lambda: body,
                               text="private synthetic-secret")


def budget(limit=45, window=60):
    clock = Clock()
    return clock, pacing.RollingBudget(limit, window, clock=clock.now, sleep=clock.sleep)


def client(limit=45, window=60):
    clock, gate = budget(limit, window)
    session = Session(clock)
    base = HTTPClient(None, session=session)
    base.auth = object()
    base.timeout = (8, 30)
    return clock, gate, session, base, pacing.ReadPacedHTTPClient(base, budget=gate)


def test_45_reads_are_immediate_and_46th_waits_for_exact_rolling_boundary():
    clock, gate = budget()
    assert [gate.acquire() for _ in range(45)] == [0.0] * 45
    assert clock.sleeps == []
    waits = []
    assert gate.acquire(waits.append) == 60.0
    assert waits == clock.sleeps == [1.0] * 60
    assert gate.acquire() == 60.0


def test_boundary_before_60_seconds_retains_last_slot_until_it_expires():
    clock, gate = budget(1)
    assert gate.acquire() == 0
    clock.advance(59.75)
    assert gate.acquire() == 60
    assert clock.sleeps == [0.25]


def test_budget_uses_rolling_window_instead_of_resetting_at_wall_clock_minute():
    clock, gate = budget(2)
    gate.acquire()
    clock.advance(20)
    gate.acquire()
    assert gate.acquire() == 60
    assert gate.acquire() == 80


def test_sleep_and_callback_are_both_outside_budget_lock():
    clock, gate = budget(1)
    gate.acquire()
    def prove_unlocked(seconds):
        assert type(seconds) in (int, float) and 0 < seconds <= 1
        assert gate._lock.acquire(blocking=False)
        gate._lock.release()
    def sleeping(seconds):
        prove_unlocked(seconds)
        clock.sleep(seconds)
    gate._sleep = sleeping
    assert gate.acquire(prove_unlocked) == 60


def test_concurrent_reservations_never_exceed_shared_rolling_budget():
    clock, gate = budget(3, 2)
    with ThreadPoolExecutor(max_workers=12) as pool:
        starts = sorted(pool.map(lambda _: gate.acquire(), range(60)))
    assert len(starts) == 60
    for number, start in enumerate(starts):
        assert sum(0 <= start - previous < 2 for previous in starts[:number + 1]) <= 3
    assert clock.sleeps and all(0 < value <= 1 for value in clock.sleeps)


def test_request_signature_and_all_kwargs_preserve_gspread_621_contract():
    assert list(inspect.signature(pacing.ReadPacedHTTPClient.request).parameters) == list(inspect.signature(HTTPClient.request).parameters)
    _, _, session, base, paced = client()
    params, body, payload, files, headers = {"ranges": "A1"}, b"original", {"values": [[1]]}, {"f": b"x"}, {"X-Synthetic": "yes"}
    paced.request("GET", "https://sheets.example.test/metadata", params, body, payload, files, headers)
    call = session.calls[0]
    assert call["method"] == "GET" and call["url"] == "https://sheets.example.test/metadata"
    for key, value in (("params", params), ("data", body), ("json", payload), ("files", files), ("headers", headers)):
        assert call[key] is value
    assert call["timeout"] == base.timeout and paced.session is base.session and paced.auth is base.auth
    assert len(session.calls) == 1


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "post", "put", "delete", "patch"])
@pytest.mark.parametrize("failed", [False, True])
def test_write_methods_are_never_paced_or_retried(method, failed):
    clock, gate, session, _, paced = client(1)
    gate.acquire()  # Exhaust the read budget without sending anything.
    session.status = 429 if failed else 200
    if failed:
        with pytest.raises(APIError):
            paced.request(method, "https://sheets.example.test/write")
    else:
        paced.request(method, "https://sheets.example.test/write")
    assert clock.now() == 0 and clock.sleeps == [] and len(session.calls) == 1


@pytest.mark.parametrize("failure", ["success", "429", "500", "network"])
def test_get_success_or_failure_sends_only_once_and_consumes_one_read_slot(failure):
    clock, _, session, _, paced = client(1)
    if failure in {"429", "500"}:
        session.status = int(failure)
        with pytest.raises(APIError):
            paced.request("get", "https://sheets.example.test/read")
    elif failure == "network":
        session.error = requests.ConnectionError("private synthetic-secret")
        with pytest.raises(requests.ConnectionError):
            paced.request("get", "https://sheets.example.test/read")
    else:
        paced.request("get", "https://sheets.example.test/read")
    assert len(session.calls) == 1 and clock.now() == 0
    session.status, session.error = 200, None
    paced.request("GET", "https://sheets.example.test/read-next")
    assert len(session.calls) == 2 and clock.now() == 60


def test_new_clients_share_process_budget_instead_of_resetting_on_rerun(monkeypatch):
    clock, gate = budget(2)
    monkeypatch.setattr(pacing, "_PROCESS_READ_BUDGET", gate)
    session = Session(clock)
    base = HTTPClient(None, session=session)
    for _ in range(3):
        pacing.ReadPacedHTTPClient(base).request("get", "https://sheets.example.test/read")
    assert [call["at"] for call in session.calls] == [0, 0, 60]


def test_wait_callback_receives_only_bounded_seconds_and_cancellation_sends_nothing():
    _, gate, session, base, _ = client(1)
    gate.acquire()
    received = []
    def cancel(seconds):
        received.append(seconds)
        raise RuntimeError("synthetic cancellation")
    paced = pacing.ReadPacedHTTPClient(base, budget=gate, on_wait=cancel)
    with pytest.raises(RuntimeError, match="cancellation"):
        paced.request("get", "https://private.example.test/path?secret=private")
    assert received == [1.0] and not session.calls


def test_clone_preserves_original_session_auth_timeout_id_and_nas_settings_without_mutating_store():
    clock, gate = budget()
    session = Session(clock)
    base = HTTPClient(None, session=session)
    base.auth, base.timeout = object(), (8, 30)
    original_sheet = Spreadsheet(base, {"id": "synthetic-id"})
    factory = lambda settings: pytest.fail("cloning must not open NAS")
    original = NasDispatchStore(original_sheet, config(), config_error=True, nas_factory=factory)
    original._worksheet_handles["sentinel"] = object()
    properties, original_handle = deepcopy(original_sheet._properties), original._worksheet_handles["sentinel"]
    before = len(session.calls)
    cloned = pacing.paced_nas_store(original, budget=gate)
    assert type(cloned) is NasDispatchStore and cloned is not original
    assert cloned.spreadsheet is not original_sheet and cloned.spreadsheet.id == original_sheet.id
    assert type(cloned.spreadsheet.client) is pacing.ReadPacedHTTPClient
    assert cloned.spreadsheet.client.session is base.session and cloned.spreadsheet.client.auth is base.auth
    assert cloned.spreadsheet.client.timeout == base.timeout
    assert cloned.nas_config is original.nas_config and cloned.nas_config_error is True and cloned._nas_factory is factory
    assert cloned._worksheet_handles == {} and cloned._worksheets == {}
    assert original._worksheet_handles["sentinel"] is original_handle and original_sheet._properties == properties
    assert len(session.calls) == before + 1  # One new paced metadata GET from normal constructor.
    assert len(gate._starts) == 1
    assert type(original_sheet.client) is HTTPClient


def test_clone_metadata_request_is_paced_and_preserves_same_sheet_identifier():
    clock, gate = budget(1)
    session = Session(clock)
    base = HTTPClient(None, session=session)
    sheet = Spreadsheet(base, {"id": "synthetic-id"})
    original = NasDispatchStore(sheet, config())
    gate.acquire()
    cloned = pacing.paced_nas_store(original, budget=gate)
    assert clock.now() == 60 and cloned.spreadsheet.id == "synthetic-id"
    assert session.calls[-1]["url"].endswith("/synthetic-id")


@pytest.mark.parametrize("kind", ["backoff", "custom", "already_paced"])
def test_custom_backoff_or_already_paced_clients_are_rejected_without_requests(kind):
    _, gate, session, base, _ = client()
    class CustomHTTPClient(HTTPClient):
        pass
    candidate = (BackOffHTTPClient(None, session=session) if kind == "backoff" else
                 CustomHTTPClient(None, session=session) if kind == "custom" else
                 pacing.ReadPacedHTTPClient(base, budget=gate))
    with pytest.raises(DispatchError):
        pacing.ReadPacedHTTPClient(candidate, budget=gate)
    assert not session.calls


def test_custom_store_and_nonstandard_spreadsheet_are_not_silently_replaced():
    _, gate, session, base, _ = client()
    sheet = Spreadsheet(base, {"id": "synthetic-id"})
    class CustomStore(NasDispatchStore):
        pass
    before = len(session.calls)
    for candidate in (CustomStore(sheet, config()), NasDispatchStore(object(), config())):
        with pytest.raises(DispatchError):
            pacing.paced_nas_store(candidate, budget=gate)
    assert len(session.calls) == before


def test_metadata_cannot_change_the_cloned_sheet_id_or_mutate_original_properties():
    _, gate, session, base, _ = client()
    sheet = Spreadsheet(base, {"id": "synthetic-id"})
    before = deepcopy(sheet._properties)
    session.properties["id"] = "unexpected-other-id"
    with pytest.raises(DispatchError, match="識別不一致"):
        pacing.paced_nas_store(NasDispatchStore(sheet, config()), budget=gate)
    assert sheet.id == "synthetic-id" and sheet._properties == before


@pytest.mark.parametrize("limit,window", [(True, 60), (0, 60), (1, 0), (1, True), (1, float("inf")), (1, float("nan"))])
def test_invalid_budget_configuration_is_rejected(limit, window):
    with pytest.raises(DispatchError):
        pacing.RollingBudget(limit, window)


@pytest.mark.parametrize("value", [True, 0, -1, float("inf"), float("nan"), "120"])
def test_invalid_per_get_wait_limit_is_rejected(value):
    with pytest.raises(DispatchError):
        pacing.RollingBudget(max_wait_seconds=value)


def test_per_get_deadline_does_not_send_or_reserve_an_extra_slot():
    clock = Clock()
    gate = pacing.RollingBudget(1, 60, clock=clock.now, sleep=clock.sleep, max_wait_seconds=2.5)
    gate.acquire()
    before = list(gate._starts)
    session = Session(clock)
    paced = pacing.ReadPacedHTTPClient(HTTPClient(None, session=session), budget=gate)
    with pytest.raises(pacing.ReadBudgetWaitExceeded) as error:
        paced.request("GET", "https://sheets.example.test/read")
    assert str(error.value) == pacing.WAIT_LIMIT_ERROR
    assert clock.sleeps == [1.0, 1.0, 0.5] and clock.now() == 2.5
    assert list(gate._starts) == before and not session.calls


def test_competing_readers_cannot_starve_one_get_past_its_wait_deadline():
    clock = Clock()
    peers = []
    gate = pacing.RollingBudget(1, 2, clock=clock.now, sleep=clock.sleep, max_wait_seconds=5)
    gate.acquire()
    def competition(seconds):
        clock.sleep(seconds)
        if clock.now() in (2, 4):
            peers.append(gate.acquire())
    gate._sleep = competition
    with pytest.raises(pacing.ReadBudgetWaitExceeded, match="等待已達上限"):
        gate.acquire()
    assert clock.now() == 5 and peers == [2, 4]
    assert list(gate._starts) == [4]


def test_stale_clock_still_bounds_wait_iterations_without_real_time_or_request():
    sleeps = []
    gate = pacing.RollingBudget(1, 60, clock=lambda: 0.0, sleep=sleeps.append, max_wait_seconds=2.5)
    gate.acquire()
    before = list(gate._starts)
    session = Session()
    paced = pacing.ReadPacedHTTPClient(HTTPClient(None, session=session), budget=gate)
    with pytest.raises(pacing.ReadBudgetWaitExceeded, match="尚未送出請求"):
        paced.request("GET", "https://sheets.example.test/read")
    assert sleeps == [1.0, 1.0, 0.5]
    assert list(gate._starts) == before and not session.calls


def test_wait_callback_time_is_accounted_before_sleeping_again():
    clock = Clock()
    gate = pacing.RollingBudget(1, 60, clock=clock.now, sleep=clock.sleep, max_wait_seconds=2.5)
    gate.acquire()
    def ui_wait(seconds):
        clock.advance(2)
    with pytest.raises(pacing.ReadBudgetWaitExceeded, match="等待已達上限"):
        gate.acquire(ui_wait)
    assert clock.sleeps == [0.5] and clock.now() == 2.5


def test_local_wait_exception_constructor_has_one_fixed_payload_only():
    error = pacing.ReadBudgetWaitExceeded()
    assert isinstance(error, DispatchError) and str(error) == pacing.WAIT_LIMIT_ERROR
    with pytest.raises(TypeError):
        pacing.ReadBudgetWaitExceeded("private endpoint or payload")
