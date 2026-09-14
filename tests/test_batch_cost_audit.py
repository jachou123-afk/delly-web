from copy import deepcopy
from types import SimpleNamespace

import pytest

from batch_cost_audit import batch_signature, merge_selection, run_batch_audit
from batch_cost_ui import _table_changed
from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_manager import DispatchError, new_batch
from dispatch_storage import CloudDispatchStore


def fixture(numbers=range(1, 70)):
    spreadsheet = FakeSpreadsheet(product_rows(numbers))
    store = CloudDispatchStore(spreadsheet)
    batch = new_batch("測試整批驗算", "測試群組", store.catalog(), "測試核對人")
    return spreadsheet, store, batch


def test_all_69_get_results_in_order_without_reviews_or_any_cloud_writes():
    spreadsheet, store, batch = fixture()
    before_batch, before_rows = deepcopy(batch), deepcopy(spreadsheet.sheets["G正版"].rows)
    calls, progress = [], []
    ws = spreadsheet.sheets["G正版"]
    original = ws.batch_get
    def bounded(ranges, value_render_option=None):
        calls.append((ranges, value_render_option))
        return original(ranges, value_render_option)
    ws.batch_get = bounded
    result = run_batch_audit(store, batch, [i["id"] for i in batch["items"]], lambda *p: progress.append(p))
    assert len(result["rows"]) == len(result["ids"]) == len(result["checks"]) == 69
    assert [r["順序"] for r in result["rows"]] == list(range(1, 70))
    assert result["counts"] == {"計算一致": 69}
    assert {r["來源依據"] for r in result["rows"]} == {"待補資料"}
    assert {r["重算成本"] for r in result["rows"]} == {"47.6"}
    assert len(calls) == 12 and max(len(c[0]) for c in calls) == 20
    assert progress[-1] == (69, 69)
    assert batch == before_batch and spreadsheet.sheets["G正版"].rows == before_rows
    assert set(spreadsheet.sheets) == {"G正版"}


def test_subset_includes_excluded_items_and_never_relies_on_old_cost_flag():
    _, store, batch = fixture(range(1, 5))
    batch["items"][2]["excluded"] = True
    batch["items"][2]["reason"] = "等待確認"
    for item in batch["items"]:
        item["source"].pop("cost_audit_required", None)
    result = run_batch_audit(store, batch, ["G正版:no3", "G正版:no1"])
    assert result["ids"] == ["G正版:no1", "G正版:no3"]
    assert "暫緩／排除" in result["rows"][1]["需處理"]
    assert batch["items"][2]["excluded"]
    assert all(not i["review"] for i in batch["items"])


def test_stale_missing_duplicate_and_good_products_are_all_listed(monkeypatch):
    _, store, batch = fixture(range(1, 5))
    fresh = deepcopy(store.catalog())
    fresh[0]["source_hash"] = "changed"
    fresh = [fresh[0], fresh[2], fresh[2], fresh[3]]
    monkeypatch.setattr(store, "catalog", lambda: fresh)
    result = run_batch_audit(store, batch, [i["id"] for i in batch["items"]])
    assert [r["計算結果"] for r in result["rows"]] == ["來源已變動"] * 3 + ["計算一致"]
    assert all(r["重算成本"] == "—" for r in result["rows"][:3])


def test_failed_read_chunk_is_not_skipped_and_later_chunks_continue(monkeypatch):
    spreadsheet, store, batch = fixture()
    ws = spreadsheet.sheets["G正版"]
    original = ws.batch_get
    def failing(ranges, value_render_option=None):
        if ranges[0] == "A121:L126":
            raise TimeoutError("測試：這一段讀取逾時")
        return original(ranges, value_render_option)
    monkeypatch.setattr(ws, "batch_get", failing)
    result = run_batch_audit(store, batch, [i["id"] for i in batch["items"]])
    assert len(result["rows"]) == 69
    assert result["counts"] == {"計算一致": 49, "讀取失敗": 20}
    assert all("逾時" in r["需處理"] for r in result["rows"][20:40])
    assert result["rows"][-1]["計算結果"] == "計算一致"


