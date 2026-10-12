from copy import deepcopy

from test_v74_safety import ns, block


def test_reviewed_weight_does_not_remove_unrelated_cost_guards():
    ambiguous = "重量欄位「重量」未標明單個或整箱，須確認重量範圍"
    issues, unit = ns["confirmed_cost_basis"](
        [ambiguous, "原文價格矛盾"], "個", "套", unit_confirmed=True,
        weight_confirmed=True, evidence="來源確認為套價及整箱重量")
    assert issues == ["原文價格矛盾"]
    assert unit == "套"
    issues, unit = ns["confirmed_cost_basis"](
        [ambiguous], "個", "套", unit_confirmed=True, weight_confirmed=True)
    assert issues == [ambiguous] and unit == "個"


def test_different_product_approval_is_scoped_and_rechecks_saved_snapshot():
    sheets = {"S生活用品": block()}
    saved = ns["extract_saved_products"](sheets["S生活用品"])[0]
    incoming = {"code": saved["code"], "name": "不同商品陶瓷碗"}
    conflicts = ns["different_name_code_conflicts"](incoming, sheets)
    keys = [row["key"] for row in conflicts]
    assert len(keys) == 1
    assert ns["duplicate_messages"]([incoming], sheets)
    assert not ns["duplicate_messages"]([incoming], sheets, approved_code_conflicts=keys)
    assert ns["duplicate_messages"]([saved], sheets, approved_code_conflicts=keys)
    assert ns["duplicate_messages"]([incoming, incoming], sheets, approved_code_conflicts=keys)
    changed = deepcopy(sheets)
    changed["S生活用品"][1][6] = 99
    assert ns["duplicate_messages"]([incoming], changed, approved_code_conflicts=keys)
    renamed = dict(incoming, name="另一商品")
    assert ns["duplicate_messages"]([renamed], sheets, approved_code_conflicts=keys)


def test_weight_review_still_requires_valid_cost_input():
    issues, unit = ns["confirmed_cost_basis"](
        ["重量欄位「重量」未標明單個或整箱，須確認重量範圍"], "個", "個",
        weight_confirmed=True, evidence="確認為整箱")
    assert "缺少有效重量來源" in ns["cost_blockers"](10, 24, 0, 0, 0, 9, 4.8, issues, unit, "個")


def test_ui_unit_confirmation_preserves_source_and_resets_on_amount_change():
    from test_v74_ui import paste, save_button
    raw = "新品測試碗四件套\n編號:TESTSET\n每箱數量:20套\n單個價格:16.8元\n整箱重量:21kg"
    app = paste(raw)
    assert save_button(app).disabled
    next(x for x in app.text_input if x.label == "本款單位／重量確認依據").set_value("使用者確認16.8元每套，20套一箱").run()
    next(x for x in app.checkbox if x.label.startswith("已確認進價為每")).check().run()
    assert not save_button(app).disabled
    next(x for x in app.number_input if x.label == "進價(RMB)").set_value(17.0).run()
    assert save_button(app).disabled
    assert app.text_area[0].value == raw


def test_ui_different_product_review_only_allows_named_intermediary():
    from test_v74_ui import paste, save_button, VALID
    app = paste(VALID, existing=True)
    next(x for x in app.checkbox if x.label.startswith("中間商同貨號")).check().run()
    next(x for x in app.text_input if x.label == "同貨號分開建檔依據").set_value("已核對圖片，供應商不同上游使用同編號").run()
    next(x for x in app.checkbox if x.label.startswith("已核對原圖及文案：本款與上列")).check().run()
    next(x for x in app.selectbox if x.label == "📂 分頁").set_value("G正版").run()
    next(x for x in app.selectbox if x.label == "🏷️ 廠商").set_value("v菲凡").run()
    app.checkbox[-1].check()
    save_button(app).click().run()
    assert any("僅適用" in x.value for x in app.error)
    assert "test_saved_rows" not in app.session_state
    next(x for x in app.selectbox if x.label == "🏷️ 廠商").set_value("v多品村").run()
    app.checkbox[-1].check()
    save_button(app).click().run()
    assert not app.exception
    assert app.session_state["test_saved_rows"][0][1] == "新品測試收納包"
    assert app.session_state["test_evidence"]["raw"] == VALID
    assert "同貨號不同商品分開建檔" in app.session_state["test_evidence"]["notes"]
