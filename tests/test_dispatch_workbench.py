from copy import deepcopy

import pytest

from dispatch_manager import DispatchError, approve_batch, batch_digest, record_observation
from dispatch_storage import BATCH_SHEET, CloudDispatchStore
from dispatch_fakes import FakeSpreadsheet, image_data, ready_batch
from dispatch_workbench import prepare_workbench, current_item, card_images, complete_current, advance_run


def setup_batch():
    store = CloudDispatchStore(FakeSpreadsheet())
    batch = ready_batch(store)
    batch = store.save_batch(approve_batch(batch, store.catalog(), [], "核對人"))
    return store, batch


def prepare(store, batch, ids=None):
    return prepare_workbench(store, batch, ids or [i["id"] for i in batch["items"]],
                             "Computer Use 查驗", history_checked=True)


def test_second_check_is_read_only_and_freezes_verified_originals():
    store, batch = setup_batch()
    before = deepcopy(store.spreadsheet.sheets[BATCH_SHEET].rows)
    run = prepare(store, batch)
    assert run["items"] == [i["id"] for i in batch["items"]]
    assert run["actor"] == "Computer Use 查驗"
    assert run["completed"] == []
    assert store.spreadsheet.sheets[BATCH_SHEET].rows == before
    assert card_images(run, current_item(batch, run))[0][1] == image_data()


def test_draft_cannot_enter_and_history_attestation_is_required():
    store, batch = setup_batch()
    with pytest.raises(DispatchError, match="聊天室"):
        prepare_workbench(store, batch, [batch["items"][0]["id"]], "核對人")
    batch["status"] = "draft"
    with pytest.raises(DispatchError, match="批次未確認"):
        prepare(store, batch)


@pytest.mark.parametrize("part", ["image", "text", "uncertain"])
def test_partial_or_uncertain_items_cannot_enter_fast_send(part):
    store, batch = setup_batch()
    changed = record_observation(batch, item_id=batch["items"][0]["id"], part=part,
                                 evidence="合成查驗", actor="測試人", target=batch["target"])
    saved = store.save_batch(changed, batch["_revision"])
    with pytest.raises(DispatchError):
        prepare(store, saved)


def test_source_formula_and_image_changes_block_preflight():
    store, batch = setup_batch()
    source = store.spreadsheet.sheets["G正版"]
    source.rows[0][1] = "來源已修改"
    with pytest.raises(DispatchError, match="來源資料"):
        prepare(store, batch)
    store, batch = setup_batch()
    ws = store.spreadsheet.sheets["G正版"]
    ws.formula_override = [[""] * 12 for _ in range(6)]
    with pytest.raises(DispatchError, match="成本公式"):
        prepare(store, batch)
    store, batch = setup_batch()
    with pytest.raises(DispatchError, match="圖片"):
        prepare_workbench(store, batch, [batch["items"][0]["id"]], "測試", history_checked=True,
                           read_asset=lambda identity: {"data": "", "sha256": identity})


def test_review_cannot_be_skipped_by_forging_approved_status():
    store, batch = setup_batch()
    changed = deepcopy(batch)
    changed["items"][0]["review"] = None
    changed = store.save_batch(changed, batch["_revision"])
    with pytest.raises(DispatchError, match="尚未逐款核對"):
        prepare(store, changed)


def test_missing_source_requires_original_explicit_acknowledgement(monkeypatch):
    store, batch = setup_batch()
    # Synthetic legacy report, isolate the domain acknowledgement from the live
    # fingerprint re-read (which has its own formula-change tests).
    changed = deepcopy(batch)
    changed["items"][0]["cost_audit"]["source_ready"] = False
    changed["approved_digest"] = batch_digest(changed)
    changed["batch_confirmation"] = {"digest": changed["approved_digest"], "missing_source_ids": []}
    changed = store.save_batch(changed, batch["_revision"])
    monkeypatch.setattr(store, "verify_cost_checks", lambda *a, **k: None)
    with pytest.raises(DispatchError):
        prepare(store, changed)
    changed["batch_confirmation"]["missing_source_ids"] = [changed["items"][0]["id"]]
    changed = store.save_batch(changed, changed["_revision"])
    run = prepare(store, changed)
    assert run["missing_source_ids"] == [changed["items"][0]["id"]]


def test_completion_requires_current_card_and_real_visual_attestation():
    store, batch = setup_batch()
    run = prepare(store, batch)
    first, second = batch["items"][:2]
    with pytest.raises(DispatchError, match="實際看到"):
        complete_current(batch, run, first["id"])
    with pytest.raises(DispatchError, match="目前"):
        complete_current(batch, run, second["id"], observed=True)
    updated = complete_current(batch, run, first["id"], observed=True, note="合成 LINE 15:30 圖文")
    assert len(updated["observations"]) == 2
    assert "合成 LINE 15:30" in updated["observations"][0]["evidence"]
    assert updated["observations"][0]["actor"] == run["actor"]
    assert updated["observations"][0]["target"] == run["target"]
    assert "非 LINE 訊息時間" in updated["observations"][0]["evidence"]
    assert not batch["observations"]
    with pytest.raises(DispatchError, match="寫後核對"):
        advance_run(run, updated, first["id"])
    saved = store.save_batch(updated, batch["_revision"])
    advanced = advance_run(run, saved, first["id"])
    assert current_item(saved, advanced)["id"] == second["id"]
    with pytest.raises(DispatchError):
        complete_current(saved, advanced, first["id"], observed=True)


def test_completed_cards_never_repeat_cost_catalog_or_asset_reads(monkeypatch):
    store, batch = setup_batch()
    run = prepare(store, batch)

    def unexpected(*args, **kwargs):
        raise AssertionError("repeated expensive preflight")

    for name in ("verify_cost_checks", "catalog", "get_asset", "list_batches"):
        monkeypatch.setattr(store, name, unexpected)
    for item in batch["items"]:
        assert current_item(batch, run)["id"] == item["id"]
        assert card_images(run, item)
        updated = complete_current(batch, run, item["id"], observed=True)
        batch = store.save_batch(updated, batch["_revision"])
        run = advance_run(run, batch, item["id"])
    assert current_item(batch, run) is None
    assert batch["status"] == "in_progress"  # No fabricated final reconciliation.


@pytest.mark.parametrize("mutation", ["revision", "copy", "target", "blocked"])
def test_changed_snapshot_and_pause_invalidate_ticket(mutation):
    store, batch = setup_batch()
    run = prepare(store, batch)
    if mutation == "revision":
        batch["_revision"] = "another-save"
    elif mutation == "copy":
        batch["items"][0]["copy"] += "改文案"
    elif mutation == "target":
        batch["target"] = "別的群"
    else:
        run["blocked"] = True
    with pytest.raises(DispatchError):
        current_item(batch, run)


def test_other_device_update_blocks_preparation():
    store, batch = setup_batch()
    store.save_batch(batch, batch["_revision"])
    with pytest.raises(DispatchError, match="另一台"):
        prepare(store, batch)


def test_only_changed_selection_needs_second_check():
    store, batch = setup_batch()
    store.spreadsheet.sheets["G正版"].rows[0][1] = "有問題先排除"
    selected = [i["id"] for i in batch["items"][1:]]
    run = prepare(store, batch, selected)
    assert run["items"] == selected