def test_change_during_formula_read_is_detected(monkeypatch):
    spreadsheet, store, batch = fixture(range(1, 3))
    ws = spreadsheet.sheets["G正版"]
    original = ws.batch_get
    def change(ranges, value_render_option=None):
        values = original(ranges, value_render_option)
        if value_render_option == "FORMULA":
            ws.rows[0][11] = "v另一供應商"
        return values
    monkeypatch.setattr(ws, "batch_get", change)
    result = run_batch_audit(store, batch, [i["id"] for i in batch["items"]])
    assert [r["計算結果"] for r in result["rows"]] == ["來源已變動", "計算一致"]


@pytest.mark.parametrize("failure", ["catalog", "evidence", "incomplete"])
def test_complete_failure_still_has_a_row_for_every_selected_item(monkeypatch, failure):
    spreadsheet, store, batch = fixture(range(1, 4))
    def fail(*a, **kw):
        raise RuntimeError("測試讀取失敗")
    if failure == "catalog":
        monkeypatch.setattr(store, "catalog", fail)
    elif failure == "evidence":
        monkeypatch.setattr(store, "_load_evidence", fail)
    else:
        ws = spreadsheet.sheets["G正版"]
        original = ws.batch_get
        monkeypatch.setattr(ws, "batch_get", lambda *a, **kw: original(*a, **kw)[:-1])
    result = run_batch_audit(store, batch, [i["id"] for i in batch["items"]])
    assert len(result["rows"]) == 3
    assert result["counts"] == {"讀取失敗": 3}


def test_wrong_cost_and_missing_inputs_are_not_counted_as_passed():
    spreadsheet, store, _ = fixture(range(1, 4))
    rows = spreadsheet.sheets["G正版"].rows
    rows[1][10], rows[1][2] = "99", "110"
    rows[9][1] = "重量 未提供"
    batch = new_batch("測試", "測試群", store.catalog(), "測試人")
    result = run_batch_audit(store, batch, [i["id"] for i in batch["items"]])
    assert result["rows"][0]["計算結果"] == "有差異"
    assert result["rows"][0]["成本差額"] == "51.4"
    assert result["rows"][1]["計算結果"] == "資料／公式待處理"
    assert result["rows"][2]["計算結果"] == "計算一致"


@pytest.mark.parametrize("selected", [[], ["unknown"]])
def test_invalid_selection_never_reads_cloud(monkeypatch, selected):
    _, store, batch = fixture(range(1, 3))
    monkeypatch.setattr(store, "catalog", lambda: pytest.fail("must not read"))
    with pytest.raises(DispatchError):
        run_batch_audit(store, batch, selected)


def test_selection_keeps_hidden_ids_and_maps_sorted_original_positions():
    assert merge_selection({"a", "hidden"}, ["a", "b"], [1]) == {"b", "hidden"}
    assert merge_selection({"b", "hidden"}, ["a", "b"], []) == {"hidden"}
    assert merge_selection({"hidden"}, ["a", "b"], [9]) == {"hidden"}


def test_cell_navigation_and_multirow_selection_are_independent(monkeypatch):
    import batch_cost_ui
    _, _, batch = fixture(range(1, 4))
    visible = [i["id"] for i in batch["items"]]
    focus = "dispatch_focus_draft_" + batch["id"]
    state = {"selected": {visible[0]}, "table": {"selection": {"rows": [0, 2], "cells": []}}, focus: visible[0]}
    monkeypatch.setattr(batch_cost_ui, "st", SimpleNamespace(session_state=state))
    _table_changed("table", "selected", visible, batch)
    assert state["selected"] == {visible[0], visible[2]} and state[focus] == visible[0]
    state["table"]["selection"]["cells"] = [[1, "品號"]]
    _table_changed("table", "selected", visible, batch)
    assert state["selected"] == {visible[0], visible[2]} and state[focus] == visible[1]
    state["table"]["selection"] = {"cells": [[2, "品號"]]}
    _table_changed("table", "selected", visible, batch, select_rows=False)
    assert state["selected"] == {visible[0], visible[2]} and state[focus] == visible[2]


def test_signature_expires_when_source_or_revision_changes():
    _, _, batch = fixture(range(1, 3))
    signature = batch_signature(batch)
    other = deepcopy(batch)
    other["items"][0]["source"]["source_hash"] = "other"
    assert batch_signature(other) != signature
    batch["_revision"] = "new revision"
    assert batch_signature(batch) != signature
