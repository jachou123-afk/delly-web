"""Opt-in, per-process pacing of batch Sheets GETs; no caching or retries."""
from collections import deque
from copy import deepcopy
import math
from threading import Lock
import time

from gspread.http_client import HTTPClient
from gspread.spreadsheet import Spreadsheet

from dispatch_manager import DispatchError
from nas_dispatch_storage import NasDispatchStore

READ_LIMIT = 45
READ_WINDOW_SECONDS = 60.0
MAX_SLEEP_SECONDS = 1.0
MAX_WAIT_SECONDS = 120.0
WAIT_LIMIT_ERROR = "批次讀取等待已達上限；尚未送出請求，請稍後明確接續"


class ReadBudgetWaitExceeded(DispatchError):
    """Local pre-send wait limit; it carries no endpoint or remote payload."""
    def __init__(self):
        super().__init__(WAIT_LIMIT_ERROR)


class RollingBudget:
    """Reserve read starts in a rolling window, shared safely across threads.

    The lock protects only the small timestamp queue. Neither a wait callback
    nor sleeping nor an HTTP request runs while this lock is held.
    """
    def __init__(self, limit=READ_LIMIT, window_seconds=READ_WINDOW_SECONDS, *,
                 clock=time.monotonic, sleep=time.sleep, max_wait_seconds=MAX_WAIT_SECONDS):
        if (type(limit) is not int or limit < 1
                or type(window_seconds) not in (int, float)
                or not math.isfinite(window_seconds) or window_seconds <= 0
                or type(max_wait_seconds) not in (int, float)
                or not math.isfinite(max_wait_seconds) or max_wait_seconds <= 0
                or not callable(clock) or not callable(sleep)):
            raise DispatchError("批次讀取節奏設定不合法")
        self._limit = limit
        self._window = float(window_seconds)
        self._max_wait = float(max_wait_seconds)
        self._clock, self._sleep = clock, sleep
        self._starts = deque()
        self._lock = Lock()

    def acquire(self, on_wait=None):
        if on_wait is not None and not callable(on_wait):
            raise DispatchError("批次讀取等待回報格式不合法")
        deadline, waited = self._clock() + self._max_wait, 0.0
        while True:
            with self._lock:
                current = self._clock()
                remaining = min(deadline - current, self._max_wait - waited)
                if remaining <= 0:
                    raise ReadBudgetWaitExceeded()
                while self._starts and self._starts[0] <= current - self._window:
                    self._starts.popleft()
                if len(self._starts) < self._limit:
                    self._starts.append(current)
                    return current
                delay = min(MAX_SLEEP_SECONDS, self._starts[0] + self._window - current, remaining)
            if on_wait is not None:
                on_wait(delay)
            # A UI callback may itself take time; never sleep beyond the
            # remaining deadline. Count requested sleeps as a second bound so
            # a faulty/stale injected clock cannot make the wait loop endless.
            remaining = min(deadline - self._clock(), self._max_wait - waited)
            if remaining <= 0:
                raise ReadBudgetWaitExceeded()
            delay = min(delay, remaining)
            self._sleep(delay)
            waited += delay


# Python imports reuse this module during a Streamlit rerun; new stores/clients
# do not reset the queue. Other processes and unpaced work are outside its scope.
_PROCESS_READ_BUDGET = RollingBudget()


class ReadPacedHTTPClient(HTTPClient):
    def __init__(self, base, *, budget=None, on_wait=None):
        if type(base) is not HTTPClient:
            raise DispatchError("批次讀取節奏僅支援原生 HTTPClient；不取代自訂或自動重試連線")
        chosen = _PROCESS_READ_BUDGET if budget is None else budget
        if not isinstance(chosen, RollingBudget) or (on_wait is not None and not callable(on_wait)):
            raise DispatchError("批次讀取節奏或等待回報格式不合法")
        # With a supplied session HTTPClient does not convert/create auth. Keep
        # the exact existing authorized session and optional auth reference.
        super().__init__(getattr(base, "auth", None), session=base.session)
        if hasattr(base, "auth"):
            self.auth = base.auth
        self.timeout = base.timeout
        self._read_budget, self._on_wait = chosen, on_wait

    def request(self, method, endpoint, params=None, data=None, json=None,
                files=None, headers=None):
        if isinstance(method, str) and method.casefold() == "get":
            self._read_budget.acquire(self._on_wait)
        # Exactly one original request call. Failures propagate; no retry,
        # backoff or pacing is added to any write method.
        return super().request(method, endpoint, params=params, data=data,
                               json=json, files=files, headers=headers)


def paced_nas_store(store, *, budget=None, on_wait=None):
    """Clone one normal store for processing, leaving the original untouched.

    The normal Spreadsheet constructor performs one fresh, paced metadata GET.
    Reject custom/subclass adapters instead of silently changing their behavior.
    """
    if type(store) is not NasDispatchStore or type(store.spreadsheet) is not Spreadsheet:
        raise DispatchError("批次讀取節奏需要原生 NAS 商品圖庫與 Google 試算表連線")
    original = store.spreadsheet
    client = ReadPacedHTTPClient(original.client, budget=budget, on_wait=on_wait)
    cloned = Spreadsheet(client, deepcopy(original._properties))
    if cloned.id != original.id:
        raise DispatchError("批次讀取試算表識別不一致，已停止")
    return NasDispatchStore(cloned, store.nas_config, config_error=store.nas_config_error,
                            nas_factory=store._nas_factory)
