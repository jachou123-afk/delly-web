"""Request-count and conflict tests; all storage is isolated and in memory."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from gspread.utils import absolute_range_name

from test_v74_safety import block, mock_cloud, ns


def batch_cloud(monkeypatch, titles=("G正版", "W玩具", "S生活用品", "W娃娃", "D吊飾")):
    sheet, book = mock_cloud(monkeypatch)
    catalog = {title: [[]] for title in titles}
    handles = [sheet if title == "G正版" else Mock(title=title) for title in titles]
    book.worksheets.return_value = handles
    book.values_batch_get.side_effect = lambda ranges, params: {"valueRanges": [
        {"range": absolute_range_name(title, "A1:T100"), "values": deepcopy(catalog[title])}
        for title in titles]}
    store = SimpleNamespace(spreadsheet=book, worksheet=Mock(return_value=sheet))
    context = {"category": "G正版", "store": store, "sheet": sheet, "rows": [[]]}
    return sheet, book, catalog, context, handles


@pytest.mark.parametrize("free_shipping", [False, True])
def test_one_submission_reads_all_categories_once_and_formats_once(monkeypatch, free_shipping):
    sheet, book, catalog, context, handles = batch_cloud(monkeypatch)
    rows = block()
    if free_shipping:
        rows[2][8] = "廣州包郵"
    sheet.get.return_value = rows
    monkeypatch.setitem(ns, "get_dispatch_store", Mock(return_value=context["store"]))

    # Only the chosen sheet is read for NO/row allocation, and its handles then
    # remain scoped to this submission. No public catalog is read at this stage.
    snapshot = ns["get_quote_save_snapshot"]("G正版")
    assert snapshot["rows"] == [[]]
    book.worksheets.assert_not_called()
    book.values_batch_get.assert_not_called()
    assert ns["save_bulk_to_worksheet"]("G正版", rows, 3, expected_rows=[[]], save_context=snapshot)

    ns["get_dispatch_store"].assert_called_once_with()
    ns["get_credentials"].assert_not_called()
    ns["open_spreadsheet"].assert_not_called()
    book.worksheet.assert_not_called()
    book.worksheets.assert_called_once_with()
    book.values_batch_get.assert_called_once_with(
        [absolute_range_name(title) for title in catalog],
        params={"majorDimension": "ROWS", "valueRenderOption": "FORMATTED_VALUE"},
    )
    assert sheet.get_all_values.call_count == 2  # preflight and final pre-write
    for handle in handles[1:]:
        handle.get_all_values.assert_not_called()
    sheet.update.assert_called_once()
    sheet.get.assert_called_once_with("A3:L8", value_render_option="FORMULA")
    sheet.format.assert_not_called()
    sheet.batch_format.assert_called_once()
    formats = sheet.batch_format.call_args.args[0]
    assert [entry["range"] for entry in formats] == ["B3", "C3:F3", "G3:K3"] + (["I5"] if free_shipping else [])
    assert formats[0]["format"] == {"backgroundColor": {"red": 1.0, "green": 0.6, "blue": 0.0}}
    assert formats[1]["format"] == {"backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.8}}
    assert formats[2]["format"] == {"backgroundColor": {"red": 0.92, "green": 0.96, "blue": 1.0}}
    if free_shipping:
        assert formats[3]["format"] == {"backgroundColor": {"red": 0.8509804, "green": 0.91764706, "blue": 0.827451}}


def test_cross_category_duplicate_is_still_rejected(monkeypatch):
    sheet, book, catalog, context, _ = batch_cloud(monkeypatch)
    catalog["W玩具"] = block()
    assert not ns["save_bulk_to_worksheet"]("G正版", block(), 3, expected_rows=[[]], save_context=context)
    sheet.update.assert_not_called()
    assert "TEST001" in ns["st"].error.call_args.args[0]
    assert "W玩具" in ns["st"].error.call_args.args[0]


@pytest.mark.parametrize("stage", ["batch", "final"])
def test_other_computer_change_before_or_after_batch_stops_write(monkeypatch, stage):
    sheet, book, catalog, context, _ = batch_cloud(monkeypatch)
    if stage == "batch":
        catalog["G正版"] = [["other computer"]]
    else:
        sheet.get_all_values.return_value = [["other computer"]]
    assert not ns["save_bulk_to_worksheet"]("G正版", block(), 3, expected_rows=[[]], save_context=context)
    sheet.update.assert_not_called()
    sheet.batch_format.assert_not_called()
    assert "雲表已變動" in ns["st"].error.call_args.args[0]


@pytest.mark.parametrize("bad_response", [None, {}, {"valueRanges": []}])
def test_incomplete_batch_cannot_be_treated_as_an_empty_catalog(monkeypatch, bad_response):
    sheet, book, catalog, context, _ = batch_cloud(monkeypatch)
    book.values_batch_get.side_effect = None
    book.values_batch_get.return_value = bad_response
    assert not ns["save_bulk_to_worksheet"]("G正版", block(), 3, expected_rows=[[]], save_context=context)
    sheet.update.assert_not_called()
    assert "尚未寫入" in ns["st"].error.call_args.args[0]


def test_failure_after_write_never_reports_success_or_retries(monkeypatch):
    sheet, book, catalog, context, _ = batch_cloud(monkeypatch)
    sheet.get.return_value = block()
    sheet.batch_format.side_effect = TimeoutError("unknown format result")
    assert not ns["save_bulk_to_worksheet"]("G正版", block(), 3, expected_rows=[[]], save_context=context)
    assert sheet.update.call_count == sheet.batch_format.call_count == 1
    assert "可能已寫入" in ns["st"].error.call_args.args[0]


def test_connection_for_a_different_sheet_is_rejected(monkeypatch):
    sheet, book, catalog, context, _ = batch_cloud(monkeypatch)
    context["category"] = "W玩具"
    assert not ns["save_bulk_to_worksheet"]("G正版", block(), 3, expected_rows=[[]], save_context=context)
    book.values_batch_get.assert_not_called()
    sheet.update.assert_not_called()


def test_snapshot_failure_does_not_create_a_sheet(monkeypatch):
    sheet, book, catalog, context, _ = batch_cloud(monkeypatch)
    context["store"].worksheet.side_effect = KeyError("missing")
    monkeypatch.setitem(ns, "get_dispatch_store", Mock(return_value=context["store"]))
    assert ns["get_quote_save_snapshot"]("G正版") is None
    book.add_worksheet.assert_not_called()
    sheet.update.assert_not_called()
