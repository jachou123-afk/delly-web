"""Formal app entry points share one GET budget; native HTTP stays memory-only."""
import ast
from pathlib import Path
from types import SimpleNamespace

from gspread.client import Client
import pytest

import batch_read_pacing as pacing
from quote_workbooks import ACTIVE_ID, LEGACY_ID
from test_batch_read_pacing import Clock, Session, budget

SOURCE = Path(__file__).parents[1] / "dolly_parser.py"


def app_factories(monkeypatch, gate, clock):
    monkeypatch.setattr(pacing, "_PROCESS_READ_BUDGET", gate)
    sessions, original_clients = [], []
    def authorize(credentials):
        assert credentials is sentinel
        session = Session(clock)
        sessions.append(session)
        client = Client(None, session=session)
        original_clients.append(client)
        return client
    sentinel = object()
    names = {"get_google_client", "clean_str", "open_spreadsheet", "get_quote_history",
             "get_new_quote_review_catalog", "get_dispatch_store", "get_paced_dispatch_store",
             "get_history_dispatch_store"}
    nodes = [n for n in ast.parse(SOURCE.read_text(encoding="utf-8")).body
             if isinstance(n, ast.FunctionDef) and n.name in names]
    namespace = {"gspread": SimpleNamespace(authorize=authorize), "get_credentials": lambda: sentinel,
                 "st": SimpleNamespace(secrets={}, error=lambda value: pytest.fail(value))}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace, sessions, original_clients


def test_new_history_raw_and_nas_factories_share_budget_including_first_metadata(monkeypatch):
    clock, gate = budget(2)
    app, sessions, originals = app_factories(monkeypatch, gate, clock)
    new = app["get_dispatch_store"]()
    history = app["get_history_dispatch_store"]()
    waits = []
    nas = app["get_paced_dispatch_store"](on_wait=waits.append)
    assert [new.spreadsheet.id, history.spreadsheet.id, nas.spreadsheet.id] == [ACTIVE_ID, LEGACY_ID, ACTIVE_ID]
    assert [s.calls[0]["at"] for s in sessions] == [0.0, 0.0, 60.0]
    assert len(waits) == 60
    assert all(store.spreadsheet.client._read_budget is gate for store in (new, history, nas))
    assert all(type(client.http_client) is pacing.HTTPClient for client in originals)
    new.spreadsheet.fetch_sheet_metadata()
    history.spreadsheet.fetch_sheet_metadata()
    assert clock.now() == 120.0
    assert [call["method"].upper() for s in sessions for call in s.calls] == ["GET"] * 5


def test_new_and_history_dedupe_reads_use_same_budget_without_a_write(monkeypatch):
    clock, gate = budget(2)
    app, sessions, _ = app_factories(monkeypatch, gate, clock)
    assert app["get_new_quote_review_catalog"]() == {}
    assert len(sessions) == 2
    assert sessions[0].calls[0]["url"].endswith(ACTIVE_ID)
    assert sessions[1].calls[0]["url"].endswith(LEGACY_ID)
    starts = [c["at"] for session in sessions for c in session.calls]
    assert sorted(starts) == [0.0, 0.0, 60.0, 60.0]
    assert all(c["method"].upper() == "GET" for session in sessions for c in session.calls)


def test_authorization_is_centralized_before_any_formal_workbook_open():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    authorizers = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and isinstance(call.func.value, ast.Name) and call.func.value.id == "gspread"
                        and call.func.attr == "authorize"):
                    authorizers.append(node.name)
    assert authorizers == ["get_google_client"]


def test_idempotent_client_wrap_reserves_one_slot_and_preserves_original_callback():
    clock, gate = budget(1)
    session = Session(clock)
    original = Client(None, session=session)
    first_waits, later_waits = [], []
    first = pacing.paced_google_client(original, budget=gate, on_wait=first_waits.append)
    again = pacing.paced_google_client(first, budget=gate)
    assert again is first and again.http_client is first.http_client
    callback_view = pacing.paced_google_client(first, budget=gate, on_wait=later_waits.append)
    assert callback_view is not first and callback_view.http_client is not first.http_client
    assert callback_view.http_client._read_budget is first.http_client._read_budget is gate
    first.open_by_key("synthetic-id")
    callback_view.open_by_key("synthetic-id")
    assert [c["at"] for c in session.calls] == [0.0, 60.0]
    assert not first_waits and len(later_waits) == 60
    assert type(original.http_client) is pacing.HTTPClient


def test_paced_nas_adapter_accepts_already_paced_store_without_second_reservation(monkeypatch):
    clock, gate = budget(2)
    app, sessions, _ = app_factories(monkeypatch, gate, clock)
    original = app["get_dispatch_store"]()
    clone = pacing.paced_nas_store(original, budget=gate)
    assert clone.spreadsheet.client is original.spreadsheet.client
    assert len(sessions[0].calls) == 2 and [c["at"] for c in sessions[0].calls] == [0.0, 0.0]
    assert not clock.sleeps


def test_rewrapping_cannot_change_existing_budget_or_hide_automatic_retry_client():
    clock, gate = budget(2)
    session = Session(clock)
    paced = pacing.paced_google_client(Client(None, session=session), budget=gate)
    _, other = budget(2)
    with pytest.raises(pacing.DispatchError, match="不可切換預算"):
        pacing.paced_google_client(paced, budget=other)
    from gspread.http_client import BackOffHTTPClient
    unsafe = Client(None, session=session, http_client=BackOffHTTPClient)
    with pytest.raises(pacing.DispatchError):
        pacing.paced_google_client(unsafe, budget=gate)
    assert not session.calls


@pytest.mark.parametrize("after_write", [False, True])
def test_local_budget_failure_keeps_pre_post_write_boundary_and_never_retries(monkeypatch, after_write):
    from test_quote_save_performance import batch_cloud
    from test_v74_safety import block, ns
    sheet, book, _, context, _ = batch_cloud(monkeypatch)
    if after_write:
        sheet.get.side_effect = pacing.ReadBudgetWaitExceeded()
    else:
        book.values_batch_get.side_effect = pacing.ReadBudgetWaitExceeded()
    assert not ns["save_bulk_to_worksheet"]("G正版", block(), 3, expected_rows=[[]], save_context=context)
    assert sheet.update.call_count == int(after_write)
    errors = " ".join(str(call.args[0]) for call in ns["st"].error.call_args_list)
    assert ("可能已寫入" if after_write else "尚未寫入") in errors
