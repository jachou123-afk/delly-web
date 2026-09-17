from copy import deepcopy

from batch_approval import preparation_issues, prepare_batch
from cost_audit import RULE_VERSION
from dispatch_diagnostics import batch_diagnostics, diagnose_item, overview_rows
from dispatch_fakes import FakeSpreadsheet, product_rows
from dispatch_manager import new_batch
from dispatch_storage import CloudDispatchStore


def setup_batch():
    rows = product_rows((1, 2, 3))
    rows["G正版"][1][1] = "包裝：彩盒"  # Legacy carton is a candidate, not price-unit evidence.
    store = CloudDispatchStore(FakeSpreadsheet(rows))
    batch = new_batch("合成診斷", "測試群", store.catalog(), "測試人")
    reports = {i["id"]: store.cost_audit(i["source"])[0] for i in batch["items"]}
    for i in batch["items"]:
        i["source"]["cost_audit_required"] = True
    references = {batch["items"][0]["id"]: [dict(stored_id="synthetic-image", binding_revision="r1")]}
    return store, batch, reports, references


def test_reasons_match_gate_and_reminders_never_become_errors_or_reviews():
    store, batch, reports, references = setup_batch()
    prepared, _ = prepare_batch(batch, references, reports)
    before = deepcopy(prepared)
    issues = preparation_issues(prepared, store.catalog(), [])
    result = batch_diagnostics(prepared, issues, references)
    assert prepared == before
    assert all(len(d["blockers"]) == len(issues.get(d["id"], [])) for d in result)
    first = result[0]
    assert first["blockers"][0]["field"] == "售價單位"
    assert "300個/箱" in first["blockers"][0]["current"]
    assert "不是已判定單位填錯" in first["blockers"][0]["reason"]
    assert "單位確認依據" in first["blockers"][0]["location"]
    assert first["warnings"][0]["field"] == "廠商原文"
    assert all(d["warnings"] for d in result)
    assert not any(i.get("unit_confirmation") or i.get("review") for i in prepared["items"])


def test_valid_image_binding_not_reported_missing_and_candidates_not_accepted():
    store, batch, reports, references = setup_batch()
    second = batch["items"][1]
    references[second["id"]] = [dict(name="synthetic.png")]
    prepared, _ = prepare_batch(batch, references, reports)
    result = batch_diagnostics(prepared, preparation_issues(prepared, store.catalog(), []), references)
    assert all(x["field"] != "商品圖片" for x in result[0]["blockers"])
    image = next(x for x in result[1]["blockers"] if x["field"] == "商品圖片")
    assert "候選 1 張" in image["current"] and "尚未成為有效保存配對" in image["current"]


def test_unknown_gate_stays_blocking_and_excluded_rows_do_not_pollute_active_scope():
    _, batch, _, _ = setup_batch()
    batch["items"][1].update(excluded=True, reason="本次範圍外")
    batch["items"][2].update(excluded=True, reason="")
    issues = {batch["items"][0]["id"]: ["未來新增的安全檢查"], batch["items"][2]["id"]: ["排除商品需填寫原因"]}
    result = batch_diagnostics(batch, issues)
    assert len(result) == 2
    assert result[0]["blockers"][0]["reason"] == "未來新增的安全檢查"
    assert result[1]["blockers"][0]["field"] == "排除原因"
    assert all("阻擋確認" in r["確認狀態"] for r in overview_rows(result))


def test_cost_difference_details_preserve_actual_expected_delta_and_location():
    _, batch, _, _ = setup_batch()
    item = batch["items"][0]
    reason = "到手成本（TWD）與獨立驗算不一致或原表缺值"
    item["cost_audit"] = dict(rule=RULE_VERSION, source_hash=item["source"]["source_hash"],
                              errors=[reason], rows=[{"項目": "到手成本（TWD）", "結果": "有差異",
                              "原表／原售價": "100", "重算結果": "105", "差額（原表−重算）": "-5"}])
    problem = diagnose_item(item, [reason])["blockers"][0]
    assert "原表 100，重算 105，差額 -5" in problem["current"]
    assert "G正版!A1" in problem["location"]
    assert "不要直接" in problem["action"]


def test_failed_or_stale_read_not_presented_as_missing_supplier_original():
    _, batch, _, _ = setup_batch()
    item = batch["items"][0]
    item["cost_audit"] = dict(rule=RULE_VERSION, source_hash="old", source_ready=False)
    result = diagnose_item(item, ["成本尚未獨立驗算，請開啟本款成本核對"],
                           checked={"error": "讀取原表失敗：測試斷線"})
    assert not result["warnings"]
    assert "測試斷線" in result["blockers"][0]["reason"]


def test_no_blocker_is_pending_confirmation_not_ready_to_send():
    _, batch, reports, _ = setup_batch()
    item = batch["items"][1]
    item["cost_audit"] = reports[item["id"]]
    result = diagnose_item(item, [])
    assert result["status"] == "僅提醒，待確認"
    assert not result["blockers"] and result["warnings"]
    item["cost_audit"]["source_ready"] = True
    assert diagnose_item(item, [])["status"] == "待整批確認"
